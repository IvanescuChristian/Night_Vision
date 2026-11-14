# Architecture

## 1. Goals and constraints

Goals:

- Make dark scenes in games more visible on any monitor (shadow lift, local
  contrast, sharpening, clarity, saturation), comparable to a gaming monitor's
  black-equalizer mode.
- Be safe by construction: image enhancement only, nothing that touches a game.
- Be cheap: a display tone curve costs nothing per frame; capture-based modes
  are bounded in CPU/GPU, memory and thread use.
- Work on Windows, X11 and (as far as the platform allows) Wayland, with GPU
  acceleration on any OpenGL 3.3 driver and a CPU fallback.

Constraints:

- No injection, memory access, API hooking, game-file changes, game networking
  or low-level keyboard hooks.
- Never leave the user's display in a bad state: ramps are validated and always
  restored.
- Optional dependencies (GPU, DXGI, PipeWire) must never break import or the
  CPU path.

## 2. Approaches considered

| Approach | Verdict | Reason |
|----------|---------|--------|
| Display gamma ramp / LUT | Adopted (default) | Zero per-frame cost and latency, uses a documented OS/driver facility. Global curve only, so no local contrast or sharpening. |
| Capture, enhance, click-through overlay (or window) | Adopted (optional) | The only way to get local contrast, sharpening, clarity and saturation. Costs a capture and a processing pass per frame; needs feedback avoidance. |
| Shader injection into the game | Rejected | Requires injecting into the game process: unsafe with anti-cheat and against the safety goals. |
| Windows Magnification API colour matrix | Rejected | Linear colour matrix only; cannot express a non-linear shadow lift. |

Decision: a hybrid. `display` mode (tone curve as the gamma ramp) is the
zero-cost default; `overlay` and `preview` modes add capture and enhancement
for the effects a global curve cannot produce. Both share one parameter model
so presets and profiles apply to either.

## 3. Module map

```
nightvision/
  __main__.py        CLI: modes, diagnostics, demo, headless, `ctl`
  app.py             NightVisionApp: modes, presets, profiles, services, owner-thread action queue
  pipeline.py        EnhancePipeline: capture thread + render thread, latest-wins handoff
  processing.py      create_enhancer / ManagedEnhancer: GPU-or-CPU selection, self-test, runtime fallback
  sinks.py           FrameSink protocol, NullSink, CallbackSink, LatestFrameSink
  mainthread.py      MainThreadDispatcher: run thread-bound window calls on the main thread
  runtime.py         OpenCV thread cap, glibc malloc arena cap
  profiles.py        Profile store (JSON), default config path, profile matching
  foreground.py      Focused-window title lookup (title only) and profile matching
  control.py         Local control server/client (Unix socket or localhost TCP + token)
  platform_detect.py OS / session / desktop / wlroots detection (pure, injectable)
  core/
    params.py        EnhanceParams: immutable, clamped, validated
    presets.py       Built-in presets
    curves.py        build_curve / build_lut / lut_to_ramp / ramp_is_safe
    enhance.py       FrameEnhancer: exact CPU path (LUT, CLAHE, unsharp, clarity, saturation)
  gpu/
    caps.py          GL capability probe, software-renderer detection, vendor family
    shaders.py       GLSL 3.30 shader sources
    processor.py     GpuFrameEnhancer: shader passes, reused textures, texture output
  backends/
    tone/            base (validation, restore-on-exit), win32, x11 (XRandR, XF86VidMode),
                     wayland (wlr-gamma stub, gammastep proxy), null, select
    capture/         base, dxgi, mss_capture, portal (xdg-desktop-portal + PipeWire),
                     synthetic, select
    present/         overlay_gl (GLFW + moderngl window/overlay), win32_affinity, select
  hotkeys/           base, win32 (RegisterHotKey), x11 (XGrabKey)
  ui/main_window.py  Tk control window
tests/               unit, integration and resource tests
```

## 4. Data and contracts

Tone path (global curve):

```
EnhanceParams --build_lut()--> uint8[256] LUT --lut_to_ramp()--> uint16 (3, N) ramp --> ToneBackend.apply()
```

