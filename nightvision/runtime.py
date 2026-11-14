# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Process-wide runtime tuning. Call configure_runtime() once from the app
entrypoint; library code (create_enhancer etc.) never changes globals."""
from __future__ import annotations

import contextlib
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


def _load_winmm():
    """The winmm library on Windows, None elsewhere or when it cannot be loaded."""
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("winmm")
    except (OSError, AttributeError):
        return None


@contextlib.contextmanager
def timer_resolution(ms: int = 1):
    """Request a finer system timer for the duration of the block (Windows only).

    WHY: the default ~15.6 ms tick makes every sleep/wait overshoot, which shows
    up as uneven frame pacing. timeBeginPeriod/timeEndPeriod are documented winmm
    calls and must be paired; since Windows 10 2004 the effect is scoped to this
    process. Yields True when a request is active, False when it is a no-op
    (other platforms, or the call failed)."""
    winmm = _load_winmm()
    ok = False
    if winmm is not None:
        try:
            ok = winmm.timeBeginPeriod(int(ms)) == 0  # TIMERR_NOERROR
        except Exception:  # noqa: BLE001
            ok = False
    try:
        yield ok
    finally:
        if ok:
            try:
                winmm.timeEndPeriod(int(ms))
            except Exception:  # noqa: BLE001
                pass
