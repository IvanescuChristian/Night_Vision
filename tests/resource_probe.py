# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Helper for resource/thread-lifetime measurements (psutil based)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import psutil

from nightvision.core.params import EnhanceParams
from nightvision.pipeline import EnhancePipeline
from nightvision.sinks import NullSink

_PROC = psutil.Process()


# Hardware GL config: the shipped path is a GLFW window created on the main
# thread (via the dispatcher) with the GPU enhancer sharing its context. SINK
# and DISP are switched on by factories() for that config; the main thread
# then pumps the dispatcher while it waits.
SINK = NullSink
DISP = None


def _sleep(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while True:
        if DISP is not None:
            DISP.run_pending()
            from nightvision.backends.present import overlay_gl
            overlay_gl.poll_events()
        left = end - time.perf_counter()
        if left <= 0:
            return
        time.sleep(min(0.01, left) if DISP is not None else left)


def _wait_first(p, timeout: float) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if p.wait_first_frame(0.0 if DISP is not None else min(0.05, end - time.perf_counter())):
            return True
        if DISP is not None:
            _sleep(0.01)
    return False


def _make_pipeline(source_factory, enhancer_factory, params, fps):
    return EnhancePipeline(source_factory, enhancer_factory, SINK, params, fps,
                           dispatcher=DISP)


def rss_mb() -> float:
    return _PROC.memory_info().rss / 1e6


def nthreads() -> int:
    return _PROC.num_threads()


def nv_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("nv-")]


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def theil_sen(xs, ys) -> float:
    """Median pairwise slope. WHY: GPU/allocator RSS sawtooths and one load
    spike moves an end-minus-start or least-squares fit; the median slope does not."""
    sl = [(ys[j] - ys[i]) / (xs[j] - xs[i])
          for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] != xs[i]]
    return median(sl) if sl else 0.0


def _warm(source_factory, enhancer_factory) -> None:
    # WHY: OpenCV/GL drivers lazily start worker pools on first use; those are
    # one-time process threads, not per-pipeline leaks, so they belong in the baseline.
    p = _make_pipeline(source_factory, enhancer_factory, EnhanceParams(gamma=1.8, local_contrast=0.3), 30)
    p.start(); _wait_first(p, 10.0); p.stop()
    _sleep(0.3)


def run_config(source_factory, enhancer_factory, seconds=24.0, warmup=4.0, fps=30.0,
               sample_s=0.5) -> dict:
    _warm(source_factory, enhancer_factory)
    base_threads = nthreads()
    base_py = threading.active_count()
    p = _make_pipeline(source_factory, enhancer_factory, EnhanceParams(gamma=1.8, local_contrast=0.3), fps)
    p.start()
    _sleep(warmup)
    during_threads = nthreads()
    rss_warm = rss_mb()
    _PROC.cpu_percent(None)
    t0 = time.perf_counter()
    s0 = p.stats()["frames_processed"]
    ts_s, rss_s = [], []
    end = t0 + (seconds - warmup)
    while time.perf_counter() < end:
        _sleep(sample_s)
        ts_s.append(time.perf_counter() - t0)
        rss_s.append(rss_mb())
    cpu = _PROC.cpu_percent(None)
    n = p.stats()["frames_processed"] - s0
    achieved = n / (time.perf_counter() - t0)
    rss_end = rss_mb()
    # Median of first/last quarter of samples, so a single spike cannot decide.
    q = max(1, len(rss_s) // 4)
    growth_med = median(rss_s[-q:]) - median(rss_s[:q]) if rss_s else rss_end - rss_warm
    # Slope over 2 s window medians: caches fill in steps and get released, so
    # raw per-sample slopes swing wildly on an 8 s run; medians damp that.
    k = max(1, int(2.0 / sample_s))
    wt = [median(ts_s[i:i + k]) for i in range(0, len(rss_s) - k + 1, k)]
    wr = [median(rss_s[i:i + k]) for i in range(0, len(rss_s) - k + 1, k)]
    slope_min = theil_sen(wt, wr) * 60.0 if len(wr) >= 3 else 0.0
    peak_growth = max(rss_s or [rss_end]) - rss_warm
    stats = p.stats()
    ts = p.threads
    t1 = time.perf_counter()
    exited = p.stop(2.0)
    join_s = time.perf_counter() - t1
    _sleep(0.2)
    return {
        "error": repr(p.last_error) if p.last_error else None,
        "backends": f"{stats['source']}+{stats['enhancer']}",
        "threads_base": base_threads, "threads_during": during_threads,
        "threads_after": nthreads(), "py_threads_base": base_py,
        "py_threads_after": threading.active_count(),
        "nv_threads_after": nv_threads(),
        "pipeline_threads_alive": [t.name for t in ts if t.is_alive()],
        "rss_warm_mb": round(rss_warm, 1), "rss_end_mb": round(rss_end, 1),
        "rss_growth_mb": round(growth_med, 1),
        "rss_growth_endstart_mb": round(rss_end - rss_warm, 1),
        "rss_slope_mb_per_min": round(slope_min, 2), "rss_peak_growth_mb": round(peak_growth, 1), "rss_samples_mb": [round(x, 1) for x in rss_s], "rss_peak_mb": round(max(rss_s or [rss_end]), 1),
        "cpu_percent": round(cpu, 1), "fps": round(achieved, 1),
        "avg_process_ms": round(stats["avg_process_ms"], 2),
        "dropped": stats["frames_dropped"], "processed": stats["frames_processed"],
        "stop_join_s": round(join_s, 3), "exited": exited,
    }


def churn(source_factory, enhancer_factory, cycles=20) -> dict:
    p = _make_pipeline(source_factory, enhancer_factory, EnhanceParams(gamma=1.5), 30)
    # WHY: with GPU drivers RSS sawtooths by tens of MB as caches fill and are
    # released, so a single end sample is noise. Compare the mean of the last 5
    # cycles with the mean of the last 5 warm-up cycles instead.
    warm = []
    for _ in range(30):
        p.start(); _wait_first(p, 10.0); p.stop()
        warm.append(rss_mb())
    _sleep(0.3)
    base_threads, base_py = nthreads(), threading.active_count()
    rss0 = median(warm[-5:])
    alive, series = [], []
    for _ in range(cycles):
        p.start()
        _wait_first(p, 5.0)
        ts = p.threads
        assert p.stop(2.0)
        alive += [t.name for t in ts if t.is_alive()]
        series.append(rss_mb())
    _sleep(0.2)
    growth = median(series[-5:]) - rss0
    slope = theil_sen(list(range(len(series))), series)
    return {"cycles": cycles, "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX"), "rss_growth_mb": round(growth, 1), "rss_slope_mb_per_cycle": round(slope, 3),
            "rss_peak_growth_mb": round(max(series) - rss0, 1),
            "threads_base": base_threads, "threads_after": nthreads(),
            "py_threads_base": base_py, "py_threads_after": threading.active_count(),
            "alive_after_stop": alive, "nv_threads_after": nv_threads(),
            "error": repr(p.last_error) if p.last_error else None}


