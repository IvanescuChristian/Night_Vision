# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Enhancement parameters: one immutable, validated value object."""
from __future__ import annotations

import math
from dataclasses import dataclass, fields, replace
from typing import Any

# Fields that shape the global tone curve (what a display gamma ramp can express).
TONE_FIELDS = (
    "gamma", "shadow_lift", "black_floor", "brightness", "contrast",
    "highlight_protect",
)

# name -> (lo, hi); single source of truth for clamping.
_RANGES: dict[str, tuple[float, float]] = {
    "gamma": (0.3, 3.0),
    "shadow_lift": (0.0, 1.0),
    "black_floor": (0.0, 0.25),
    "brightness": (-0.5, 0.5),
    "contrast": (0.5, 2.0),
    "highlight_protect": (0.0, 1.0),
    "local_contrast": (0.0, 1.0),
    "clahe_tiles": (2, 32),
    "sharpen": (0.0, 2.0),
    "clarity": (0.0, 1.0),
    "saturation": (0.0, 2.0),
}
# Public name for UI/other consumers; same dict object so both stay in sync.
PARAM_RANGES = _RANGES


@dataclass(frozen=True)
class EnhanceParams:
    gamma: float = 1.0               # >1 brightens: out = in ** (1/gamma)
    shadow_lift: float = 0.0
    black_floor: float = 0.0
    brightness: float = 0.0
    contrast: float = 1.0            # pivot at 0.5
    highlight_protect: float = 0.5
    local_contrast: float = 0.0      # CLAHE strength (capture mode only)
    clahe_tiles: int = 8
    sharpen: float = 0.0             # capture mode only
    clarity: float = 0.0             # capture mode only
    saturation: float = 1.0

    def validated(self) -> "EnhanceParams":
        """Return a clamped copy. NaN/inf/garbage fall back to the default so a
        corrupt settings file can never produce an invalid curve."""
        out: dict[str, Any] = {}
        for f in fields(self):
            lo, hi = _RANGES[f.name]
            v = getattr(self, f.name)
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = float(f.default)
            if not math.isfinite(v):
                v = float(f.default)
            v = min(max(v, lo), hi)
            out[f.name] = int(round(v)) if f.name == "clahe_tiles" else v
        return replace(self, **out)

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EnhanceParams":
        """Unknown keys are ignored (forward compat); missing keys get defaults."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known}).validated()

    @property
    def tone_is_identity(self) -> bool:
        d = EnhanceParams()
        return all(getattr(self, n) == getattr(d, n)
                   for n in TONE_FIELDS if n != "highlight_protect")
