# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
from __future__ import annotations

import numpy as np

from .base import ToneBackend


class NullToneBackend(ToneBackend):
    """Records the last ramp; used for dry-run and tests."""

    name = "null"

    def __init__(self) -> None:
        super().__init__()
        self.last_ramp: np.ndarray | None = None
        self.restored = 0

    def available(self) -> bool:
        return True

    def _read_ramp(self):
        n = 256
        return np.tile(np.linspace(0, 65535, n).astype(np.uint16), (3, 1))

    def _write_ramp(self, ramp) -> None:
        self.last_ramp = np.array(ramp, copy=True)

    def _restore_original(self) -> None:
        self.restored += 1
        self.last_ramp = None
