#!/usr/bin/env python3
"""Generate the Windows version resource for PyInstaller from the single version source.

The version is read from nightvision/__init__.py (text only, nothing is imported).

    python packaging/make_version_info.py --print-version
    python packaging/make_version_info.py --output build/version_info.txt
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANY = "IvanescuChristian"
PRODUCT = "Night Vision"
EXE_NAME = "NightVision.exe"
COPYRIGHT = "Copyright (c) 2026 Christian Ivanescu"


def read_version() -> str:
    text = (ROOT / "nightvision" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("__version__ not found in nightvision/__init__.py")
    return m.group(1)


def version_tuple(version: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", version.split("+")[0].split("-")[0])][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def render(version: str, exe_name: str = EXE_NAME) -> str:
    t = version_tuple(version)
    dotted = ".".join(str(n) for n in t)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={t},
    prodvers={t},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', '{COMPANY}'),
         StringStruct('FileDescription', '{PRODUCT} - display-side image enhancement for dark scenes'),
         StringStruct('FileVersion', '{dotted}'),
         StringStruct('InternalName', 'NightVision'),
         StringStruct('LegalCopyright', '{COPYRIGHT}'),
         StringStruct('OriginalFilename', '{exe_name}'),
         StringStruct('ProductName', '{PRODUCT}'),
         StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write(path: Path, exe_name: str = EXE_NAME) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(read_version(), exe_name), encoding="utf-8")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-version", action="store_true")
    ap.add_argument("--output", type=Path)
    a = ap.parse_args()
    if a.print_version:
        print(read_version())
    if a.output:
        print(write(a.output))
