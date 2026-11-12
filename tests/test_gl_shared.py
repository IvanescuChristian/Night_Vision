# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""GPU enhancer sharing the presenting sink's GL context (zero-copy).

WHY: on Linux a standalone EGL context next to GLFW's GLX one aborts the
process (X BadAccess), so overlay/preview modes must build the enhancer on
GlOverlaySink.ctx. These tests need a display (run under xvfb-run).
"""
import threading
import time

import numpy as np
import pytest

pytest.importorskip("moderngl")
pytest.importorskip("glfw")

from nightvision import processing
from nightvision.backends.capture.synthetic import SyntheticCapture
from nightvision.core.enhance import FrameEnhancer
from nightvision.core.params import EnhanceParams
from nightvision.pipeline import EnhancePipeline

pytestmark = pytest.mark.gui


def _pump_for(disp, seconds):
    """Main-thread loop: what the app's pump does for the render thread."""
    from nightvision.backends.present import overlay_gl
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        disp.run_pending()
        overlay_gl.poll_events()
        time.sleep(0.01)

TONE = EnhanceParams(gamma=1.7, shadow_lift=0.75, black_floor=0.02, contrast=1.1,
                     highlight_protect=0.8)


def _frame(seed, w=640, h=360):
    rng = np.random.default_rng(seed)
    f = np.empty((h, w, 4), np.uint8)
    f[..., :3] = rng.integers(0, 90, (h, w, 3))
    f[..., 3] = 255
    return f


@pytest.fixture
def sink():
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    s = GlOverlaySink(monitor_rect=(0, 0, 640, 360), mode="window")
    s.open()
    yield s
    s.close()


def _readback(sink, tex):
    import moderngl
    sink._ctx.screen.use()
    tex.use(0)
    sink._ctx.clear(0, 0, 0, 0)
    sink._vao.render(moderngl.TRIANGLE_STRIP)
    return sink.read_back()


def test_shared_context_zero_copy_matches_cpu(sink, monkeypatch):
    def no_standalone():
        raise AssertionError("standalone context must not be opened")
    monkeypatch.setattr(processing, "_open_context", no_standalone)
    enh, name, reasons = processing.create_enhancer(TONE, prefer="gpu", ctx=sink.ctx)
    try:
        assert name == "gpu", reasons
        assert enh.gl_backend == "shared" and enh.zero_copy
        for i in range(10):
            f = _frame(i)
            tex = enh.process_to_texture(f)
            sink.present_texture(tex)
        tex = enh.process_to_texture(f)
        out = _readback(sink, tex)
        if out.shape[:2] != f.shape[:2]:
            pytest.skip(f"window framebuffer {out.shape[:2]} != frame (WM resized it)")
        want = FrameEnhancer(TONE).process(f)[..., [2, 1, 0]]
        diff = np.abs(out[..., :3].astype(int) - want.astype(int))
        assert diff.max() <= 2, f"max diff {diff.max()}"
    finally:
        enh.close()
    # The sink's context must survive the enhancer closing.
    sink.present(_frame(99))


def test_identity_texture_equals_input(sink):
    enh, name, reasons = processing.create_enhancer(EnhanceParams(), prefer="gpu",
                                                    ctx=sink.ctx)
    try:
        assert name == "gpu", reasons
        f = _frame(3)
        out = _readback(sink, enh.process_to_texture(f))
        if out.shape[:2] != f.shape[:2]:
            pytest.skip("window framebuffer resized")
        assert np.abs(out[..., :3].astype(int) - f[..., [2, 1, 0]].astype(int)).max() <= 2
    finally:
        enh.close()


def test_pipeline_preview_shared_gpu_runs_and_exits():
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    from nightvision.mainthread import MainThreadDispatcher
    disp = MainThreadDispatcher()
    holder = {}

    def sink_factory():
        holder["sink"] = GlOverlaySink(monitor_rect=(0, 0, 640, 360), mode="window",
                                       dispatcher=disp)
        return holder["sink"]

    def enh_factory(sink):
        e, name, reasons = processing.create_enhancer(TONE, prefer="gpu", ctx=sink.ctx)
        holder["enh"], holder["reasons"] = e, reasons
        return e

    before = threading.active_count()
    p = EnhancePipeline(lambda: SyntheticCapture(320, 180, fps=60), enh_factory,
                        sink_factory, TONE, 30, dispatcher=disp)
    p.start()
    _pump_for(disp, 2.0)
    assert p.last_error is None, p.last_error
    s = p.stats()
    assert p.stop(3.0)
    assert holder["enh"].gl_backend == "shared", holder["reasons"]
    assert s["frames_processed"] > 10 and s["sink"] == "GlOverlaySink"
    assert all(not t.is_alive() for t in p.threads)
    assert threading.active_count() <= before
    print(f"shared gpu: {holder['enh'].caps.renderer} fps={s['fps']:.1f}")


def test_pipeline_falls_back_to_copy_path_when_gpu_dropped():
    """A runtime GPU failure must switch to process()+present() by itself."""
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    from nightvision.mainthread import MainThreadDispatcher
    disp = MainThreadDispatcher()
    holder = {}

    def sink_factory():
        return GlOverlaySink(monitor_rect=(0, 0, 640, 360), mode="window", dispatcher=disp)

    def enh_factory(sink):
        e, _, _ = processing.create_enhancer(TONE, prefer="gpu", ctx=sink.ctx)
        real = e.gpu.process_to_texture
        calls = {"n": 0}

        def flaky(frame):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("GL_CONTEXT_LOST")
            return real(frame)
        e.gpu.process_to_texture = flaky
        holder["enh"] = e
        return e

    p = EnhancePipeline(lambda: SyntheticCapture(320, 180, fps=60), enh_factory,
                        sink_factory, TONE, 30, dispatcher=disp)
    p.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and p.stats()["frames_processed"] < 12:
        _pump_for(disp, 0.05)
    err = p.last_error
    s = p.stats()
    assert p.stop(3.0)
    assert err is None, err
    e = holder["enh"]
    assert e.backend == "cpu" and not e.zero_copy and len(e.fallback_events) == 1
    assert s["frames_processed"] >= 12
