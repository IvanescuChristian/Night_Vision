# Windows APIs used by Night_Vision

This is the complete list of Windows APIs Night_Vision's own code calls, found
by a source audit (no process-opening, memory, injection, hook, driver or
input-synthesis APIs are used). Microsoft's documentation is linked for each.
Nothing here is a guarantee about how any anti-cheat treats the tool; see the
README section "Anti-cheat and safety".

Status: none of this has been run on native Windows yet.

## APIs called by Night_Vision

| API | Library | Used for | Docs |
|-----|---------|----------|------|
| SetWindowDisplayAffinity (WDA_EXCLUDEFROMCAPTURE) | user32 | Exclude our own overlay window from capture, to avoid a feedback loop | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity |
| GetDeviceGammaRamp / SetDeviceGammaRamp | gdi32 | Read and set the display gamma ramp (display mode) | https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getdevicegammaramp , https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-setdevicegammaramp |
| GetDC / ReleaseDC | user32 | Device context of the primary display for the gamma calls | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdc |
| RegisterHotKey / UnregisterHotKey | user32 | System-wide hotkeys (no keyboard hook) | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey |
| GetMessageW / PostThreadMessageW, GetCurrentThreadId | user32, kernel32 | Hotkey message loop and stopping it | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmessagew |
| GetForegroundWindow | user32 | Focused window (auto-profile only, off by default) | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow |
| GetWindowTextW | user32 | Title of the focused window (auto-profile only) | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowtextw |
| GetClassNameW | user32 | Class of the focused window (auto-profile only) | https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclassnamew |
| timeBeginPeriod / timeEndPeriod | winmm | Finer timer resolution on the render thread for frame pacing | https://learn.microsoft.com/en-us/windows/win32/api/timeapi/nf-timeapi-timebeginperiod , https://learn.microsoft.com/en-us/windows/win32/api/timeapi/nf-timeapi-timeendperiod |

## Capture APIs

Default on Windows: the `mss` library, which uses GDI: GetWindowDC,
CreateCompatibleDC, CreateDIBSection, BitBlt (SRCCOPY | CAPTUREBLT),
EnumDisplayMonitors, GetMonitorInfoW, EnumDisplayDevicesW, GetSystemMetrics.
`mss` also calls `SetProcessDpiAwareness(2)` (shcore) or `SetProcessDPIAware`
for its own process; Night_Vision's own code does not.

- BitBlt: https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-bitblt
- EnumDisplayMonitors: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumdisplaymonitors
- SetProcessDpiAwareness: https://learn.microsoft.com/en-us/windows/win32/api/shellscalingapi/nf-shellscalingapi-setprocessdpiawareness

Optional: `dxcam`, which uses DXGI Desktop Duplication (documented, Windows 8
and newer): https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api
It is not installed by default and has never been executed in this project.
With `--monitor N`, DXGI output index N-1 is used; this mapping is unverified
on Windows.

## Caveats from Microsoft's documentation

- SetWindowDisplayAffinity: the window must belong to the calling process (we
  only use it on our own overlay). WDA_EXCLUDEFROMCAPTURE needs Windows 10
  version 2004 or newer; older versions behave like WDA_MONITOR (a black box).
  It only works with desktop composition (DWM) enabled.
- SetDeviceGammaRamp: Microsoft strongly recommends not using this API. Windows
  heuristics may silently refuse a ramp, other programs can overwrite it, it is
  reset on some display events, behaviour under HDR is undefined, and a call
  can take up to about 200 ms. Night_Vision validates ramps, restores the
  original on exit and keeps a crash-recovery backup.
- timeBeginPeriod: since Windows 10 version 2004 the effect is no longer
  global (per process), and every call must be paired with timeEndPeriod.
- RegisterHotKey: registers a system-wide hotkey and fails if the combination
  is already registered by another program.
- GetWindowText on another process's window reads the caption without opening
  that process.

## What is not used

OpenProcess, ReadProcessMemory, WriteProcessMemory, VirtualAllocEx,
CreateRemoteThread, DLL injection, SetWindowsHookEx (including
WH_KEYBOARD_LL), kernel drivers, SendInput, keybd_event, mouse_event, reads of
game files, registry writes.

## Linux equivalents

- Tone: XRandR `XRRSetCrtcGamma`, then XF86VidMode; on wlroots Wayland a
  `gammastep` proxy.
- Hotkeys: X11 `XGrabKey`.
- Focused window (auto-profile): `_NET_ACTIVE_WINDOW`, `_NET_WM_NAME`,
  `WM_CLASS`; no process ID.
- Capture: `mss` via XCB (`XShmGetImage` / `XGetImage`); Wayland via
  xdg-desktop-portal ScreenCast and PipeWire.
- No input synthesis (no XTest, no xdotool).
