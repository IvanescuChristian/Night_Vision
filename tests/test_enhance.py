# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import statistics
import threading
import time

import cv2
import numpy as np
import pytest

from nightvision.core.enhance import FrameEnhancer, enhance_frame
from nightvision.core.params import EnhanceParams
from nightvision.core.presets import PRESETS, get_preset


def _scene(h=240, w=320):
    rng = np.random.default_rng(7)
    img = np.full((h, w, 3), 14, np.uint8)
    img[80:160, 100:220] = 30
    noise = rng.integers(-2, 3, img.shape)
    return np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)


def _gray(img):
    return cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY).astype(float)


def test_identity_equal_and_not_aliased():
    img = _scene()
    src = img.copy()
    out = FrameEnhancer(EnhanceParams()).process(img)
    assert np.array_equal(out, src) and out is not img
    assert np.array_equal(img, src)


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_does_not_mutate_input(name):
    img = _scene()
    src = img.copy()
    out = enhance_frame(img, get_preset(name))
    assert out.shape == img.shape and out.dtype == np.uint8
    assert np.array_equal(img, src)


def test_bgra_alpha_preserved():
    img = np.dstack([_scene(), np.random.default_rng(3).integers(0, 256, (240, 320), dtype=np.uint8)])
    src = img.copy()
    out = enhance_frame(img, get_preset("night_vision"))
    assert out.shape == img.shape
    assert np.array_equal(out[:, :, 3], img[:, :, 3])
    assert np.array_equal(img, src)
    ident = enhance_frame(img, EnhanceParams())
    assert np.array_equal(ident, img)


def test_rejects_bad_frames():
    with pytest.raises(ValueError):
        FrameEnhancer().process(np.zeros((4, 4), np.uint8))


def test_night_vision_brightens_and_separates():
    img = _scene()
    out = enhance_frame(img, get_preset("night_vision"))
    g0, g1 = _gray(img), _gray(out)
    assert g1.mean() > g0.mean() + 10
    fig = (slice(100, 140), slice(120, 200))
    bg = (slice(10, 50), slice(10, 80))
    assert g1[fig].mean() - g1[bg].mean() > g0[fig].mean() - g0[bg].mean()


def test_each_stage_runs():
    img = _scene()
    for kw in ({"local_contrast": 1.0}, {"clarity": 1.0}, {"sharpen": 2.0},
               {"saturation": 0.0}, {"saturation": 2.0}):
        out = enhance_frame(img, EnhanceParams(**kw))
        assert out.shape == img.shape and not np.array_equal(out, img), kw


def test_set_params_threaded():
    img = _scene()
    enh = FrameEnhancer(EnhanceParams())
    stop = threading.Event()
    errors = []

    def worker():
        try:
            while not stop.is_set():
                out = enh.process(img)
                assert out.shape == img.shape
        except Exception as e:  # pragma: no cover
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    try:
        names = list(PRESETS)
        for i in range(60):
            enh.set_params(PRESETS[names[i % len(names)]])
            time.sleep(0.002)
    finally:
        stop.set()
        t.join(10)
    assert not errors


@pytest.mark.slow
def test_perf_720p_night_vision():
    frame = np.random.default_rng(0).integers(0, 80, (720, 1280, 3), dtype=np.uint8)
    enh = FrameEnhancer(get_preset("night_vision"))
    for _ in range(3):
        enh.process(frame)
    times = []
    for _ in range(15):
        t0 = time.perf_counter()
        enh.process(frame)
        times.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(times)
    print(f"night_vision 1280x720 median {med:.1f} ms")
    assert med < 60
