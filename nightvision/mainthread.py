# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Run callables on the owner (main) thread from any other thread.

WHY: GLFW requires init, window creation/destruction and event polling on the
main thread, while the pipeline's render thread owns the GL context. The render
thread asks the owner to do the thread-bound GLFW calls through this queue; the
owner services it from its pump loop.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

QUEUE_SIZE = 64


class DispatcherError(RuntimeError):
    """The dispatcher is shut down or saturated."""


class MainThreadDispatcher:
    def __init__(self, maxsize: int = QUEUE_SIZE) -> None:
        self.owner_thread = threading.current_thread()
        self._q: "queue.Queue[tuple[Future, Callable[[], Any]]]" = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()  # makes "check closed + enqueue" atomic vs shutdown
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def on_owner_thread(self) -> bool:
        return threading.current_thread() is self.owner_thread

    def call(self, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
        """Run fn on the owner thread and return its result (or re-raise)."""
        if self.on_owner_thread:
            # Waiting on ourselves would deadlock; the owner may run it directly.
            if self._closed:
                raise DispatcherError("main-thread dispatcher is shut down")
            return fn()
        fut: Future = Future()
        with self._lock:
            if self._closed:
                raise DispatcherError("main-thread dispatcher is shut down")
            try:
                self._q.put_nowait((fut, fn))
            except queue.Full:
                raise DispatcherError("main-thread queue is full") from None
        try:
            return fut.result(timeout)
        except FutureTimeout:
            # Not yet started -> cancelled so the owner never runs a stale call.
            fut.cancel()
            raise TimeoutError(f"main thread did not service the call within {timeout}s") from None

    def run_pending(self, max_items: int = QUEUE_SIZE) -> int:
        """Owner thread only: run queued calls; returns how many ran."""
        if not self.on_owner_thread:
            raise DispatcherError("run_pending must be called on the owner thread")
        n = 0
        while n < max_items:
            try:
                fut, fn = self._q.get_nowait()
            except queue.Empty:
                break
            if not fut.set_running_or_notify_cancel():
                continue
            n += 1
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 - delivered to the caller
                fut.set_exception(exc)
        return n

    def shutdown(self) -> None:
        """Reject new calls and fail pending ones so no caller hangs."""
        with self._lock:
            self._closed = True
            while True:
                try:
                    fut, _ = self._q.get_nowait()
                except queue.Empty:
                    break
                if fut.set_running_or_notify_cancel():
                    fut.set_exception(DispatcherError("main-thread dispatcher shut down"))
