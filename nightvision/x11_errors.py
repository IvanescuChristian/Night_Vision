# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Keep Tk's Xlib error handler alive across GL context work (Linux/X11).

Xlib has ONE process-global error handler. Tk installs its own on the first
tk.Tk(); creating a GLX context (moderngl/glcontext's X11 backend) silently
replaces it with Xlib's default, which calls exit(1) on any error. A later
harmless BadWindow raised inside Tk then kills the whole app with no traceback.
Every place that creates or releases a GL context or touches GLFW therefore
runs inside preserve_x_error_handler(), which puts the previous handler back.
"""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import sys
import threading
from typing import Iterator, Optional

_lock = threading.Lock()
_x11 = None
_x11_tried = False


def _load() -> Optional[ctypes.CDLL]:
    global _x11, _x11_tried
    if _x11_tried:
        return _x11
    _x11_tried = True
    if not sys.platform.startswith("linux"):
        return None
    try:
        x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        # Handlers are passed around as raw pointers, never as Python
        # callbacks, so there is no ctypes thunk that could be collected.
        x.XSetErrorHandler.restype = ctypes.c_void_p
        x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
        _x11 = x
    except Exception:  # noqa: BLE001 - no libX11: nothing to protect
        _x11 = None
    return _x11


def _swap(x, handler) -> Optional[int]:
    """Install `handler` (an address or None) and return the previous one."""
    return x.XSetErrorHandler(handler)


@contextlib.contextmanager
def preserve_x_error_handler() -> Iterator[None]:
    """Restore the Xlib error handler that was active on entry. Never raises.

    XSetErrorHandler has no getter: passing NULL installs the default and
    returns the previous one, which is put back immediately."""
    x = _load()
    saved = None
    have = False
    if x is not None:
        try:
            with _lock:
                saved = _swap(x, None)
                _swap(x, saved)
            have = True
        except Exception:  # noqa: BLE001
            have = False
    try:
        yield
    finally:
        if have:
            try:
                with _lock:
                    current = _swap(x, None)
                    # Re-install whatever was active: the saved handler if the
                    # body replaced it, otherwise the one we just read back.
                    _swap(x, saved if current != saved else current)
            except Exception:  # noqa: BLE001
                pass
