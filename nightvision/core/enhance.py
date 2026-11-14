# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Frame enhancement for capture mode (BGR/BGRA uint8)."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .curves import bayer_index, build_float_lut, build_lut, dithered_lut_table
from .params import EnhanceParams

_ID_LUT = np.arange(256, dtype=np.uint8)


@dataclass(frozen=True)
class _State:
    """Everything process() needs, precomputed. Swapped as one reference so a
    worker thread never sees a half-updated mix of old and new settings."""
    params: EnhanceParams
    lut: Optional[np.ndarray]  # (64*256,) uint8 tone+dither table, None when identity
    split: Optional[tuple]     # (lo, k) uint8 (256,) equivalent of lut, or None if not expressible
    clahe: Optional[object]
    needs_lab: bool


def _make_state(params: EnhanceParams) -> _State:
    p = params.validated()
    # Identity is decided on the 8-bit LUT (as before), the applied curve is
    # the unrounded float one.
    table = (None if np.array_equal(build_lut(p), _ID_LUT)
             else dithered_lut_table(build_float_lut(p)))
    lut = None if table is None else table.ravel()
    split = None if table is None else _split_table(table)
    clahe = None
    if p.local_contrast > 0.0:
        clahe = cv2.createCLAHE(clipLimit=3.0,
                                tileGridSize=(p.clahe_tiles, p.clahe_tiles))
    needs_lab = (clahe is not None or p.clarity > 0.0 or p.sharpen > 0.0
                 or p.saturation != 1.0)
    return _State(p, lut, split, clahe, needs_lab)


def _split_table(table: np.ndarray) -> Optional[tuple]:
    """Rewrite the (64, 256) table as lo[v] + (rank >= k[v]).

    The thresholds are increasing and span less than one code, so each column
    steps up by at most one, at most once, as the rank grows. That turns the
    per-sample gather into two byte LUTs and a compare (all SIMD in OpenCV).
    Returns None, keeping the gather path, if the table does not have that form."""
    lo = table[0].copy()
    up = table > lo[None, :]
    k = np.where(up.any(axis=0), up.argmax(axis=0), 64).astype(np.uint8)
    rebuilt = lo[None, :] + (np.arange(64)[:, None] >= k[None, :])
    if not np.array_equal(rebuilt, table):
        return None
    return lo, k


_STRIP = 64  # rows per strip; a multiple of 8 so every strip sees the same pattern
_STRIP_SPLIT = 256  # larger strips: OpenCV's per-call threading overhead dominates below this


@functools.lru_cache(maxsize=2)
def _rank_plane(w: int, ch: int) -> np.ndarray:
    """uint16 (_STRIP, w, ch): Bayer rank << 8, the row offset into the table."""
    r = bayer_index(_STRIP, w).astype(np.uint16) << 8
    out = np.ascontiguousarray(np.repeat(r[:, :, None], ch, axis=2))
    out.flags.writeable = False
    return out


@functools.lru_cache(maxsize=2)
def _rank_plane8(w: int, ch: int) -> np.ndarray:
    """uint8 (_STRIP_SPLIT, w, ch): Bayer rank 0..63 at each sample."""
    r = bayer_index(_STRIP_SPLIT, w)
    out = np.ascontiguousarray(np.repeat(r[:, :, None], ch, axis=2))
    out.flags.writeable = False
    return out


@functools.lru_cache(maxsize=2)
def _ones(w: int, ch: int) -> np.ndarray:
    out = np.ones((_STRIP_SPLIT, w, ch), np.uint8)
    out.flags.writeable = False
    return out


