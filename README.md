# Night_Vision

Night_Vision is a display-side image enhancement tool for dark scenes in games
(Escape from Tarkov, Rust, and similar). It works like the "black equalizer" or
"night vision" mode found on gaming monitors, but on any monitor: it lifts
shadows and improves the visibility of dark areas and figures using a gamma /
shadow-lift tone curve, local contrast, sharpening, clarity and saturation
controls, with presets, per-game profiles and global hotkeys. The goal is to
level the playing field for players whose displays crush dark detail.

Status: working MVP (version 0.1.1). Read the verification table below before
relying on it on a given platform.

## Anti-cheat and safety

Night_Vision is an image-enhancement tool. Please read the honest limits below.

What it does NOT do (checked by a source audit; details in
[docs/WINDOWS_APIS.md](docs/WINDOWS_APIS.md)):

- It does not open other processes and does not read or write game memory.
- It does not inject DLLs or load libraries into any other process.
- It does not install hooks on games, graphics APIs or the keyboard
  (no low-level keyboard hook; hotkeys use only `RegisterHotKey` on Windows
  and an X11 key grab on X11).
- It uses no kernel driver.
- It does not read or modify game files. It only touches its own files:
  `profiles.json`, the control socket/token and `gamma_backup.json`.
- It does not synthesise keyboard or mouse input.
- It does not modify other programs' windows.
- It has no network interaction with games. The only IPC is a local control
  channel used by `nightvision ctl ...`.

What it does read: only if automatic profile switching is turned on (it is
**off by default**), it reads the focused window's title and class name about
once per second. It opens no process handle and records no process ID.

Honest limits, please read:

- No overlay or screen-filter tool can be guaranteed safe with Easy Anti-Cheat
  (Rust) or BattlEye (Escape from Tarkov). The anti-cheat vendor and the game's
  own rules decide what is allowed, and they can change.
- Check the game's rules and support pages yourself before using any overlay or
  display tool in an online game. Use Night_Vision at your own risk.
- Night_Vision is not "undetectable" and makes no attempt to be. It does not
  hide itself, rename itself or evade anything.
- A tool that only adjusts display output or draws its own window is a
  different risk class from one that reads or modifies the game, but that is a
  description of what the tool does, not a guarantee.

Lower-risk options, from lowest risk upward:

1. Your monitor's own colour/gamma/"black equalizer" settings, or your GPU
   driver's colour and gamma settings. These involve no extra software.
2. Night_Vision `display` mode: gamma ramp only, no screen capture and no
   overlay window. This is the lowest-risk in-app mode.
3. Night_Vision `overlay` mode (captures the screen and draws its own
   window) carries more surface than `display` mode, so prefer `display` mode
   for anti-cheat protected games.

## Features

- Global tone curve: gamma, shadow lift, black floor, brightness, contrast,
  highlight protection (monotonic, bounded, validated).
- Capture-based enhancement: local contrast (CLAHE), sharpening, clarity,
  saturation.
- Four exclusive modes: off, display, overlay, preview.
- Built-in presets and per-game profiles (matched by window title).
- Global hotkeys and a local `ctl` command interface (works under Wayland
  through desktop-environment shortcuts).
- Vendor-neutral GPU processing (OpenGL 3.3) with automatic fallback to an
  OpenCV CPU path.
- Safety nets: the display gamma ramp is always restored on exit, and unsafe
  ramps are rejected.
- `--monitor N` (1-based, default 1) selects which monitor is captured; the
  overlay is created at exactly that monitor's rectangle. An invalid index
  exits with code 2 and lists the monitors. CLI only, not saved in profiles.
- Clean shutdown on SIGINT/SIGTERM (SIGBREAK on Windows): the display is
  restored before exit.
- Gamma crash recovery: before the first non-original ramp is applied a
  backup `gamma_backup.json` is written atomically to the config directory
  (override with the `NIGHTVISION_GAMMA_BACKUP` environment variable). It is
  removed after a normal restore and, if a crash left it behind, validated and
  applied on the next start.
- Tone precision: the tone curve is a floating-point LUT and the final 8-bit
  write uses a static 8x8 ordered (Bayer) dither. This reduces rounding error
  in flat areas. Honest limit: it does not remove banding that comes from a
  steep shadow curve applied to an 8-bit source; those steps are in the source.
