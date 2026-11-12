# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Frame enhancement for capture mode (BGR/BGRA uint8)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .curves import build_lut
from .params import EnhanceParams

_ID_LUT = np.arange(256, dtype=np.uint8)


@dataclass(frozen=True)
class _State:
    """Everything process() needs, precomputed. Swapped as one reference so a
    worker thread never sees a half-updated mix of old and new settings."""
    params: EnhanceParams
    lut: Optional[np.ndarray]
    clahe: Optional[object]
    needs_lab: bool


def _make_state(params: EnhanceParams) -> _State:
    p = params.validated()
    lut = build_lut(p)
    lut = None if np.array_equal(lut, _ID_LUT) else lut
    clahe = None
    if p.local_contrast > 0.0:
        clahe = cv2.createCLAHE(clipLimit=3.0,
                                tileGridSize=(p.clahe_tiles, p.clahe_tiles))
    needs_lab = (clahe is not None or p.clarity > 0.0 or p.sharpen > 0.0
                 or p.saturation != 1.0)
    return _State(p, lut, clahe, needs_lab)


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
            bgr = cv2.LUT(bgr, st.lut)  # new array; input untouched

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
