# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Tk's Xlib error handler must survive every GL/GLFW entry point."""
import ctypes
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gui,
              pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="Xlib error handlers are X11-only")]
tk = pytest.importorskip("tkinter")

from nightvision.x11_errors import preserve_x_error_handler  # noqa: E402


def _handler():
    """Current Xlib handler address. XSetErrorHandler has no getter, so read by
    swapping in NULL and putting the previous one straight back."""
    x = ctypes.CDLL("libX11.so.6")
    x.XSetErrorHandler.restype = ctypes.c_void_p
    x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    cur = x.XSetErrorHandler(None)
    x.XSetErrorHandler(cur)
    return cur


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


def test_context_manager_restores_replaced_handler(tk_root):
    before = _handler()
    x = ctypes.CDLL("libX11.so.6")
    x.XSetErrorHandler.restype = ctypes.c_void_p
    x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    with preserve_x_error_handler():
        x.XSetErrorHandler(None)  # what a GLX context creation does
        assert _handler() != before
    assert _handler() == before


def test_context_manager_never_raises_on_body_error(tk_root):
    before = _handler()
    with pytest.raises(ValueError):
        with preserve_x_error_handler():
            raise ValueError("boom")
    assert _handler() == before


def test_gpu_context_keeps_tk_handler(tk_root):
    from nightvision.gpu.processor import GpuFrameEnhancer, create_context
    from nightvision.core.params import EnhanceParams
    before = _handler()
    try:
        ctx = create_context()
    except Exception as exc:
        pytest.skip(f"no OpenGL 3.3 context available: {exc}")
    assert _handler() == before
    enh = GpuFrameEnhancer(EnhanceParams(), ctx=ctx)
    enh.close()
    owned = GpuFrameEnhancer(EnhanceParams())  # owns its context: close() releases it
    owned.close()
    assert _handler() == before
    ctx.release()


def test_glfw_overlay_keeps_tk_handler(tk_root):
    pytest.importorskip("glfw")
    from nightvision.backends.present.overlay_gl import GlOverlaySink
    before = _handler()
    sink = GlOverlaySink(monitor_rect=(0, 0, 320, 180), mode="window")
    try:
        sink.open()
    except Exception as exc:
        pytest.skip(f"GLFW window unavailable: {exc}")
    try:
        assert _handler() == before
    finally:
        sink.close()
    assert _handler() == before


def test_repro_sequence_survives():
    # Original crash: foreground read, GL context, then a Tk UI test in one
    # process. Run in a child so a regression cannot take the suite down.
    root = Path(__file__).resolve().parent
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_foreground.py::test_x11_reads_active_window_when_set",
         "tests/test_gpu.py::test_identity_equal", "tests/test_ui.py"],
        cwd=root.parent, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
