# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import os

import pytest


def pytest_collection_modifyitems(config, items):
    # WHY: gui tests open real windows/capture devices; on headless CI or a
    # bare SSH session there is no display, so they would fail rather than
    # skip. Skipping them keeps the default run green without a virtual display.
    if os.environ.get("DISPLAY"):
        return
    skip_gui = pytest.mark.skip(reason="no DISPLAY set; run under a virtual display")
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip_gui)


@pytest.fixture(autouse=True)
def _isolated_user_state(monkeypatch, tmp_path):
    # WHY: tests must never read or write the real per-user config dir (gamma
    # crash-recovery backup, profiles.json). The explicit env override covers
    # the backup; the XDG/APPDATA base covers anything resolved through
    # profiles.default_config_dir(). Tests that probe those lookups set their
    # own values afterwards, which wins over this default.
    monkeypatch.setenv("NIGHTVISION_GAMMA_BACKUP", str(tmp_path / "gamma_backup.json"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