- `EnhanceParams` (frozen dataclass): gamma, shadow_lift, black_floor,
  brightness, contrast, highlight_protect (tone fields); local_contrast,
  clahe_tiles, sharpen, clarity (capture-only); saturation. `validated()`
  clamps to fixed ranges and replaces NaN/inf/garbage with defaults;
  `from_dict` ignores unknown keys.
- `build_curve` produces a monotonic curve: gamma, a shadow lift anchored at
  black and white, contrast/brightness around 0.5, soft highlight compression,
  then a black floor. `ramp_is_safe` and the backend's `validate_ramp` reject
  non-monotonic, near-black, washed-out or flat ramps.
- `ToneBackend`: `available()` (never raises), `unavailable_reason()`, read and
  write of the ramp; stores the original ramp and restores it on `restore()`,
  at interpreter exit (registered handler) and at app shutdown.

Frame path:

- `CaptureSource`: `available()`, `unavailable_reason()`, `open()`, `grab()`
  (newest HxWx4 uint8 BGRA frame or None), `close()`, `size`. All three
  lifecycle calls run on the capture thread because several backends are
  thread-affine.
- Enhancer interface (`FrameEnhancer` on CPU, `GpuFrameEnhancer` on GPU):
  `set_params(params)`, `process(frame) -> frame`, `close()`. The GPU enhancer
  also offers `process_to_texture` so a GL sink can present without readback.
  `ManagedEnhancer` wraps both, exposes `backend`, `zero_copy`,
  `fallback_reason` and `fallback_events`.
- `FrameSink`: `open()`, `present(frame)`, `close()`, all on the render thread.

## 5. Backend selection and fallback order

| Layer | Windows | X11 | Wayland (wlroots) | Wayland (GNOME/KDE) |
|-------|---------|-----|-------------------|---------------------|
| Tone | SetDeviceGammaRamp | XRandR CRTC gamma, then XF86VidMode | wlr-gamma-control (stub, unimplemented), then gammastep proxy | none (no client gamma API) -> use capture modes |
| Capture | DXGI duplication (optional dxcam), then GDI (mss) | mss | xdg-desktop-portal ScreenCast + PipeWire (GStreamer), then mss (XWayland clients only) | same as wlroots |
| Present | overlay (excluded from capture) or window | window; overlay forced = experimental | window only | window only |

In every case a null/dry-run tone backend is the final fallback. Windows note:
the system may limit ramp range through the `GdiIcmGammaRange` registry value;
the project never writes the registry. The gammastep proxy can only express a
power curve plus brightness, so the shadow-lift shape is approximated.

Processing: GPU OpenGL 3.3 core shaders through moderngl (any conformant
driver), else the CPU OpenCV path. The GPU path performs a capability probe
(renderable targets, texture-size limit), a startup self-test against the CPU
result, and falls back to the CPU at runtime on GL failure. `auto` uses the CPU
on software renderers. The CPU path has exact CLAHE; the GPU path uses a
local-contrast approximation of it.

Hotkeys: Windows `RegisterHotKey`, X11 `XGrabKey`; Wayland has none from a
client, so bind `nightvision ctl ...` in desktop settings. The control channel
is local only: a Unix socket (mode 0600, in the per-user runtime directory) on
POSIX, 127.0.0.1 plus a random token stored in a per-user file on Windows; one
JSON line per message, 4 KiB cap, fixed command set.

## 6. Concurrency and lifecycle

```
owner/main thread (UI, window create/poll/destroy, action queue drain, dispatcher pump)
   |  start/stop, set_params
   v
EnhancePipeline
   capture thread:  CaptureSource.open/grab/close  --(size-1 latest-wins slot)-->
   render thread:   Enhancer + FrameSink.open/present/close
                    window calls on the render thread's behalf --> main-thread dispatcher
hotkey thread  \
control thread  >--> bounded action queue --> drained on the owner thread
```

- The handoff slot holds one frame; a newer frame replaces an unconsumed one,
  so memory never grows and latency stays low.
- Stop uses an event, bounded joins (under 2 s) and daemon threads, so a hung
  native call cannot block exit. Each resource is created and closed on the
  thread that uses it. Errors in either thread are captured and surfaced to the app.
