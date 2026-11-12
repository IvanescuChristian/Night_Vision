# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Windows Desktop Duplication (DXGI) capture through the optional `dxcam` package.

NOT VERIFIED: written without access to Windows; never executed. dxcam is an
optional dependency (pip install dxcam). It wraps the OS Desktop Duplication
API, which is a screen-capture API (no injection into any process).
"""
from __future__ import annotations

import sys
from typing import Optional

import numpy as np

from .base import CaptureError, CaptureSource

try:
    import dxcam as _dxcam
except Exception as _e:  # noqa: BLE001
    _dxcam = None
    _ERR = f"dxcam not installed or failed to import ({_e.__class__.__name__}); pip install dxcam"
else:
    _ERR = ""


class DxgiCapture(CaptureSource):
    name = "dxgi"

    def __init__(self, output_idx: int = 0):
        super().__init__()
        self._output = output_idx
        self._cam = None
        self._size = (0, 0)

    def available(self) -> bool:
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str:
        if not sys.platform.startswith("win"):
            return "DXGI Desktop Duplication is Windows-only"
        return _ERR

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def _open(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise CaptureError(f"dxgi: {reason}")
        try:
            # BGRA output avoids a colour conversion and matches the pipeline format.
            self._cam = _dxcam.create(output_idx=self._output, output_color="BGRA")
            if self._cam is None:
                raise CaptureError("dxgi: dxcam could not create a duplicator")
            self._size = (int(self._cam.width), int(self._cam.height))
        except CaptureError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CaptureError(f"dxgi: {e}") from e

    def _grab(self) -> Optional[np.ndarray]:
        # dxcam returns None when the screen has not changed since the last grab.
        try:
            f = self._cam.grab()
        except Exception as e:  # noqa: BLE001
            raise CaptureError(f"dxgi grab failed: {e}") from e
        if f is None:
            return None
        return np.ascontiguousarray(f)

    def _close(self) -> None:
        cam, self._cam = self._cam, None
        if cam is not None:
            for fn in ("release", "stop"):
                try:
                    getattr(cam, fn)()
                except Exception:  # noqa: BLE001
                    pass
