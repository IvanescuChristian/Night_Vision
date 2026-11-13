# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import tracemalloc

import numpy as np
import pytest

from nightvision.backends.capture.mss_capture import MssCapture

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def cap():
    c = MssCapture(monitor=1, region={"left": 0, "top": 0, "width": 640, "height": 360})
    c.open()
    try:
        yield c
    finally:
        c.close()


def test_grab_allocates_about_one_frame(cap):
    cap.grab()
    frame_bytes = 640 * 360 * 4
    peaks = []
    tracemalloc.start()
    try:
        for _ in range(10):
            before, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            fr = cap.grab()
            _, peak = tracemalloc.get_traced_memory()
            peaks.append(peak - before)
            del fr
    finally:
        tracemalloc.stop()
    # one frame-sized buffer (mss' own snapshot) plus small bookkeeping; the old
    # path peaked at ~3 frames
    assert sorted(peaks)[len(peaks) // 2] <= 1.2 * frame_bytes


def test_frame_contiguous_writable_bgra(cap):
    fr = cap.grab()
    assert fr.shape == (360, 640, 4) and fr.dtype == np.uint8
    assert fr.flags.c_contiguous and fr.flags.writeable


def test_consecutive_frames_are_independent(cap):
    a = cap.grab()
    b = cap.grab()
    assert not np.shares_memory(a, b)
    ref_b = b.copy()
    a[:] = 123
    assert np.array_equal(b, ref_b)
    ref_a = a.copy()
    b[:] = 7
    assert np.array_equal(a, ref_a)


def test_contents_match_mss_reference(cap):
    mss = pytest.importorskip("mss")
    with (getattr(mss, "MSS", None) or mss.mss)() as sct:
        shot = sct.grab({"left": 0, "top": 0, "width": 640, "height": 360})
        ref = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(360, 640, 4).copy()
    fr = cap.grab()
    # the virtual screen may change between grabs; same dims and mostly equal on a static desktop
    assert fr.shape == ref.shape
    assert (fr == ref).mean() > 0.9
