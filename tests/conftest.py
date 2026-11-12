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
