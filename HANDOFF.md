# Continuing development

## State of the MVP

Version 0.1.0. Implemented: parameter model, tone curve and presets, CPU
enhancement path, OpenGL 3.3 GPU path with probe/self-test/fallback, tone
backends (Windows, X11, wlroots proxy, null), capture backends (DXGI, mss,
portal/PipeWire, synthetic), two-thread pipeline, GLFW window/overlay sink with
a main-thread dispatcher, hotkeys, local control channel, profiles with
title-based auto-switch, Tk control window and CLI.

Verified on Linux: core, CPU path, X11 XRandR apply/restore (headless X
server, no visual readback), mss capture, GL preview window, X11 hotkeys via
synthesized key events, control socket, GPU path on a Mesa software renderer
and on an integrated AMD Radeon GPU.

NOT VERIFIED: anything on native Windows (gamma ramp, DXGI, RegisterHotKey,
capture exclusion, overlay); real Wayland compositors (portal/PipeWire capture,
gammastep proxy); X11 overlay mode on a real desktop/window manager; NVIDIA,
discrete AMD and Intel GPUs.

## Running the tests

```
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests marked `gui` need a display: run them on a desktop session or on a
headless display server. Without `DISPLAY` they are skipped automatically.
Resource/lifetime tests are marked `slow`.

## Repository layout

```
nightvision/   package (see ARCHITECTURE.md section 3 for the module map)
tests/         unit, integration and resource tests
measurements/  raw benchmark output
README.md  ARCHITECTURE.md  UPDATE_TRACKING.md  HANDOFF.md  LICENSE
pyproject.toml  requirements.txt  requirements-dev.txt
```

## Open items and next steps

1. `wlr-gamma-control`: implement through Wayland protocol bindings (currently
   a stub; wlroots falls back to the gammastep proxy).
2. Layer-shell overlay for wlroots and KDE (true topmost click-through overlay
   on Wayland).
3. X11 overlay: avoid the capture feedback loop (for example capture exclusion
   or capturing another output) and verify on real window managers.
4. Windows verification: gamma ramp, DXGI capture, `RegisterHotKey`, capture
   exclusion and overlay behaviour, including the `GdiIcmGammaRange` limit.
5. DXGI: validate the optional `dxcam` path, multi-monitor selection and
   frame-pacing.
6. Per-monitor gamma on Windows (currently a single global ramp).
7. Packaging: installer and/or a frozen executable.
8. Exact CLAHE on the GPU (currently a local-contrast approximation).

## Per-vendor GPU test checklist

Run on each of: NVIDIA, AMD discrete, Intel, AMD integrated, software renderer.

1. `python -m nightvision --list-backends` shows the GL renderer and the
   processing path in use (GPU or CPU, with the reason if CPU).
2. `python -m nightvision --demo --gpu gpu` runs for 60 seconds without errors.
3. The shader self-test passes: no fallback reason is reported.
4. Compare `--gpu cpu` and `--gpu gpu` visually on the demo; differences should
   be limited to the local-contrast approximation.
5. Run `python -m pytest -q tests/test_gpu.py tests/test_gpu_caps.py tests/test_gl_shared.py`.
6. Optional resource test on real hardware GL:
   `NV_TEST_HW_GL=1 python -m pytest -q tests/test_resources.py -s`.
7. Check driver-reset handling: after any GPU failure, `fallback_events`
   records it and processing continues on the CPU path.
8. Hybrid laptops: set the per-application GPU preference in the OS graphics
   settings for the Python interpreter, then repeat steps 1 to 3.
9. Record the results:

| Field | Value |
|-------|-------|
| Date | |
| OS / driver version | |
| GPU model and vendor | |
| Renderer string (`--list-backends`) | |
| Path chosen (GPU/CPU) | |
| Self-test passed | |
| Demo 60 s clean | |
| GPU vs CPU visual match | |
| Targeted tests pass | |
| Resource test (720p / 1080p ms) | |
| Notes | |

## Windows checklist

1. `python -m nightvision --list-backends`: tone backend `win32`, capture
   `dxgi` (or `mss` fallback).
2. `--mode display --preset night_vision` visibly changes the screen; mode off
   and app exit restore it. Test with the `GdiIcmGammaRange` value at its
   default and note any clamping.
3. `--mode overlay` with a windowed/borderless game or the demo: overlay is
   topmost and click-through, and is not captured by the capturer (no feedback).
4. Hotkeys `ctrl+alt+n` and `ctrl+alt+p` work, including when another
   application has focus; a conflicting registration is reported.
5. `nightvision ctl status` reaches the instance (localhost + token file).
6. Auto-switch matches a profile by window title; no process handle is opened.
7. Optional `dxcam` installed: capture works and frame rate meets the target.

## Wayland checklist

1. Test on at least one wlroots compositor and on GNOME and KDE.
2. `--list-backends` reports the expected tone/capture/present choices and
   reasons for each compositor.
3. Capture: the portal consent dialog appears and PipeWire frames arrive
   (GStreamer and PyGObject installed); denied consent fails cleanly.
4. `--mode preview` shows the enhanced window; `--mode overlay` is refused
   with a window fallback.
5. wlroots: with `gammastep` installed, `--mode display` approximates the curve
   and exit restores the original; without it, a clear reason is shown.
6. Bind `nightvision ctl toggle` and `nightvision ctl next-preset` in the
   compositor's shortcut settings and confirm they work.
