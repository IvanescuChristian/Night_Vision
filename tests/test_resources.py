# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Resource and thread-lifetime measurements (slow). Writes
measurements/raw/resources.json (gitignored) and prints a compact table.

Every measurement runs in a fresh subprocess that applies
nightvision.runtime.configure_runtime() like the app, so CPU% reflects the
shipped configuration and allocator state never leaks between configs."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from . import resource_probe as rp  # noqa: F401  (import check; probe runs as a subprocess)

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parent.parent
RESULTS: dict = {}

# Leak bounds. A real per-frame leak at 720p is MBs per frame (hundreds of
# MB/min), so these catch leaks while tolerating allocator/driver sawtooth.
STEADY_SLOPE_MB_PER_MIN = 60.0  # observed noise: -8..+32 on a flat 8 MB sawtooth
STEADY_CEILING_MB = 40.0
# WHY 2.0: three runs on a quiet host gave churn slopes of 0.00-0.24 MB/cycle
# (growth -6..+5 MB), while one loaded-host run measured 1.58, so 1.0 was
# flaky. A real leak of one 720p RGBA texture set is several MB per cycle (a
# single 1280x720x4 texture is 3.7 MB; float targets are larger), which still
# exceeds 2.0 MB/cycle and would also blow the 40 MB ceiling within 20 cycles.
CHURN_SLOPE_MB_PER_CYCLE = 2.0
CHURN_CEILING_MB = 40.0


def _probe(name, kind, arena_max=None, raw_malloc=False):
    env = dict(os.environ)
    if not name.endswith("-hw"):
        # WHY: software configs must stay on the default software renderer even
        # when the hardware driver is selected for the hardware config.
        env.pop("GALLIUM_DRIVER", None)
    if arena_max:
        env["MALLOC_ARENA_MAX"] = str(arena_max)
    if raw_malloc:
        env["NV_PROBE_NO_ARENA_LIMIT"] = "1"
    out = subprocess.run([sys.executable, "-m", "tests.resource_probe", name, kind], cwd=ROOT,
                         env=env, capture_output=True, text=True, timeout=300)
    skip = [l for l in out.stdout.splitlines() if l.startswith("SKIP ")]
    if skip:
        pytest.skip(skip[0][5:])
    line = [l for l in out.stdout.splitlines() if l.startswith("RESULT ")]
    assert line, out.stdout[-500:] + out.stderr[-1500:]
    return json.loads(line[0][7:])


@pytest.fixture(scope="module", autouse=True)
def _write_summary():
    yield
    out = ROOT / "measurements" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    (out / "resources.json").write_text(json.dumps(RESULTS, indent=2))
    rows = [(k, v) for k, v in RESULTS.items() if "steady" in v]
    print("\n%-17s %5s %5s %6s %7s %8s %9s %6s %9s %9s %5s" % (
        "config", "fps", "ms", "cpu%", "rssdMB", "MB/min", "thr b/d/a", "join",
        "churnMB", "MB/cycle", "drop"))
    for k, v in rows:
        s, c = v["steady"], v.get("churn", {})
        print("%-17s %5.1f %5.1f %6.1f %7.1f %8.2f %3d/%d/%-3d %6.3f %9s %9s %5d" % (
            k, s["fps"], s["avg_process_ms"], s["cpu_percent"], s["rss_growth_mb"],
            s["rss_slope_mb_per_min"], s["threads_base"], s["threads_during"],
            s["threads_after"], s["stop_join_s"], c.get("rss_growth_mb", "-"),
            c.get("rss_slope_mb_per_cycle", "-"), s["dropped"]))


@pytest.mark.parametrize("name", ["synthetic+cpu", "mss+cpu", "synthetic+gpu", "synthetic+gpu-hw"])
def test_config(name):
    if name == "mss+cpu" and not os.environ.get("DISPLAY"):
        pytest.skip("no DISPLAY; run under xvfb-run")
    r = _probe(name, "steady")
    RESULTS[name] = {"steady": r}
    # Thread lifetime: strict, independent of host load.
    assert r["error"] is None, r
    assert r["exited"] and r["stop_join_s"] < 2.0
    assert r["threads_after"] == r["threads_base"], r
    assert r["nv_threads_after"] == [] and r["pipeline_threads_alive"] == []
    assert r["py_threads_after"] == r["py_threads_base"]
    assert r["fps"] > 5
    # Memory: slope (median of pairwise slopes) plus an absolute ceiling.
    assert r["rss_slope_mb_per_min"] < STEADY_SLOPE_MB_PER_MIN, r
    assert r["rss_peak_growth_mb"] < STEADY_CEILING_MB, r

    # Informational: default glibc arenas. Many-core hosts show allocator
    # fragmentation as RSS growth per thread cycle; not asserted.
    RESULTS[name]["churn_default_malloc"] = _probe(name, "churn", raw_malloc=True)
    c = _probe(name, "churn", arena_max=2)
    RESULTS[name]["churn"] = c
    assert c["error"] is None, c
    assert c["alive_after_stop"] == [] and c["nv_threads_after"] == []
    assert c["threads_after"] == c["threads_base"], c
    assert c["py_threads_after"] == c["py_threads_base"], c
    assert c["rss_slope_mb_per_cycle"] < CHURN_SLOPE_MB_PER_CYCLE, c
    assert c["rss_growth_mb"] < CHURN_CEILING_MB, c
