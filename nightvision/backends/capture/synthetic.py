# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Deterministic dark test scene (tests and demo mode)."""
from __future__ import annotations

import time
from typing import Optional

import numpy as np

from .base import CaptureSource


class SyntheticCapture(CaptureSource):
    """Dim figure moving over a darker gradient background.

    Frame content depends only on the frame index, so runs are reproducible.
    With realtime=True grab() paces itself to fps and returns None between
    frames; with realtime=False every call yields the next frame immediately.
    """
    name = "synthetic"

    def __init__(self, width: int = 1280, height: int = 720, fps: float = 60.0,
                 realtime: bool = True):
        super().__init__()
        self._w, self._h, self._fps, self._realtime = int(width), int(height), float(fps), realtime
        self._idx = 0
        self._next_t = 0.0
        self._bg: Optional[np.ndarray] = None

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    @property
    def size(self) -> tuple[int, int]:
        return (self._w, self._h)

    @property
    def frame_index(self) -> int:
        return self._idx

    def _open(self) -> None:
        ys = np.linspace(4, 14, self._h, dtype=np.float32)[:, None]
        xs = np.linspace(0, 6, self._w, dtype=np.float32)[None, :]
        base = (ys + xs).astype(np.uint8)
        bg = np.empty((self._h, self._w, 4), np.uint8)
        bg[..., 0] = base + 4  # slightly blue night tint
        bg[..., 1] = base
        bg[..., 2] = base
        bg[..., 3] = 255
        self._bg = bg
        self._idx = 0
        self._next_t = time.perf_counter()

    def frame_at(self, idx: int) -> np.ndarray:
        if self._bg is None:
            self._open()
        f = self._bg.copy()
        fh = max(8, self._h // 5)
        fw = max(4, self._w // 14)
        t = (idx % 240) / 240.0
        cx = int(fw + t * (self._w - 2 * fw))
        cy = int(self._h * 0.55 + np.sin(t * 2 * np.pi) * self._h * 0.1)
        x0, x1 = max(0, cx - fw // 2), min(self._w, cx + fw // 2)
        y0, y1 = max(0, cy - fh // 2), min(self._h, cy + fh // 2)
        f[y0:y1, x0:x1, :3] = (38, 30, 26)  # dim, but above the background
        return f

    def _grab(self) -> Optional[np.ndarray]:
        if self._realtime:
            now = time.perf_counter()
            if now < self._next_t:
                time.sleep(min(self._next_t - now, 0.005))
                return None
            self._next_t = max(self._next_t + 1.0 / self._fps, now - 1.0 / self._fps)
        f = self.frame_at(self._idx)
        self._idx += 1
        return f

    def _close(self) -> None:
        self._bg = None
