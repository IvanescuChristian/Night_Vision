# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import os
import subprocess
import sys
import time

import pytest

from nightvision.core.params import EnhanceParams
from nightvision.foreground import foreground_window_title, match_profile
from nightvision.profiles import Profile

P = EnhanceParams()
PROFILES = [
    Profile("off", P),
    Profile("Escape from Tarkov", P, ["EscapeFromTarkov.exe"]),
    Profile("Rust", P, ["RustClient.exe"]),
    Profile("Deep Dark Game", P, []),
]


@pytest.mark.parametrize("title,expected", [
    ("EscapeFromTarkov", "Escape from Tarkov"),
    ("escapefromtarkov | UnityWndClass", "Escape from Tarkov"),
    ("RustClient", "Rust"),
    ("Deep Dark Game - menu", "Deep Dark Game"),   # name fallback
    ("deep dark game", "Deep Dark Game"),
    ("Firefox | Navigator", None),
    ("Turn it off now", None),                      # "off" is a preset name: ignored
    ("", None),
    (None, None),
])
def test_match_profile(title, expected):
    p = match_profile(title, PROFILES)
    assert (p.name if p else None) == expected


def test_match_profile_accepts_mapping_and_exe_path():
    d = {p.name: p for p in PROFILES}
    d["Path"] = Profile("Path", P, ["C:\\Games\\Foo\\FooGame.exe"])
    assert match_profile("FooGame", d).name == "Path"


def test_exe_beats_name():
    a = Profile("Shadow Realm", P, [])
    b = Profile("Other", P, ["realm.exe"])
    assert match_profile("shadow realm", [a, b]).name == "Other"


def test_platform_functions_never_crash():
    r = foreground_window_title()
    assert r is None or isinstance(r, str)
    assert foreground_window_title(env={}, platform="linux") is None
    assert foreground_window_title(env={"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"},
                                   platform="linux") is None
    assert foreground_window_title(env={}, platform="darwin") is None
    # Windows branch on a non-Windows host must degrade to None, not raise
    assert foreground_window_title(platform="win32") is None or sys.platform == "win32"


@pytest.mark.gui
def test_x11_live_read_under_xvfb(monkeypatch):
    tk = pytest.importorskip("tkinter")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    root = tk.Tk()
    try:
        root.title("NvForegroundProbe")
        root.update()
        title = foreground_window_title()
        # A WM-less Xvfb never sets _NET_ACTIVE_WINDOW: None is the correct answer.
        assert title is None or isinstance(title, str)
        if title is not None:
            assert "NvForegroundProbe" in title or "tk" in title.lower()
    finally:
        root.destroy()


@pytest.mark.gui
def test_x11_reads_active_window_when_set(monkeypatch):
    """Set _NET_ACTIVE_WINDOW ourselves (as a WM would) and check we read it."""
    tk = pytest.importorskip("tkinter")
    import ctypes
    import ctypes.util
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    root = tk.Tk()
    try:
        root.title("NvActiveProbe")
        root.update()
        x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.c_int]
        x.XFlush.argtypes = [ctypes.c_void_p]
        dpy = x.XOpenDisplay(None)
        assert dpy
        try:
            # Tk stores WM_NAME on a wrapper that is the parent of winfo_id();
            # a real WM would publish exactly that client window.
            x.XQueryTree.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                ctypes.POINTER(ctypes.c_uint)]
            rt, par, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_uint()
            kids = ctypes.POINTER(ctypes.c_ulong)()
            x.XQueryTree(dpy, root.winfo_id(), ctypes.byref(rt), ctypes.byref(par),
                         ctypes.byref(kids), ctypes.byref(n))
            val = ctypes.c_ulong(par.value)
            x.XChangeProperty(dpy, x.XDefaultRootWindow(dpy),
                              x.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", 0), 33, 32, 0,
                              ctypes.byref(val), 1)
            x.XFlush(dpy)
            title = foreground_window_title()
        finally:
            x.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x.XCloseDisplay(dpy)
        assert isinstance(title, str) and title
        assert "NvActiveProbe" in title
    finally:
        root.destroy()
