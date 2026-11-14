# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import ctypes
import subprocess
import sys

import numpy as np
import pytest

from nightvision.backends.tone import base, x11
from nightvision.backends.tone.base import ToneBackend, ToneBackendError
from nightvision.backends.tone.null import NullToneBackend
from nightvision.backends.tone.select import TONE_BACKENDS, select_tone_backend
from nightvision.backends.tone.wayland import GammastepProxyBackend, fit_power_gamma
from nightvision.backends.tone.win32 import Win32GammaBackend
from nightvision.platform_detect import detect


def ramp(g=(1.0, 1.0, 1.0), n=256, gain=1.0):
    x = np.linspace(0, 1, n)
    return np.stack([np.rint(gain * x ** e * 65535) for e in g]).astype(np.uint16)


# ---- safety / base
@pytest.mark.parametrize("bad", [
    np.zeros((3, 256), np.uint16),                       # black
    np.full((3, 256), 40000, np.uint16),                 # min > 50%
    ramp()[::-1, ::-1].copy(),                           # non-monotonic
    ramp().astype(np.float32),                           # dtype
    ramp()[:2],                                          # shape
])
def test_unsafe_rejected(bad):
    b = NullToneBackend()
    with pytest.raises(ToneBackendError):
        b.apply(bad)
    assert b.last_ramp is None


def test_restore_idempotent_and_original_captured_once():
    b = NullToneBackend()
    b.apply(ramp((0.8,) * 3))
    first = b._original.copy()
    b.apply(ramp((0.6,) * 3))
    assert np.array_equal(b._original, first)
    b.restore(); b.restore()
    assert b.restored == 1


def test_atexit_registry_restores():
    b = NullToneBackend()
    b.apply(ramp())
    base._restore_all()
    assert b.restored == 1
    b.close()


def test_context_manager_restores():
    with NullToneBackend() as b:
        b.apply(ramp())
    assert b.restored == 1


# ---- win32
def test_win32_import_and_unavailable_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert Win32GammaBackend().available() is False


# ---- x11 with fake libs
class FakeXrr:
    def __init__(self, sizes):
        self.sizes = sizes
        self.gamma = {c: [list(np.arange(s) * 10 + k) for k in range(3)] for c, s in sizes.items()}
        self.sets = []

    def XRRGetCrtcGammaSize(self, d, c): return self.sizes[c]
    def XRRGetCrtcGamma(self, d, c):
        s = self.sizes[c]
        g = x11._XRRCrtcGamma()
        g.size = s
        g.red, g.green, g.blue = [(ctypes.c_ushort * s)(*self.gamma[c][k]) for k in range(3)]
        self._keep = getattr(self, "_keep", []) + [g]
        return ctypes.pointer(g)
    def XRRAllocGamma(self, s):
        g = x11._XRRCrtcGamma()
        g.size = s
        g.red, g.green, g.blue = [(ctypes.c_ushort * s)() for _ in range(3)]
        self._keep = getattr(self, "_keep", []) + [g]
        return ctypes.pointer(g)
    def XRRSetCrtcGamma(self, d, c, g):
        s = g.contents.size
        self.sets.append((c, s, [[ch[i] for i in range(s)] for ch in (g.contents.red, g.contents.green, g.contents.blue)]))
    def XRRFreeGamma(self, g): pass
    def XRRGetScreenResourcesCurrent(self, d, w):
        ids = list(self.sizes)
        self._ids = (ctypes.c_ulong * len(ids))(*ids)
        r = x11._XRRScreenResources()
        r.ncrtc = len(ids)
        r.crtcs = ctypes.cast(self._ids, ctypes.POINTER(ctypes.c_ulong))
        self._res = r
        return ctypes.pointer(r)
    def XRRFreeScreenResources(self, r): pass


class FakeX11:
    def XOpenDisplay(self, n): return 1
    def XDefaultRootWindow(self, d): return 1
    def XCloseDisplay(self, d): pass
    def XSync(self, d, f): pass


def test_xrandr_two_crtcs_apply_restore(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":9")
    xrr = FakeXrr({11: 256, 12: 1024})
    monkeypatch.setattr(x11, "load_xlib", lambda: (FakeX11(), xrr))
    b = x11.XRandRGammaBackend()
    assert b.available()
    r = ramp((0.7,) * 3)
    b.apply(r)
    assert [(c, s) for c, s, _ in xrr.sets] == [(11, 256), (12, 1024)]
    assert xrr.sets[0][2][0][255] == 65535 and xrr.sets[1][2][0][1023] == 65535
    assert xrr.sets[1][2][1][512] > xrr.sets[1][2][1][100]
    xrr.sets.clear()
    b.restore(); b.restore()
    assert len(xrr.sets) == 2
    for c, s, data in xrr.sets:
        assert data == xrr.gamma[c]
    b.close()


