# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Choose a tone backend for the current platform."""
from __future__ import annotations

from ...platform_detect import PlatformInfo
from .base import ToneBackend
from .null import NullToneBackend
from .wayland import GammastepProxyBackend, WlrGammaControlBackend
from .win32 import Win32GammaBackend
from .x11 import XF86VidModeGammaBackend, XRandRGammaBackend

TONE_BACKENDS: dict[str, type[ToneBackend]] = {
    "win32": Win32GammaBackend,
    "xrandr": XRandRGammaBackend,
    "xf86vidmode": XF86VidModeGammaBackend,
    "wlr-gamma": WlrGammaControlBackend,
    "gammastep-proxy": GammastepProxyBackend,
    "null": NullToneBackend,
}


def _order(info: PlatformInfo) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    if info.session == "win32":
        return ["win32"], reasons
    if info.session == "x11":
        return ["xrandr", "xf86vidmode"], reasons
    if info.session in ("wayland", "xwayland-only"):
        if info.wlroots:
            return ["wlr-gamma", "gammastep-proxy"], reasons
        # WHY: X11 gamma under XWayland does not reach the real output, so never offered.
        reasons.append("compositor exposes no gamma API to clients; use capture mode")
        return [], reasons
    reasons.append(f"no display session ({info.session}); tone backends unavailable")
    return [], reasons


def _make(name: str, info: PlatformInfo) -> ToneBackend:
    cls = TONE_BACKENDS[name]
    return cls(wlroots=info.wlroots) if name == "gammastep-proxy" else cls()


def select_tone_backend(info: PlatformInfo, prefer: str | None = None,
                        dry_run: bool = False) -> tuple[ToneBackend, list[str]]:
    reasons: list[str] = []
    if dry_run:
        return NullToneBackend(), ["dry-run: using null backend"]
    names, r = _order(info)
    reasons += r
    if prefer:
        if prefer not in TONE_BACKENDS:
            reasons.append(f"prefer={prefer!r}: unknown backend")
        else:
            b = _make(prefer, info)
            if b.available():
                return b, reasons
            reasons.append(f"{prefer}: {b.unavailable_reason()} (preferred)")
            b.close()
    for n in names:
        if n == prefer:
            continue
        b = _make(n, info)
        if b.available():
            return b, reasons
        reasons.append(f"{n}: {b.unavailable_reason()}")
        b.close()
    reasons.append("falling back to null backend")
    return NullToneBackend(), reasons
