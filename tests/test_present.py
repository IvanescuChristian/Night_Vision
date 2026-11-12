# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import numpy as np
import pytest

from nightvision.backends.present import win32_affinity
from nightvision.backends.present.select import select_present_backend
from nightvision.platform_detect import PlatformInfo


def info(os_, session):
    return PlatformInfo(os_, session, "", False, session in ("x11", "xwayland-only"))


@pytest.mark.parametrize("pi,prefer,mode", [
    (info("windows", "win32"), None, "overlay"),
    (info("windows", "win32"), "window", "window"),
    (info("linux", "x11"), None, "window"),
    (info("linux", "x11"), "overlay", "overlay"),
    (info("linux", "wayland"), None, "window"),
    (info("linux", "wayland"), "overlay", "window"),
    (info("linux", "xwayland-only"), "overlay", "window"),
    (info("linux", "headless"), None, None),
    (info("macos", "headless"), None, None),
])
def test_selection_matrix(pi, prefer, mode):
    factory, got, reasons = select_present_backend(pi, prefer)
    assert got == mode
    assert reasons
    assert (factory is None) == (mode is None)


def test_x11_overlay_reason_flags_experimental():
    _, _, reasons = select_present_backend(info("linux", "x11"), "overlay")
    assert any("EXPERIMENTAL" in r for r in reasons)


def test_bad_prefer_rejected():
    with pytest.raises(ValueError):
        select_present_backend(info("linux", "x11"), "fullscreen")


def test_affinity_noop_off_windows():
    if win32_affinity.available():
        pytest.skip("running on Windows")
    assert win32_affinity.exclude_from_capture(1234) is False


def _frame(w=640, h=360, seed=0):
    rng = np.random.default_rng(seed)
    f = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    f[..., 3] = 255
    return f


@pytest.mark.gui
def test_window_mode_roundtrip():
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    sink = GlOverlaySink(monitor_rect=(0, 0, 640, 360), mode="window")
    sink.open()
    try:
        assert not sink.closed
        for i in range(10):
            f = _frame(seed=i)
            sink.present(f)
        # Redraw and read before swapping: back-buffer contents after a swap are undefined.
        sink._ctx.screen.use()
        sink._tex.use(0)
        sink._ctx.clear(0, 0, 0, 0)
        sink._vao.render(__import__("moderngl").TRIANGLE_STRIP)
        out = sink.read_back()
    finally:
        sink.close()
    if out.shape[:2] != f.shape[:2]:
        pytest.skip(f"window framebuffer {out.shape[:2]} != frame (WM resized it)")
    # frame is BGRA, readback is RGBA, both top row first
    expected = f[..., [2, 1, 0]].astype(int)
    diff = np.abs(out[..., :3].astype(int) - expected)
    assert diff.max() <= 2, f"max diff {diff.max()}"
    assert sink.closed


@pytest.mark.gui
def test_overlay_mode_opens_and_reports_passthrough(capsys):
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    sink = GlOverlaySink(monitor_rect=(0, 0, 320, 180), mode="overlay")
    sink.open()
    try:
        sink.present(_frame(320, 180))
        print("passthrough_applied:", sink.passthrough_applied,
              "capture_excluded:", sink.capture_excluded)
    finally:
        sink.close()
    assert sink.capture_excluded is False  # not Windows


@pytest.mark.gui
def test_present_texture_zero_copy_shared_context():
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    from nightvision.gpu.processor import GpuFrameEnhancer
    sink = GlOverlaySink(monitor_rect=(0, 0, 160, 90), mode="window")
    sink.open()
    try:
        enh = GpuFrameEnhancer(ctx=sink.ctx)
        tex = enh.process_to_texture(_frame(160, 90)[..., :3].copy())
        sink.present_texture(tex)
        with pytest.raises(ValueError):
            import moderngl
            other = moderngl.create_standalone_context(require=330, backend="egl")
            try:
                sink.present_texture(other.texture((2, 2), 4))
            finally:
                other.release()
    finally:
        sink.close()


@pytest.mark.gui
def test_glfw_window_create_destroy_on_main_thread(monkeypatch):
    """GLFW requires window create/destroy on the main thread even when the
    sink itself is driven from a render thread (segfault at exit otherwise)."""
    import threading
    import time
    pytest.importorskip("glfw")
    import glfw
    from nightvision.backends.present import overlay_gl
    from nightvision.mainthread import MainThreadDispatcher

    seen = {}
    real_create, real_destroy = glfw.create_window, glfw.destroy_window

    def create(*a, **k):
        seen["create"] = threading.current_thread()
        return real_create(*a, **k)

    def destroy(*a, **k):
        seen["destroy"] = threading.current_thread()
        return real_destroy(*a, **k)
    monkeypatch.setattr(glfw, "create_window", create)
    monkeypatch.setattr(glfw, "destroy_window", destroy)

    disp = MainThreadDispatcher()
    main = threading.current_thread()
    err = []

    def render():
        sink = overlay_gl.GlOverlaySink(monitor_rect=(0, 0, 160, 90), mode="window",
                                        dispatcher=disp)
        try:
            sink.open()
            sink.present(_frame(160, 90))
            seen["present"] = threading.current_thread()
        except Exception as e:  # noqa: BLE001
            err.append(e)
        finally:
            sink.close()

    t = threading.Thread(target=render, daemon=True)
    t.start()
    end = time.monotonic() + 15
    while t.is_alive() and time.monotonic() < end:
        disp.run_pending()
        overlay_gl.poll_events()
        time.sleep(0.005)
    assert not t.is_alive() and not err, err
    assert seen["create"] is main and seen["destroy"] is main
    assert seen["present"] is t
    assert not overlay_gl.has_open_windows()
