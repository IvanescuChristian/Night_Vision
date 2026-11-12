# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""GLFW + moderngl presentation sink (FrameSink: open/present/close).

Threading: GLFW requires init, window creation/destruction and event polling on
the MAIN thread; only make_context_current/swap_buffers/swap_interval and GL
calls may run elsewhere. open()/present()/close() are called on the render
thread and route the main-thread-only GLFW calls through a MainThreadDispatcher
(the owner calls poll_events() from its pump). Without a dispatcher everything
runs inline on the calling thread (single-threaded use, tests).

Modes
-----
overlay: undecorated, topmost, transparent framebuffer, click-through
    (GLFW_MOUSE_PASSTHROUGH, glfw >= 3.4), never takes focus.
    Windows: protected from the capturer by WDA_EXCLUDEFROMCAPTURE.
    X11: the capturer WILL see the overlay (feedback loop) -- EXPERIMENTAL,
    NOT VERIFIED on a real desktop; select.py only picks it when forced.
    Wayland: GLFW cannot create topmost/positioned surfaces (would need
    wlr-layer-shell); use window mode. Future work.
window: ordinary resizable preview window (second monitor, Wayland, debug).

Zero-copy path
--------------
present_texture(tex) draws a moderngl texture directly. The texture must live
in THIS sink's context, so build the enhancer on it:
    sink.open(); enh = GpuFrameEnhancer(params, ctx=sink.ctx)
