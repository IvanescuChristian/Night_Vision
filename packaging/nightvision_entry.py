"""PyInstaller entry point (the package uses relative imports, so it cannot be the script itself)."""
import os
import sys

# WHY: the windowed Windows build (console=False) starts with sys.stdout and
# sys.stderr set to None; any print() or logging write would then raise. Point
# them at the null device so output is silently discarded instead of crashing.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

from nightvision.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
