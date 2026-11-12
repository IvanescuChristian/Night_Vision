# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Global hotkeys via RegisterHotKey (Windows) / XGrabKey (X11) only.

No low-level keyboard hooks and no keyboard-sniffing libraries: these two
APIs deliver only the exact registered combinations to us.
"""
from .base import HotkeyManager, NullHotkeyManager, create_hotkey_manager, parse_hotkey

__all__ = ["HotkeyManager", "NullHotkeyManager", "create_hotkey_manager", "parse_hotkey"]