- GLFW windows are created, polled and destroyed on the main thread through
  the dispatcher; rendering happens on the render thread with a shared GL
  context (zero-copy: the enhanced texture is drawn directly).
- X error handler: Xlib has a single process-global handler that GLX context
  creation overwrites (Tk's is lost, and the default one exits the process).
  All GL/GLFW init, create, release and terminate calls therefore run inside
  `x11_errors.preserve_x_error_handler()`. GL work on the render thread briefly
  swaps the handler while Tk runs on the main thread; the window is tiny and
  the previous handler is restored immediately.
- Capture is capped at twice the target fps. OpenCV threads are capped at
  min(4, cores/2); glibc malloc arenas are limited; GPU textures are reused;
  neutral stages are skipped.
- The gamma ramp is restored on mode change, app shutdown and interpreter exit.

## 7. GPU portability policy

- Target OpenGL 3.3 core only; no vendor extensions or vendor SDKs.
- Probe capabilities and software renderers before use; verify every render
  target format the pipeline needs is actually renderable.
- Self-test the shaders against the CPU path at startup; on failure use the CPU
  path and record the reason.
- On a runtime GL error, drop to the CPU path and keep the event in
  `fallback_events`.
- Frames larger than the maximum texture size use the CPU path.
- `auto` prefers the CPU on software renderers; `--gpu gpu` forces them for
  testing.

## 8. Safety-by-construction checklist

- [x] No process injection or library loading into other processes.
- [x] No game memory reads or writes.
- [x] No hooks on game or graphics APIs.
- [x] No game file access or modification.
- [x] No network interaction with games; control IPC is local only.
- [x] No low-level keyboard hooks; only OS hotkey registration.
- [x] Foreground detection reads the window title only; no process handle.
- [x] Display ramps validated; unsafe ramps rejected; original ramp restored.
- [x] No registry writes.
- [x] Control channel accepts a fixed command set and executes nothing from
      message content.

## 9. Verification status

| Area | Status |
|------|--------|
| Core, parameters, curves, presets, profiles | Verified (Linux) |
| CPU enhancement path | Verified (Linux) |
| Tone backend X11 XRandR apply/restore | Verified on a headless X server (no visual readback) |
| mss capture | Verified (Linux) |
| GL preview window | Verified (Linux) |
| X11 hotkeys | Verified via synthesized key events |
| Control socket | Verified (Linux) |
| GPU path | Verified on a Mesa software renderer and on an integrated AMD Radeon GPU |
| Windows: gamma ramp, DXGI, RegisterHotKey, capture exclusion, overlay | NOT VERIFIED (no native Windows run) |
| Wayland compositors: portal/PipeWire capture, gammastep proxy | NOT VERIFIED |
| X11 overlay mode on a real desktop/window manager | NOT VERIFIED |
| NVIDIA GPUs | NOT VERIFIED |
| Discrete AMD GPUs | NOT VERIFIED |
| Intel GPUs | NOT VERIFIED |
| `--monitor N`, clean shutdown on signals, gamma crash recovery, float LUT + dither, capture allocation and pacing changes | Verified by tests on Linux under a virtual display (software GL) |
| Windows `dxcam` output index for `--monitor N` (N-1) | NOT VERIFIED |
| Windows `timeBeginPeriod`/`timeEndPeriod` pacing | Only tested with a fake |
| SIGBREAK handling, `WDA_EXCLUDEFROMCAPTURE` | NOT VERIFIED on Windows |

## 10. Measurements

Recorded 2026-11-13 on one Linux development machine (16 logical cores).
Only two renderers were measured: the Mesa software renderer (llvmpipe) and an
integrated AMD Radeon GPU. NVIDIA, discrete
AMD, Intel and native Windows were not measured. Treat the numbers as an
order-of-magnitude guide, not a guarantee for other machines.

<!-- MEASUREMENTS:BEGIN -->
### Method

- Opt-in test `tests/test_resources.py` (slow). Every configuration runs in a
  fresh process that applies the shipped runtime configuration (OpenCV thread
  cap, malloc arena limit), so numbers reflect the application defaults.
- Steady state: 24 s per configuration (4 s warm-up excluded), target 30 fps,
  1280x720 frames, `night_vision`-like parameters, output to a null sink.
- Churn: 30 warm-up start/stop cycles, then 20 measured start/stop cycles of
  the pipeline; growth is the median RSS of the last 5 cycles minus the median
  RSS of the last 5 warm-up cycles, and slope is the median pairwise slope.
- Metrics: achieved fps, average process time per frame (ms), process CPU %
  (sum over all cores, so it can exceed 100), RSS steady slope (MB/min, median
  pairwise slope of 2 s medians), RSS growth over the churn run, OS thread
  count before / during / after the run, and the time `stop()` needed to join
  both threads. Thread counts must return to the baseline exactly.
- Memory bounds asserted: steady slope under 60 MB/min and growth under 40 MB;
  churn slope under 2 MB/cycle and growth under 40 MB. Thread assertions are
  exact. A leaked 720p RGBA texture set is several MB per cycle and exceeds
  both churn bounds.
- Hardware configuration: the shipped path, a GLFW window created on the main
  thread with the GPU enhancer sharing its GL context and drawing the result
  without a readback.

### Steady state and churn (1280x720, 30 fps target)

| Config | fps | avg process ms | process CPU % | RSS slope MB/min | churn growth MB (slope MB/cycle) | threads base/during/after | stop join s |
|--------|-----|----------------|---------------|------------------|----------------------------------|---------------------------|-------------|
| synthetic capture + CPU | 30.0 | 8.5 | 54.4 | 0.20 | 0.1 (0.001) | 34 / 36 / 34 | 0.001 |
| screen capture (mss) + CPU | 30.0 | 11.5 | 77.4 | 0.31 | 0.1 (0.004) | 34 / 36 / 34 | 0.001 |
| synthetic + GPU, Mesa software renderer (llvmpipe), readback | 30.0 | 7.9 | 142.9 | 0.00 | 2.5 (-0.07) | 31 / 66 / 31 | 0.005 |
| synthetic + GPU, integrated AMD Radeon, zero-copy window | 30.0 | 4.0 | 7.7 | 0.00 | 11.6 (0.57) | 31 / 38 / 31 | 0.066 |

Notes: the software GL renderer starts one thread per core while
active (31 to 66 threads) and releases them on stop. Across three repeat runs
of the default configurations on an otherwise idle machine, churn slopes were
-0.07 to 0.24 MB/cycle for CPU and software-GL configurations, and one run
under heavy host load reached 1.58 MB/cycle, which is why the churn bound is
2 MB/cycle. The 11.6 MB hardware churn growth is a driver cache sawtooth (the
slope stays under 0.6 MB/cycle and the steady slope is flat).

### Per-frame processing time (median ms, 1280x720 and 1920x1080)

CPU uses the shipped capped OpenCV thread count. GPU "readback" is `process()`
(result copied to system memory); GPU "zero-copy" is `process_to_texture()`
followed by a GL finish (result stays on the GPU). Medians of 100 frames after
20 warm-up frames, synthetic scene.

| Preset | Resolution | CPU | GPU llvmpipe readback | GPU llvmpipe zero-copy | GPU integrated AMD readback | GPU integrated AMD zero-copy |
|--------|------------|-----|-----------------------|------------------------|-----------------------------|------------------------------|
| night_vision | 1280x720 | 16.9 | 7.3 | 6.8 | 1.1 | 0.7 |
| tarkov_dark | 1280x720 | 17.1 | 17.7 | 17.5 | 1.3 | 1.0 |
| night_vision | 1920x1080 | 35.8 | 12.3 | 12.0 | 1.9 | 1.2 |
| tarkov_dark | 1920x1080 | 37.5 | 36.2 | 33.4 | 2.5 | 1.9 |

Observations: the software GL renderer is roughly on par with the CPU path for
the heavier preset, which is why automatic mode prefers the CPU on software
renderers. The integrated GPU is an order of magnitude faster than the CPU
path in these runs. GPU times are measured with a finish call, so they include
GPU execution on this driver, but a driver may still pipeline work that a
real display path overlaps differently.

### Display (tone) mode cost

Display mode applies a gamma ramp once per change and costs nothing per
frame. Building the ramp from a preset and applying it on the null backend
took 0.13 ms median (0.71 ms worst of 200 applications). Real backends add the
operating system call.

### Not measured

NVIDIA, discrete AMD and Intel GPUs, native Windows (DXGI capture, gamma ramp,
capture exclusion), and real Wayland compositors. Figures above are from a
single Linux machine with 16 logical cores.
<!-- MEASUREMENTS:END -->

### Quality pass measurements (software GL on a virtual display)

These figures were measured with the Mesa software renderer (llvmpipe) on a
virtual display. They are NOT representative of real GPUs; they only show the
direction of each change on that setup. Several runs were single and noisy.

- Tone precision: 8x8 block-mean error against a floating-point reference
  dropped from 0.17-0.21 codes to 0.013-0.014 (CPU) and 0.023-0.040 (GPU).
  The dither does not remove step banding from steep shadow curves on an 8-bit
  source (largest shadow LUT step: 18 codes for `night_vision`, 26 for
  `rust_night`); the gradient max-jump metric was unchanged or slightly higher.
- CPU vs GPU agreement: at most 1 code for tone-only presets; for presets with
  local contrast the mean difference is at most 3.5 and the maximum at most 35
  (the tested bound).
- mss capture: allocations per 1920x1080 grab went from 3 frames to 1; median
  grab time 1.74 ms to 0.87 ms.
- CPU fallback tone step: 4.6 ms to 2.1 ms, bit-identical output.
- Pacing (CPU enhancer, synthetic 1920x1080, 10 s): `balanced` at 60 fps
  target went from 53.0 fps (frame-time std 4.4 ms) to 58.7 fps (std 1.6 ms);
  at 30 fps the rate is unchanged (about 30 fps, CPU 108% to 93%).
  `night_vision` on the CPU path is compute-bound and did not improve (23.6 to
  22.2 fps at 30; 26.9 to 22.8 at 60; noisy single runs, unexplained).
- Median frame times, before to after (ms): CPU `balanced` 15.8 to 13.6; CPU
  `night_vision` 34.6 to 36.1; GPU (llvmpipe) `balanced` 6.6 to 7.2; GPU
  (llvmpipe) `night_vision` 11.9 to 12.9.
- Resource test: mss + CPU peak RSS growth 8.2 MB (ceiling 40 MB).
- Tests: 270 passed / 2 skipped before the pass, 354 passed / 2 skipped after.

Statistics note: `frames_dropped` counts captures superseded before rendering.
Capture runs at twice the target rate, so about half are dropped by design; the
UI therefore shows `frames_late` (render iterations that missed their
deadline) instead.

## 11. Known limitations

- `display` mode is a global curve only; no local contrast, sharpening or
  clarity.
- `wlr-gamma-control` is a stub; wlroots tone control currently depends on the
  gammastep proxy, which approximates the curve.
- GNOME/KDE Wayland offer no client gamma API; overlays cannot be positioned
  topmost from a regular client (a layer-shell overlay is future work).
- The X11 overlay may be captured by the grabber (feedback) and is experimental.
- The GPU path approximates CLAHE; the CPU path is exact.
- With some Mesa hardware drivers, a standalone (windowless) GL context
  created on a non-main thread crashed the process at thread exit in testing.
  The shipped overlay/preview path (window and context created on the main
  thread, shared with the enhancer) is unaffected; headless GPU processing on
  that driver is not recommended.
- Windows gamma ramp is a single global ramp (no per-monitor control yet) and
  may be range-limited by the system.
- Everything on native Windows and real Wayland compositors is unverified.
- A second running instance would "recover" the first instance's live
  `gamma_backup.json` and apply it; untested.
- If the X server restarts and assigns new CRTC ids, crash recovery restores
  nothing.
- The `dxcam` output index for `--monitor N` is mapped as N-1 and is unverified
  on Windows.
- The Windows timer-resolution calls are only tested with a fake.
- Dithering does not remove banding that originates from steep shadow curves on
  8-bit sources.
- `--monitor N` is CLI only; it is not persisted and has no UI control.
