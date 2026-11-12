# Night_Vision

Night_Vision is a display-side image enhancement tool for dark scenes in games
(Escape from Tarkov, Rust, and similar). It works like the "black equalizer" or
"night vision" mode found on gaming monitors, but on any monitor: it lifts
shadows and improves the visibility of dark areas and figures using a gamma /
shadow-lift tone curve, local contrast, sharpening, clarity and saturation
controls, with presets, per-game profiles and global hotkeys. The goal is to
level the playing field for players whose displays crush dark detail.

Status: working MVP (version 0.1.0). Read the verification table below before
relying on it on a given platform.

## Safety: this is image enhancement only

Night_Vision is **not** a cheat, hack or malware, and it is built so that it
cannot behave like one:

- No process injection and no DLL/shared-library loading into any other process.
- No reading or writing of game memory.
- No hooking of game or graphics APIs.
- No modification of game files.
- No network interaction with games. The only IPC is a local control channel
  (Unix socket with 0600 permissions on POSIX systems; 127.0.0.1 plus a
  random token file on Windows) used by `nightvision ctl ...`.
- No low-level keyboard hooks. Hotkeys use only the operating system's hotkey
  registration (`RegisterHotKey` on Windows, an X11 key grab on X11).
- Foreground-game detection (optional, for automatic profile switching) reads
  only the focused window's title. It never opens a handle to the game process.

It is equivalent in kind to changing your monitor's colour settings, your GPU
control panel's gamma/brightness, or running a screen-capture overlay.

Honest caveat: some games' rules or anti-cheat policies may still restrict
overlays or display gamma changes. You are responsible for checking the rules
of the game you play. `display` mode (a plain tone curve applied through the
display gamma ramp) is the most conservative option because it draws nothing
and captures nothing.

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

Other options: `--fps N` (default 30), `--config PATH`, `--profile NAME`.

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
- The gamma ramp is restored automatically on exit; if a hard kill ever leaves
  the screen looking wrong, restarting the display session resets it.

## License

Night_Vision is **source-available**, not open source. It is licensed under
the PolyForm Strict License 1.0.0 with Additional Terms (see `LICENSE`):
attribution is required; modifications, derivative works and redistribution
are not permitted; commercial use is allowed only under a prior written
agreement with the licensor (including revenue share); all rights not granted
are reserved. For commercial licensing contact christian.ivanescu@isoskills.fi.
The full terms are in the `LICENSE` file, which governs.
