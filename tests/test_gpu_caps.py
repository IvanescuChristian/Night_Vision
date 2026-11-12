# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import pytest

from nightvision.gpu import caps as caps_mod
from nightvision.gpu.caps import (GpuCaps, is_software_renderer, probe_gpu,
                                  vendor_family)


@pytest.mark.parametrize("renderer,expected", [
    ("llvmpipe (LLVM 20.1.2, 256 bits)", True),
    ("softpipe", True),
    ("Mesa DRI swrast", True),
    ("Software Rasterizer", True),
    ("llvmpipe", True),
    ("zink Vulkan 1.3(llvmpipe (LLVM 15.0.7, 256 bits) (MESA_LLVMPIPE))", True),
    ("Microsoft Basic Render Driver", True),
    ("GDI Generic", True),
    ("D3D12 (Microsoft Basic Render Driver)", True),
    ("lavapipe", True),
    ("NVIDIA GeForce RTX 4070/PCIe/SSE2", False),
    ("AMD Radeon RX 7800 XT (radeonsi, navi32, LLVM 17.0.6, DRM 3.57)", False),
    ("Intel(R) UHD Graphics 770", False),
    ("D3D12 (AMD Radeon(TM) Graphics)", False),
    ("Mesa Intel(R) Xe Graphics (TGL GT2)", False),
    ("", False),
])
def test_software_detection_matrix(renderer, expected):
    assert is_software_renderer(renderer) is expected


@pytest.mark.parametrize("vendor,renderer,family", [
    ("NVIDIA Corporation", "NVIDIA GeForce RTX 4070/PCIe/SSE2", "nvidia"),
    ("ATI Technologies Inc.", "AMD Radeon RX 7800 XT", "amd"),
    ("Microsoft Corporation", "D3D12 (AMD Radeon(TM) Graphics)", "amd"),
    ("Intel", "Intel(R) UHD Graphics 770", "intel"),
    ("Mesa", "llvmpipe", "unknown"),
])
def test_vendor_family(vendor, renderer, family):
    assert vendor_family(vendor, renderer) == family


@pytest.fixture(scope="module")
def ctx():
    pytest.importorskip("moderngl")
    from nightvision.gpu.processor import create_context
    try:
        c = create_context()
    except Exception as exc:
        pytest.skip(f"no OpenGL 3.3 context: {exc}")
    yield c
    c.release()


def test_probe_live_context_and_fbo_completeness(ctx):
    caps = probe_gpu(ctx)
    assert caps.ok, caps.problems
    assert caps.version_code >= 330 and caps.max_texture_size >= 2048
    assert caps.renderer and caps.vendor
    # Independent of probe_gpu: every required target really is a complete FBO.
    for name, comps, dtype in caps_mod.REQUIRED_TARGETS:
        assert caps_mod._check_target(ctx, name, comps, dtype) is None, name
    assert ctx.error == "GL_NO_ERROR"


def test_probe_reports_old_gl_and_never_raises():
    class Old:
        info = {"GL_VENDOR": "X", "GL_RENDERER": "Y", "GL_MAX_TEXTURE_SIZE": "4096"}
        version_code = 210
    caps = probe_gpu(Old())
    assert not caps.ok and any("330" in p for p in caps.problems)


def test_probe_reports_unrenderable_format(ctx, monkeypatch):
    real = caps_mod._check_target

    def fake(c, name, comps, dtype):
        return "boom" if name == "R8" else real(c, name, comps, dtype)
    monkeypatch.setattr(caps_mod, "_check_target", fake)
    caps = probe_gpu(ctx)
    assert not caps.ok and caps.problems == ["boom"]


def test_summary_mentions_kind():
    c = GpuCaps("V", "llvmpipe", 450, 16384, True, True)
    assert "software" in c.summary()
