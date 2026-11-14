# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Tone precision: float LUT, ordered dither, float display ramp.

Dither cannot remove the large steps a steep tone curve creates from an 8-bit
source (adjacent input codes map several output codes apart); it removes the
extra rounding error on top of that, so block averages follow the exact curve.
"""
import dataclasses

import cv2
import numpy as np
import pytest

from nightvision.core.curves import (BAYER8, build_curve, build_float_lut, build_lut,
                                     curve_to_ramp, dither_threshold, ramp_is_safe)
from nightvision.core.enhance import FrameEnhancer
from nightvision.core.params import EnhanceParams
from nightvision.core.presets import PRESETS

# Expected CPU vs GPU agreement (BGR channels, 8-bit codes). Tone-only presets
# differ only through the half-float tone target (and float rounding in the
# shader), so at most one code. Presets with local contrast / clarity / sharpen
# use different algorithms on the two paths (see gpu/processor.py): measured
# max about 25 and mean about 3 on the test scene.
TONE_ONLY_MAX_DIFF = 1
FULL_PRESET_MEAN_DIFF = 3.5
FULL_PRESET_MAX_DIFF = 35

_TONE_ONLY = [n for n, p in PRESETS.items()
              if p.local_contrast == 0 and p.clarity == 0 and p.sharpen == 0
              and p.saturation == 1.0]


def _tone_params(p):
    return dataclasses.replace(p, local_contrast=0.0, clarity=0.0, sharpen=0.0,
                               saturation=1.0)


@pytest.mark.parametrize("name", list(PRESETS))
def test_float_lut_monotonic_and_matches_curve(name):
    p = PRESETS[name]
    f = build_float_lut(p)
    assert f.dtype == np.float32 and f.shape == (256,)
    assert np.all(np.diff(f.astype(np.float64)) >= 0)
    assert f.min() >= 0.0 and f.max() <= 1.0
    assert np.abs(f - build_curve(p, 256)).max() <= 6e-8  # float32 cast only


@pytest.mark.parametrize("name", list(PRESETS))
def test_build_lut_is_rounded_float_lut(name):
    p = PRESETS[name]
    lut = build_lut(p).astype(np.float64)
    f = build_float_lut(p).astype(np.float64) * 255.0
    # equal except float32 ties at exactly .5: never more than 0.5 + eps away
    assert np.abs(lut - f).max() <= 0.5 + 1e-3
    assert np.array_equal(build_lut(p), np.rint(build_curve(p, 256) * 255.0).astype(np.uint8))


@pytest.mark.parametrize("name", list(PRESETS))
@pytest.mark.parametrize("size", [256, 1024])
def test_curve_ramp_matches_float_curve_and_is_safe(name, size):
    p = PRESETS[name]
    r = curve_to_ramp(p, size)
    assert r.shape == (3, size) and r.dtype == np.uint16
    assert ramp_is_safe(r), name
    err = np.abs(r[0].astype(np.float64) - build_curve(p, size) * 65535.0).max()
    assert err <= 0.5 + 1e-6  # rounding to 16 bit only, i.e. <= 0.5/65535 of range
    assert np.array_equal(r[0], r[1]) and np.array_equal(r[1], r[2])


def test_curve_ramp_identity_is_exact():
    assert curve_to_ramp(EnhanceParams()).tolist()[0] == [i * 257 for i in range(256)]


def test_dither_pattern_is_zero_mean_bounded_static():
    assert sorted(BAYER8.ravel().tolist()) == list(range(64))
    t = dither_threshold(37, 61)
    assert t.shape == (37, 61) and t.dtype == np.float32
    assert 0.0 < t.min() and t.max() < 1.0
    tile = dither_threshold(8, 8).astype(np.float64)
    assert abs(tile.mean() - 0.5) < 1e-6  # offset mean: (t - 0.5) averages to 0
    assert np.abs(tile - 0.5).max() <= 0.5
    assert np.array_equal(dither_threshold(37, 61), t)
    # position-only: every 8x8 window shows all 64 levels once
    assert len(np.unique(dither_threshold(16, 16)[3:11, 5:13])) == 64


def test_dither_is_unbiased_on_flat_field():
    p = EnhanceParams(gamma=1.7, shadow_lift=0.75)
    img = np.full((64, 64, 3), 9, np.uint8)
    out = FrameEnhancer(p).process(img)
    ref = build_float_lut(p)[9].astype(np.float64) * 255.0
    assert abs(out.astype(np.float64).mean() - ref) < 0.02
    assert len(np.unique(out)) <= 2  # neighbouring codes only


@pytest.mark.parametrize("shape", [(37, 61), (130, 70), (64, 8)])
def test_cpu_tone_matches_explicit_dither_formula(shape):
    p = PRESETS["balanced"]
    rng = np.random.default_rng(3)
    img = rng.integers(0, 256, shape + (3,), dtype=np.uint8)
    f = build_float_lut(p)
    want = np.clip(np.floor(f[img] * np.float32(255.0)
                            + dither_threshold(*shape)[:, :, None]), 0, 255)
    assert np.array_equal(FrameEnhancer(p).process(img), want.astype(np.uint8))


def _scene(h=256, w=384):
    """Dark flat patches (the banding-prone case) plus a gentle ramp."""
    rng = np.random.default_rng(11)
    cells = rng.integers(0, 70, (h // 16, w // 16))
    g = np.kron(cells, np.ones((16, 16), int))
    g[:, w // 2:] = np.rint(np.linspace(0, 40, w - w // 2))[None, :]
    g = np.clip(g + (rng.random((h, w)) < 0.05), 0, 255)
    return np.stack([g, g, np.clip(g * 0.9, 0, 255).astype(int)], axis=2).astype(np.uint8)


def _block_means(a):
    a = a.astype(np.float64)
    h, w = a.shape[:2]
    return a.reshape(h // 8, 8, w // 8, 8, -1).mean(axis=(1, 3))


def _reference(img, p):
    """float64 tone math with no intermediate rounding, in 8-bit code units."""
    c = build_curve(p, 256) * 255.0
    return c[img]


def _old_path(img, p):
    return cv2.LUT(img, build_lut(p))


@pytest.mark.parametrize("name", ["night_vision", "tarkov_dark"])
def test_block_mean_error_lower_than_8bit_path(name):
    p = _tone_params(PRESETS[name])
    img = _scene()
    ref = _block_means(_reference(img, p))
    new = np.abs(_block_means(FrameEnhancer(p).process(img)) - ref).mean()
    old = np.abs(_block_means(_old_path(img, p)) - ref).mean()
    assert new < old, (name, new, old)
    assert new < 0.5 * old


@pytest.fixture(scope="module")
def ctx():
    pytest.importorskip("moderngl")
    from nightvision.gpu.processor import create_context
    try:
        c = create_context()
    except Exception as exc:
        pytest.skip(f"no OpenGL 3.3 context available: {exc}")
    yield c
    c.release()


@pytest.mark.gui
class TestGpuPrecision:
    def _gpu(self, ctx, p, img):
        from nightvision.gpu.processor import GpuFrameEnhancer
        e = GpuFrameEnhancer(p, ctx=ctx)
        try:
            return e.process(img)
        finally:
            e.close()

    @pytest.mark.parametrize("name", ["night_vision", "tarkov_dark"])
    def test_gpu_block_mean_error_lower_than_8bit_path(self, ctx, name):
        p = _tone_params(PRESETS[name])
        img = _scene()
        ref = _block_means(_reference(img, p))
        new = np.abs(_block_means(self._gpu(ctx, p, img)[:, :, :3]) - ref).mean()
        old = np.abs(_block_means(_old_path(img, p)) - ref).mean()
        assert new < old and new < 0.5 * old, (name, new, old)

    @pytest.mark.parametrize("name", list(PRESETS))
    def test_cpu_vs_gpu_within_bound(self, ctx, name):
        p = PRESETS[name]
        img = _scene()
        cpu = FrameEnhancer(p).process(img).astype(int)
        gpu = self._gpu(ctx, p, img)[:, :, :3].astype(int)
        d = np.abs(cpu - gpu)
        if name == "off":
            assert d.max() == 0  # neutral passthrough on both
        elif name in _TONE_ONLY:
            assert d.max() <= TONE_ONLY_MAX_DIFF, (name, d.max())
        else:
            assert d.mean() <= FULL_PRESET_MEAN_DIFF and d.max() <= FULL_PRESET_MAX_DIFF, \
                (name, d.mean(), d.max())

    def test_neutral_passthrough_not_dithered(self, ctx):
        img = _scene()
        assert np.array_equal(self._gpu(ctx, EnhanceParams(), img)[:, :, :3], img)

    def test_alpha_untouched(self, ctx):
        img = _scene()
        a = np.dstack([img, np.full(img.shape[:2], 77, np.uint8)])
        out = self._gpu(ctx, PRESETS["balanced"], a)
        assert out.shape[2] == 4 and np.all(out[:, :, 3] == 77)
