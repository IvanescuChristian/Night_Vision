# Installing Night Vision on Linux (prebuilt tarball)

The Linux release is a tarball containing a self-contained PyInstaller
one-folder build (Python, OpenCV, NumPy and the GL/capture libraries are
included) plus a per-user install script. No root access is needed.

## 1. Download and verify

From the project's GitHub Releases page download:

- `NightVision-<version>-linux-x86_64.tar.gz`
- `SHA256SUMS.txt`

Verify the download (this checks only the files present in the folder):

```
sha256sum -c --ignore-missing SHA256SUMS.txt
```

It must print `OK` for the tarball. If it does not, delete the file and
download it again. The checksums only prove the file was not corrupted or
swapped after the release was published; the build is not code-signed.

## 2. Install

```
tar -xzf NightVision-<version>-linux-x86_64.tar.gz
cd NightVision-<version>-linux-x86_64
./install.sh
```

This copies the app to `~/.local/opt/NightVision`, links the command
`~/.local/bin/nightvision` and adds a launcher entry
(`~/.local/share/applications/nightvision.desktop`). Make sure
`~/.local/bin` is on your `PATH`.

To upgrade, download the newer tarball and run its `install.sh`; it stops the
running copy from `~/.local/opt/NightVision`, replaces the old files as a whole
and keeps `~/.config/nightvision`. See [UPGRADING.md](UPGRADING.md).

## 3. Run

```
nightvision                       # control window
nightvision --list-backends       # what this session supports and why
nightvision --headless --preset night_vision --mode display
nightvision --help
```

See the README for modes, presets and profiles.

## 4. Uninstall

```
~/.local/opt/NightVision/uninstall.sh            # keeps your settings
~/.local/opt/NightVision/uninstall.sh --purge    # also deletes ~/.config/nightvision
```

(the copy in the extracted folder works too), or by hand: delete `~/.local/opt/NightVision`, `~/.local/bin/nightvision` and
`~/.local/share/applications/nightvision.desktop`. Without `--purge`, settings under
`~/.config/nightvision` are kept.

## System requirements

- x86-64 Linux with an X11 session (or XWayland). glibc at least as new as the
  build machine: release tarballs are built on Ubuntu 22.04 (glibc 2.35).
- `libGL.so.1` from the system (Debian/Ubuntu package `libgl1`, Fedora
  `mesa-libGL`). It is intentionally not bundled so the bundle uses your
  GPU driver. Without it the GPU path is unavailable.
- Core X11 libraries (`libxcb`, present on any desktop install).
- Everything else, including Tcl/Tk for the control window, is in the bundle.

## Known limits

- Wayland: tone (gamma) control is not available on GNOME or KDE; use a
  capture mode. Capture and overlay support there is not yet verified (see the
  README platform table).
- The X11 overlay mode is experimental and unverified on real desktops.
- The tarball is not code-signed and there is no auto-update.
- Only x86-64 is built. Other architectures: install from source (README).
- The bundle is large (about 230 MB unpacked) because it carries Python and
  OpenCV.
