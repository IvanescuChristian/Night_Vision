# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Gamma crash recovery with fake hardware only: no real display ramp is touched."""
import json

import numpy as np
import pytest

from nightvision.app import NightVisionApp
from nightvision.backends.tone import base, x11
from nightvision.backends.tone.base import ToneBackend
from nightvision.platform_detect import PlatformInfo
from tests.test_tone_backends import FakeX11, FakeXrr, ramp

INFO = PlatformInfo("linux", "x11", "", False, True)


class FakeHw:
    """Stands in for the display hardware; survives across backend instances."""

    def __init__(self):
        self.ramp = ramp((1.0, 1.0, 1.0))


class FakeTone(ToneBackend):
    name = "fake"
    persist_backup = True

    def __init__(self, hw):
        super().__init__()
        self.hw = hw

    def available(self):
        return True

    def _read_ramp(self):
        return self.hw.ramp.copy()

    def _write_ramp(self, r):
        self.hw.ramp = np.array(r, copy=True)


@pytest.fixture
def bak(tmp_path, monkeypatch):
    p = tmp_path / "gamma_backup.json"
    monkeypatch.setenv(base.BACKUP_ENV, str(p))
    return p


def test_default_path_is_in_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(base.BACKUP_ENV, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    if base.os.name != "nt":
        assert base.backup_path() == tmp_path / "nightvision" / "gamma_backup.json"


def test_backup_written_before_first_apply_and_deleted_on_restore(bak):
    hw = FakeHw()
    original = hw.ramp.copy()
    seen = {}

    class Spy(FakeTone):
        def _write_ramp(self, r):
            seen.setdefault("file_existed_at_first_write", bak.exists())
            super()._write_ramp(r)

    b = Spy(hw)
    assert not bak.exists()
    b.apply(ramp((0.6,) * 3))
    assert seen["file_existed_at_first_write"]
    data = json.loads(bak.read_text())
    assert data["backend"] == "fake"
    assert np.array_equal(np.array(data["outputs"]["0"], dtype=np.uint16), original)
    b.apply(ramp((0.5,) * 3))  # later applies must not replace the original
    assert np.array_equal(np.array(json.loads(bak.read_text())["outputs"]["0"]), original)
    b.restore()
    assert not bak.exists()
    assert np.array_equal(hw.ramp, original)
    b.apply(ramp((0.7,) * 3))  # a new cycle writes it again
    assert bak.exists()
    b.close()
    assert not bak.exists()


def test_failed_restore_keeps_backup(bak):
    hw = FakeHw()

    class Broken(FakeTone):
        def _restore_original(self):
            raise OSError("display gone")

    b = Broken(hw)
    b.apply(ramp((0.6,) * 3))
    with pytest.raises(OSError):
        b.restore()
    assert bak.exists()
    base._live.discard(b)


def test_non_persisting_backend_writes_nothing(bak):
    class Plain(FakeTone):
        persist_backup = False

    Plain(FakeHw()).apply(ramp((0.6,) * 3))
    assert not bak.exists()


def test_recovered_after_simulated_crash(bak):
    hw = FakeHw()
    original = hw.ramp.copy()
    crashed = FakeTone(hw)
    crashed.apply(ramp((0.5,) * 3))
    base._live.discard(crashed)  # the process "dies": no restore, file stays
    assert bak.exists() and not np.array_equal(hw.ramp, original)

    fresh = FakeTone(hw)
    assert fresh.recover() is True
    assert np.array_equal(hw.ramp, original)
    assert not bak.exists()
    assert np.array_equal(fresh._original, original)  # not the stale modified ramp
    fresh.apply(ramp((0.6,) * 3))
    fresh.restore()
    assert np.array_equal(hw.ramp, original)
    assert fresh.recover() is False  # nothing left


def test_app_start_recovers(bak, tmp_path):
    hw = FakeHw()
    original = hw.ramp.copy()
    crashed = FakeTone(hw)
    crashed.apply(ramp((0.5,) * 3))
    base._live.discard(crashed)
    app = NightVisionApp(tmp_path / "p.json", info=INFO, tone_backend=FakeTone(hw),
                         enable_hotkeys=False, enable_control=False)
    try:
        app.start_services()
        assert np.array_equal(hw.ramp, original) and not bak.exists()
    finally:
        app.shutdown()


@pytest.mark.parametrize("content", [
    "not json",
    json.dumps({"version": 1, "backend": "fake", "outputs": {"0": [[0] * 256] * 3}}),  # black
    json.dumps({"version": 1, "backend": "fake", "outputs": {"0": [[70000, 1]] * 3}}),
    json.dumps({"version": 1, "backend": "fake", "outputs": {}}),
    json.dumps({"version": 9, "backend": "fake", "outputs": {}}),
    json.dumps({"backend": "fake"}),
])
def test_invalid_backup_is_ignored_and_removed(bak, content):
    hw = FakeHw()
    before = hw.ramp.copy()
    bak.write_text(content)
    b = FakeTone(hw)
    assert b.recover() is False
    assert not bak.exists()
    assert np.array_equal(hw.ramp, before)  # nothing applied


def test_backup_of_other_backend_is_left_alone(bak):
    bak.write_text(json.dumps({"version": 1, "backend": "xrandr",
                               "outputs": {"0": ramp().tolist()}}))
    hw = FakeHw()
    assert FakeTone(hw).recover() is False
    assert bak.exists()


def test_xrandr_recovery_sets_original_to_recovered_ramp(bak, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":9")
    xrr = FakeXrr({11: 256, 12: 1024})
    monkeypatch.setattr(x11, "load_xlib", lambda: (FakeX11(), xrr))
    for c, n in xrr.sizes.items():  # realistic (safe) identity ramps
        xrr.gamma[c] = [list(ramp(n=n)[k].astype(int)) for k in range(3)]
    pristine = {c: [list(r) for r in v] for c, v in xrr.gamma.items()}

    crashed = x11.XRandRGammaBackend()
    crashed.apply(ramp((0.6,) * 3))
    base._live.discard(crashed)
    data = json.loads(bak.read_text())
    assert set(data["outputs"]) == {"11", "12"}

    xrr.sets.clear()
    fresh = x11.XRandRGammaBackend()
    assert fresh.recover() is True
    assert not bak.exists()
    assert {c: d for c, _, d in xrr.sets} == pristine
    assert set(fresh._originals) == {11, 12}
    for c in (11, 12):
        assert fresh._originals[c].tolist() == pristine[c]
    xrr.sets.clear()
    fresh.apply(ramp((0.5,) * 3))
    xrr.sets.clear()
    fresh.restore()
    assert {c: d for c, _, d in xrr.sets} == pristine
    assert not bak.exists()
