# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import json
import os
import subprocess
import sys
import time

import pytest

from nightvision.__main__ import main
from nightvision.core.presets import PRESETS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args, env=None, timeout=60, **kw):
    e = dict(os.environ, PYTHONPATH=ROOT)
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "nightvision", *args], capture_output=True,
                          text=True, timeout=timeout, env=e, cwd=ROOT, **kw)


def test_list_presets(capsys):
    assert main(["--list-presets"]) == 0
    assert capsys.readouterr().out.split() == list(PRESETS)


def test_list_backends_dry_run(capsys, tmp_path):
    assert main(["--list-backends", "--dry-run", "--config", str(tmp_path / "p.json")]) == 0
    out = capsys.readouterr().out
    for key in ("platform:", "tone: null", "capture:", "process:", "present:", "hotkeys:"):
        assert key in out


def test_usage_errors_have_meaningful_codes(capsys, tmp_path):
    assert main(["--mode", "bogus"]) == 2
    assert main(["--list-backends", "--dry-run", "--preset", "nope",
                 "--config", str(tmp_path / "p.json")]) == 2
    assert "unknown preset" in capsys.readouterr().err


def test_ctl_without_instance(tmp_path):
    r = run(["ctl", "status"], env=_ctl_env(tmp_path))
    assert r.returncode == 4
    assert "no running" in r.stderr


def test_ctl_needs_name(tmp_path):
    r = run(["ctl", "preset"], env=_ctl_env(tmp_path))
    assert r.returncode == 2


def _ctl_env(rt):
    # WHY: the control channel lives under XDG_RUNTIME_DIR on POSIX but under
    # %LOCALAPPDATA% on Windows (TCP + token file); redirect whichever applies so
    # tests never touch, or collide with, a real running instance.
    return {"XDG_RUNTIME_DIR": str(rt), "LOCALAPPDATA": str(rt)}


def test_headless_ctl_roundtrip_and_quit(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir(mode=0o700)
    env = dict(os.environ, PYTHONPATH=ROOT, **_ctl_env(rt))
    proc = subprocess.Popen(
        [sys.executable, "-m", "nightvision", "--headless", "--dry-run",
         "--config", str(tmp_path / "p.json")],
        env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().startswith("nightvision running")
        r = run(["ctl", "status"], env=_ctl_env(rt))
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["mode"] == "off"
        assert run(["ctl", "preset", "balanced"], env=_ctl_env(rt)).returncode == 0
        assert run(["ctl", "toggle"], env=_ctl_env(rt)).returncode == 0
        deadline = time.time() + 5
        st = {}
        while time.time() < deadline:
            st = json.loads(run(["ctl", "status"], env=_ctl_env(rt)).stdout)
            if st["mode"] == "display":
                break
            time.sleep(0.1)
        assert st["mode"] == "display" and st["preset"] == "balanced"
        bad = run(["ctl", "preset", "bogus"], env=_ctl_env(rt))
        assert bad.returncode == 1
        # second headless instance must refuse instead of fighting over the socket
        dup = run(["--headless", "--dry-run", "--config", str(tmp_path / "p2.json")],
                  env=_ctl_env(rt))
        assert dup.returncode == 5
        assert run(["ctl", "quit"], env=_ctl_env(rt)).returncode == 0
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Popen.terminate() is TerminateProcess on Windows, not a catchable SIGTERM")
def test_headless_sigterm_exits_cleanly(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir(mode=0o700)
    env = dict(os.environ, PYTHONPATH=ROOT, **_ctl_env(rt))
    proc = subprocess.Popen(
        [sys.executable, "-m", "nightvision", "--headless", "--dry-run",
         "--config", str(tmp_path / "p.json")],
        env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().startswith("nightvision running")
        proc.terminate()
        assert proc.wait(timeout=5) == 0
        assert not (rt / "nightvision.sock").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


@pytest.mark.gui
def test_demo_smoke():
    pytest.importorskip("glfw")
    pytest.importorskip("moderngl")
    if not os.environ.get("DISPLAY"):
        pytest.skip("needs a display")
    r = run(["--demo", "--max-seconds", "2"], timeout=30)
    assert r.returncode == 0, r.stderr
