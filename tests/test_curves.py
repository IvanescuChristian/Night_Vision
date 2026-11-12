# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import numpy as np
import pytest

from nightvision.core.curves import build_curve, build_lut, lut_to_ramp, ramp_is_safe
from nightvision.core.params import EnhanceParams


def test_identity_lut_exact():
    assert np.array_equal(build_lut(EnhanceParams()), np.arange(256))
    lut = build_lut(EnhanceParams())
    assert lut.shape == (256,) and lut.dtype == np.uint8


def test_curve_range_and_shape():
    c = build_curve(EnhanceParams(gamma=2), n=64)
    assert c.shape == (64,) and c.dtype == np.float64
    assert c.min() >= 0 and c.max() <= 1


def _random_params(rng):
    return EnhanceParams(
        gamma=rng.uniform(0.3, 3), shadow_lift=rng.uniform(0, 1),
        black_floor=rng.uniform(0, 0.25), brightness=rng.uniform(-0.5, 0.5),
        contrast=rng.uniform(0.5, 2), highlight_protect=rng.uniform(0, 1))


def test_monotonic_random_grid():
    rng = np.random.default_rng(1234)
    for _ in range(500):
        p = _random_params(rng)
        assert np.all(np.diff(build_lut(p).astype(int)) >= 0), p
        assert np.all(np.diff(build_curve(p)) >= 0), p


def test_shadow_lift_raises_lows_keeps_white():
    base = build_lut(EnhanceParams())
    lut = build_lut(EnhanceParams(shadow_lift=1.0))
    assert lut[0] == 0
    assert all(lut[i] > base[i] for i in (10, 20, 40, 60))
    assert lut[255] >= 254
    # fades out by mid-tones
    assert lut[200] - base[200] < lut[40] - base[40]


def test_black_floor_raises_black():
    assert build_lut(EnhanceParams(black_floor=0.1))[0] >= 25


def test_highlight_protect_reduces_clipping():
    def clipped(hp):
        return int((build_lut(EnhanceParams(brightness=0.3, contrast=1.3,
                                            highlight_protect=hp)) == 255).sum())
    assert clipped(1.0) < clipped(0.0)
    assert clipped(1.0) <= 1


def test_ramp_shape_dtype_monotonic_safe():
    for src in (build_lut(EnhanceParams(gamma=1.8)), build_curve(EnhanceParams(gamma=1.8))):
        r = lut_to_ramp(src)
        assert r.shape == (3, 256) and r.dtype == np.uint16
        assert np.all(np.diff(r.astype(int), axis=1) >= 0)
        assert ramp_is_safe(r)
    assert lut_to_ramp(build_lut(EnhanceParams())).tolist()[0] == [i * 257 for i in range(256)]
    assert lut_to_ramp(build_lut(EnhanceParams()), size=1024).shape == (3, 1024)


def test_ramp_is_safe_rejects_bad():
    good = lut_to_ramp(np.arange(256, dtype=np.uint8))
    assert ramp_is_safe(good)
    assert not ramp_is_safe(good[::-1].copy() * 0 + good[:, ::-1])          # decreasing
    assert not ramp_is_safe(np.zeros((3, 256), np.uint16))                   # black screen
    assert not ramp_is_safe((good // 8))                                     # max < 25%
    assert not ramp_is_safe(good // 4 + 40000)                               # min > 50%
    assert not ramp_is_safe(np.full((3, 256), 30000, np.uint16))             # flat
    assert not ramp_is_safe(good[:2])                                        # wrong shape
    bad = good.copy(); bad[1, 100] = 0
    assert not ramp_is_safe(bad)


def test_lut_to_ramp_rejects_bad_input():
    with pytest.raises(ValueError):
        lut_to_ramp(np.zeros((2, 5)))