Both then run on the render thread and share one context.
"""
from __future__ import annotations

import logging
import threading
import weakref
from typing import Any, Callable, Optional

import numpy as np

from . import win32_affinity

log = logging.getLogger(__name__)

try:  # optional dependency; sink construction works, open() raises
    import glfw
except Exception:  # pragma: no cover
    glfw = None
try:
    import moderngl
except Exception:  # pragma: no cover
    moderngl = None

# glfw.init/terminate are process-global: terminate only with the last window.
_state_lock = threading.Lock()
_live_sinks: "weakref.WeakSet[GlOverlaySink]" = weakref.WeakSet()


def has_open_windows() -> bool:
    return bool(_live_sinks)


def poll_events() -> None:
    """Owner (main) thread only: pump GLFW events and publish close requests.

    Sinks created with a dispatcher never poll themselves; this is their only
    source of window_should_close.
    """
    if glfw is None:
        return
    sinks = list(_live_sinks)
    if not sinks:
        return
    glfw.poll_events()
    for s in sinks:
        s._refresh_close_flag()


# Flip v here (texture row 0 = image top, GL y is up) and swizzle BGRA in the
# shader so a texture owned by the enhancer is never mutated.
_VERT = """
#version 330
in vec2 pos;
out vec2 uv;
void main() {
    uv = vec2(pos.x * 0.5 + 0.5, 0.5 - pos.y * 0.5);
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""
_FRAG = """
#version 330
uniform sampler2D tex;
in vec2 uv;
out vec4 color;
void main() {
    color = vec4(texture(tex, uv).bgr, 1.0);
}
"""


class GlOverlaySink:
    def __init__(self, monitor_rect: Optional[tuple[int, int, int, int]] = None,
                 mode: str = "overlay", exclude_from_capture: bool = True,
                 dispatcher: Any = None) -> None:
        if mode not in ("overlay", "window"):
            raise ValueError(f"mode must be 'overlay' or 'window', got {mode!r}")
        self.monitor_rect = monitor_rect  # (x, y, w, h) in screen pixels
        self.mode = mode
        self.exclude_from_capture = exclude_from_capture
        self.passthrough_applied = False
        self.capture_excluded = False
        self._win = None
        self._ctx = None
        self._prog = None
        self._vao = None
        self._vbo = None
        self._tex = None
        self._tex_size: Optional[tuple[int, int]] = None
        self._glfw_inited = False
        self._dispatcher = dispatcher
        self._should_close = False  # written by main-thread polling, read by render thread

    def _on_main(self, fn: Callable[[], Any]) -> Any:
        return self._dispatcher.call(fn) if self._dispatcher is not None else fn()

    def _refresh_close_flag(self) -> None:
        win = self._win
        if win is not None and glfw.window_should_close(win):
            self._should_close = True

    # ---- properties ------------------------------------------------------
    @property
    def ctx(self):
        """moderngl context (valid after open()); share it with the enhancer."""
        return self._ctx

    @property
    def closed(self) -> bool:
        """True once the user closed the preview window (or after close())."""
        win = self._win
        if win is None or self._should_close:
            return True
        if self._dispatcher is None:
            return bool(glfw.window_should_close(win))
        return False

    # ---- lifecycle -------------------------------------------------------
    def open(self) -> None:
        if glfw is None or moderngl is None:
            raise RuntimeError("glfw and moderngl are required (pip install nightvision[gpu])")
        try:
            self._on_main(self._create_window)
            # Context current + GL objects belong to the calling (render) thread.
            glfw.make_context_current(self._win)
            glfw.swap_interval(1 if self.mode == "window" else 0)
            self._init_gl()
        except Exception:
            self.close()
            raise

    def _create_window(self) -> None:
        """Main thread: glfw.init, hints, window."""
        if not glfw.init():
            raise RuntimeError("glfw.init() failed (no display?)")
        self._glfw_inited = True
        glfw.default_window_hints()
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)
        x, y, w, h = self.monitor_rect or (0, 0, 1280, 720)
        overlay = self.mode == "overlay"
        if overlay:
            glfw.window_hint(glfw.DECORATED, glfw.FALSE)
            glfw.window_hint(glfw.FLOATING, glfw.TRUE)
            glfw.window_hint(glfw.RESIZABLE, glfw.FALSE)
            glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.TRUE)
            # WHY: stealing focus from the game would minimise exclusive-ish
            # fullscreen games and swallow input.
            glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)
            glfw.window_hint(glfw.FOCUSED, glfw.FALSE)
            glfw.window_hint(glfw.MOUSE_PASSTHROUGH, glfw.TRUE)
        self._win = glfw.create_window(w, h, "NightVision", None, None)
        if not self._win:
            self._win = None
            self._terminate_if_last()
            raise RuntimeError("glfw.create_window failed")
        with _state_lock:
            _live_sinks.add(self)
        if overlay:
            if self.monitor_rect:
                glfw.set_window_pos(self._win, x, y)
            try:
                self.passthrough_applied = bool(
                    glfw.get_window_attrib(self._win, glfw.MOUSE_PASSTHROUGH))
            except Exception:
                self.passthrough_applied = False
            if not self.passthrough_applied:
                log.warning("click-through (MOUSE_PASSTHROUGH) not honoured by this "
                            "platform/glfw; the overlay will intercept mouse input")
            if self.exclude_from_capture and win32_affinity.available():
                self.capture_excluded = win32_affinity.exclude_from_capture(
                    glfw.get_win32_window(self._win))
        elif self.monitor_rect:
            glfw.set_window_pos(self._win, x, y)

    def _terminate_if_last(self) -> None:
        with _state_lock:
            if self._glfw_inited and not _live_sinks:
                glfw.terminate()
            self._glfw_inited = False

    def _destroy_window(self) -> None:
        """Main thread: destroy the window; terminate GLFW with the last one."""
        win, self._win = self._win, None
        if win is not None:
            with _state_lock:
                _live_sinks.discard(self)
            glfw.destroy_window(win)
        self._terminate_if_last()

    def _init_gl(self) -> None:
        self._ctx = moderngl.create_context(require=330)
        self._prog = self._ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        self._prog["tex"].value = 0
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")
        self._vbo = self._ctx.buffer(quad.tobytes())
        self._vao = self._ctx.vertex_array(self._prog, [(self._vbo, "2f", "pos")])

    def close(self) -> None:
        for obj in (self._vao, self._vbo, self._prog, self._tex):
            try:
                if obj is not None:
                    obj.release()
            except Exception:
                pass
        self._vao = self._vbo = self._prog = self._tex = None
        self._tex_size = None
        if self._ctx is not None:
            try:
                self._ctx.release()
            except Exception:
                pass
            self._ctx = None
        if self._win is not None:
            try:
                glfw.make_context_current(None)  # detach from the render thread first
            except Exception:
                pass
        if self._win is not None or self._glfw_inited:
            self._on_main(self._destroy_window)

    # ---- presenting ------------------------------------------------------
    def present(self, frame: np.ndarray) -> None:
        if self._win is None:
            raise RuntimeError("sink is not open")
        if frame.ndim != 3 or frame.shape[2] != 4 or frame.dtype != np.uint8:
            raise ValueError("expected HxWx4 uint8 BGRA frame")
        h, w = frame.shape[:2]
        if self._tex_size != (w, h):
            if self._tex is not None:
                self._tex.release()
            self._tex = self._ctx.texture((w, h), 4)
            self._tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self._tex_size = (w, h)
        self._tex.write(np.ascontiguousarray(frame))
        self._draw(self._tex)

    def present_texture(self, tex) -> None:
        """Zero-copy: draw an RGBA8 texture holding BGR(A) data (as returned by
        GpuFrameEnhancer.process_to_texture). It must belong to self.ctx, i.e.
        the enhancer was created with ctx=sink.ctx."""
        if self._win is None:
            raise RuntimeError("sink is not open")
        if tex.ctx is not self._ctx:
            raise ValueError("texture belongs to a different GL context; "
                             "create the enhancer with ctx=sink.ctx")
        self._draw(tex)

    def _draw(self, tex) -> None:
        fw, fh = glfw.get_framebuffer_size(self._win)
        self._ctx.screen.use()
        self._ctx.viewport = (0, 0, fw, fh)
        self._ctx.clear(0.0, 0.0, 0.0, 0.0)
        tex.use(0)
        self._vao.render(moderngl.TRIANGLE_STRIP)
        glfw.swap_buffers(self._win)
        if self._dispatcher is None:
            glfw.poll_events()
            self._refresh_close_flag()

    def read_back(self) -> np.ndarray:
        """Read the default framebuffer as HxWx4 RGBA, top row first (tests/debug).
        Valid right after present() on drivers that preserve the front buffer
        contents; callers needing certainty should draw then read before swap."""
        fw, fh = glfw.get_framebuffer_size(self._win)
        raw = self._ctx.screen.read(viewport=(0, 0, fw, fh), components=4)
        img = np.frombuffer(raw, np.uint8).reshape(fh, fw, 4)
        return np.ascontiguousarray(img[::-1])
