# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Tone (gamma ramp) backend base class with safety checks and crash-safe restore."""
from __future__ import annotations

import abc
import atexit
import threading
import weakref

import numpy as np


class ToneBackendError(Exception):
    pass


_live: "weakref.WeakSet[ToneBackend]" = weakref.WeakSet()
_live_lock = threading.Lock()


def _restore_all() -> None:
    with _live_lock:
        backends = list(_live)
    for b in backends:
        try:
            b.restore()
        except Exception:  # noqa: BLE001 - at exit, keep restoring the rest
            pass


# WHY: a crash/Ctrl-C must not leave the desktop with a modified gamma ramp.
atexit.register(_restore_all)


def validate_ramp(ramp) -> np.ndarray:
    """Return a validated (3, N) uint16 array or raise ToneBackendError.

    Inline checks (not imported from core) because a bad ramp can black out the screen.
    """
    arr = np.asarray(ramp)
    if arr.ndim != 2 or arr.shape[0] != 3 or arr.shape[1] < 2:
        raise ToneBackendError(f"ramp must have shape (3, N>=2), got {arr.shape}")
    if arr.dtype != np.uint16:
        raise ToneBackendError(f"ramp dtype must be uint16, got {arr.dtype}")
    if np.any(np.diff(arr.astype(np.int32), axis=1) < 0):
        raise ToneBackendError("unsafe ramp: not monotonic non-decreasing")
    if int(arr.max()) < 0.25 * 65535:
        raise ToneBackendError("unsafe ramp: max below 25% of range (screen would go dark)")
    if int(arr.min()) > 0.5 * 65535:
        raise ToneBackendError("unsafe ramp: min above 50% of range (screen would wash out)")
    return arr


def resample_ramp(ramp, n: int) -> np.ndarray:
    ramp = np.asarray(ramp)
    if ramp.shape[1] == n:
        return ramp.astype(np.uint16)
    src = np.linspace(0.0, 1.0, ramp.shape[1])
    dst = np.linspace(0.0, 1.0, n)
    out = np.stack([np.interp(dst, src, ramp[c].astype(np.float64)) for c in range(3)])
    return np.clip(np.rint(out), 0, 65535).astype(np.uint16)


class ToneBackend(abc.ABC):
    name: str = "base"

    def __init__(self) -> None:
        self._original: np.ndarray | None = None
        self._applied = False
        with _live_lock:
            _live.add(self)

    @abc.abstractmethod
    def available(self) -> bool:
        """Cheap probe; must never raise."""

    def unavailable_reason(self) -> str:
        return "unavailable"

    @abc.abstractmethod
    def _read_ramp(self) -> np.ndarray | None:
        """Current hardware ramp (3, N) uint16, or None if it cannot be read."""

    @abc.abstractmethod
    def _write_ramp(self, ramp: np.ndarray) -> None:
        ...

    def _restore_original(self) -> None:
        """Default: write back the captured ramp. Backends without readback override."""
        if self._original is not None:
            self._write_ramp(self._original)

    def apply(self, ramp) -> None:
        arr = validate_ramp(ramp)
        if self._original is None:
            # Captured once: later applies must not overwrite it with our own output.
            self._original = self._read_ramp()
        self._write_ramp(arr)
        self._applied = True

    def restore(self) -> None:
        if not self._applied:
            return
        self._applied = False  # idempotent even if the write below fails
        self._restore_original()

    def close(self) -> None:
        try:
            self.restore()
        finally:
            with _live_lock:
                _live.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
