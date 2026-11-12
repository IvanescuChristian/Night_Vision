# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Wayland tone backends.

Only wlroots-based compositors expose a client gamma API (wlr-gamma-control).
GNOME/KDE do not; use capture mode there.
"""
from __future__ import annotations

import shutil
import subprocess

import numpy as np

from .base import ToneBackend, ToneBackendError


class WlrGammaControlBackend(ToneBackend):
    """wlr-gamma-control-unstable-v1 via pywayland: NOT IMPLEMENTED (stub)."""

    name = "wlr-gamma"

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "needs pywayland protocol bindings (not bundled)"

    def _read_ramp(self):
        raise ToneBackendError(self.unavailable_reason())

    def _write_ramp(self, ramp) -> None:
        raise ToneBackendError(self.unavailable_reason())


def fit_power_gamma(ramp) -> tuple[float, float, float, float]:
    """Fit out = brightness * in**g per channel (least squares in log-log, mid-range).

    Returns (gr, gg, gb, brightness). gammastep's -g is the *inverse* exponent
    convention (value > 1 brightens), so callers convert; here g is the plain
    power-law exponent of the ramp. brightness is the mean of per-channel gains
    (output at input=1), clipped to [0, 1].
    """
    r = np.asarray(ramp, dtype=np.float64) / 65535.0
    n = r.shape[1]
    x = np.linspace(0.0, 1.0, n)
    mask = (x >= 0.1) & (x <= 0.9)
    lx = np.log(x[mask])
    gammas, gains = [], []
    for c in range(3):
        y = np.clip(r[c][mask], 1e-6, None)
        slope, intercept = np.polyfit(lx, np.log(y), 1)
        gammas.append(float(slope))
        gains.append(float(np.exp(intercept)))
    b = float(np.clip(np.mean(gains), 0.0, 1.0))
    return gammas[0], gammas[1], gammas[2], b


class GammastepProxyBackend(ToneBackend):
    """PROXY backend: shells out to gammastep one-shot (-O).

    Only a power curve + brightness can be expressed, so the shadow-lift SHAPE of
    the real ramp is approximated, not reproduced. Restore uses `gammastep -x`.
    """

    name = "gammastep-proxy"
    TIMEOUT = 10

    def __init__(self, wlroots: bool = True) -> None:
        super().__init__()
        self._wlroots = wlroots

    def available(self) -> bool:
        return self._wlroots and shutil.which("gammastep") is not None

    def unavailable_reason(self) -> str:
        if not self._wlroots:
            return "not a wlroots compositor"
        return "gammastep not found on PATH"

    def build_argv(self, ramp) -> list[str]:
        gr, gg, gb, b = fit_power_gamma(ramp)
        # gammastep applies out = in**(1/g); our fitted exponent e maps to g = 1/e.
        inv = [1.0 / max(e, 1e-3) for e in (gr, gg, gb)]
        inv = [min(max(v, 0.1), 10.0) for v in inv]
        b = min(max(b, 0.1), 1.0)
        return ["gammastep", "-m", "wayland", "-O", "6500",
                "-g", ":".join(f"{v:.3f}" for v in inv), "-b", f"{b:.3f}", "-P"]

    def _read_ramp(self):
        return None  # no readback; restore is `gammastep -x`

    def _write_ramp(self, ramp) -> None:
        argv = self.build_argv(ramp)
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=self.TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise ToneBackendError(f"gammastep failed: {e}") from e
        if p.returncode != 0:
            raise ToneBackendError(f"gammastep exited {p.returncode}: {(p.stderr or '').strip()}")

    def _restore_original(self) -> None:
        try:
            subprocess.run(["gammastep", "-x", "-m", "wayland"], capture_output=True,
                           text=True, timeout=self.TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise ToneBackendError(f"gammastep reset failed: {e}") from e
