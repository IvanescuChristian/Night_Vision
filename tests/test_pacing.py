# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import sys
import threading
import time

import numpy as np

from nightvision import runtime
from nightvision.backends.capture.synthetic import SyntheticCapture
from nightvision.core.enhance import FrameEnhancer
from nightvision.core.params import EnhanceParams
from nightvision.pipeline import EnhancePipeline, _wait_until
from nightvision.sinks import NullSink


WIN = sys.platform == "win32"


class RecSink(NullSink):
    def __init__(self):
        super().__init__()
        self.t = []

    def present(self, frame):
        self.t.append(time.perf_counter())
        super().present(frame)


def test_steady_pacing_30fps():
    sinks = []

    def sf():
        s = RecSink()
        sinks.append(s)
        return s

    p = EnhancePipeline(lambda: SyntheticCapture(width=640, height=360, fps=120),
                        lambda: FrameEnhancer(), sf, EnhanceParams(gamma=1.8), target_fps=30)
    p.start()
    time.sleep(3.0)
    st = p.stats()
    assert p.stop() is True
    iv = np.diff(np.array(sinks[0].t))
    period = 1 / 30
    # measured ~33.3 ms mean; the bounds leave room for a loaded CI machine
    assert abs(st["fps"] - 30) / 30 < 0.05
    # Windows shared runners wake late by a timer tick or more, so the tail gets more room
    assert np.percentile(iv, 95) < (2.5 if WIN else 1.5) * period
    assert iv.mean() * 1000 < 1.05 * period * 1000


def test_wait_until_accuracy_and_stop():
    stop = threading.Event()
    overs = []
    for _ in range(5):
        deadline = time.perf_counter() + 0.05
        _wait_until(stop, deadline)
        overs.append(time.perf_counter() - deadline)
    # Never early (deterministic). The overshoot bound is on the median because one
    # scheduler hiccup on a shared runner (seen: 10.7 ms on Windows CI) says nothing
    # about the algorithm; Windows timers are coarser (~15.6 ms tick), so a looser bound.
    assert min(overs) >= 0
    assert sorted(overs)[len(overs) // 2] < (0.03 if WIN else 0.01)
    # returns promptly when stopped
    threading.Timer(0.02, stop.set).start()
    t = time.perf_counter()
    _wait_until(stop, t + 5.0)
    assert time.perf_counter() - t < 0.5


class FakeWinmm:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def timeBeginPeriod(self, ms):
        self.calls.append(("begin", ms))
        return self.rc

    def timeEndPeriod(self, ms):
        self.calls.append(("end", ms))
        return 0


def test_timer_resolution_paired(monkeypatch):
    fake = FakeWinmm()
    monkeypatch.setattr(runtime, "_load_winmm", lambda: fake)
    with runtime.timer_resolution(1) as active:
        assert active and fake.calls == [("begin", 1)]
    assert fake.calls == [("begin", 1), ("end", 1)]


def test_timer_resolution_exception_safe_and_failed_begin(monkeypatch):
    fake = FakeWinmm()
    monkeypatch.setattr(runtime, "_load_winmm", lambda: fake)
    try:
        with runtime.timer_resolution(1):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert fake.calls == [("begin", 1), ("end", 1)]
    bad = FakeWinmm(rc=97)  # TIMERR_NOCANDO: nothing to undo
    monkeypatch.setattr(runtime, "_load_winmm", lambda: bad)
    with runtime.timer_resolution(1) as active:
        assert not active
    assert bad.calls == [("begin", 1)]


def test_timer_resolution_noop_off_windows():
    assert runtime._load_winmm() is None or __import__("sys").platform == "win32"
    with runtime.timer_resolution(1) as active:
        assert active is False or __import__("sys").platform == "win32"


def test_pipeline_pairs_timer_resolution(monkeypatch):
    fake = FakeWinmm()
    monkeypatch.setattr(runtime, "_load_winmm", lambda: fake)
    p = EnhancePipeline(lambda: SyntheticCapture(width=160, height=90, fps=120),
                        lambda: FrameEnhancer(), NullSink, EnhanceParams(gamma=1.8), target_fps=60)
    p.start()
    assert p.wait_first_frame()
    assert fake.calls == [("begin", 1)]
    assert p.stop() is True
    assert fake.calls == [("begin", 1), ("end", 1)]
