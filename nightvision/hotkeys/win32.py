# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""RegisterHotKey backend. Importable everywhere; start() needs Windows."""
from __future__ import annotations

import ctypes
import logging
import sys
import threading

from .base import HotkeyManager

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MOD_NOREPEAT = 0x4000  # holding the keys must not spam the callback

_MOD_BITS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
_VK = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
       "tab": 0x09, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24,
       "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26,
       "right": 0x27, "down": 0x28}


def vk_for(key: str) -> int:
    if key in _VK:
        return _VK[key]
    if len(key) == 1 and key.isalnum():
        return ord(key.upper())  # VK codes for 0-9/A-Z equal their ASCII
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    raise ValueError(f"unsupported key {key!r}")


class Win32HotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()

    def start(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Win32HotkeyManager requires Windows")
        # Resolve keys up front so a bad key fails on the caller's thread.
        for _, _, key, _ in self._bindings:
            vk_for(key)
        self._thread = threading.Thread(target=self._run, name="nv-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def _run(self) -> None:
        from ctypes import wintypes
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()  # type: ignore[attr-defined]
        by_id = {}
        for i, (text, mods, key, cb) in enumerate(self._bindings, 1):
            bits = MOD_NOREPEAT
            for m in mods:
                bits |= _MOD_BITS[m]
            if user32.RegisterHotKey(None, i, bits, vk_for(key)):
                by_id[i] = cb
            else:
                self.failed.append(text)
                log.warning("RegisterHotKey(%s) failed (already taken?)", text)
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in by_id:
                    try:
                        by_id[msg.wParam]()
                    except Exception:
                        log.exception("hotkey callback failed")
        finally:
            for i in by_id:
                user32.UnregisterHotKey(None, i)

    def stop(self, timeout: float = 2.0) -> None:
        t = self._thread
        if t is None:
            return
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)  # type: ignore[attr-defined]
        t.join(timeout)
        self._thread = None
