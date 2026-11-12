# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import numpy as np
import pytest

from nightvision.backends.capture import CaptureError, select_capture_backend
from nightvision.backends.capture.dxgi import DxgiCapture
from nightvision.backends.capture.mss_capture import MssCapture
from nightvision.backends.capture.portal import PortalPipeWireCapture
from nightvision.backends.capture.synthetic import SyntheticCapture
from nightvision.platform_detect import PlatformInfo


def info(session, os_="linux"):
    return PlatformInfo(os_, session, "gnome", False, session in ("x11", "xwayland-only"))


class Fake(SyntheticCapture):
    def __init__(self, name, ok, reason=""):
        super().__init__(8, 8)
        self.name, self._ok, self._reason = name, ok, reason

    def available(self):
        return self._ok

    def unavailable_reason(self):
        return self._reason


def test_synthetic_frames_deterministic():
    a = SyntheticCapture(320, 180, realtime=False)
    b = SyntheticCapture(320, 180, realtime=False)
    with a, b:
        fa = [a.grab() for _ in range(3)]
        fb = [b.grab() for _ in range(3)]
    assert fa[0].shape == (180, 320, 4) and fa[0].dtype == np.uint8
    assert a.size == (320, 180)
    for x, y in zip(fa, fb):
        assert np.array_equal(x, y)
    assert not np.array_equal(fa[0], fa[2])  # figure moves
    assert fa[0][..., :3].max() < 60  # dark scene


def test_synthetic_realtime_returns_none_between_frames():
    with SyntheticCapture(64, 36, fps=5) as s:
        assert s.grab() is not None
        assert s.grab() is None


def test_grab_before_open_and_close_idempotent():
    s = SyntheticCapture(16, 16)
    with pytest.raises(CaptureError):
        s.grab()
    s.close(); s.close()


def test_selection_order(monkeypatch):
    import nightvision.backends.capture.select as sel
    made = []

    def fake_make(name, i):
        made.append(name)
        return Fake(name, False, "nope")
    monkeypatch.setattr(sel, "_make", fake_make)
    for session, os_, expect in [("win32", "windows", ["dxgi", "mss"]), ("x11", "linux", ["mss"]),
                                 ("wayland", "linux", ["portal", "mss"])]:
        made.clear()
        with pytest.raises(CaptureError) as ei:
            select_capture_backend(info(session, os_))
        assert made == expect
        assert "nope" in str(ei.value)


def test_selection_picks_first_available_and_prefer(monkeypatch):
    import nightvision.backends.capture.select as sel
    monkeypatch.setattr(sel, "_make", lambda n, i: Fake(n, n == "mss", "x"))
    src, reasons = select_capture_backend(info("wayland"))
    assert src.name == "mss" and any(r.startswith("portal:") for r in reasons)
    src, _ = select_capture_backend(info("win32", "windows"), prefer="mss")
    assert src.name == "mss"


def test_selection_synthetic_and_headless():
    src, _ = select_capture_backend(info("headless"), synthetic=True)
    assert src.name == "synthetic"
    with pytest.raises(CaptureError, match="no display session"):
        select_capture_backend(info("headless"))


def test_wayland_mss_flagged_xwayland():
    # portal is unavailable in this environment, so mss is the fallback and must be flagged
    m = MssCapture(wayland_session=True, env={"DISPLAY": ":0"})
    assert "XWayland" in m.caveat


def test_mss_unavailable_without_display():
    m = MssCapture(env={})
    assert not m.available() and "DISPLAY" in m.unavailable_reason()


def test_portal_and_dxgi_unavailable_here_with_reasons():
    p = PortalPipeWireCapture(env={})
    assert not p.available() and p.unavailable_reason()
    d = DxgiCapture()
    assert not d.available() and "Windows" in d.unavailable_reason()
    for c in (p, d):
        with pytest.raises(CaptureError):
            c.open()
        c.close()


@pytest.mark.gui
def test_mss_live_grab():
    with MssCapture(monitor=1) as m:
        f = m.grab()
        assert f.dtype == np.uint8 and f.ndim == 3 and f.shape[2] == 4
        assert (f.shape[1], f.shape[0]) == m.size
    m.close()
