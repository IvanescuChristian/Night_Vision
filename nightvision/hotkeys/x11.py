# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""XGrabKey backend via ctypes/libX11 (own Display connection, own thread).

Works on X11 and XWayland (for XWayland only while an X client has focus, so
create_hotkey_manager does not use it on Wayland sessions).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import select
import threading
from typing import Optional

from .base import HotkeyManager

log = logging.getLogger(__name__)

ShiftMask, LockMask, ControlMask, Mod1Mask, Mod2Mask, Mod4Mask = 1, 2, 4, 8, 16, 64
KeyPress = 2
GrabModeAsync = 1
_MOD_MASK = {"shift": ShiftMask, "ctrl": ControlMask, "alt": Mod1Mask, "win": Mod4Mask}
# WHY: grabs match modifiers exactly, so NumLock (Mod2) / CapsLock (Lock) would
# silently disable the hotkey. Grab every combination.
_LOCK_VARIANTS = (0, LockMask, Mod2Mask, LockMask | Mod2Mask)
_KEYSYM_ALIASES = {"esc": "Escape", "enter": "Return", "pageup": "Prior", "pagedown": "Next",
                   "del": "Delete", "ins": "Insert", "backspace": "BackSpace"}


class _XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
                ("state", ctypes.c_uint), ("keycode", ctypes.c_uint),
                ("same_screen", ctypes.c_int)]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xkey", _XKeyEvent), ("pad", ctypes.c_long * 24)]


_ERR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _load_x11():
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    x = ctypes.CDLL(name)
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XStringToKeysym.restype = ctypes.c_ulong
    x.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x.XKeysymToKeycode.restype = ctypes.c_ubyte
    x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong,
                           ctypes.c_int, ctypes.c_int, ctypes.c_int]
    x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XPending.argtypes = [ctypes.c_void_p]
    x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x.XConnectionNumber.argtypes = [ctypes.c_void_p]
    x.XSetErrorHandler.restype = ctypes.c_void_p
    x.XSetErrorHandler.argtypes = [_ERR_HANDLER]
    return x


def keysym_for(x, key: str) -> int:
    for cand in (_KEYSYM_ALIASES.get(key, key), key, key.capitalize(), key.upper()):
        ks = x.XStringToKeysym(cand.encode())
        if ks:
            return ks
    raise ValueError(f"unknown key {key!r}")


class X11HotkeyManager(HotkeyManager):
    def __init__(self, display: Optional[str] = None) -> None:
        super().__init__()
        self._display_name = display
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.start_error: Optional[str] = None

    def start(self) -> None:
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="nv-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        if self.start_error:
            raise RuntimeError(self.start_error)

    def _run(self) -> None:
        try:
            x = _load_x11()
        except OSError as exc:
            self.start_error = f"libX11 unavailable: {exc}"
            self._ready.set()
            return
        dpy = x.XOpenDisplay(self._display_name.encode() if self._display_name else None)
        if not dpy:
            self.start_error = "cannot open X display"
            self._ready.set()
            return
        try:
            root = x.XDefaultRootWindow(dpy)
            table: dict[tuple[int, int], object] = {}
            errors: list[int] = []

            def on_error(_d, _e):
                errors.append(1)  # BadAccess when another client owns the grab
                return 0
            handler = _ERR_HANDLER(on_error)  # keep a reference alive
            prev = x.XSetErrorHandler(handler)
            for text, mods, key, cb in self._bindings:
                try:
                    code = x.XKeysymToKeycode(dpy, keysym_for(x, key))
                except ValueError:
                    code = 0
                if not code:
                    self.failed.append(text)
                    continue
                mask = 0
                for m in mods:
                    mask |= _MOD_MASK[m]
                errors.clear()
                for extra in _LOCK_VARIANTS:
                    x.XGrabKey(dpy, code, mask | extra, root, 0, GrabModeAsync, GrabModeAsync)
                x.XSync(dpy, 0)
                if errors:
                    self.failed.append(text)
                    log.warning("XGrabKey(%s) failed (already grabbed?)", text)
                    continue
                table[(code, mask)] = cb
            x.XSetErrorHandler(_ERR_HANDLER(prev) if prev else None)
            self._handler = handler
            self._ready.set()
            fd = x.XConnectionNumber(dpy)
            ev = _XEvent()
            relevant = ShiftMask | ControlMask | Mod1Mask | Mod4Mask
            while not self._stop.is_set():
                # WHY: select with a short timeout instead of blocking in XNextEvent,
                # so stop() never has to interrupt a blocked Xlib call.
                if not x.XPending(dpy):
                    select.select([fd], [], [], 0.1)
                    continue
                x.XNextEvent(dpy, ctypes.byref(ev))
                if ev.type == KeyPress:
                    cb = table.get((ev.xkey.keycode, ev.xkey.state & relevant))
                    if cb:
                        try:
                            cb()
                        except Exception:
                            log.exception("hotkey callback failed")
        finally:
            self._ready.set()
            x.XCloseDisplay(dpy)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout)
            self._thread = None
