# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""The GUI must shut down cleanly (display restored, exit 0) on SIGINT/SIGTERM."""
import os
import signal
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = [pytest.mark.gui,
              pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX signals")]


def _env(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir(mode=0o700)
    e = dict(os.environ, PYTHONPATH=ROOT, XDG_RUNTIME_DIR=str(rt),
             NIGHTVISION_GAMMA_BACKUP=str(tmp_path / "gamma_backup.json"))
    e.pop("WAYLAND_DISPLAY", None)
    return e


def _wait_until_up(env, proc, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            pytest.fail(f"GUI exited early: rc={proc.returncode}\n{proc.stderr.read()}")
        r = subprocess.run([sys.executable, "-m", "nightvision", "ctl", "status"],
                           capture_output=True, text=True, env=env, cwd=ROOT, timeout=15)
        if r.returncode == 0:
            return
        time.sleep(0.2)
    pytest.fail("GUI did not come up")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_gui_exits_cleanly_on_signal(tmp_path, sig):
    env = _env(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "nightvision", "--dry-run", "--config", str(tmp_path / "p.json")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT)
    try:
        _wait_until_up(env, proc)
        proc.send_signal(sig)
        try:
            _, err = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            pytest.fail(f"GUI did not exit after {sig.name}")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0, err
    assert f"stopped by {sig.name}; display restored" in err
    assert "Traceback" not in err
    assert (tmp_path / "p.json").exists()  # shutdown ran to its last step (profiles saved)
