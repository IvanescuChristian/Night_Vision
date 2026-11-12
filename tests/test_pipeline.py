# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import threading
import time

import pytest

from nightvision.backends.capture.synthetic import SyntheticCapture
from nightvision.core.enhance import FrameEnhancer
from nightvision.core.params import EnhanceParams
from nightvision.pipeline import EnhancePipeline
from nightvision.sinks import LatestFrameSink, NullSink


def make(src_kw=None, enh=None, sink=None, fps=60, params=None):
    kw = dict(width=160, height=90, fps=120)
    kw.update(src_kw or {})
    return EnhancePipeline(lambda: SyntheticCapture(**kw), enh or (lambda: FrameEnhancer()),
                           sink or NullSink, params or EnhanceParams(gamma=1.8), target_fps=fps)


def wait_for(cond, t=3.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_frames_flow_and_names():
    p = make()
    p.start()
    assert wait_for(lambda: p.stats()["frames_processed"] >= 5)
    assert {t.name for t in p.threads} == {"nv-capture", "nv-render"}
    assert p.stop() is True
    s = p.stats()
    assert s["source"] == "synthetic" and s["enhancer"] == "FrameEnhancer" and s["sink"] == "NullSink"
    assert s["frames_captured"] >= s["frames_processed"]


def test_latest_wins_drops_counted():
    # source much faster than render target
    p = make(src_kw=dict(fps=500), fps=20)
    p.start()
    assert wait_for(lambda: p.stats()["frames_processed"] >= 6, 5)
    p.stop()
    s = p.stats()
    assert s["frames_dropped"] > 0
    assert s["frames_captured"] - s["frames_dropped"] - s["frames_processed"] in (0, 1)


def test_set_params_mid_run():
    sink = LatestFrameSink()
    p = make(sink=lambda: sink, params=EnhanceParams())
    p.start()
    assert p.wait_first_frame()
    p.set_params(EnhanceParams(gamma=2.5))
    assert wait_for(lambda: p._enhancer is not None and p._enhancer.params.gamma == 2.5)
    p.stop()


def test_stop_within_2s_idempotent_and_restart():
    p = make()
    p.start()
    assert p.wait_first_frame()
    t = time.monotonic()
    assert p.stop(2.0)
    assert time.monotonic() - t < 2.0
    assert p.stop()  # idempotent
    assert not p.running
    p.start()
    assert wait_for(lambda: p.stats()["frames_processed"] >= 3)
    assert p.stop()


class BoomEnhancer:
    name = "boom"

    def __init__(self):
        self.closed_on = None
        self.n = 0

    def set_params(self, p): pass

    def process(self, f):
        self.n += 1
        if self.n == 3:
            raise RuntimeError("kaboom")
        return f

    def close(self):
        self.closed_on = threading.get_ident()


def test_enhancer_exception_surfaces_and_threads_exit():
    holder = {}

    class Src(SyntheticCapture):
        def _close(self):
            self.closed_on = threading.get_ident()
            super()._close()

    def sf():
        holder["src"] = Src(64, 36, fps=200)
        return holder["src"]

    def ef():
        holder["enh"] = BoomEnhancer()
        return holder["enh"]

    p = EnhancePipeline(sf, ef, NullSink, EnhanceParams(), 60)
    p.start()
    assert wait_for(lambda: not p.running)
    assert isinstance(p.last_error, RuntimeError)
    assert all(not t.is_alive() for t in p.threads)
    # closed on the thread that created them
    assert holder["enh"].closed_on == p.thread_idents["render"]
    assert holder["src"].closed_on == p.thread_idents["capture"]


def test_factory_failure_is_captured():
    def bad():
        raise ValueError("no gpu")
    p = EnhancePipeline(lambda: SyntheticCapture(32, 32), bad, NullSink)
    p.start()
    assert wait_for(lambda: not p.running)
    assert isinstance(p.last_error, ValueError)


def test_sink_created_opened_closed_on_render_thread():
    sinks = []

    def sf():
        s = NullSink(); sinks.append(s); return s
    p = make(sink=sf)
    p.start()
    assert p.wait_first_frame()
    p.stop()
    r = p.thread_idents["render"]
    assert sinks[0].opened_on == r and sinks[0].closed_on == r


def test_latest_frame_sink_single_slot():
    s = LatestFrameSink()
    import numpy as np
    assert s.take() is None
    a, b = np.zeros(1), np.ones(1)
    s.present(a); s.present(b)
    assert s.take() is b and s.take() is None


class _TexSink(NullSink):
    """Sink with a zero-copy entry point; records which one was used."""

    def __init__(self):
        super().__init__()
        self.events = []
        self.textures = 0

    def open(self):
        self.events.append("sink.open")
        super().open()

    def close(self):
        self.events.append("sink.close")
        super().close()

    def present_texture(self, tex):
        self.textures += 1


class _TexEnhancer:
    name = "tex"
    zero_copy = True

    def __init__(self, events):
        self.events = events
        events.append("enh.make")

    def set_params(self, p): pass

    def process(self, f):
        return f

    def process_to_texture(self, f):
        return object()

    def close(self):
        self.events.append("enh.close")


def test_sink_opens_first_and_factory_receives_it_zero_copy():
    sink = _TexSink()
    seen = {}

    def ef(sink):
        seen["sink"] = sink
        return _TexEnhancer(sink.events)

    p = make(enh=ef, sink=lambda: sink)
    p.start()
    assert wait_for(lambda: sink.textures >= 3)
    assert p.stop()
    assert seen["sink"] is sink
    assert sink.events[:2] == ["sink.open", "enh.make"]
    # enhancer closes before the sink: a shared GL context dies with the sink
    assert sink.events[-2:] == ["enh.close", "sink.close"]
    assert sink.frames == 0  # never used the copy path


def test_copy_path_when_zero_copy_turns_off():
    sink = _TexSink()
    enh = {}

    def ef(sink):
        enh["e"] = _TexEnhancer(sink.events)
        return enh["e"]

    p = make(enh=ef, sink=lambda: sink)
    p.start()
    assert wait_for(lambda: sink.textures >= 2)
    enh["e"].zero_copy = False  # what ManagedEnhancer does after a GPU drop
    assert wait_for(lambda: sink.frames >= 2)
    assert p.stop()


def test_explicit_needs_sink_flag_with_opaque_factory():
    got = []

    class F:
        def __call__(self, **kw):
            got.append(kw)
            return FrameEnhancer()
    p = EnhancePipeline(lambda: SyntheticCapture(64, 36, fps=120), F(), NullSink,
                        EnhanceParams(), 60, enhancer_needs_sink=True)
    p.start()
    assert p.wait_first_frame()
    assert p.stop()
    assert isinstance(got[0]["sink"], NullSink)


def test_stop_on_owner_thread_services_dispatcher_without_deadlock():
    from nightvision.mainthread import MainThreadDispatcher
    d = MainThreadDispatcher()
    seen = {}

    class NeedsMainSink(NullSink):
        def close(self):
            # like GlOverlaySink.close(): destroy the window on the main thread
            seen["thread"] = d.call(threading.current_thread, timeout=5)
            super().close()

    p = EnhancePipeline(lambda: SyntheticCapture(64, 36, fps=200), FrameEnhancer,
                        NeedsMainSink, EnhanceParams(), 60, dispatcher=d)
    p.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not p.wait_first_frame(0.01):
        d.run_pending()
    t = time.monotonic()
    assert p.stop(2.0)
    assert time.monotonic() - t < 2.0
    assert seen["thread"] is threading.current_thread()
