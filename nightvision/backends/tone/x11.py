# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""X11 gamma backends via ctypes (XRandR preferred, XF86VidMode fallback).

NOTE: under XWayland these calls usually succeed but do not change the real
output, so select.py never picks them on a Wayland session.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os

import numpy as np

from .base import ToneBackend, ToneBackendError, resample_ramp


def _load(names):
    for n in names:
        path = ctypes.util.find_library(n)
        if path:
            try:
                return ctypes.CDLL(path)
            except OSError:
                continue
    return None


class _XRRCrtcGamma(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_int),
        ("red", ctypes.POINTER(ctypes.c_ushort)),
        ("green", ctypes.POINTER(ctypes.c_ushort)),
        ("blue", ctypes.POINTER(ctypes.c_ushort)),
    ]


class _XRRScreenResources(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_ulong),
        ("configTimestamp", ctypes.c_ulong),
        ("ncrtc", ctypes.c_int),
        ("crtcs", ctypes.POINTER(ctypes.c_ulong)),
        ("noutput", ctypes.c_int),
        ("outputs", ctypes.POINTER(ctypes.c_ulong)),
        ("nmode", ctypes.c_int),
        ("modes", ctypes.c_void_p),
    ]


def load_xlib():
    """Return (libX11, libXrandr) or None. Separate function so tests can fake it."""
    x11, xrr = _load(["X11"]), _load(["Xrandr"])
    if x11 is None or xrr is None:
        return None
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    xrr.XRRGetScreenResourcesCurrent.restype = ctypes.POINTER(_XRRScreenResources)
    xrr.XRRGetScreenResourcesCurrent.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRFreeScreenResources.argtypes = [ctypes.POINTER(_XRRScreenResources)]
    xrr.XRRGetCrtcGammaSize.restype = ctypes.c_int
    xrr.XRRGetCrtcGammaSize.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRGetCrtcGamma.restype = ctypes.POINTER(_XRRCrtcGamma)
    xrr.XRRGetCrtcGamma.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xrr.XRRAllocGamma.restype = ctypes.POINTER(_XRRCrtcGamma)
    xrr.XRRAllocGamma.argtypes = [ctypes.c_int]
    xrr.XRRSetCrtcGamma.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XRRCrtcGamma)]
    xrr.XRRFreeGamma.argtypes = [ctypes.POINTER(_XRRCrtcGamma)]
    return x11, xrr


class XRandRGammaBackend(ToneBackend):
    name = "xrandr"

    def __init__(self) -> None:
        super().__init__()
        self._reason = ""
        self._originals: dict[int, np.ndarray] = {}

    def _open(self):
        if not os.environ.get("DISPLAY"):
            self._reason = "DISPLAY not set"
            return None
        libs = load_xlib()
        if libs is None:
            self._reason = "libX11/libXrandr not found"
            return None
        x11, xrr = libs
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            self._reason = "cannot open X display"
            return None
        return x11, xrr, dpy

    def _crtcs(self, x11, xrr, dpy):
        res = xrr.XRRGetScreenResourcesCurrent(dpy, x11.XDefaultRootWindow(dpy))
        if not res:
            return [], None
        r = res.contents
        return [int(r.crtcs[i]) for i in range(r.ncrtc)], res

    def available(self) -> bool:
        try:
            h = self._open()
            if h is None:
                return False
            x11, xrr, dpy = h
            try:
                crtcs, res = self._crtcs(x11, xrr, dpy)
                try:
                    if any(xrr.XRRGetCrtcGammaSize(dpy, c) > 0 for c in crtcs):
                        return True
                    self._reason = "no CRTC reports a gamma size > 0"
                    return False
                finally:
                    if res:
                        xrr.XRRFreeScreenResources(res)
            finally:
                x11.XCloseDisplay(dpy)
        except Exception as e:  # noqa: BLE001
            self._reason = f"probe failed: {e}"
            return False

    def unavailable_reason(self) -> str:
        return self._reason or "unavailable"

    def _read_ramp(self):
        # Originals are per CRTC (sizes can differ); captured in _write_ramp's first pass.
        return None

    def _each_crtc(self, fn):
        h = self._open()
        if h is None:
            raise ToneBackendError(f"XRandR unavailable: {self._reason}")
        x11, xrr, dpy = h
        try:
            crtcs, res = self._crtcs(x11, xrr, dpy)
            try:
                for c in crtcs:
                    size = xrr.XRRGetCrtcGammaSize(dpy, c)
                    if size > 0:
                        fn(xrr, dpy, c, size)
                x11.XSync(dpy, 0)
            finally:
                if res:
                    xrr.XRRFreeScreenResources(res)
        finally:
            x11.XCloseDisplay(dpy)

    @staticmethod
    def _set(xrr, dpy, crtc, size, ramp3):
        g = xrr.XRRAllocGamma(size)
        try:
            for ch, name in enumerate(("red", "green", "blue")):
                ptr = getattr(g.contents, name)
                for i in range(size):
                    ptr[i] = int(ramp3[ch][i])
            xrr.XRRSetCrtcGamma(dpy, crtc, g)
        finally:
            xrr.XRRFreeGamma(g)

    def _write_ramp(self, ramp) -> None:
        def go(xrr, dpy, crtc, size):
            if crtc not in self._originals:
                g = xrr.XRRGetCrtcGamma(dpy, crtc)
                try:
                    o = g.contents
                    self._originals[crtc] = np.array(
                        [[ch[i] for i in range(size)] for ch in (o.red, o.green, o.blue)],
                        dtype=np.uint16,
                    )
                finally:
                    xrr.XRRFreeGamma(g)
            self._set(xrr, dpy, crtc, size, resample_ramp(ramp, size))

        self._each_crtc(go)

    def _restore_original(self) -> None:
        def go(xrr, dpy, crtc, size):
            o = self._originals.get(crtc)
            if o is not None and o.shape[1] == size:
                self._set(xrr, dpy, crtc, size, o)

        self._each_crtc(go)


