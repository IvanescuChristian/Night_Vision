# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Win32 GDI gamma ramp backend (primary monitor DC).

Windows may clamp or reject extreme ramps (typically anything deviating far from
identity) unless the registry value
HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ICM\\GdiIcmGammaRange (DWORD 256)
is set. This module never writes the registry; on failure it raises a
ToneBackendError that explains this. Per-monitor DCs are future work.
"""
from __future__ import annotations

import ctypes
import sys

import numpy as np

from .base import ToneBackend, ToneBackendError, resample_ramp


def _gdi():
    # Lazy: ctypes.windll only exists on Windows.
    return ctypes.windll.gdi32, ctypes.windll.user32  # type: ignore[attr-defined]


class Win32GammaBackend(ToneBackend):
    name = "win32"

    def available(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            _gdi()
            return True
        except Exception:  # noqa: BLE001
            return False

    def unavailable_reason(self) -> str:
        return "not running on Windows"

    def _with_dc(self, fn):
        gdi32, user32 = _gdi()
        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.SetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        hdc = user32.GetDC(None)
        if not hdc:
            raise ToneBackendError("GetDC(None) failed")
        try:
            return fn(gdi32, hdc)
        finally:
            user32.ReleaseDC(None, hdc)

    def _read_ramp(self):
        buf = (ctypes.c_uint16 * 256 * 3)()

        def go(gdi32, hdc):
            if not gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(buf)):
                raise ToneBackendError("GetDeviceGammaRamp failed")

        self._with_dc(go)
        return np.ctypeslib.as_array(buf).astype(np.uint16).copy()

    def _write_ramp(self, ramp) -> None:
        r = resample_ramp(ramp, 256)
        buf = (ctypes.c_uint16 * 256 * 3)()
        np.ctypeslib.as_array(buf)[:] = r

        def go(gdi32, hdc):
            if not gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(buf)):
                raise ToneBackendError(
                    "SetDeviceGammaRamp rejected the ramp. Windows clamps ramps that deviate "
                    "strongly from identity unless the registry DWORD "
                    "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ICM\\GdiIcmGammaRange "
                    "is set to 256 (nightvision does not modify the registry). Try a milder "
                    "profile or set it manually."
                )

        self._with_dc(go)
