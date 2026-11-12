# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
from dataclasses import replace

import numpy as np
import pytest

from nightvision import processing
from nightvision.core.enhance import FrameEnhancer
from nightvision.core.params import EnhanceParams
from nightvision.core.presets import get_preset

pytest.importorskip("moderngl")


def _frame(h=64, w=96, seed=5):
    rng = np.random.default_rng(seed)
    base = np.full((h, w, 3), 18, np.uint8)
    base[h // 4:h // 2, w // 4:w // 2] = 40
    return np.clip(base.astype(int) + rng.integers(-3, 4, base.shape), 0, 255).astype(np.uint8)


def _absdiff(a, b):
    return np.abs(a.astype(int) - b.astype(int))


@pytest.fixture
def made():
    items = []
    yield items
    for e in items:
        e.close()


def _create(made, *a, **k):
    enh, name, reasons = processing.create_enhancer(*a, **k)
    made.append(enh)
    return enh, name, reasons


def test_cpu_requested(made):
    enh, name, reasons = _create(made, get_preset("balanced"), prefer="cpu")
    assert name == "cpu" and reasons == ["cpu requested"] and enh.caps is None
    assert enh.process(np.full((8, 8, 3), 10, np.uint8)).shape == (8, 8, 3)
    enh.close()
    enh.close()


def test_bad_prefer():
    with pytest.raises(ValueError):
        processing.create_enhancer(EnhanceParams(), prefer="tpu")


def test_auto_picks_cpu_on_software_renderer(made):
    enh, name, reasons = _create(made, EnhanceParams(), prefer="auto")
    if enh.caps is None:
        pytest.skip("no GL context here")
    if not enh.caps.is_software:
        pytest.skip(f"hardware renderer {enh.caps.renderer}: auto legitimately uses the GPU")
    assert name == "cpu" and enh.backend == "cpu"
    assert reasons == [f"software GL renderer {enh.caps.renderer}; "
                       "CPU path is as fast and exact"]
    assert enh.caps.vendor and enh.gl_backend in ("egl", "default")


def test_gpu_forced_on_software_renderer(made):
    enh, name, reasons = _create(made, get_preset("night_vision"), prefer="gpu")
    if enh.caps is None:
        pytest.skip("no GL context here")
    assert name == "gpu" and reasons == [] and enh.backend == "gpu"
    assert enh.process(_frame()).shape == (64, 96, 3)


def test_self_test_failure_falls_back_to_cpu(made, monkeypatch):
    def bad(gpu):
        raise RuntimeError("tone self-test differs from CPU by 9 (> 2)")
    monkeypatch.setattr(processing, "_self_test", bad)
    enh, name, reasons = _create(made, EnhanceParams(), prefer="gpu")
    if enh.caps is None:
        pytest.skip("no GL context here")
    assert name == "cpu"
    assert any("self-test" in r for r in reasons)
    assert enh.process(_frame()).shape == (64, 96, 3)


def test_real_self_test_passes_on_this_driver(made):
    enh, name, _ = _create(made, EnhanceParams(), prefer="gpu")
    if enh.caps is None:
        pytest.skip("no GL context here")
    assert name == "gpu"
    # The self-test must leave the user's params untouched.
    assert enh.gpu.params == EnhanceParams().validated()


def test_no_context_falls_back_with_reason(made, monkeypatch):
    def boom():
        raise RuntimeError("no gl here")
    monkeypatch.setattr(processing, "_open_context", boom)
    for prefer in ("auto", "gpu"):
        enh, name, reasons = _create(made, EnhanceParams(), prefer=prefer)
        assert name == "cpu" and any("no gl here" in r for r in reasons)
        enh.set_params(get_preset("night_vision"))
        assert enh.process(_frame()).shape == (64, 96, 3)


def test_unusable_caps_fall_back(made, monkeypatch):
    real = processing._probe

    def probe(ctx):
        return replace(real(ctx), ok=False, problems=["RG16F not renderable"])
    monkeypatch.setattr(processing, "_probe", probe)
    enh, name, reasons = _create(made, EnhanceParams(), prefer="gpu")
    if enh.caps is None:
        pytest.skip("no GL context here")
    assert name == "cpu" and "RG16F not renderable" in reasons[0]


def _forced_gpu(made, params):
    enh, name, _ = _create(made, params, prefer="gpu")
    if name != "gpu":
        pytest.skip("no GL context here")
    return enh


def test_runtime_gl_failure_switches_to_cpu_transparently(made):
    p = get_preset("night_vision")
    enh = _forced_gpu(made, p)
    img = _frame()
    assert enh.process(img).shape == img.shape and not enh.fallback_events

    def lost(frame):
        raise RuntimeError("GL_CONTEXT_LOST")
    enh.gpu.process = lost
    out = enh.process(img)
    assert enh.backend == "cpu" and len(enh.fallback_events) == 1
    assert "GL_CONTEXT_LOST" in enh.fallback_events[0]
    assert np.array_equal(out, FrameEnhancer(p).process(img))
    # Later frames and param changes keep working on the CPU, no new events.
    p2 = get_preset("tarkov_dark")
    enh.set_params(p2)
    assert np.array_equal(enh.process(img), FrameEnhancer(p2).process(img))
    assert len(enh.fallback_events) == 1
    with pytest.raises(RuntimeError):
        enh.process_to_texture(img)


def test_close_tolerates_dead_context(made):
    enh = _forced_gpu(made, EnhanceParams())

    def dead():
        raise RuntimeError("context lost")
    enh.gpu.close = dead
    enh.close()  # must not raise
    enh.close()


def test_programming_errors_do_not_trigger_fallback(made):
    enh = _forced_gpu(made, get_preset("night_vision"))
    with pytest.raises(ValueError):
        enh.process(np.zeros((4, 4), np.uint8))
    assert enh.backend == "gpu" and not enh.fallback_events


def test_frame_larger_than_texture_limit_uses_cpu(made, monkeypatch):
    real = processing._probe
    monkeypatch.setattr(processing, "_probe",
                        lambda ctx: replace(real(ctx), max_texture_size=100))
    p = get_preset("night_vision")
    enh = _forced_gpu(made, p)  # the 96x64 self-test frame still fits
    big = _frame(60, 160)
    out = enh.process(big)
    assert np.array_equal(out, FrameEnhancer(p).process(big))
    assert enh.backend == "gpu"  # GPU kept for frames that fit
    assert len(enh.fallback_events) == 1 and "oversize" in enh.fallback_events[0]
    enh.process(big)
    assert len(enh.fallback_events) == 1
    assert enh.process(_frame()).shape == (64, 96, 3)


def test_frame_size_hint_beyond_limit_selects_cpu(made, monkeypatch):
    real = processing._probe
    monkeypatch.setattr(processing, "_probe",
                        lambda ctx: replace(real(ctx), max_texture_size=100))
    enh, name, reasons = _create(made, EnhanceParams(), prefer="gpu",
                                 frame_size=(160, 60))
    if enh.caps is None:
        pytest.skip("no GL context here")
    assert name == "cpu" and "GL_MAX_TEXTURE_SIZE" in reasons[0]


def test_shared_ctx_is_used_and_never_released(made):
    ctx, _ = processing._open_context()
    try:
        enh, name, reasons = processing.create_enhancer(
            get_preset("night_vision"), prefer="gpu", ctx=ctx)
        if name != "gpu":
            pytest.skip(f"no usable GL here: {reasons}")
        assert enh.gl_backend == "shared" and enh.zero_copy
        tex = enh.process_to_texture(_frame())
        assert tex.ctx is ctx
        enh.close()
        ctx.info  # still alive: the sink, not the enhancer, owns it
        assert ctx.error == "GL_NO_ERROR"
    finally:
        ctx.release()


def test_shared_ctx_software_renderer_auto_uses_cpu():
    ctx, _ = processing._open_context()
    try:
        if not processing._probe(ctx).is_software:
            pytest.skip("hardware renderer")
        enh, name, reasons = processing.create_enhancer(prefer="auto", ctx=ctx)
        assert name == "cpu" and not enh.zero_copy and reasons
        enh.close()
        ctx.info
    finally:
        ctx.release()


def test_zero_copy_drops_after_runtime_failure(made):
    ctx, _ = processing._open_context()
    try:
        enh, name, _r = processing.create_enhancer(
            get_preset("night_vision"), prefer="gpu", ctx=ctx)
        if name != "gpu":
            pytest.skip("no usable GL here")

        def lost(frame):
            raise RuntimeError("GL_CONTEXT_LOST")
        enh.gpu.process_to_texture = lost
        assert enh.process_to_texture(_frame()) is None
        assert not enh.zero_copy and enh.backend == "cpu"
        assert enh.process(_frame()).shape == (64, 96, 3)
        enh.close()
        ctx.info  # shared context untouched by the fallback
    finally:
        ctx.release()
