# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import os
import threading

import pytest

from nightvision.hotkeys import NullHotkeyManager, create_hotkey_manager, parse_hotkey
from nightvision.platform_detect import PlatformInfo


@pytest.mark.parametrize("text,mods,key", [
    ("ctrl+alt+n", {"ctrl", "alt"}, "n"),
    ("Ctrl + Shift + F5", {"ctrl", "shift"}, "f5"),
    ("super+space", {"win"}, "space"),
    ("control+option+1", {"ctrl", "alt"}, "1"),
    ("n+alt", {"alt"}, "n"),
])
def test_parse_ok(text, mods, key):
    assert parse_hotkey(text) == (frozenset(mods), key)


@pytest.mark.parametrize("bad", ["", "n", "ctrl+alt", "ctrl+a+b", "ctrl++n", "  "])
def test_parse_bad(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_win32_manager_imports_and_refuses_on_linux():
    import sys
    from nightvision.hotkeys.win32 import Win32HotkeyManager, vk_for
    assert vk_for("n") == ord("N") and vk_for("f5") == 0x74
    if sys.platform != "win32":
        m = Win32HotkeyManager()
        m.register("ctrl+alt+n", lambda: None)
        with pytest.raises(RuntimeError):
            m.start()


def test_factory_policy():
    def pi(o, s):
        return PlatformInfo(o, s, "", False, s == "x11")
    m, r = create_hotkey_manager(pi("linux", "wayland"))
    assert isinstance(m, NullHotkeyManager) and "nightvision ctl" in r[0]
    m, _ = create_hotkey_manager(pi("windows", "win32"))
    assert type(m).__name__ == "Win32HotkeyManager"
    m, _ = create_hotkey_manager(pi("linux", "x11"))
    assert type(m).__name__ == "X11HotkeyManager"
    m, _ = create_hotkey_manager(pi("linux", "headless"))
    assert isinstance(m, NullHotkeyManager)


@pytest.mark.gui
def test_x11_grab_fires_and_stops_fast():
    import ctypes
    import ctypes.util
    import time
    from nightvision.hotkeys.x11 import X11HotkeyManager, _load_x11, keysym_for
    xt_name = ctypes.util.find_library("Xtst")
    if not xt_name:
        pytest.skip("libXtst not installed")
    xt = ctypes.CDLL(xt_name)
    x = _load_x11()
    xt.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

    fired = threading.Event()
    mgr = X11HotkeyManager()
    mgr.register("ctrl+alt+n", fired.set)
    mgr.start()
    try:
        assert mgr.failed == []
        dpy = x.XOpenDisplay(None)
        try:
            codes = [x.XKeysymToKeycode(dpy, keysym_for(x, k)) for k in ("Control_L", "Alt_L", "n")]
            for c in codes:
                xt.XTestFakeKeyEvent(dpy, c, 1, 0)
            for c in reversed(codes):
                xt.XTestFakeKeyEvent(dpy, c, 0, 0)
            x.XSync(dpy, 0)
        finally:
            x.XCloseDisplay(dpy)
        assert fired.wait(2.0), "hotkey callback did not fire"
    finally:
        t0 = time.monotonic()
        mgr.stop(2.0)
    assert time.monotonic() - t0 < 2.0
    assert mgr._thread is None


@pytest.mark.gui
def test_x11_conflicting_grab_reported_not_fatal():
    from nightvision.hotkeys.x11 import X11HotkeyManager
    a, b = X11HotkeyManager(), X11HotkeyManager()
    a.register("ctrl+alt+m", lambda: None)
    b.register("ctrl+alt+m", lambda: None)
    a.start()
    try:
        b.start()
        assert b.failed == ["ctrl+alt+m"]
        b.stop()
    finally:
        a.stop()
