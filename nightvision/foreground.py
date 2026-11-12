# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Foreground-window title lookup for optional automatic profile switching.

Anti-cheat safety: only the window *title/class* is read, through the window
manager's public window APIs. No process handle is ever opened (no
OpenProcess, no PID lookup, no module/file inspection), because protected game
processes must not be touched by anything, even for a read.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
import threading
from typing import Iterable, Mapping, Optional

log = logging.getLogger(__name__)

_MIN_NAME_LEN = 4  # shorter profile names ("off") would match unrelated titles


# ---------------------------------------------------------------- matching
def _stem(exe: str) -> str:
    base = os.path.basename(exe.replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    return (stem if ext.lower() in (".exe", ".app", ".x86_64", ".bin", "") else base).lower()


def match_profile(title: Optional[str], profiles: Iterable) -> Optional[object]:
    """Return the first profile whose exe stem or name appears in `title`.

    Executable stems are tried first: they are what users configure on purpose,
    while a profile *name* is only a fallback guess. Names that are built-in
    preset names (or very short) are ignored for the name pass, otherwise the
    "off"/"balanced" profiles would hijack any window containing that word.
    """
    if not title:
        return None
    t = title.lower()
    plist = list(profiles.values()) if isinstance(profiles, Mapping) else list(profiles)
    for p in plist:
        for exe in p.exe_names:
            s = _stem(exe)
            if len(s) >= 3 and s in t:
                return p
    from .core.presets import PRESETS
    for p in plist:
        n = p.name.strip().lower()
        if len(n) >= _MIN_NAME_LEN and p.name not in PRESETS and n in t:
            return p
    return None


# ---------------------------------------------------------------- Windows
def _windows_title() -> Optional[str]:
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        cbuf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cbuf, 256)
        out = " | ".join(s for s in (title, cbuf.value) if s)
        return out or None
    except Exception as exc:  # noqa: BLE001 - best effort, never fatal
        log.debug("windows foreground lookup failed: %s", exc)
        return None


# ---------------------------------------------------------------- X11
_x11_lock = threading.Lock()
_XA_WINDOW = 33
_XA_STRING = 31
_ERR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _ignore_x_error(_dpy, _ev):  # a window can vanish between the two calls
    return 0


_ignore_cb = _ERR_HANDLER(_ignore_x_error)


def _load_x11():
    name = ctypes.util.find_library("X11") or "libX11.so.6"
    x = ctypes.CDLL(name)
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XInternAtom.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XGetWindowProperty.restype = ctypes.c_int
    x.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
        ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
    x.XFree.argtypes = [ctypes.c_void_p]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XSetErrorHandler.restype = ctypes.c_void_p
    x.XSetErrorHandler.argtypes = [_ERR_HANDLER]
    return x


def _get_prop(x, dpy, win, atom, req_type) -> Optional[tuple[bytes, int, int]]:
    """-> (raw bytes, format, nitems) or None."""
    atype = ctypes.c_ulong()
    fmt = ctypes.c_int()
    n = ctypes.c_ulong()
    after = ctypes.c_ulong()
    data = ctypes.POINTER(ctypes.c_ubyte)()
    rc = x.XGetWindowProperty(dpy, win, atom, 0, 1024, 0, req_type, ctypes.byref(atype),
                              ctypes.byref(fmt), ctypes.byref(n), ctypes.byref(after),
                              ctypes.byref(data))
    if rc != 0 or not data or n.value == 0:
        if data:
            x.XFree(data)
        return None
    try:
        unit = ctypes.sizeof(ctypes.c_long) if fmt.value == 32 else fmt.value // 8
        return ctypes.string_at(data, n.value * unit), fmt.value, n.value
    finally:
        x.XFree(data)


def _x11_title() -> Optional[str]:
    try:
        x = _load_x11()
    except OSError:
        return None
    with _x11_lock:
        dpy = x.XOpenDisplay(None)
        if not dpy:
            return None
        # Swap in a no-op error handler: the default one calls exit() on a
        # BadWindow, which must never take the app down. Restored below.
        prev = x.XSetErrorHandler(_ignore_cb)
        try:
            root = x.XDefaultRootWindow(dpy)
            atom = lambda s: x.XInternAtom(dpy, s, 0)  # noqa: E731
            r = _get_prop(x, dpy, root, atom(b"_NET_ACTIVE_WINDOW"), _XA_WINDOW)
            if r is None or r[1] != 32:
                return None
            win = ctypes.c_ulong.from_buffer_copy(r[0][:ctypes.sizeof(ctypes.c_ulong)]).value
            if not win:
                return None
            parts: list[str] = []
            nm = _get_prop(x, dpy, win, atom(b"_NET_WM_NAME"), atom(b"UTF8_STRING"))
            if nm is None:
                nm = _get_prop(x, dpy, win, atom(b"WM_NAME"), 0)
            if nm is not None:
                parts.append(nm[0].decode("utf-8", "replace"))
            cl = _get_prop(x, dpy, win, atom(b"WM_CLASS"), _XA_STRING)
            if cl is not None:
                parts.extend(s for s in cl[0].decode("latin-1").split("\0") if s)
            x.XSync(dpy, 0)
            return " | ".join(parts) or None
        finally:
            x.XSetErrorHandler(ctypes.cast(prev, _ERR_HANDLER) if prev else _ERR_HANDLER(0))
            x.XCloseDisplay(dpy)


def foreground_window_title(env=None, platform: Optional[str] = None) -> Optional[str]:
    """Title (plus window class) of the focused window, or None if unknown.

    Wayland compositors deliberately do not expose the focused window to
    clients, so there the answer is always None.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    try:
        if platform.startswith("win"):
            return _windows_title()
        if platform.startswith("linux"):
            if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE", "").lower() == "wayland":
                return None
            if not env.get("DISPLAY"):
                return None
            return _x11_title()
    except Exception as exc:  # noqa: BLE001
        log.debug("foreground lookup failed: %s", exc)
    return None
