# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Headless and demo runs must stop cleanly (exit 0, reason reported) on SIGINT/SIGTERM."""
import os
import signal
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX signals")

ARGS = {
    "headless": ["--headless", "--dry-run"],
    "demo_preview": ["--demo", "--headless", "--dry-run", "--mode", "preview"],
}


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("kind", sorted(ARGS))
def test_stops_cleanly_on_signal(tmp_path, kind, sig):
    rt = tmp_path / "run"
    rt.mkdir(mode=0o700)
    env = dict(os.environ, PYTHONPATH=ROOT, XDG_RUNTIME_DIR=str(rt),
               NIGHTVISION_GAMMA_BACKUP=str(tmp_path / "gamma_backup.json"))
    env.pop("WAYLAND_DISPLAY", None)
    if kind == "demo_preview" and not env.get("DISPLAY"):
        pytest.skip("preview needs a display")
    proc = subprocess.Popen(
        [sys.executable, "-m", "nightvision", *ARGS[kind], "--config", str(tmp_path / "p.json")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT)
    try:
        # Signal only once the loop is running: before that the default handler would win.
        out = ""
        if kind == "headless":
            out = proc.stdout.readline()
            assert "nightvision running" in out
        else:
            time.sleep(4)
            if proc.poll() is not None:
                pytest.skip(f"preview unavailable here: {proc.stderr.read()}")
        proc.send_signal(sig)
        try:
            _, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail(f"did not exit after {sig.name}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0, err
    assert f"stopped by {sig.name}" in err
