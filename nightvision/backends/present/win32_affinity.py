# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Windows-only feedback-loop guard for the overlay.

WHY: if the overlay is visible to the screen capturer, the enhanced image is
captured, enhanced again and so on. SetWindowDisplayAffinity with
WDA_EXCLUDEFROMCAPTURE hides our own window from capture APIs. This only
changes how OUR window is composed; it touches no other process.
"""
from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

WDA_NONE = 0x0
WDA_MONITOR = 0x1
WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 version 2004+


def available() -> bool:
    return sys.platform == "win32"


def exclude_from_capture(hwnd: int) -> bool:
    """True if the affinity was applied. False on non-Windows or on failure
    (e.g. pre-2004 Windows 10, where the call is rejected)."""
    if not available() or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        user32.SetWindowDisplayAffinity.restype = ctypes.c_int
        ok = bool(user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), WDA_EXCLUDEFROMCAPTURE))
    except Exception as exc:
        log.warning("SetWindowDisplayAffinity failed: %s", exc)
        return False
    if not ok:
        log.warning("SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) rejected "
                    "(needs Windows 10 2004+); overlay may be captured")
    return ok
