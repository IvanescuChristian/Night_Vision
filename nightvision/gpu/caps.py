# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Vendor-neutral GPU capability detection (plain OpenGL 3.3 core queries).

Nothing here is vendor specific: NVIDIA, AMD (discrete) and integrated GPUs
are all treated the same way, through the GL_* strings and by actually
creating the framebuffers the pipeline needs. Only a software rasteriser is
singled out, because on those the CPU path is faster and exact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_VERSION_CODE = 330

# Renderer substrings of CPU rasterisers. Hardware-adapter strings look like
# "D3D12 (AMD Radeon ...)" and are real hardware, so they must not match;
# the Windows fallback adapter shows up as "Microsoft Basic Render Driver".
_SOFTWARE_PATTERNS = (
    "llvmpipe", "softpipe", "swrast", "lavapipe", "software rasterizer",
    "microsoft basic render", "gdi generic", "swiftshader",
)

# (components, dtype) of every render target the pipeline allocates; each is
# created as an FBO attachment and verified, since "supported" in a GL string
# is not the same as "renderable" on every driver.
REQUIRED_TARGETS = (
    ("RGBA8", 4, "f1"),
    ("R8", 1, "f1"),
    ("RG16F", 2, "f2"),
)


class FrameTooLargeError(RuntimeError):
    """Frame exceeds GL_MAX_TEXTURE_SIZE; callers fall back to the CPU path."""


def is_software_renderer(renderer: str) -> bool:
    r = (renderer or "").lower()
    return any(p in r for p in _SOFTWARE_PATTERNS)


def vendor_family(vendor: str, renderer: str = "") -> str:
    """Coarse family for display only (never used to pick code paths)."""
    s = f"{vendor} {renderer}".lower()
    for family, pat in (("nvidia", r"nvidia|geforce|quadro|rtx"),
                        ("amd", r"\bamd\b|\bati\b|radeon"),
                        ("intel", r"intel"),
                        ("apple", r"apple"),
                        ("qualcomm", r"qualcomm|adreno")):
        if re.search(pat, s):
            return family
    return "unknown"


@dataclass(frozen=True)
class GpuCaps:
    vendor: str
    renderer: str
    version_code: int
    max_texture_size: int
    is_software: bool
    ok: bool
    problems: list[str] = field(default_factory=list)

    @property
    def family(self) -> str:
        return vendor_family(self.vendor, self.renderer)

    def summary(self) -> str:
        kind = "software" if self.is_software else "hardware"
        return f"{self.renderer} ({self.vendor}, GL {self.version_code}, {kind})"


def _check_target(ctx, name: str, components: int, dtype: str) -> str | None:
    tex = fbo = None
    try:
        tex = ctx.texture((8, 8), components, dtype=dtype)
        fbo = ctx.framebuffer([tex])  # moderngl raises if the FBO is incomplete
        fbo.use()
        fbo.clear(0.25, 0.0, 0.0, 1.0)
        if ctx.error != "GL_NO_ERROR":
            return f"{name} render target raised {ctx.error}"
        return None
    except Exception as exc:
        return f"{name} not renderable as a framebuffer attachment: {exc}"
    finally:
        for o in (fbo, tex):
            if o is not None:
                try:
                    o.release()
                except Exception:
                    pass


def probe_gpu(ctx) -> GpuCaps:
    """Inspect a live context. Never raises; failures land in `problems`."""
    problems: list[str] = []
    info = getattr(ctx, "info", {}) or {}
    vendor = str(info.get("GL_VENDOR", "unknown"))
    renderer = str(info.get("GL_RENDERER", "unknown"))
    version_code = int(getattr(ctx, "version_code", 0) or 0)
    try:
        max_tex = int(info.get("GL_MAX_TEXTURE_SIZE", 0))
    except (TypeError, ValueError):
        max_tex = 0
    if version_code < MIN_VERSION_CODE:
        problems.append(f"OpenGL {version_code} < required {MIN_VERSION_CODE}")
    if max_tex <= 0:
        problems.append("GL_MAX_TEXTURE_SIZE unavailable")
    if not problems:  # FBO checks are meaningless on a context that is too old
        for name, comps, dtype in REQUIRED_TARGETS:
            err = _check_target(ctx, name, comps, dtype)
            if err:
                problems.append(err)
    return GpuCaps(vendor, renderer, version_code, max_tex,
                   is_software_renderer(renderer), not problems, problems)
