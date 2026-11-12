# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Hotkey manager interface and factory.

Callbacks run on the hotkey thread, NOT the UI thread: consumers must marshal
(queue / after_idle / signal) before touching UI state.
"""
from __future__ import annotations

import abc
from typing import Callable

MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift",
    "win": "win", "super": "win", "meta": "win", "cmd": "win",
}


def parse_hotkey(text: str) -> tuple[frozenset[str], str]:
    """'Ctrl+Alt+N' -> (frozenset({'ctrl','alt'}), 'n'). Pure.

    At least one modifier is required: a bare global key would steal that key
    from every application (and games).
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty hotkey")
    parts = [p.strip().lower() for p in text.split("+")]
    if any(not p for p in parts):
        raise ValueError(f"malformed hotkey {text!r}")
    mods: set[str] = set()
    keys: list[str] = []
    for p in parts:
        if p in MODIFIERS:
            mods.add(MODIFIERS[p])
        else:
            keys.append(p)
    if len(keys) != 1:
        raise ValueError(f"hotkey {text!r} needs exactly one non-modifier key")
    if not mods:
        raise ValueError(f"hotkey {text!r} needs at least one modifier")
    return frozenset(mods), keys[0]


class HotkeyManager(abc.ABC):
    def __init__(self) -> None:
        self._bindings: list[tuple[str, frozenset[str], str, Callable[[], None]]] = []
        self.failed: list[str] = []  # hotkeys the OS refused (already taken)

    def register(self, hotkey: str, callback: Callable[[], None]) -> None:
        """Before start(). Raises ValueError on a malformed hotkey."""
        mods, key = parse_hotkey(hotkey)
        self._bindings.append((hotkey, mods, key, callback))

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self, timeout: float = 2.0) -> None: ...


class NullHotkeyManager(HotkeyManager):
    def start(self) -> None:
        pass

    def stop(self, timeout: float = 2.0) -> None:
        pass


def create_hotkey_manager(info):
    """-> (manager, reasons)."""
    if info.os == "windows":
        from .win32 import Win32HotkeyManager
        return Win32HotkeyManager(), ["windows: RegisterHotKey"]
    if info.os == "linux" and info.session == "x11":
        from .x11 import X11HotkeyManager
        return X11HotkeyManager(), ["x11: XGrabKey"]
    if info.os == "linux" and info.session in ("wayland", "xwayland-only"):
        return NullHotkeyManager(), [
            "compositor-managed shortcuts: bind `nightvision ctl ...` in your desktop settings"]
    return NullHotkeyManager(), [f"no global hotkeys for os={info.os} session={info.session}"]