- Capture and pacing: fewer per-frame copies in the mss capture path and a
  more even render pacing, so frame times are steadier.
- A Tk control window, a headless mode, and a `--demo` synthetic scene so you
  can try it without any game.

## How it works

| Mode | What happens | Cost | Limits |
|------|--------------|------|--------|
| `off` | Nothing is applied. | none | |
| `display` | The tone curve is applied as the display gamma ramp. | zero per-frame cost, zero added latency | Global curve only (no local contrast, sharpening or clarity). |
| `overlay` | Capture the screen, enhance each frame, show it in a click-through topmost overlay. On Windows the overlay is excluded from capture to avoid a feedback loop. | per-frame GPU/CPU work | X11 overlay is experimental; not possible on Wayland (a window is used instead). |
| `preview` | Capture, enhance and show the result in a normal window (for example on a second monitor). | per-frame GPU/CPU work | |

Only one mode is active at a time.

## Platform support

"Verified" means exercised by the project's tests or manual runs on the stated
setup. "Not yet verified" means the code exists but has not been run there.

| Platform / component | Status |
|----------------------|--------|
| Core (curves, presets, profiles, parameters) and CPU enhancement path | Verified (Linux) |
| Linux X11: tone backend (XRandR apply/restore) | Verified on a headless X server; no visual readback of the result |
| Linux X11: screen capture (mss) | Verified |
| Preview window (OpenGL) | Verified (Linux) |
| X11 global hotkeys | Verified with synthesized key events |
| Control socket / `ctl` | Verified (Linux) |
| GPU path | Verified on a Mesa software renderer and on an integrated AMD Radeon GPU |
| Windows (gamma ramp, DXGI capture, `RegisterHotKey`, capture exclusion, overlay) | Not yet verified on native Windows |
| Wayland compositors (portal/PipeWire capture, gammastep proxy) | Not yet verified |
| X11 overlay mode on a real desktop/window manager | Not yet verified |
| NVIDIA, discrete AMD, Intel GPUs | Not yet verified |

Backends and fallback order:

- Tone: Windows `SetDeviceGammaRamp`; X11 XRandR gamma then XF86VidMode;
  wlroots Wayland `wlr-gamma-control` (stub, not implemented yet) then a
  `gammastep` proxy (a power-curve approximation; needs `gammastep`
  installed); GNOME/KDE Wayland have no client gamma API, so use a capture mode.
- Capture: Windows DXGI Desktop Duplication (optional `dxcam`) then GDI
  (mss); X11/XWayland via mss (XWayland sees only X clients); Wayland via
  xdg-desktop-portal ScreenCast + PipeWire through GStreamer (optional
  PyGObject; shows a user consent dialog).
- Processing: GPU OpenGL 3.3 shaders (moderngl) then the CPU OpenCV path.

## Install

Status of each route:

