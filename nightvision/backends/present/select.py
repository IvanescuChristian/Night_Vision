# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Choose the presentation backend/mode for a platform."""
from __future__ import annotations

from typing import Callable, Optional


def _factory(mode: str) -> Callable:
    def make(monitor_rect=None, **kw):
        # Lazy import: glfw/moderngl are optional extras.
        from .overlay_gl import GlOverlaySink
        return GlOverlaySink(monitor_rect=monitor_rect, mode=mode, **kw)
    return make


def select_present_backend(info, prefer: Optional[str] = None):
    """-> (sink_factory | None, mode | None, reasons).

    prefer: None | 'overlay' | 'window'. Policy:
      windows        overlay (capture-excluded via display affinity); window if asked.
      linux x11      window by default (an X11 overlay is visible to the capturer
                     -> feedback loop); overlay only when forced, EXPERIMENTAL.
      wayland        window only (no topmost/positioned surfaces from GLFW;
                     layer-shell is future work). Forced overlay is refused.
      headless/other no sink.
    """
    if prefer not in (None, "overlay", "window"):
        raise ValueError(f"prefer must be None, 'overlay' or 'window', got {prefer!r}")
    reasons: list[str] = []
    session = info.session
    if info.os == "windows":
        mode = prefer or "overlay"
        reasons.append("windows: display affinity hides the overlay from capture"
                       if mode == "overlay" else "windows: window mode requested")
    elif info.os == "linux" and session == "x11":
        mode = prefer or "window"
        if mode == "overlay":
            reasons.append("x11 overlay forced: EXPERIMENTAL, NOT VERIFIED; the capturer "
                           "may see the overlay (feedback loop) unless capture is of another monitor")
        else:
            reasons.append("x11: window mode (an overlay would be captured by the grabber); "
                           "put the preview on a second monitor or force overlay")
    elif info.os == "linux" and session in ("wayland", "xwayland-only"):
        mode = "window"
        if prefer == "overlay":
            reasons.append("wayland: overlay refused, GLFW cannot create topmost positioned "
                           "surfaces (layer-shell is future work); using window mode")
        else:
            reasons.append("wayland: window mode only")
    else:
        reasons.append(f"no presentation backend for os={info.os} session={session}")
        return None, None, reasons
    return _factory(mode), mode, reasons