def _tone_split(bgr: np.ndarray, lo: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Same result as _tone_dithered(bgr, table) for a table `_split_table` accepts."""
    h, w, ch = bgr.shape
    rank, one = _rank_plane8(w, ch), _ones(w, ch)
    out = np.empty((h, w, ch), np.uint8)
    for y in range(0, h, _STRIP_SPLIT):
        n = min(_STRIP_SPLIT, h - y)
        s = bgr[y:y + n]
        m = cv2.compare(rank[:n], cv2.LUT(s, k), cv2.CMP_GE)  # 255 where the code steps up
        cv2.bitwise_and(m, one[:n], dst=m)
        cv2.add(cv2.LUT(s, lo), m, dst=out[y:y + n])
    return out


def _tone_dithered(bgr: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Float tone curve + static 8x8 Bayer dither, quantised to uint8.

    Same math as the GPU path, code = floor(curve(v) * 255 + threshold), done
    as one lookup in a (64 thresholds x 256 levels) table. Works in row strips
    so the index temporaries stay small (a full-frame uint16 index would add
    two frame-sized buffers per call). The GPU tone pass goes through a
    half-float target, so the two can differ by one code on a small fraction
    of pixels."""
    h, w, ch = bgr.shape
    rank = _rank_plane(w, ch)
    out = np.empty((h, w, ch), np.uint8)
    for y in range(0, h, _STRIP):
        n = min(_STRIP, h - y)
        idx = cv2.add(bgr[y:y + n], rank[:n], dtype=cv2.CV_16U)
        table.take(idx, out=out[y:y + n], mode="clip")
    return out


def _unsharp(l: np.ndarray, blurred: np.ndarray, amount: float) -> np.ndarray:
    return cv2.addWeighted(l, 1.0 + amount, blurred, -amount, 0.0)


class FrameEnhancer:
    """Not safe for concurrent process() calls from several threads (CLAHE
    objects are stateful), but set_params() may be called from any thread."""

    def __init__(self, params: Optional[EnhanceParams] = None):
        self._state = _make_state(params or EnhanceParams())

    @property
    def params(self) -> EnhanceParams:
        return self._state.params

    def set_params(self, params: EnhanceParams) -> None:
        # Build fully first, then a single attribute assignment (atomic in CPython).
        self._state = _make_state(params)

    def process(self, frame: np.ndarray) -> np.ndarray:
        st = self._state  # snapshot once for the whole frame
        if frame.ndim != 3 or frame.shape[2] not in (3, 4) or frame.dtype != np.uint8:
            raise ValueError("expected HxWx3 or HxWx4 uint8 frame")
        if st.lut is None and not st.needs_lab:
            return frame.copy()

        alpha = None
        if frame.shape[2] == 4:
            alpha = frame[:, :, 3]
            bgr = np.ascontiguousarray(frame[:, :, :3])
        else:
            bgr = frame

        if st.lut is not None:
            # new array; input untouched
            bgr = (_tone_split(bgr, *st.split) if st.split is not None
                   else _tone_dithered(bgr, st.lut))

        if st.needs_lab:
            bgr = self._lab_stage(bgr, st)

        if alpha is None:
            return bgr if bgr is not frame else bgr.copy()
        out = np.empty_like(frame)
        out[:, :, :3] = bgr
        out[:, :, 3] = alpha
        return out

    @staticmethod
    def _lab_stage(bgr: np.ndarray, st: _State) -> np.ndarray:
        p = st.params
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l = lab[:, :, 0]
        l = np.ascontiguousarray(l)

        if st.clahe is not None:
            l2 = st.clahe.apply(l)
            l = cv2.addWeighted(l, 1.0 - p.local_contrast, l2, p.local_contrast, 0.0)

        if p.clarity > 0.0:
            # Large-radius blur is done at 1/4 resolution: same look, far cheaper.
            h, w = l.shape
            small = cv2.resize(l, (max(1, w // 4), max(1, h // 4)),
                               interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 6.0)
            big = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
            l = _unsharp(l, big, p.clarity * 0.8)

        if p.sharpen > 0.0:
            l = _unsharp(l, cv2.GaussianBlur(l, (0, 0), 1.2), p.sharpen)

        lab[:, :, 0] = l
        if p.saturation != 1.0:
            ab = lab[:, :, 1:].astype(np.float32)
            ab = (ab - 128.0) * p.saturation + 128.0
            lab[:, :, 1:] = np.clip(ab + 0.5, 0, 255).astype(np.uint8)
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def enhance_frame(frame: np.ndarray, params: EnhanceParams) -> np.ndarray:
    return FrameEnhancer(params).process(frame)
