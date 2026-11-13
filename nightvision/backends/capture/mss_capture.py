# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""X11/XWayland (XShm/XGetImage) and Windows (GDI) capture via the mss library."""
from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np

from .base import CaptureError, CaptureSource

try:  # guarded: mss is a hard dependency of the project but may fail on import without a display lib
    import mss as _mss
except Exception as _e:  # noqa: BLE001
    _mss = None
    _MSS_ERR = f"mss import failed: {_e}"
else:
    _MSS_ERR = ""


def list_monitors() -> list[tuple[int, int, int, int]]:
    """Physical monitors as (left, top, width, height), in mss order (1-based index = pos + 1).

    Skips mss' entry 0 (the combined virtual screen).
    """
    if _mss is None:
        raise CaptureError(_MSS_ERR)
    try:
        sct = getattr(_mss, "MSS", None) and _mss.MSS() or _mss.mss()
    except Exception as e:  # noqa: BLE001
        raise CaptureError(f"mss: cannot open display: {e}") from e
    try:
        return [(int(m["left"]), int(m["top"]), int(m["width"]), int(m["height"]))
                for m in sct.monitors[1:]]
    finally:
        sct.close()


class MssCapture(CaptureSource):
    """monitor: mss monitor index (1 = first physical; 0 = all combined), or
    region: dict(left, top, width, height) in screen pixels.

    The mss instance is created inside open(), which the pipeline calls on the
    capture thread: mss keeps per-thread X/GDI handles and must not cross threads.
    """
    name = "mss"

    def __init__(self, monitor: int = 1, region: Optional[dict] = None,
                 wayland_session: bool = False, env=None):
        super().__init__()
        self._monitor, self._region = monitor, region
        self._wayland = wayland_session
        self._env = os.environ if env is None else env
        self._sct = None
        self._target: Optional[dict] = None
        self._size = (0, 0)

    def available(self) -> bool:
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str:
        if _mss is None:
            return _MSS_ERR
        if sys.platform.startswith("win"):
            return ""
        if not self._env.get("DISPLAY"):
            return "no X display (DISPLAY unset): pure Wayland session without XWayland"
        return ""

    @property
    def caveat(self) -> str:
        """Non-fatal limitation, shown to the user next to the backend name."""
        if self._wayland and not sys.platform.startswith("win"):
            # WHY: under XWayland the X server only holds X clients' pixels;
            # native Wayland windows appear black or missing.
            return "XWayland: only X11 clients are visible; native Wayland windows are not captured"
        return ""

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def _open(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise CaptureError(f"mss: {reason}")
        try:
            # mss.MSS replaces the deprecated mss.mss factory in newer releases.
            self._sct = getattr(_mss, "MSS", None) and _mss.MSS() or _mss.mss()
            if self._region is not None:
                r = self._region
                self._target = {"left": int(r["left"]), "top": int(r["top"]),
                                "width": int(r["width"]), "height": int(r["height"])}
            else:
                mons = self._sct.monitors
                if not 0 <= self._monitor < len(mons):
                    have = "; ".join(f"{i}: {m['width']}x{m['height']} at ({m['left']},{m['top']})"
                                     for i, m in enumerate(mons) if i)
                    raise CaptureError(f"mss: monitor {self._monitor} not found; "
                                       f"available monitors: {have or 'none'}")
                self._target = mons[self._monitor]
            self._size = (int(self._target["width"]), int(self._target["height"]))
        except CaptureError:
            self._close()
            raise
        except Exception as e:  # noqa: BLE001
            self._close()
            raise CaptureError(f"mss: cannot open display: {e}") from e

    def _grab(self) -> Optional[np.ndarray]:
        try:
            shot = self._sct.grab(self._target)
        except Exception as e:  # noqa: BLE001
            raise CaptureError(f"mss grab failed: {e}") from e
        # WHY no copy: every mss backend returns a fresh bytearray per grab (a
        # snapshot of its shared buffer), owned by the ScreenShot and never reused,
        # so a view of it is already an independent, writable, contiguous frame.
        # (shot.bgra would add a further full-frame bytes copy.)
        raw = shot.raw
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return arr if arr.flags.writeable else arr.copy()

    def _close(self) -> None:
        sct, self._sct = self._sct, None
        if sct is not None:
            sct.close()
