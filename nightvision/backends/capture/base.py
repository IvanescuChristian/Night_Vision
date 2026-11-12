# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Capture source interface. Frames are HxWx4 uint8 BGRA."""
from __future__ import annotations

import abc
from typing import Optional

import numpy as np


class CaptureError(Exception):
    pass


class CaptureSource(abc.ABC):
    name = "base"

    def __init__(self) -> None:
        self._opened = False

    @abc.abstractmethod
    def available(self) -> bool:
        """Cheap probe; must never raise."""

    @abc.abstractmethod
    def unavailable_reason(self) -> str:
        """Empty string when available; must never raise."""

    @abc.abstractmethod
    def _open(self) -> None: ...

    @abc.abstractmethod
    def _grab(self) -> Optional[np.ndarray]: ...

    @abc.abstractmethod
    def _close(self) -> None: ...

    @property
    @abc.abstractmethod
    def size(self) -> tuple[int, int]:
        """(width, height) of produced frames; (0, 0) until open()."""

    # WHY: open/grab/close are thread-affine for several backends (mss, GStreamer),
    # so the pipeline calls all three from the capture thread.
    def open(self) -> None:
        if self._opened:
            return
        self._open()
        self._opened = True

    def grab(self) -> Optional[np.ndarray]:
        """Newest frame as HxWx4 uint8 BGRA, or None when nothing new."""
        if not self._opened:
            raise CaptureError(f"{self.name}: grab() before open()")
        return self._grab()

    def close(self) -> None:
        if not self._opened:
            return
        self._opened = False
        try:
            self._close()
        except Exception:  # noqa: BLE001 - close must be safe in cleanup paths
            pass

    def __enter__(self) -> "CaptureSource":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
