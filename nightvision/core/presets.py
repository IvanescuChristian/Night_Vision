# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Built-in presets. Values are already inside valid ranges."""
from __future__ import annotations

from .params import EnhanceParams

PRESETS: dict[str, EnhanceParams] = {
    "off": EnhanceParams(),
    "balanced": EnhanceParams(gamma=1.25, shadow_lift=0.3, contrast=1.05,
                              highlight_protect=0.6),
    "night_vision": EnhanceParams(gamma=1.7, shadow_lift=0.75, black_floor=0.02,
                                  contrast=1.1, highlight_protect=0.8,
                                  local_contrast=0.4, saturation=0.9),
    "black_equalizer": EnhanceParams(shadow_lift=0.8, highlight_protect=0.5),
    "clarity": EnhanceParams(gamma=1.15, shadow_lift=0.25, highlight_protect=0.6,
                             sharpen=0.6, clarity=0.5),
    "tarkov_dark": EnhanceParams(gamma=1.5, shadow_lift=0.65, contrast=1.1,
                                 highlight_protect=0.75, local_contrast=0.3,
                                 sharpen=0.3, clarity=0.3, saturation=1.05),
    "rust_night": EnhanceParams(gamma=1.8, shadow_lift=0.7, black_floor=0.03,
                                contrast=1.05, highlight_protect=0.8,
                                local_contrast=0.25, saturation=0.95),
}


def get_preset(name: str) -> EnhanceParams:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(
            f"unknown preset {name!r}; available: {', '.join(sorted(PRESETS))}"
        ) from None
