# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Global tone curve -> 8-bit LUT or display gamma ramp."""
from __future__ import annotations

import functools

import numpy as np

from .params import EnhanceParams

_KNEE = 0.7  # highlight compression starts here


def build_curve(params: EnhanceParams, n: int = 256) -> np.ndarray:
    """Float64 curve in [0,1], monotonic non-decreasing, sampled at n points."""
    p = params.validated()
    x = np.linspace(0.0, 1.0, n)
    y = x.copy()

    if p.gamma != 1.0:
        y = y ** (1.0 / p.gamma)

    if p.shadow_lift > 0.0:
        # y*(1-y)^4 vanishes at both ends (black stays anchored, white untouched)
        # and peaks in the shadows. Gain 3 keeps the derivative >= 0.34 for
        # shadow_lift <= 1, so the curve remains monotonic.
        y = y + 3.0 * p.shadow_lift * y * (1.0 - y) ** 4

    if p.contrast != 1.0 or p.brightness != 0.0:
        y = (y - 0.5) * p.contrast + 0.5 + p.brightness

    # Overshoot above white is squeezed into [_KNEE, 1] instead of hard-clipped,
    # so lifted bright areas keep their gradations. Blended with a hard clip by
    # highlight_protect. Identity whenever nothing overshoots.
    ymax = y[-1]
    hard = np.clip(y, 0.0, 1.0)
    if p.highlight_protect > 0.0 and ymax > 1.0:
        t = np.clip((y - _KNEE) / (ymax - _KNEE), 0.0, None)
        soft = np.where(y > _KNEE, _KNEE + (1.0 - _KNEE) * t, y)
        soft = np.clip(soft, 0.0, 1.0)
        y = (1.0 - p.highlight_protect) * hard + p.highlight_protect * soft
    else:
        y = hard

    if p.black_floor > 0.0:
        y = p.black_floor + (1.0 - p.black_floor) * y

    return np.maximum.accumulate(np.clip(y, 0.0, 1.0))


def build_lut(params: EnhanceParams) -> np.ndarray:
    """uint8 (256,) LUT; identity params give exactly arange(256)."""
    c = build_curve(params, 256)
    lut = np.rint(c * 255.0).astype(np.uint8)
    return np.maximum.accumulate(lut)


def build_float_lut(params: EnhanceParams) -> np.ndarray:
    """float32 (256,) tone LUT in [0,1]: the unrounded, monotonic curve.

    build_lut() is round(this * 255) (up to float32 ties); the float version
    keeps the sub-LSB detail that the 8-bit LUT throws away, so the output
    stage can dither instead of banding."""
    return build_curve(params, 256).astype(np.float32)


def _bayer(n: int) -> np.ndarray:
    m = np.zeros((1, 1), dtype=np.int64)
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return m


BAYER8 = _bayer(8)  # (8, 8) ints 0..63, each exactly once


def bayer_glsl() -> str:
    """The Bayer matrix as a GLSL int array initialiser, row-major [y][x]."""
    return ", ".join(str(int(v)) for v in BAYER8.ravel())


def _bayer_rank_levels() -> np.ndarray:
    return (np.arange(64, dtype=np.float32) + 0.5) / 64.0


@functools.lru_cache(maxsize=4)
def bayer_index(h: int, w: int) -> np.ndarray:
    """uint8 (h, w) Bayer rank 0..63 of each pixel position (fixed pattern)."""
    out = np.tile(BAYER8.astype(np.uint8), (-(-h // 8), -(-w // 8)))[:h, :w]
    out = np.ascontiguousarray(out)
    out.flags.writeable = False  # cached and shared
    return out


def dither_threshold(h: int, w: int) -> np.ndarray:
    """float32 (h, w) static ordered-dither thresholds in (0, 1), mean 0.5.

    Output code = floor(value * 255 + threshold): with threshold 0.5 this is
    plain rounding, and the +-0.5 LSB spread makes the result unbiased. The
    pattern is fixed per pixel position (no temporal change, so no flicker)
    and is the same one the GPU shader derives from gl_FragCoord."""
    return _bayer_rank_levels()[bayer_index(h, w)]


def dithered_lut_table(flut: np.ndarray) -> np.ndarray:
    """(64, 256) uint8: row k is floor(flut * 255 + (k + 0.5) / 64), clipped.
    Lets the CPU apply tone + dither as one table lookup per sample."""
    x = flut.astype(np.float32)[None, :] * np.float32(255.0) + _bayer_rank_levels()[:, None]
    return np.clip(np.floor(x), 0.0, 255.0).astype(np.uint8)


def curve_to_ramp(params: EnhanceParams, size: int = 256) -> np.ndarray:
    """(3, size) uint16 display gamma ramp sampled straight from the float
    curve, so the only rounding is the final 16-bit one (<= 0.5/65535)."""
    c = build_curve(params, size)
    r = np.rint(np.clip(c, 0.0, 1.0) * 65535.0).astype(np.uint16)
    r = np.maximum.accumulate(r)
    return np.stack([r, r, r])


def lut_to_ramp(lut_or_curve: np.ndarray, size: int = 256) -> np.ndarray:
    """Convert a uint8 LUT or float curve (1-D, or 3xN per-channel) to a
    (3, size) uint16 gamma ramp (0..65535), resampled and made monotonic."""
    a = np.asarray(lut_or_curve)
    if a.dtype == np.uint8:
        a = a.astype(np.float64) / 255.0
    else:
        a = a.astype(np.float64)
    if a.ndim == 1:
        a = np.stack([a, a, a])
    if a.ndim != 2 or a.shape[0] != 3 or a.shape[1] < 2:
        raise ValueError("expected a 1-D curve or a (3, N) per-channel curve")
    src = np.linspace(0.0, 1.0, a.shape[1])
    dst = np.linspace(0.0, 1.0, size)
    rows = []
    for ch in a:
        r = np.interp(dst, src, np.clip(ch, 0.0, 1.0))
        r = np.maximum.accumulate(r)
        rows.append(np.rint(r * 65535.0).astype(np.uint16))
    return np.stack(rows)


def ramp_is_safe(ramp: np.ndarray) -> bool:
    """Sanity gate before handing a ramp to the OS.

    A display gamma ramp is applied to the whole screen, including the UI the
    user needs to undo it. A bad ramp (non-monotonic, all-black, nearly flat or
    washed out) can leave the screen unreadable, so we refuse anything that:
    is not (3, N>=2), has non-finite values, decreases, tops out below 25% of
    full range (screen goes near-black), starts above 50% (washed out), or
    spans less than 10% of the range (flat, no usable image).
    """
    try:
        r = np.asarray(ramp)
        if r.ndim != 2 or r.shape[0] != 3 or r.shape[1] < 2:
            return False
        r = r.astype(np.float64)
        if not np.all(np.isfinite(r)):
            return False
        full = 65535.0
        if np.any(np.diff(r, axis=1) < 0):
            return False
        if np.any(r.max(axis=1) < 0.25 * full):
            return False
        if np.any(r.min(axis=1) > 0.5 * full):
            return False
        if np.any(r.max(axis=1) - r.min(axis=1) < 0.1 * full):
            return False
        return True
    except Exception:
        return False
