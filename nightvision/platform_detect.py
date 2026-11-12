# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Pure platform/session detection (injectable env for tests)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_WLROOTS_DESKTOPS = {"sway", "hyprland", "river", "wayfire", "labwc", "niri"}


@dataclass(frozen=True)
class PlatformInfo:
    os: str  # 'windows' | 'linux' | 'macos' | 'other'
    session: str  # 'win32' | 'x11' | 'wayland' | 'xwayland-only' | 'headless'
    desktop: str
    wlroots: bool
    has_x_display: bool


def _os_name(platform: str) -> str:
    if platform.startswith("win") or platform == "cygwin":
        return "windows"
    if platform.startswith("linux"):
        return "linux"
    if platform == "darwin":
        return "macos"
    return "other"


def detect(env=None, platform: str | None = None) -> PlatformInfo:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    osname = _os_name(platform)

    raw = env.get("XDG_CURRENT_DESKTOP", "") or ""
    # Value may be colon-separated ("ubuntu:GNOME"); first token identifies the shell.
    desktop = raw.split(":")[0].strip().lower()
    tokens = {t.strip().lower() for t in raw.split(":") if t.strip()}
    wlroots = bool(
        tokens & _WLROOTS_DESKTOPS
        or env.get("SWAYSOCK")
        or env.get("HYPRLAND_INSTANCE_SIGNATURE")
    )
    has_x = bool(env.get("DISPLAY"))

    if osname == "windows":
        return PlatformInfo("windows", "win32", desktop, False, False)
    if osname != "linux":
        return PlatformInfo(osname, "headless", desktop, False, has_x)

    wayland = bool(env.get("WAYLAND_DISPLAY")) or env.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    if wayland:
        # WHY: with only XWayland available, X11 gamma calls do not reach the real output,
        # so 'xwayland-only' is reported separately from a native wayland session
        # only when no Wayland socket is advertised.
        session = "wayland" if env.get("WAYLAND_DISPLAY") else "xwayland-only"
        return PlatformInfo("linux", session, desktop, wlroots, has_x)
    if has_x:
        return PlatformInfo("linux", "x11", desktop, wlroots, True)
    return PlatformInfo("linux", "headless", desktop, wlroots, False)
