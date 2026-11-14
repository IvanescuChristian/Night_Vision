# Continuing development

## State of the MVP

Version 0.1.0. Implemented: parameter model, tone curve and presets, CPU
enhancement path, OpenGL 3.3 GPU path with probe/self-test/fallback, tone
backends (Windows, X11, wlroots proxy, null), capture backends (DXGI, mss,
portal/PipeWire, synthetic), two-thread pipeline, GLFW window/overlay sink with
a main-thread dispatcher, hotkeys, local control channel, profiles with
title-based auto-switch, Tk control window and CLI.

The quality pass (monitor selection, signal shutdown, gamma crash recovery, float
LUT with dither, capture and pacing) is implemented and tested on Linux under a
virtual display only.

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

## X error handler rule

Xlib's error handler is process-global and Tk's gets replaced by GLX context
creation, after which any stray X error exits the process. Any code that
creates GL contexts or touches GLFW must go through
`nightvision.x11_errors.preserve_x_error_handler()`.

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
7. Packaging (see "Remaining packaging steps" below).
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

## Real-hardware test checklist (quality pass)

Nothing below has been run on real hardware yet.

1. Windows: `--monitor 1` and `--monitor 2` on a multi-monitor PC; the overlay
   covers exactly that monitor; an invalid index exits with code 2 and lists
   monitors.
2. Windows, optional `dxcam`: confirm output index N-1 matches monitor N.
3. Windows timer: `timeBeginPeriod(1)`/`timeEndPeriod` on the render thread;
   check frame pacing at 60 fps and that nothing else is affected.
4. Windows gamma backup: start display mode, kill the process hard, start again
   and confirm the ramp is restored from `gamma_backup.json`; try
   `NIGHTVISION_GAMMA_BACKUP`; test starting a second instance (expected risk:
   it recovers the first instance's live backup).
5. Windows signals: Ctrl+C and Ctrl+Break (SIGBREAK) from a console close
   cleanly and restore the display.
6. Windows capture exclusion: `WDA_EXCLUDEFROMCAPTURE` keeps the overlay out of
   the capture (no feedback loop) on Windows 10 2004+ and 11.
7. Wayland: SIGTERM/SIGINT shutdown, and gamma recovery with the gammastep
   proxy.
8. NVIDIA, discrete AMD, Intel: the per-vendor checklist above plus a visual
   check that the dither is not visible and the CPU/GPU outputs match.
9. X11: restart the X server while in display mode and note whether the
   original ramp is restored (new CRTC ids are a known gap).

## Remaining packaging steps

1. Linux artifact (tarball with install script), built with PyInstaller
   one-dir: DONE. Built and smoke-tested on Linux (tarball, `--help`,
   `--list-backends`, headless run); never run on Windows.
2. Windows CI workflow (windows-latest): tests, PyInstaller one-dir (no UPX,
   version metadata, GUI and console executables), Inno Setup installer,
   SHA256 checksums: DONE as `.github/workflows/release.yml`, validated with
   actionlint. It has never run on a real Windows runner.
3. Release on a version tag (installer, Linux artifact and checksums attached
   to a GitHub Release, tag checked against `nightvision/__init__.py` and
   `pyproject.toml`): DONE in the same workflow, validated with actionlint
   only; the release job has never run.
4. Code signing: DECIDED, Windows builds ship unsigned (no certificate purchase);
   docs/AV_AND_SIGNING.md and docs/WINDOWS_SETUP.md explain SmartScreen, checksum
   and false-positive steps. Nothing left to do here.
5. First real Windows run of the CI-built installer (docs/WINDOWS_SETUP.md).
