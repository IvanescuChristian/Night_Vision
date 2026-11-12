# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Capture -> enhance -> present pipeline on two threads.

Threads are daemon=True with explicit bounded joins: a native call that never
returns (a hung GPU driver or compositor) must not block interpreter exit, while
normal shutdown still goes through stop() so resources are closed in order.
Each resource is created AND closed on the thread that uses it (mss and GL
contexts are thread-affine).
"""
from __future__ import annotations

import inspect
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from .core.params import EnhanceParams


class EnhancePipeline:
    def __init__(self, source_factory: Callable[[], Any],
                 enhancer_factory: Callable[[], Any],
                 sink_factory: Callable[[], Any],
                 params: Optional[EnhanceParams] = None,
                 target_fps: float = 30.0,
                 enhancer_needs_sink: Optional[bool] = None,
                 dispatcher: Any = None):
        # Main-thread dispatcher; stop() on its owner thread services it while
        # joining, because the render thread's sink.close() calls back into it.
        self._dispatcher = dispatcher
        self._source_factory = source_factory
        self._enhancer_factory = enhancer_factory
        # A GPU enhancer may need the sink's GL context, so the factory can ask
        # for the opened sink. None = detect a `sink` parameter by signature.
        if enhancer_needs_sink is None:
            try:
                enhancer_needs_sink = "sink" in inspect.signature(enhancer_factory).parameters
            except (TypeError, ValueError):
                enhancer_needs_sink = False
        self._enhancer_needs_sink = bool(enhancer_needs_sink)
        self._sink_factory = sink_factory
        self._params = params or EnhanceParams()
        self._fps = float(target_fps)

        self._lock = threading.Lock()          # guards slot + counters
        self._cond = threading.Condition(self._lock)
        self._slot: Optional[np.ndarray] = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._enhancer = None                  # live only while running
        self._pending_params: Optional[EnhanceParams] = None
        self._last_error: Optional[BaseException] = None
        self._reset_stats()
        self.thread_idents: dict[str, int] = {}

    # -- public ------------------------------------------------------------
    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    @property
    def threads(self) -> list[threading.Thread]:
        return list(self._threads)

    def start(self) -> None:
        if self.running:
            return
        self._stop = threading.Event()  # fresh event: old threads can never see the new one
        self._last_error = None
        self._slot = None
        self._reset_stats()
        self._ready = threading.Event()
        self._threads = [
            threading.Thread(target=self._capture_loop, name="nv-capture", daemon=True,
                             args=(self._stop,)),
            threading.Thread(target=self._render_loop, name="nv-render", daemon=True,
                             args=(self._stop,)),
        ]
        for t in self._threads:
            t.start()

    def stop(self, timeout: float = 2.0) -> bool:
        """Signal and join both threads. True when both exited in time."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        deadline = time.monotonic() + timeout
        d = self._dispatcher
        pump = d is not None and d.on_owner_thread
        for t in self._threads:
            if t is threading.current_thread():
                continue
            while t.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if pump:
                    d.run_pending()
                    t.join(min(0.005, remaining))
                else:
                    t.join(remaining)
        if pump:
            d.run_pending()  # a last close() request may have landed as the thread exited
        return not self.running

    def set_params(self, params: EnhanceParams) -> None:
        # Applied by the render thread before its next frame: a GPU enhancer
        # must only be touched from the thread that owns its context.
        with self._lock:
            self._params = params
            self._pending_params = params

    def wait_first_frame(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)

    def stats(self) -> dict:
        with self._lock:
            now = time.perf_counter()
            span = (now - self._t0) if self._t0 else 0.0
            n = self._processed
            return {
                "fps": (n / span) if span > 0 else 0.0,
                "frames_captured": self._captured,
                "frames_processed": n,
                "frames_dropped": self._dropped,
                "avg_process_ms": (self._proc_s / n * 1000.0) if n else 0.0,
                "source": self._source_name,
                "enhancer": self._enhancer_name,
                "sink": self._sink_name,
            }

    # -- internals ---------------------------------------------------------
    def _reset_stats(self) -> None:
        self._captured = self._processed = self._dropped = 0
        self._proc_s = 0.0
        self._t0 = 0.0
        self._source_name = self._enhancer_name = self._sink_name = ""
        self._ready = getattr(self, "_ready", threading.Event())

    def _fail(self, e: BaseException, stop: threading.Event) -> None:
        if self._last_error is None:
            self._last_error = e
        stop.set()
        with self._cond:
            self._cond.notify_all()

    def _capture_loop(self, stop: threading.Event) -> None:
        self.thread_idents["capture"] = threading.get_ident()
        source = None
        try:
            source = self._source_factory()
            self._source_name = getattr(source, "name", type(source).__name__)
            source.open()
            # WHY: an unpaced grab loop (mss can do hundreds of fps) just burns CPU
            # on frames the render thread would drop; 2x target keeps latency low.
            min_gap = 1.0 / (2.0 * self._fps) if self._fps > 0 else 0.0
            last = 0.0
            while not stop.is_set():
                gap = min_gap - (time.perf_counter() - last)
                if gap > 0:
                    stop.wait(gap)
                    continue
                last = time.perf_counter()
                frame = source.grab()
                if frame is None:
                    time.sleep(0.001)  # no new frame: avoid a hot spin
                    continue
                with self._cond:
                    self._captured += 1
                    if self._slot is not None:
                        self._dropped += 1  # latest wins; old frame was never rendered
                    self._slot = frame
                    self._cond.notify()
        except BaseException as e:  # noqa: BLE001 - surfaced via last_error
            self._fail(e, stop)
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:  # noqa: BLE001
                    pass

    def _render_loop(self, stop: threading.Event) -> None:
        self.thread_idents["render"] = threading.get_ident()
        enhancer = sink = None
        try:
            # Sink first: a GL sink owns the context the enhancer must share.
            sink = self._sink_factory()
            self._sink_name = type(sink).__name__
            sink.open()
            enhancer = (self._enhancer_factory(sink=sink) if self._enhancer_needs_sink
                        else self._enhancer_factory())
            self._enhancer_name = getattr(enhancer, "name", type(enhancer).__name__)
            with self._lock:
                enhancer.set_params(self._params)
                self._pending_params = None
            present_tex = getattr(sink, "present_texture", None)
            self._enhancer = enhancer
            period = 1.0 / self._fps if self._fps > 0 else 0.0
            next_t = time.perf_counter()
            self._t0 = next_t
            while not stop.is_set():
                with self._cond:
                    while self._slot is None and not stop.is_set():
                        self._cond.wait(0.05)
                    frame, self._slot = self._slot, None
                    newp, self._pending_params = self._pending_params, None
                if frame is None:
                    break
                if newp is not None:
                    enhancer.set_params(newp)
                t = time.perf_counter()
                # Re-checked per frame: a runtime GPU drop flips zero_copy off.
                tex = None
                if present_tex is not None and getattr(enhancer, "zero_copy", False):
                    tex = enhancer.process_to_texture(frame)
                if tex is not None:
                    present_tex(tex)
                else:
                    sink.present(enhancer.process(frame))
                dt = time.perf_counter() - t
                with self._lock:
                    self._processed += 1
                    self._proc_s += dt
                self._ready.set()
                next_t += period
                now = time.perf_counter()
                if next_t > now:
                    stop.wait(next_t - now)  # interruptible pacing
                else:
                    next_t = now  # behind schedule: don't try to catch up in a burst
        except BaseException as e:  # noqa: BLE001
            self._fail(e, stop)
        finally:
            self._enhancer = None
            # Enhancer first: on a shared GL context the sink's close releases it.
            for res in (enhancer, sink):
                if res is not None:
                    try:
                        res.close()
                    except Exception:  # noqa: BLE001
                        pass
