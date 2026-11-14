# Update tracking

Checkpointed development log, newest first.

## 2026-11-14

- 0.1.1: first published release (the 0.1.0 tag predates CI test fixes; no release was built for it).
- Checkpoint, packaging: two-executable Windows build (GUI and console) with an
  Inno Setup installer, Linux tarball with install and uninstall scripts, and
  the CI and release workflow (tests, both builds, smoke tests that start the
  frozen app, tag checked against both version sources, generated release
  notes). actionlint reports no findings. Verified on Linux only; the Windows
  build, installer and release job have not run. Headless and demo runs now
  exit 0 with a "stopped by SIGTERM" message on SIGTERM/SIGINT (previously the
  demo run exited 143).

- Quality pass (committed): `--monitor N` (capture and overlay rectangle follow
  the chosen monitor), clean shutdown on SIGINT/SIGTERM/SIGBREAK, gamma crash
  recovery (`gamma_backup.json`, `NIGHTVISION_GAMMA_BACKUP`), float tone LUT
  with ordered dither, fewer capture copies, hybrid render wait and Windows
  timer resolution, `frames_late` statistic. Tests: 270 passed / 2 skipped
  before, 354 passed / 2 skipped after.
- Non-invasive audit: no process access, injection, hooks, drivers, game-file
  reads or input synthesis found. The exact API list is in docs/WINDOWS_APIS.md.
- Documentation pass: README anti-cheat and safety section (honest limits),
  install routes, known limits; ARCHITECTURE verification status, quality-pass
  measurements and limitations; new docs/WINDOWS_APIS.md, docs/WINDOWS_SETUP.md
  and docs/AV_AND_SIGNING.md; HANDOFF checklists refreshed.
- UI: the diagnostics line shows `frames_late` instead of `frames_dropped`,
  which counts about half of all captures by design.

## 2026-11-13

- X error handler (fixed): Xlib has one process-global error handler. Tk
  installs its own on the first tk.Tk(); creating a GLX context (moderngl,
  default backend) replaces it with Xlib's default, which calls exit(1). A
  later harmless BadWindow inside Tk then ended the whole process silently
  (seen as a test run dying after test_identity_equal; a risk in the real app,
  which combines the Tk UI and GL contexts). Probing showed only GLX
  standalone-context creation changes the handler (EGL, GLFW init/window/
  terminate and context release do not). Added nightvision/x11_errors.py
  (preserve_x_error_handler) and wrapped every GL/GLFW init, create, release
  and terminate site in gpu/processor.py and backends/present/overlay_gl.py.
  tests/test_x11_errors.py covers the entry points and the original crash
  sequence.
- Measurements recorded: steady-state, 20-cycle churn and per-frame figures
  for the CPU path, the Mesa software renderer (llvmpipe) and an integrated
  AMD Radeon GPU are in ARCHITECTURE.md
  section 10. Churn memory bound relaxed from 1 to 2 MB/cycle after one noisy
  run (idle runs stayed under 0.25 MB/cycle). The hardware measurement uses
  the shipped window path; a standalone GL context created on a non-main
  thread crashes at thread exit on that driver.
- Documentation: README, ARCHITECTURE, HANDOFF and this log added. Measurements
  section filled (see above).
- Main-thread window handling (done): added a main-thread dispatcher so
  window creation, event polling and destruction run on the main thread while
  rendering stays on the render thread; the pipeline services it while
  joining. Fixed: native window creation/destruction now on the main thread;
  render thread only draws, which resolves a teardown crash on hardware
  drivers.
- Shared GL context: the GPU enhancer now shares the presentation window's GL
  context so enhanced frames can be drawn without a readback.
- GPU hardening: added a capability probe (renderable target formats, texture
  size limit, software-renderer detection), a startup shader self-test against
  the CPU path, and a runtime fallback to the CPU path with recorded events.
  Fixed the automatic mode to prefer the CPU on software renderers.

## 2026-11-12

- App, UI and CLI: added the application core (modes, presets, profiles,
  automatic profile switching by window title), a Tk control window, and the
  command line (modes, diagnostics, demo, headless, `ctl`).
- Present, hotkeys and control: added the GLFW + moderngl window/overlay sink,
  Windows capture-exclusion helper, `RegisterHotKey` and X11 key-grab hotkeys,
  and the local control server and client.
- Capture, pipeline and resource test: added mss, DXGI (optional), portal and
  synthetic capture sources; the two-thread pipeline with a size-1 latest-wins
  handoff; runtime tuning (thread and allocator caps); and an opt-in resource
  and lifetime test.
- GPU path: added the OpenGL 3.3 shader enhancer and its shader sources.
- Tone backends: added Windows, X11 (XRandR, XF86VidMode), Wayland
  (`wlr-gamma-control` stub, gammastep proxy) and null backends with ramp
  validation and restore-on-exit.
- Core: added the parameter model, tone curve, LUT/ramp conversion, safety
  checks, presets and the CPU frame enhancer.
- Scaffold and license: added the package layout, build configuration,
  requirements, and the PolyForm Strict 1.0.0 license with additional terms.
