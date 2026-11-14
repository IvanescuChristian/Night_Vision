# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""GPU implementation of FrameEnhancer (OpenGL 3.3 core through moderngl).

Pipeline (each stage is skipped when its parameter is neutral):
  1. tone      per-channel lookup in a 256x1 R32F texture filled from the CPU
               build_float_lut() (unrounded curve), written to a half-float
               target. The 8-bit quantisation happens only in the last pass,
               with a static 8x8 Bayer dither (same pattern as the CPU path),
               so CPU and GPU agree to within one code on tone-only presets.
  2. stats     luma and luma^2 reduced to 1/4 resolution; blurred at sigma 6
               for clarity (same geometry as the CPU path).
  3. local     APPROXIMATION of CLAHE: local mean/variance normalisation from
               a 1/8-res blurred (mean, variance) of luma, gain clamped like
               CLAHE's clip limit. It is NOT CLAHE: there are no per-tile
               histograms or equalisation, so the look is similar in spirit
               (flat dark areas gain contrast) but not numerically equal.
  4. sharpen   small-sigma unsharp on luma at full resolution (blurs the
               tone-mapped luma, whereas the CPU blurs luma after clarity).
  5. saturation scaled chroma around luma (Y'CbCr-like), not LAB.

All GL work must happen on the thread that created the enhancer.
"""
from __future__ import annotations

import math
import os
import sys
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.curves import build_float_lut, build_lut
from ..core.params import EnhanceParams
from ..x11_errors import preserve_x_error_handler
from . import shaders
from .caps import FrameTooLargeError, GpuCaps, probe_gpu

try:  # moderngl is an optional extra
    import moderngl
except ImportError:  # pragma: no cover - exercised only without the extra
    moderngl = None

_ID_LUT = np.arange(256, dtype=np.uint8)
_CLARITY_SIGMA = 6.0      # at 1/4 res, mirrors core/enhance.py
_SHARPEN_SIGMA = 1.2      # full res, mirrors core/enhance.py
_LC_GAIN_MAX = 3.0        # CLAHE clipLimit used by the CPU path
_LC_TARGET_SD = 0.12


class GpuUsageError(RuntimeError):
    """Programming error (closed enhancer, wrong thread). Not a GPU fault, so
    callers must not treat it as a reason to fall back to the CPU."""


class GlError(RuntimeError):
    """A GL call left an error flag set (out of memory, context loss, ...)."""


def _backend_order(prefer_egl: Optional[bool]) -> list[str]:
    if not (sys.platform.startswith("linux") or sys.platform == "win32"):
        return ["default"]
    if prefer_egl is None:
        # EGL first only when no display server is reachable: a process that
        # has created an EGL context and later opens a GLX window (the overlay
        # presenter) is aborted by Xlib's default error handler (BadAccess on
        # GLXMakeCurrent; reproduced on a virtual X server with Mesa), so when a display
        # exists the platform-native backend must be the one used.
        # Windows: WGL follows the OS per-app GPU preference (Settings >
        # Graphics), which is what a hybrid-laptop user expects.
        prefer_egl = (sys.platform.startswith("linux")
                      and not os.environ.get("DISPLAY")
                      and not os.environ.get("WAYLAND_DISPLAY"))
    return ["egl", "default"] if prefer_egl else ["default", "egl"]


def open_context(prefer_egl: Optional[bool] = None):
    """Standalone GL 3.3 context -> (ctx, backend_name). Raises RuntimeError.

    Backends are tried in order and the first success wins: creating a GLX
    context in a process that already holds an EGL one can abort the whole
    process (Xlib's default error handler exits), so never create both."""
    if moderngl is None:
        raise RuntimeError("moderngl is not installed (pip install nightvision[gpu])")
    errors = []
    for backend in _backend_order(prefer_egl):
        kw = {"backend": "egl"} if backend == "egl" else {}
        try:
            # GLX context creation clobbers Tk's Xlib error handler.
            with preserve_x_error_handler():
                return moderngl.create_standalone_context(require=330, **kw), backend
        except Exception as exc:  # driver errors come in many types
            errors.append(f"{backend}: {exc}")
    raise RuntimeError("no OpenGL 3.3 context: " + "; ".join(errors))


def create_context(prefer_egl: Optional[bool] = None):
    return open_context(prefer_egl)[0]


@dataclass(frozen=True)
class _State:
    """Swapped as one reference so process() never sees mixed settings."""
    params: EnhanceParams
    lut: np.ndarray  # float32 (256,)
    tone_identity: bool
    lc: float
    clarity: float
    sharpen: float
    saturation: float

    @property
    def neutral(self) -> bool:
        return (self.tone_identity and self.lc == 0.0 and self.clarity == 0.0
                and self.sharpen == 0.0 and self.saturation == 1.0)


def _make_state(params: EnhanceParams) -> _State:
    p = params.validated()
    return _State(p, build_float_lut(p), bool(np.array_equal(build_lut(p), _ID_LUT)), p.local_contrast,
                  p.clarity * 0.8, p.sharpen, p.saturation)


class _Target:
    """A texture plus the framebuffer that renders into it."""
    __slots__ = ("tex", "fbo")

    def __init__(self, tex, fbo):
        self.tex, self.fbo = tex, fbo

    def release(self):
        self.fbo.release()
        self.tex.release()


class GpuFrameEnhancer:
    def __init__(self, params: Optional[EnhanceParams] = None, ctx=None,
                 caps: Optional[GpuCaps] = None):
        if moderngl is None:
            raise RuntimeError("moderngl is not installed (pip install nightvision[gpu])")
        self._owns_ctx = ctx is None
        self._ctx = ctx if ctx is not None else create_context()
        self._thread = threading.get_ident()
        self.caps = caps if caps is not None else probe_gpu(self._ctx)
        self._state = _make_state(params or EnhanceParams())
        self._uploaded: Optional[_State] = None
        self._closed = False
        self.allocations = 0  # textures created so far; stable across same-size frames
        self._size: Optional[tuple[int, int, int]] = None
        self._input = None
        self._targets: dict[str, _Target] = {}
        self._progs: dict[str, object] = {}
        self._vaos: dict[str, object] = {}
        self._lut_tex = None
        self._out_tex = None  # last result texture (RGBA8)
        self._park: Optional[_Target] = None

    # ---- public API -------------------------------------------------------
    @property
    def params(self) -> EnhanceParams:
        return self._state.params

    def set_params(self, params: EnhanceParams) -> None:
        # Any thread: only builds CPU data and swaps a reference. The GL
        # upload of the LUT happens inside process() on the owning thread.
        self._state = _make_state(params)

    def process(self, frame: np.ndarray) -> np.ndarray:
        self._check_thread()
        st = self._state
        self._check_frame(frame)
        if st.neutral:
            return frame.copy()  # same shortcut as the CPU path
        self._ctx.error  # reading clears stale flags so only this frame's errors count
        tex = self._run(frame, st)
        h, w, ch = frame.shape
        data = tex.read(alignment=1)
        err = self._ctx.error  # moderngl does not raise on GL errors by itself
        if err != "GL_NO_ERROR":
            raise GlError(err)
        out = np.frombuffer(data, np.uint8).reshape(h, w, 4)
        return np.ascontiguousarray(out if ch == 4 else out[:, :, :3])

    def process_to_texture(self, frame: np.ndarray):
        """Run the pipeline and return the RGBA8 moderngl texture (no readback),
        for zero-copy presentation by a GL overlay sharing this context.

        Texture row 0 is the TOP image row (uploaded as-is), so sampling with
        v=0 gives the top; GL window coordinates have y up, so a presenting
        quad must flip v. The texture belongs to the enhancer: it is
        overwritten by the next call and released by close(). Channel order is
        BGR(A) as in the input; swizzle (texture.swizzle = 'BGRA') to present.
        """
        self._check_thread()
        self._check_frame(frame)
        return self._run(frame, self._state)

    def close(self) -> None:
        if self._closed:
            return
        self._check_thread()
        self._closed = True
        if self._park is not None:
            self._park.fbo.use()  # keep a live FBO bound while releasing
        for t in self._targets.values():
            t.release()
        self._targets.clear()
        for o in (*self._vaos.values(), *self._progs.values(), self._input,
                  self._lut_tex):
            if o is not None:
                o.release()
        self._vaos.clear()
        self._progs.clear()
        self._input = self._lut_tex = self._out_tex = None
        if self._park is not None:
            self._park.release()
            self._park = None
        if self._owns_ctx:
            with preserve_x_error_handler():
                self._ctx.release()

    # ---- internals --------------------------------------------------------
    def _check_thread(self) -> None:
        if self._closed:
            raise GpuUsageError("GpuFrameEnhancer is closed")
        if threading.get_ident() != self._thread:
            raise GpuUsageError(
                "GpuFrameEnhancer must be used on the thread that created it "
                "(OpenGL contexts are bound to one thread)")

    def _check_frame(self, frame: np.ndarray) -> None:
        if frame.ndim != 3 or frame.shape[2] not in (3, 4) or frame.dtype != np.uint8:
            raise ValueError("expected HxWx3 or HxWx4 uint8 frame")
        limit = self.caps.max_texture_size
        if limit and max(frame.shape[:2]) > limit:
            raise FrameTooLargeError(
                f"frame {frame.shape[1]}x{frame.shape[0]} exceeds "
                f"GL_MAX_TEXTURE_SIZE {limit}")

    def _alloc(self, size, components, dtype="f1", linear=True):
        tex = self._ctx.texture(size, components, dtype=dtype)
        tex.repeat_x = tex.repeat_y = False  # clamp: blurs must not wrap edges
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR) if linear else \
            (moderngl.NEAREST, moderngl.NEAREST)
        self.allocations += 1
        return tex

    def _target(self, name, size, components, dtype="f1") -> _Target:
        t = self._targets.get(name)
        if t is None:
            tex = self._alloc(size, components, dtype)
            t = self._targets[name] = _Target(tex, self._ctx.framebuffer([tex]))
        return t

    def _program(self, name, frag):
        if name not in self._progs:
            prog = self._ctx.program(vertex_shader=shaders.VERTEX, fragment_shader=frag)
            self._progs[name] = prog
            self._vaos[name] = self._ctx.vertex_array(prog, [])
        return self._progs[name]

    @staticmethod
    def _set(prog, **uniforms):
        for k, v in uniforms.items():
            if k in prog:  # the compiler may drop unused uniforms
                prog[k].value = v

    def _pass(self, name, frag, target: _Target, textures, **uniforms):
        prog = self._program(name, frag)
        for unit, tex in enumerate(textures):
            tex.use(unit)
        target.fbo.use()
        self._ctx.viewport = (0, 0, *target.tex.size)
        self._set(prog, **uniforms)
        self._vaos[name].render(moderngl.TRIANGLES, vertices=3)

    def _blur(self, src: _Target, tmp: _Target, sigma: float):
        """Separable blur of an RG stats target; the result ends in `src`."""
        radius = min(shaders.BLUR_MAX_RADIUS, max(1, int(math.ceil(3 * sigma))))
        w, h = src.tex.size
        self._pass("blur", shaders.BLUR, tmp, [src.tex], src=0, sigma=sigma,
                   radius=radius, stepv=(1.0 / w, 0.0), from_color=False)
        self._pass("blur", shaders.BLUR, src, [tmp.tex], src=0, sigma=sigma,
                   radius=radius, stepv=(0.0, 1.0 / h), from_color=False)

    def _run(self, frame: np.ndarray, st: _State):
        h, w, ch = frame.shape
        if self._size != (w, h, ch):
            self._reset_buffers((w, h, ch))
        if self._uploaded is not st:
            self._lut_tex.write(np.ascontiguousarray(st.lut, np.float32).tobytes(), alignment=1)
            self._uploaded = st
        self._input.write(np.ascontiguousarray(frame).data, alignment=1)

        # Pass 1: tone (skipped when identity; downstream reads the input).
        if st.tone_identity:
            tone_tex = self._input
        else:
            t = self._target("tone", (w, h), 4, "f2")
            self._pass("tone", shaders.TONE, t, [self._input, self._lut_tex],
                       src=0, lut=1)
            tone_tex = t.tex

        qsize = (max(1, w // 4), max(1, h // 4))
        esize = (max(1, w // 8), max(1, h // 8))
        need_q = st.clarity > 0.0 or st.lc > 0.0
        q = None
        if need_q:
            q = self._target("q", qsize, 2, "f2")
            self._pass("down4", shaders.DOWN, q, [tone_tex], src=0,
                       texel=(1.0 / w, 1.0 / h), off=1.0, from_color=True)
        coarse = q
        if st.lc > 0.0:
            e = self._target("e", esize, 2, "f2")
            self._pass("down2", shaders.DOWN, e, [q.tex], src=0,
                       texel=(1.0 / qsize[0], 1.0 / qsize[1]), off=0.0,
                       from_color=False)
            tile = esize[0] / max(2, st.params.clahe_tiles)
            self._blur(e, self._target("e_tmp", esize, 2, "f2"),
                       min(12.0, max(1.5, tile / 2.0)))
        if st.clarity > 0.0:
            # Blur q in place only after the local pass has consumed it.
            self._blur(q, self._target("q_tmp", qsize, 2, "f2"), _CLARITY_SIGMA)
        fine = None
        if st.sharpen > 0.0:
            ft = self._target("fine_tmp", (w, h), 2, "f2")
            fine = self._target("fine", (w, h), 2, "f2")
            radius = min(shaders.BLUR_MAX_RADIUS, max(1, int(math.ceil(3 * _SHARPEN_SIGMA))))
            self._pass("blur", shaders.BLUR, ft, [tone_tex], src=0,
                       sigma=_SHARPEN_SIGMA, radius=radius,
                       stepv=(1.0 / w, 0.0), from_color=True)
            self._pass("blur", shaders.BLUR, fine, [ft.tex], src=0,
                       sigma=_SHARPEN_SIGMA, radius=radius,
                       stepv=(0.0, 1.0 / h), from_color=False)

        out = self._target("out", (w, h), 4)
        spare = tone_tex
        local_tex = self._targets["e"].tex if st.lc > 0.0 else spare
        coarse_tex = q.tex if st.clarity > 0.0 else spare
        fine_tex = fine.tex if fine is not None else spare
        self._pass("compose", shaders.COMPOSE, out,
                   [tone_tex, local_tex, coarse_tex, fine_tex],
                   tone=0, lc_tex=1, clarity_tex=2, sharpen_tex=3, lc_amount=st.lc,
                   lc_gain_max=_LC_GAIN_MAX, lc_target_sd=_LC_TARGET_SD,
                   clarity_amount=st.clarity, sharpen_amount=st.sharpen,
                   saturation=st.saturation)
        self._out_tex = out.tex
        return out.tex

    def _park_fbo(self) -> None:
        """Bind a tiny permanent FBO so no released target stays bound."""
        if self._park is None:
            tex = self._ctx.texture((1, 1), 4)
            self._park = _Target(tex, self._ctx.framebuffer([tex]))
        self._park.fbo.use()

    def _reset_buffers(self, size):
        # WHY: releasing the currently bound FBO leaves a dangling binding that
        # moderngl restores on the next framebuffer() call, which raises
        # GL_INVALID_OPERATION; process() would then misreport a GPU fault.
        self._park_fbo()
        for t in self._targets.values():
            t.release()
        self._targets.clear()
        if self._input is not None:
            self._input.release()
        w, h, ch = size
        self._input = self._alloc((w, h), ch)
        if self._lut_tex is None:
            self._lut_tex = self._alloc((256, 1), 1, dtype="f4", linear=False)
            self._uploaded = None
        self._size = size
