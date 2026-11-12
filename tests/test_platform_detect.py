# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import pytest

from nightvision.platform_detect import detect

CASES = [
    ("windows", {}, "win32", ("windows", "win32", "", False, False)),
    ("x11", {"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "XFCE"}, "linux", ("linux", "x11", "xfce", False, True)),
    ("xwayland", {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
     "linux", ("linux", "wayland", "ubuntu", False, True)),
    ("sway", {"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/x"}, "linux", ("linux", "wayland", "", True, False)),
    ("hypr", {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "w", "XDG_CURRENT_DESKTOP": "Hyprland"},
     "linux", ("linux", "wayland", "hyprland", True, False)),
    ("gnome-wl", {"WAYLAND_DISPLAY": "w", "XDG_CURRENT_DESKTOP": "GNOME"}, "linux", ("linux", "wayland", "gnome", False, False)),
    ("xwayland-only", {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":1"}, "linux", ("linux", "xwayland-only", "", False, True)),
    ("headless", {}, "linux", ("linux", "headless", "", False, False)),
    ("mac", {}, "darwin", ("macos", "headless", "", False, False)),
]


@pytest.mark.parametrize("name,env,plat,exp", CASES, ids=[c[0] for c in CASES])
def test_matrix(name, env, plat, exp):
    i = detect(env, plat)
    assert (i.os, i.session, i.desktop, i.wlroots, i.has_x_display) == exp
