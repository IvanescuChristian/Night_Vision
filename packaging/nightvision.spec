# PyInstaller spec: one-dir build (never one-file), no UPX, no obfuscation.
# Build from the repository root:  pyinstaller packaging/nightvision.spec --noconfirm
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is provided by PyInstaller
sys.path.insert(0, str(ROOT / "packaging"))
import make_version_info  # noqa: E402

datas, binaries, hiddenimports = [], [], []
# GPU/capture libraries ship native code that import analysis cannot see.
for pkg in ("moderngl", "glcontext", "glfw", "mss"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

win = sys.platform == "win32"

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "nightvision_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "psutil"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821


def make_exe(name, console, exe_file):
    version_file = None
    if win:
        version_file = str(make_version_info.write(ROOT / "build" / f"version_{name}.txt", exe_file))
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        strip=False,
        upx=False,
        console=console,
        version=version_file,
    )


# Windows: NightVision.exe is windowed (shortcuts use it); NightVisionCLI.exe has a
# console for --help, ctl and diagnostics. Both share one COLLECT (one folder).
# Linux: a single console binary.
exes = [make_exe("NightVision", not win, "NightVision.exe")]
if win:
    exes.append(make_exe("NightVisionCLI", True, "NightVisionCLI.exe"))

coll = COLLECT(  # noqa: F821
    *exes,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NightVision",
)