- Windows installer: see [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md). The
  installer is unsigned. It is built and smoke-tested by CI; not yet run on a
  real Windows PC. Downloads are on the
  [Releases page](https://github.com/IvanescuChristian/Night_Vision/releases). See
  [docs/AV_AND_SIGNING.md](docs/AV_AND_SIGNING.md) for SmartScreen and antivirus
  notes.
- Linux tarball (x86-64, no root needed): see
  [docs/LINUX_INSTALL.md](docs/LINUX_INSTALL.md). Each release lists SHA256
  checksums in `SHA256SUMS.txt` on the Releases page; verify downloads against it.
- Upgrading from an earlier version: see [docs/UPGRADING.md](docs/UPGRADING.md).
- From source (below).

### From source

Requires Python 3.10 or newer.

```
pip install -r requirements.txt
```

Optional GPU extra (OpenGL processing, GLFW windows/overlay):

```
pip install .[gpu]
```

Optional extras for specific platforms: `dxcam` for Windows DXGI capture;
PyGObject with GStreamer and PipeWire plus the xdg-desktop-portal
implementation of your desktop for Wayland capture; `gammastep` for the
wlroots tone proxy. The Tk control window needs a Python build with Tk.

## Quick start

```
python -m nightvision                                  # open the control window
python -m nightvision --demo                           # synthetic dark scene, no game needed
python -m nightvision --mode display --preset night_vision
python -m nightvision --list-backends                  # what this machine would use, and why
python -m nightvision --list-presets
python -m nightvision --headless --mode display --preset balanced
python -m nightvision --gpu cpu                        # force the CPU path (auto | gpu | cpu)
python -m nightvision --dry-run --mode display         # null tone backend, changes nothing
```

Control a running instance (also bind these in your desktop's shortcut
settings, which is the way to get hotkeys on Wayland):

```
nightvision ctl toggle
nightvision ctl next-preset
nightvision ctl preset tarkov_dark
nightvision ctl profile my_profile
nightvision ctl status
nightvision ctl quit
```

Default hotkeys (Windows and X11): `ctrl+alt+n` toggles enhancement,
`ctrl+alt+p` switches to the next preset. Profiles can define their own
hotkey.

Other options: `--fps N` (default 30), `--monitor N` (default 1),
`--config PATH`, `--profile NAME`.

## Presets

`off`, `balanced`, `night_vision`, `black_equalizer`, `clarity`,
`tarkov_dark`, `rust_night`.

In `display` mode only the tone-curve part of a preset applies. Local contrast,
sharpening and clarity need `overlay` or `preview` mode.

## Per-game profiles

A profile stores enhancement parameters, optional executable names to match,
and an optional hotkey. Profiles live in a JSON file:

- Windows: `%APPDATA%\NightVision\profiles.json`
- Linux: `$XDG_CONFIG_HOME/nightvision/profiles.json`, or
  `~/.config/nightvision/profiles.json`

Override with `--config PATH`. Automatic switching (a checkbox in the control
window) matches the focused window's title against the profile's executable
names or profile name; it never inspects the process.

## Troubleshooting

- Windows: display gamma ramps can be limited by the system. If the effect is
  weaker than expected, Windows may be clamping the ramp range (the
  `GdiIcmGammaRange` registry setting governs this). Night_Vision never writes
  the registry; use an overlay/preview mode instead or adjust the setting
  yourself.
- Wayland: GNOME and KDE expose no gamma API to clients, so `display` mode is
  unavailable there; use `preview` (window). Overlays cannot be placed on top
  of other windows from a normal client. wlroots compositors can use the
  `gammastep` proxy. Capture asks for your permission through the desktop
  portal.
- Software OpenGL: if `--list-backends` reports a software renderer, the
  automatic mode deliberately uses the CPU path (a software renderer is no
  faster). `--gpu gpu` forces it for testing.
- If the GPU path fails at runtime it falls back to the CPU path; the reason is
  shown in the diagnostics.
- The gamma ramp is restored automatically on exit. After a hard kill the next
  start restores it from `gamma_backup.json`; if the screen still looks wrong,
  restarting the display session resets it.

## Known limits

- Everything on native Windows, real Wayland compositors, NVIDIA, discrete AMD
  and Intel GPUs is unverified. See the table above.
- `display` mode is a global curve only, and Windows may limit or silently
  refuse gamma ramps (Microsoft discourages `SetDeviceGammaRamp`).
- Dithering does not remove banding from steep shadow curves on 8-bit sources.
- `--monitor N` with DXGI capture maps to output N-1; not verified on Windows.
- The high-resolution timer used for pacing on Windows has only been tested
  with a fake.
- A second running instance may "recover" the first instance's live gamma
  backup; this is untested. If the X server restarts with new CRTC ids, the
  backup restores nothing.
- No signed installer; the Windows build is unsigned and will stay so for now, so SmartScreen warns.
- No anti-cheat guarantee of any kind (see above).

## License

Night_Vision is **source-available**, not open source. It is licensed under
the PolyForm Strict License 1.0.0 with Additional Terms (see `LICENSE`):
attribution is required; modifications, derivative works and redistribution
are not permitted; commercial use is allowed only under a prior written
agreement with the licensor (including revenue share); all rights not granted
are reserved. For commercial licensing contact massini.christian@gmail.com.
The full terms are in the `LICENSE` file, which governs.
