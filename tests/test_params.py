# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import math

from nightvision.core.params import EnhanceParams, TONE_FIELDS


def test_defaults_valid_and_unchanged():
    p = EnhanceParams()
    assert p.validated() == p


def test_clamping():
    p = EnhanceParams(gamma=99, shadow_lift=-1, black_floor=5, brightness=-3,
                      contrast=0, local_contrast=9, clahe_tiles=1000,
                      sharpen=50, clarity=-2, saturation=10).validated()
    assert (p.gamma, p.shadow_lift, p.black_floor) == (3.0, 0.0, 0.25)
    assert (p.brightness, p.contrast, p.local_contrast) == (-0.5, 0.5, 1.0)
    assert (p.clahe_tiles, p.sharpen, p.clarity, p.saturation) == (32, 2.0, 0.0, 2.0)
    assert isinstance(p.clahe_tiles, int)


def test_nan_and_garbage_fall_back_to_default():
    p = EnhanceParams(gamma=math.nan, contrast="x").validated()  # type: ignore[arg-type]
    assert p.gamma == 1.0 and p.contrast == 1.0


def test_dict_roundtrip_and_unknown_keys():
    p = EnhanceParams(gamma=1.4, clahe_tiles=12, saturation=0.8)
    assert EnhanceParams.from_dict(p.to_dict()) == p
    d = p.to_dict() | {"future_field": 1}
    assert EnhanceParams.from_dict(d) == p
    assert EnhanceParams.from_dict({}) == EnhanceParams()


def test_tone_fields_exist():
    d = EnhanceParams().to_dict()
    assert all(f in d for f in TONE_FIELDS)