W, H = 1280, 720


def synth_factory():
    from nightvision.backends.capture.synthetic import SyntheticCapture
    return lambda: SyntheticCapture(W, H, fps=60)


def mss_factory():
    from nightvision.backends.capture.mss_capture import MssCapture
    return lambda: MssCapture(monitor=1)


def cpu_enhancer():
    from nightvision.core.enhance import FrameEnhancer
    return FrameEnhancer


def gpu_enhancer():
    """(factory, None) or (None, skip_reason). Probes on the calling thread."""
    try:
        from nightvision.processing import create_enhancer
        enh, name, reasons = create_enhancer(None, prefer="gpu")
    except Exception as e:  # noqa: BLE001
        return None, f"GPU path unavailable: {e}"
    enh.close()
    if name != "gpu":
        return None, f"GPU not selected (got {name}): {reasons}"
    return (lambda: create_enhancer(None, prefer="gpu")[0]), None


def _hw_factories():
    global SINK, DISP
    try:
        from nightvision.mainthread import MainThreadDispatcher
        from nightvision.backends.present.overlay_gl import GlOverlaySink
        from nightvision.processing import create_enhancer
    except Exception as e:  # noqa: BLE001
        return None, None, f"GPU path unavailable: {e}"
    DISP = MainThreadDispatcher()
    SINK = lambda: GlOverlaySink(monitor_rect=(0, 0, W, H), mode="window", dispatcher=DISP)  # noqa: E731

    def enhancer(sink):
        enh, name, reasons = create_enhancer(None, prefer="gpu", ctx=sink.ctx)
        if name != "gpu":
            raise RuntimeError(f"GPU not selected: {reasons}")
        return enh
    return synth_factory(), enhancer, None


def factories(name):
    if name == "synthetic+cpu":
        return synth_factory(), cpu_enhancer(), None
    if name == "mss+cpu":
        return mss_factory(), cpu_enhancer(), None
    if name == "synthetic+gpu-hw":
        if os.environ.get("NV_TEST_HW_GL") != "1":
            return None, None, "set NV_TEST_HW_GL=1 (and e.g. GALLIUM_DRIVER=d3d12) to measure hardware GL"
        return _hw_factories()
    if name == "synthetic+gpu":
        ef, why = gpu_enhancer()
        return synth_factory(), ef, why
    raise KeyError(name)


def _configure_like_app() -> None:
    # WHY: measure the shipped configuration (OpenCV thread cap, arena limit).
    # NV_PROBE_NO_ARENA_LIMIT keeps the informational "default malloc" churn
    # run honest, since configure_runtime() would otherwise cap arenas for it.
    from nightvision import runtime
    if os.environ.get("NV_PROBE_NO_ARENA_LIMIT"):
        import cv2
        cv2.setNumThreads(runtime.default_cpu_threads())
    else:
        runtime.configure_runtime()


if __name__ == "__main__":
    # Subprocess entry: `python -m tests.resource_probe <config> <steady|churn>`.
    # A fresh process keeps allocator state from earlier runs out of the numbers
    # and lets the caller set MALLOC_ARENA_MAX / GALLIUM_DRIVER for the child only.
    _configure_like_app()
    sf, ef, why = factories(sys.argv[1])
    if why:
        print("SKIP " + why)
        sys.exit(0)
    kind = sys.argv[2] if len(sys.argv) > 2 else "churn"
    res = run_config(sf, ef) if kind == "steady" else churn(sf, ef, cycles=20)
    print("RESULT " + json.dumps(res))