def test_xrandr_unavailable_cases(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert x11.XRandRGammaBackend().available() is False
    monkeypatch.setenv("DISPLAY", ":9")
    monkeypatch.setattr(x11, "load_xlib", lambda: None)
    assert x11.XRandRGammaBackend().available() is False
    monkeypatch.setattr(x11, "load_xlib", lambda: (FakeX11(), FakeXrr({1: 0})))
    b = x11.XRandRGammaBackend()
    assert b.available() is False and "gamma size" in b.unavailable_reason()


# ---- fit / gammastep
@pytest.mark.parametrize("g", [1.0, 0.8, 1.5])
def test_fit_power_gamma(g):
    gr, gg, gb, br = fit_power_gamma(ramp((g, g, g)))
    assert all(abs(v - g) < 0.05 for v in (gr, gg, gb))
    assert abs(br - 1.0) < 0.05


def test_fit_per_channel_and_gain():
    gr, gg, gb, br = fit_power_gamma(ramp((1.0, 0.8, 1.5), gain=0.9))
    assert abs(gr - 1) < .05 and abs(gg - .8) < .05 and abs(gb - 1.5) < .05
    assert abs(br - 0.9) < 0.05


def test_gammastep_argv_and_restore(monkeypatch):
    calls = []
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/gammastep")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    b = GammastepProxyBackend(wlroots=True)
    assert b.available()
    b.apply(ramp((0.5, 0.5, 0.5)))
    argv, kw = calls[0]
    assert argv[:6] == ["gammastep", "-m", "wayland", "-O", "6500", "-g"]
    assert argv[6].split(":") == ["2.000"] * 3 and argv[-3:-1] == ["-b", "1.000"] and argv[-1] == "-P"
    assert kw["timeout"] > 0
    b.restore()
    assert calls[1][0] == ["gammastep", "-x", "-m", "wayland"]


def test_gammastep_unavailable(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    assert not GammastepProxyBackend(wlroots=True).available()
    monkeypatch.setattr("shutil.which", lambda n: "/x")
    assert not GammastepProxyBackend(wlroots=False).available()


# ---- selection
def _info(env, plat="linux"):
    return detect(env, plat)


def test_select_gnome_wayland_none():
    b, why = select_tone_backend(_info({"WAYLAND_DISPLAY": "w", "DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "GNOME"}))
    assert b.name == "null"
    assert any("use capture mode" in r for r in why)


def test_select_sway_uses_gammastep(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/x/gammastep")
    b, why = select_tone_backend(_info({"WAYLAND_DISPLAY": "w", "SWAYSOCK": "s"}))
    assert b.name == "gammastep-proxy"
    assert any(r.startswith("wlr-gamma: needs pywayland") for r in why)


def test_select_x11_order_and_skip(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":9")
    monkeypatch.setattr(x11, "load_xlib", lambda: None)
    b, why = select_tone_backend(_info({"DISPLAY": ":9"}))
    assert b.name == "null"
    assert [r.split(":")[0] for r in why[:2]] == ["xrandr", "xf86vidmode"]


def test_select_x11_xrandr_chosen(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":9")
    monkeypatch.setattr(x11, "load_xlib", lambda: (FakeX11(), FakeXrr({1: 256})))
    b, _ = select_tone_backend(_info({"DISPLAY": ":9"}))
    assert b.name == "xrandr"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="on real Windows the win32 backend is genuinely selected")
def test_select_windows_on_linux_falls_to_null():
    b, why = select_tone_backend(_info({}, "win32"))
    assert b.name == "null" and why[0].startswith("win32:")


def test_select_dry_run_and_prefer():
    assert select_tone_backend(_info({"DISPLAY": ":0"}), dry_run=True)[0].name == "null"
    b, why = select_tone_backend(_info({}), prefer="null")
    assert b.name == "null"
    assert set(TONE_BACKENDS) >= {"win32", "xrandr", "xf86vidmode", "wlr-gamma", "gammastep-proxy", "null"}


# ---- live probe
@pytest.mark.gui
def test_live_xrandr_probe():
    b = x11.XRandRGammaBackend()
    ok = b.available()
    print("LIVE xrandr available:", ok, b.unavailable_reason())
    if not ok:
        pytest.skip(f"XRandR gamma unavailable here: {b.unavailable_reason()}")
    b.apply(ramp((0.8,) * 3))
    b.restore()
    b.close()
