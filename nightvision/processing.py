# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Enhancer selection: vendor-neutral OpenGL 3.3 GPU path, CPU fallback.

The GPU path uses only core OpenGL 3.3 (no CUDA, no vendor extensions), so it
runs on NVIDIA, AMD and integrated GPUs alike. Capability detection, shader
compilation and a numeric self-test all run at startup; any failure selects
the CPU path and the reason is returned so the UI can show it. A GPU that
fails later (context loss, driver reset) is dropped transparently.

Hybrid laptops (Windows): which GPU the context lands on is the OS per-app
preference (Settings > Graphics), honoured by the default WGL backend. We do
not use vendor exports (NvOptimusEnablement / AmdPowerXpressRequestHighPerformance),
so the user, not the app, picks the adapter.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

import numpy as np

from .core.enhance import FrameEnhancer
from .core.params import EnhanceParams
from .gpu.caps import FrameTooLargeError, GpuCaps

log = logging.getLogger(__name__)

# Tone-only parity: the LUT comes from the CPU, so only 8-bit rounding in the
# texture path may differ.
SELF_TEST_MAX_DIFF = 2
_SELF_TEST_TONE = dict(gamma=1.7, shadow_lift=0.75, black_floor=0.02,
                       contrast=1.1, highlight_protect=0.8)
# Touches every pass (local contrast, clarity, sharpen, saturation) so every
# shader is compiled and linked now, not on the first real frame.
_SELF_TEST_ALL_PASSES = dict(gamma=1.5, local_contrast=0.5, clarity=0.5,
                             sharpen=0.5, saturation=0.9)


class Enhancer(Protocol):
    def set_params(self, params: EnhanceParams) -> None: ...
    def process(self, frame: np.ndarray) -> np.ndarray: ...
    def close(self) -> None: ...


