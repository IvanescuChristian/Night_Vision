# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Frame sinks. open()/present()/close() are all called on the render thread."""
from __future__ import annotations

import threading
from typing import Callable, Optional, Protocol

import numpy as np


class FrameSink(Protocol):
    def open(self) -> None: ...
    def present(self, frame: np.ndarray) -> None: ...
    def close(self) -> None: ...


class NullSink:
    """Discards frames; counts them (benchmarks and tests)."""

    def __init__(self) -> None:
        self.frames = 0
        self.opened_on: Optional[int] = None
        self.closed_on: Optional[int] = None

    def open(self) -> None:
        self.opened_on = threading.get_ident()

    def present(self, frame: np.ndarray) -> None:
        self.frames += 1

    def close(self) -> None:
        self.closed_on = threading.get_ident()


class CallbackSink:
    """Calls fn(frame) on the render thread. The frame is only valid for the
    call; copy it or hand it to a bounded queue if it must outlive it."""

    def __init__(self, fn: Callable[[np.ndarray], None]) -> None:
        self._fn = fn

    def open(self) -> None:
        pass

    def present(self, frame: np.ndarray) -> None:
        self._fn(frame)

    def close(self) -> None:
        pass


class LatestFrameSink:
    """Single-slot, thread-safe latest frame.

    WHY: UI toolkits (Tk, Qt) may only be touched from their own thread, so the
    render thread publishes here and the UI thread polls take() from its timer.
    One slot means a slow UI can never make memory grow.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._seq = 0
        self._taken = 0

    def open(self) -> None:
        pass

    def present(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._seq += 1

    def take(self) -> Optional[np.ndarray]:
        """Newest frame not yet taken, else None."""
        with self._lock:
            if self._seq == self._taken:
                return None
            self._taken = self._seq
            return self._frame

    def peek(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame

    def close(self) -> None:
        with self._lock:
            self._frame = None
