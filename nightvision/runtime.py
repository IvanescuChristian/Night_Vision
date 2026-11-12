# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Process-wide runtime tuning. Call configure_runtime() once from the app
entrypoint; library code (create_enhancer etc.) never changes globals."""
from __future__ import annotations

import ctypes
import os
import sys

import cv2

_M_ARENA_MAX = -8  # glibc malloc.h


def default_cpu_threads() -> int:
    return min(4, ((os.cpu_count() or 2) // 2) or 1)


def _limit_malloc_arenas(max_arenas: int = 2) -> bool:
    """glibc only. WHY: each thread gets its own malloc arena, and RSS grew
    on repeated pipeline restarts (new worker threads each time) in our churn
    test; two arenas keep that bounded at negligible contention cost."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL("libc.so.6")
        return bool(libc.mallopt(_M_ARENA_MAX, max_arenas))
    except (OSError, AttributeError):  # musl or no libc.so.6
        return False


def configure_runtime(cpu_threads: int | None = None) -> dict:
    """Cap OpenCV's thread pool and limit malloc arenas. Idempotent.

    WHY the cap: OpenCV's default pool used ~250% CPU at 720p30 in our
    resource run; a display tool should leave cores to the game it enhances.
    """
    n = default_cpu_threads() if cpu_threads is None else max(1, int(cpu_threads))
    cv2.setNumThreads(n)
    return {"cpu_threads": n, "malloc_arena_limited": _limit_malloc_arenas()}
