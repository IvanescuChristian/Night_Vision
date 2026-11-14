#!/usr/bin/env python3
"""Write a sha256sum-compatible checksum file (works on Windows and Linux).

    python packaging/checksums.py -o SHA256SUMS.txt file1 file2 ...
"""
import argparse
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args()
    lines = [f"{sha256(p)}  {p.name}\n" for p in sorted(a.files, key=lambda p: p.name)]
    a.output.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("".join(lines), end="")
