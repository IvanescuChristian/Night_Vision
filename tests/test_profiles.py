# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import json
import os

import pytest

from nightvision import profiles as pr
from nightvision.core.params import EnhanceParams
from nightvision.core.presets import PRESETS
from nightvision.profiles import Profile, ProfileStore


def test_defaults_when_missing(tmp_path):
    s = ProfileStore(tmp_path / "p.json").load()
    for n in PRESETS:
        assert s.get(n).params == PRESETS[n]
    assert s.get("Escape from Tarkov").exe_names == ["EscapeFromTarkov.exe"]
    assert s.get("Escape from Tarkov").params == PRESETS["tarkov_dark"]
    assert s.get("Rust").params == PRESETS["rust_night"]
    assert s.active == "off"
    assert not (tmp_path / "p.json").exists()


def test_roundtrip(tmp_path):
    path = tmp_path / "sub" / "p.json"
    s = ProfileStore(path).load()
    s.upsert(Profile("Mine", EnhanceParams(gamma=1.7), ["a.exe"], "ctrl+alt+n"))
    s.active = "Mine"
    s.save()
    t = ProfileStore(path).load()
    assert t.active == "Mine"
    assert t.get("Mine") == s.get("Mine")
    assert t.get("Mine").hotkey == "ctrl+alt+n"
    assert t.delete("Mine") and t.get("Mine") is None and t.active == "off"
    assert not t.delete("nope")


def test_atomic_save_keeps_old_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    s = ProfileStore(path).load()
    s.save()
    before = path.read_text()
    s.active = "balanced"

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(pr.os, "replace", boom)
    with pytest.raises(OSError):
        s.save()
    assert path.read_text() == before
    assert os.listdir(tmp_path) == ["p.json"]


@pytest.mark.parametrize("content", ["{not json", "[]", '{"profiles": 5}', '{"profiles":[{"x":1}]}'])
def test_corrupt_recovery(tmp_path, caplog, content):
    path = tmp_path / "p.json"
    path.write_text(content)
    with caplog.at_level("WARNING"):
        s = ProfileStore(path).load()
    assert (tmp_path / "p.json.corrupt").read_text() == content
    assert s.get("Rust") is not None and s.active == "off"
    assert any("backed up" in r.message for r in caplog.records)


def test_match_exe_case_insensitive(tmp_path):
    s = ProfileStore(tmp_path / "p.json").load()
    assert s.match_exe("escapefromtarkov.EXE").name == "Escape from Tarkov"
    assert s.match_exe("RUSTCLIENT.exe").name == "Rust"
    assert s.match_exe("notepad.exe") is None


def test_default_config_path(monkeypatch, tmp_path):
    # os.name cannot be faked: pathlib itself keys off it.
    if os.name == "nt":
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert pr.default_config_path() == tmp_path / "NightVision" / "profiles.json"
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert pr.default_config_path() == tmp_path / "nightvision" / "profiles.json"
