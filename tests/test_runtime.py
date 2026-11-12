# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import cv2
import pytest

from nightvision import runtime


@pytest.fixture(autouse=True)
def restore_threads():
    before = cv2.getNumThreads()
    yield
    cv2.setNumThreads(before)


def test_default_threads_policy(monkeypatch):
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: 16)
    assert runtime.default_cpu_threads() == 4
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: 4)
    assert runtime.default_cpu_threads() == 2
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: 1)
    assert runtime.default_cpu_threads() == 1
    monkeypatch.setattr(runtime.os, "cpu_count", lambda: None)
    assert runtime.default_cpu_threads() == 1


def test_configure_sets_cv2_threads_and_is_idempotent():
    info = runtime.configure_runtime(cpu_threads=2)
    assert info["cpu_threads"] == 2 and cv2.getNumThreads() == 2
    info2 = runtime.configure_runtime(cpu_threads=2)
    assert info2 == info and cv2.getNumThreads() == 2
    runtime.configure_runtime()
    assert cv2.getNumThreads() == runtime.default_cpu_threads()


def test_threads_never_below_one():
    assert runtime.configure_runtime(cpu_threads=0)["cpu_threads"] == 1


def test_mallopt_failure_is_tolerated(monkeypatch):
    def nope(*a, **k):
        raise OSError("no libc.so.6")
    monkeypatch.setattr(runtime.ctypes, "CDLL", nope)
    assert runtime.configure_runtime(cpu_threads=1)["malloc_arena_limited"] is False