class XF86VidModeGammaBackend(ToneBackend):
    name = "xf86vidmode"

    def __init__(self) -> None:
        super().__init__()
        self._reason = ""

    def _open(self):
        if not os.environ.get("DISPLAY"):
            self._reason = "DISPLAY not set"
            return None
        x11, vm = _load(["X11"]), _load(["Xxf86vm"])
        if x11 is None or vm is None:
            self._reason = "libX11/libXxf86vm not found"
            return None
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            self._reason = "cannot open X display"
            return None
        vm.XF86VidModeGetGammaRampSize.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        arr = ctypes.POINTER(ctypes.c_ushort)
        vm.XF86VidModeGetGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, arr, arr, arr]
        vm.XF86VidModeSetGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, arr, arr, arr]
        return x11, vm, dpy

    def _size(self, vm, dpy) -> int:
        n = ctypes.c_int(0)
        if not vm.XF86VidModeGetGammaRampSize(dpy, 0, ctypes.byref(n)):
            return 0
        return n.value

    def available(self) -> bool:
        try:
            h = self._open()
            if h is None:
                return False
            x11, vm, dpy = h
            try:
                ok = self._size(vm, dpy) > 0
                if not ok:
                    self._reason = "gamma ramp size is 0"
                return ok
            finally:
                x11.XCloseDisplay(dpy)
        except Exception as e:  # noqa: BLE001
            self._reason = f"probe failed: {e}"
            return False

    def unavailable_reason(self) -> str:
        return self._reason or "unavailable"

    def _rw(self, ramp=None):
        h = self._open()
        if h is None:
            raise ToneBackendError(f"XF86VidMode unavailable: {self._reason}")
        x11, vm, dpy = h
        try:
            n = self._size(vm, dpy)
            if n <= 0:
                raise ToneBackendError("XF86VidMode reports gamma size 0")
            bufs = [(ctypes.c_ushort * n)() for _ in range(3)]
            if ramp is None:
                if not vm.XF86VidModeGetGammaRamp(dpy, 0, n, *bufs):
                    raise ToneBackendError("XF86VidModeGetGammaRamp failed")
                return np.array([list(b) for b in bufs], dtype=np.uint16)
            r = resample_ramp(ramp, n)
            for ch in range(3):
                bufs[ch][:] = [int(v) for v in r[ch]]
            if not vm.XF86VidModeSetGammaRamp(dpy, 0, n, *bufs):
                raise ToneBackendError("XF86VidModeSetGammaRamp failed")
            return None
        finally:
            x11.XCloseDisplay(dpy)

    def _read_ramp(self):
        return self._rw()

    def _write_ramp(self, ramp) -> None:
        self._rw(ramp)
