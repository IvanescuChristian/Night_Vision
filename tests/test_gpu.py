# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import threading

import cv2
import numpy as np
import pytest

pytest.importorskip("moderngl", reason="moderngl not installed (pip install nightvision[gpu])")

from nightvision.core.enhance import FrameEnhancer
from nightvision.core.params import EnhanceParams
from nightvision.core.presets import get_preset
from nightvision.gpu.processor import GpuFrameEnhancer, create_context


@pytest.fixture(scope="module")
def ctx():
    try:
        c = create_context()
    except Exception as exc:
        pytest.skip(f"no OpenGL 3.3 context available: {exc}")
    yield c
    c.release()


@pytest.fixture
def make(ctx):
    made = []

    def _make(params):
        e = GpuFrameEnhancer(params, ctx=ctx)
        made.append(e)
        return e
    yield _make
    for e in made:
        e.close()


def _scene(h=240, w=320):
    rng = np.random.default_rng(7)
    img = np.full((h, w, 3), 14, np.uint8)
    img[80:160, 100:220] = 30
    return np.clip(img.astype(int) + rng.integers(-2, 3, img.shape), 0, 255).astype(np.uint8)


def _gray(img):
    return cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY).astype(float)


def _absdiff(a, b):
    return np.abs(a.astype(int) - b.astype(int))


def test_identity_equal(make):
    img = _scene()
    enh = make(EnhanceParams())
    out = enh.process(img)
    assert out is not img and _absdiff(out, img).max() <= 1
    # Also through the real GL path (process() short-circuits neutral params).
    tex = enh.process_to_texture(img)
    raw = np.frombuffer(tex.read(alignment=1), np.uint8).reshape(240, 320, 4)[:, :, :3]
    assert _absdiff(raw, img).max() <= 1


def test_tone_only_matches_cpu(make):
    img = _scene()
    p = EnhanceParams(gamma=1.7, shadow_lift=0.75, black_floor=0.02, contrast=1.1,
                      highlight_protect=0.8)
    assert _absdiff(make(p).process(img), FrameEnhancer(p).process(img)).max() <= 2


def test_night_vision_close_to_cpu_and_separates(make):
    img = _scene()
    p = get_preset("night_vision")
    g = make(p).process(img)
    c = FrameEnhancer(p).process(img)
    assert _absdiff(g, c).mean() <= 12

    def sep(x):
        gr = _gray(x)
        return gr[100:140, 120:200].mean() - gr[:50].mean()
    assert sep(g) > sep(img)


def test_detail_passes_run_and_differ(make):
    img = _scene()
    p = get_preset("tarkov_dark")
    g = make(p).process(img)
    assert g.shape == img.shape and g.dtype == np.uint8
    assert _absdiff(g, FrameEnhancer(p).process(img)).mean() <= 12


def test_bgra_alpha_preserved_and_input_untouched(make):
    alpha = np.random.default_rng(3).integers(0, 256, (240, 320), dtype=np.uint8)
    img = np.dstack([_scene(), alpha])
    src = img.copy()
    out = make(get_preset("night_vision")).process(img)
    assert out.shape == img.shape
    assert np.array_equal(out[:, :, 3], alpha)
    assert np.array_equal(img, src)


def test_no_reallocation_across_same_size_frames(make):
    enh = make(get_preset("tarkov_dark"))
    enh.process(_scene())
    n = enh.allocations
    for _ in range(3):
        enh.process(_scene())
    enh.set_params(get_preset("night_vision"))  # same passes minus sharpen/clarity
    enh.process(_scene())
    assert enh.allocations == n
    enh.process(_scene(120, 160))
    assert enh.allocations > n  # a size change legitimately reallocates


def test_wrong_thread_raises(make):
    enh = make(get_preset("night_vision"))
    errors = []

    def work():
        for call in (lambda: enh.process(_scene()), enh.close,
                     lambda: enh.process_to_texture(_scene())):
            try:
                call()
                errors.append(None)
            except RuntimeError as exc:
                errors.append(str(exc))

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert len(errors) == 3 and all(e and "thread" in e for e in errors)
    enh.set_params(EnhanceParams(gamma=1.2))  # allowed from any thread
    t2 = threading.Thread(target=lambda: enh.set_params(EnhanceParams(gamma=1.4)))
    t2.start()
    t2.join()
    assert enh.process(_scene()).shape == (240, 320, 3)


def test_close_idempotent_and_blocks_use(ctx):
    enh = GpuFrameEnhancer(get_preset("night_vision"), ctx=ctx)
    enh.process(_scene())
    enh.close()
    enh.close()
    with pytest.raises(RuntimeError):
        enh.process(_scene())


def test_rejects_bad_frames(make):
    with pytest.raises(ValueError):
        make(EnhanceParams()).process(np.zeros((4, 4), np.uint8))


# ---- shader portability (strict GLSL compilers: AMD, NVIDIA, Intel, Mesa) ---
import re

from nightvision.gpu import shaders

_SOURCES = {n: getattr(shaders, n) for n in ("VERTEX", "TONE", "DOWN", "BLUR", "COMPOSE")}


@pytest.mark.parametrize("name", list(_SOURCES))
def test_shader_source_is_portable(name):
    src = _SOURCES[name]
    assert src.startswith("#version 330 core\n"), "#version must be the first token"
    assert "#extension" not in src
    if name != "VERTEX":
        assert "layout(location = 0) out vec4 o;" in src
    # `step` is a GLSL built-in; some compilers reject shadowing it.
    assert not re.search(r"\buniform\s+\w+\s+step\b", src)
    # Integer literals next to float arithmetic are rejected by strict
    # compilers; every numeric literal in a float expression must have a point
    # (allowed ints: array/ivec constructors, loop counters, version number).
    body = re.sub(r"#version 330 core|//.*", "", src)
    body = re.sub(r"(ivec[234]|int)\s*\([^;]*\)", "", body)
    assert not re.search(r"(?<![\w.])\d+(?![\w.])\s*[*/+-]\s*[a-z(]", body.replace("1; i", ""))


def test_blur_loop_bound_is_constant():
    assert "const int MAX_RADIUS = 36;" in shaders.BLUR
    assert "@" not in shaders.BLUR


def test_all_shaders_compile_and_link(ctx):
    for name in ("TONE", "DOWN", "BLUR", "COMPOSE"):
        prog = ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=_SOURCES[name])
        prog.release()
    assert ctx.error == "GL_NO_ERROR"


def test_backend_order(monkeypatch):
    from nightvision.gpu import processor as P
    monkeypatch.setattr(P.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert P._backend_order(None) == ["egl", "default"]  # headless
    monkeypatch.setenv("DISPLAY", ":0")
    assert P._backend_order(None) == ["default", "egl"]  # EGL+GLX in one process aborts
    assert P._backend_order(True) == ["egl", "default"]
    monkeypatch.setattr(P.sys, "platform", "win32")
    assert P._backend_order(None) == ["default", "egl"]
    monkeypatch.setattr(P.sys, "platform", "darwin")
    assert P._backend_order(None) == ["default"]