class ManagedEnhancer:
    """Enhancer with an optional GPU engine and a CPU engine behind it.

    Attributes for the UI: `backend` ('gpu' or 'cpu', reflects the engine used
    now), `caps` (GpuCaps of the probed renderer, or None), `reasons` (why the
    GPU is not in use), `fallback_events` (runtime GPU failures, in order).
    """

    def __init__(self, params: EnhanceParams, gpu=None, ctx=None,
                 caps: Optional[GpuCaps] = None,
                 reasons: Optional[list[str]] = None,
                 gl_backend: Optional[str] = None,
                 shared_ctx: bool = False):
        self._params = params
        self._gpu = gpu
        self._ctx = ctx
        self._cpu: Optional[FrameEnhancer] = None
        self._closed = False
        self.caps = caps
        self.gl_backend = gl_backend
        self.reasons: list[str] = list(reasons or [])
        self.fallback_events: list[str] = []
        self._oversize_seen: set[tuple[int, int]] = set()
        # True when the GL context belongs to a presenting sink: we must never
        # release it, and the result texture can be drawn without a readback.
        self._shared_ctx = shared_ctx

    # ---- state ------------------------------------------------------------
    @property
    def backend(self) -> str:
        return "gpu" if self._gpu is not None else "cpu"

    @property
    def params(self) -> EnhanceParams:
        return self._params

    @property
    def zero_copy(self) -> bool:
        """True while process_to_texture() can feed a sink on the same context.
        Turns False by itself if the GPU is dropped at runtime."""
        return self._gpu is not None and self._shared_ctx

    @property
    def gpu(self):
        """The live GpuFrameEnhancer, or None (CPU path / after fallback)."""
        return self._gpu

    def set_params(self, params: EnhanceParams) -> None:
        self._params = params.validated()
        if self._gpu is not None:
            self._gpu.set_params(params)
        if self._cpu is not None:
            self._cpu.set_params(params)

    # ---- processing -------------------------------------------------------
    def _cpu_engine(self) -> FrameEnhancer:
        if self._cpu is None:
            self._cpu = FrameEnhancer(self._params)
        return self._cpu

    def process(self, frame: np.ndarray) -> np.ndarray:
        if self._closed:
            raise RuntimeError("enhancer is closed")
        if self._gpu is None:
            return self._cpu_engine().process(frame)
        from .gpu.processor import GpuUsageError
        try:
            return self._gpu.process(frame)
        except FrameTooLargeError as exc:
            # Per frame: the GPU stays usable if the size later shrinks.
            size = frame.shape[1::-1]
            if size not in self._oversize_seen:
                self._oversize_seen.add(size)
                self._record(f"cpu used for oversize frame: {exc}")
            return self._cpu_engine().process(frame)
        except (ValueError, GpuUsageError):
            raise  # caller bugs, identical on the CPU path
        except Exception as exc:  # GL error, context loss, driver reset
            self._drop_gpu(f"gpu failed at runtime, switched to cpu: "
                           f"{type(exc).__name__}: {exc}")
            return self._cpu_engine().process(frame)

    def process_to_texture(self, frame: np.ndarray):
        """Zero-copy GPU result for an overlay sharing the GL context. Only
        valid while `backend == 'gpu'`; raises RuntimeError otherwise so the
        caller can switch to process(). Returns None when this frame had to be
        handled elsewhere (oversize frame, or the GPU was just dropped): the
        caller then uses process()."""
        if self._gpu is None:
            raise RuntimeError("GPU path is not active")
        from .gpu.processor import GpuUsageError
        try:
            return self._gpu.process_to_texture(frame)
        except FrameTooLargeError as exc:
            size = frame.shape[1::-1]
            if size not in self._oversize_seen:
                self._oversize_seen.add(size)
                self._record(f"cpu used for oversize frame: {exc}")
            return None
        except (ValueError, GpuUsageError):
            raise
        except Exception as exc:
            self._drop_gpu(f"gpu failed at runtime, switched to cpu: "
                           f"{type(exc).__name__}: {exc}")
            return None  # caller falls back to process() + present()

    def _record(self, msg: str) -> None:
        log.warning(msg)
        self.fallback_events.append(msg)
        self.reasons.append(msg)

    def _drop_gpu(self, msg: str) -> None:
        self._record(msg)
        self._release_gpu()

    def _release_gpu(self) -> None:
        gpu, ctx, self._gpu, self._ctx = self._gpu, self._ctx, None, None
        # (self._ctx is None for a shared context: the sink owns and releases it)
        for fn in (gpu.close if gpu is not None else None,
                   ctx.release if ctx is not None else None):
            if fn is None:
                continue
            try:
                fn()
            except Exception:  # a lost context cannot release cleanly
                log.debug("GPU cleanup failed", exc_info=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._release_gpu()


# ---- GPU bring-up -----------------------------------------------------------
def _open_context():
    from .gpu.processor import open_context  # lazy: moderngl is optional
    return open_context()


def _probe(ctx) -> GpuCaps:
    from .gpu.caps import probe_gpu
    return probe_gpu(ctx)


def _synthetic_frame() -> np.ndarray:
    h, w = 64, 96
    y, x = np.mgrid[0:h, 0:w]
    f = np.empty((h, w, 3), np.uint8)
    f[..., 0] = (x * 255 // (w - 1))
    f[..., 1] = (y * 255 // (h - 1))
    f[..., 2] = ((x // 8 + y // 8) % 2) * 120 + 8
    return f


def _self_test(gpu) -> None:
    """Raise RuntimeError if the GPU disagrees with the CPU on a fixed frame."""
    frame = _synthetic_frame()
    saved = gpu.params
    try:
        tone = EnhanceParams(**_SELF_TEST_TONE)
        gpu.set_params(tone)
        got = gpu.process(frame)
        want = FrameEnhancer(tone).process(frame)
        diff = int(np.abs(got.astype(int) - want.astype(int)).max())
        if diff > SELF_TEST_MAX_DIFF:
            raise RuntimeError(f"tone self-test differs from CPU by {diff} "
                               f"(> {SELF_TEST_MAX_DIFF})")
        gpu.set_params(EnhanceParams(**_SELF_TEST_ALL_PASSES))
        full = gpu.process(frame)
        if full.shape != frame.shape or full.dtype != np.uint8:
            raise RuntimeError("full-pipeline self-test returned a bad frame")
    finally:
        gpu.set_params(saved)


def _try_gpu(params: EnhanceParams, prefer: str,
             frame_size: Optional[tuple[int, int]], shared=None):
    """-> (enhancer|None, caps|None, gl_backend|None, reasons).
    `shared` is a caller-owned moderngl context to build on instead of opening
    a standalone one."""
    from .gpu.processor import GpuFrameEnhancer  # lazy: moderngl is optional
    ctx = gpu = caps = backend = None
    try:
        if shared is not None:
            ctx, backend = shared, "shared"
        else:
            try:
                ctx, backend = _open_context()
            except Exception as exc:
                return None, None, None, [f"gpu unavailable: {exc}"]
        caps = _probe(ctx)
        if not caps.ok:
            return None, caps, backend, [
                f"gpu unusable ({caps.summary()}): " + "; ".join(caps.problems)]
        if caps.is_software and prefer == "auto":
            return None, caps, backend, [
                f"software GL renderer {caps.renderer}; "
                "CPU path is as fast and exact"]
        if frame_size and max(frame_size) > caps.max_texture_size:
            return None, caps, backend, [
                f"frame {frame_size[0]}x{frame_size[1]} exceeds "
                f"GL_MAX_TEXTURE_SIZE {caps.max_texture_size}"]
        gpu = GpuFrameEnhancer(params, ctx=ctx, caps=caps)
        _self_test(gpu)
        enh = ManagedEnhancer(params, gpu=gpu,
                              ctx=None if shared is not None else ctx,
                              caps=caps, gl_backend=backend,
                              shared_ctx=shared is not None)
        ctx = gpu = None  # ownership moved
        return enh, caps, backend, []
    except Exception as exc:
        return None, caps, backend, [
            f"gpu self-test failed: {type(exc).__name__}: {exc}"]
    finally:
        for o in (gpu, None if shared is not None else ctx):
            if o is not None:
                try:
                    (o.close if hasattr(o, "close") else o.release)()
                except Exception:
                    log.debug("GPU cleanup failed", exc_info=True)


def create_enhancer(params: Optional[EnhanceParams] = None,
                    prefer: str = "auto",
                    frame_size: Optional[tuple[int, int]] = None,
                    ctx=None
                    ) -> tuple[ManagedEnhancer, str, list[str]]:
    """Return (enhancer, backend_name, reasons).

    prefer: 'auto' uses the GPU only on real hardware (a software GL renderer
    like llvmpipe is no faster than the CPU path and not bit-exact); 'gpu'
    also accepts software renderers (testing); 'cpu' never touches GL.
    `frame_size` is (width, height); when given, a frame beyond the GPU's
    texture limit selects the CPU up front (otherwise it is handled per frame).
    The result never raises for GPU problems: it is always a working enhancer,
    `reasons` says why the GPU is not in use, and `enhancer.caps` carries
    vendor/renderer for display. A GPU enhancer is bound to the calling
    thread; create it on the thread that will run process().
    `ctx`: an existing moderngl context owned by a presenting sink (GlOverlaySink.ctx).
    The GPU engine is built on it and `enhancer.zero_copy` is True, so the sink
    can draw `process_to_texture()` directly. WHY: on Linux a second standalone
    (EGL) context next to the sink's GLX one aborts the process (X BadAccess).
    The caller keeps ownership: close the enhancer before releasing the context,
    on the thread that owns it.
    This function has no global side effects (see runtime.configure_runtime).
    """
    if prefer not in ("auto", "gpu", "cpu"):
        raise ValueError(f"prefer must be 'auto', 'gpu' or 'cpu', got {prefer!r}")
    params = (params or EnhanceParams()).validated()
    if prefer == "cpu":
        return ManagedEnhancer(params, reasons=["cpu requested"]), "cpu", ["cpu requested"]
    enh, caps, backend, reasons = _try_gpu(params, prefer, frame_size, ctx)
    if enh is not None:
        return enh, "gpu", []
    for r in reasons:
        log.info("cpu path: %s", r)
    cpu = ManagedEnhancer(params, caps=caps, reasons=reasons, gl_backend=backend,
                          shared_ctx=ctx is not None)
    return cpu, "cpu", list(reasons)
