# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Choose a capture backend for the current platform."""
from __future__ import annotations

from ...platform_detect import PlatformInfo
from .base import CaptureError, CaptureSource
from .dxgi import DxgiCapture
from .mss_capture import MssCapture, list_monitors
from .portal import PortalPipeWireCapture
from .synthetic import SyntheticCapture


def _order(info: PlatformInfo) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    if info.session == "win32":
        return ["dxgi", "mss"], reasons
    if info.session == "x11":
        return ["mss"], reasons
    if info.session in ("wayland", "xwayland-only"):
        # WHY: the portal is the only path that sees native Wayland windows;
        # mss is a last resort that only sees XWayland clients.
        return ["portal", "mss"], reasons
    reasons.append(f"no display session ({info.session}); no screen capture backend")
    return [], reasons


def monitor_rect(index: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of monitor `index` (1-based, as mss lists them).

    Raises CaptureError naming the available monitors when the index is out of range.
    """
    mons = list_monitors()
    if not 1 <= index <= len(mons):
        have = "; ".join(f"{i}: {w}x{h} at ({x},{y})" for i, (x, y, w, h) in enumerate(mons, 1))
        raise CaptureError(f"monitor {index} not found; available monitors: {have or 'none'}")
    return mons[index - 1]


def _make(name: str, info: PlatformInfo, monitor: int = 1) -> CaptureSource:
    if name == "dxgi":
        return DxgiCapture(output_idx=monitor - 1)  # NOT VERIFIED on Windows
    if name == "portal":
        return PortalPipeWireCapture()
    wayland = info.session in ("wayland", "xwayland-only")
    return MssCapture(monitor=monitor, wayland_session=wayland)


def select_capture_backend(info: PlatformInfo, prefer: str | None = None,
                           synthetic: bool = False, monitor: int = 1) -> tuple[CaptureSource, list[str]]:
    reasons: list[str] = []
    if synthetic:
        return SyntheticCapture(), ["synthetic scene requested"]
    names, r = _order(info)
    reasons += r
    if prefer:
        if prefer not in ("dxgi", "mss", "portal"):
            reasons.append(f"prefer={prefer!r}: unknown backend")
        else:
            names = [prefer] + [n for n in names if n != prefer]
    for n in names:
        # Only pass a non-default monitor: keeps _make(name, info) callable as before.
        src = _make(n, info, monitor) if monitor != 1 else _make(n, info)
        if src.available():
            cav = getattr(src, "caveat", "")
            if cav:
                reasons.append(f"{n}: {cav}")
            return src, reasons
        reasons.append(f"{n}: {src.unavailable_reason()}")
    raise CaptureError("no capture backend available: " + "; ".join(reasons))
