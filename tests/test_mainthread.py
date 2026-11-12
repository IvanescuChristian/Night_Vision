# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import threading
import time

import pytest

from nightvision.mainthread import DispatcherError, MainThreadDispatcher


def _in_thread(fn):
    out = {}

    def run():
        try:
            out["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            out["e"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def test_inline_on_owner_thread():
    d = MainThreadDispatcher()
    assert d.call(threading.current_thread) is threading.current_thread()


def test_cross_thread_call_runs_on_owner():
    d = MainThreadDispatcher()
    t, out = _in_thread(lambda: d.call(threading.current_thread))
    deadline = time.monotonic() + 2
    while t.is_alive() and time.monotonic() < deadline:
        d.run_pending()
        time.sleep(0.001)
    assert out["v"] is threading.main_thread() or out["v"] is d.owner_thread


def test_exception_propagates_to_caller():
    d = MainThreadDispatcher()

    def boom():
        raise ValueError("x")
    t, out = _in_thread(lambda: d.call(boom))
    while t.is_alive():
        d.run_pending()
        time.sleep(0.001)
    assert isinstance(out["e"], ValueError)


def test_timeout_when_owner_never_pumps_and_call_is_cancelled():
    d = MainThreadDispatcher()
    ran = []
    t, out = _in_thread(lambda: d.call(lambda: ran.append(1), timeout=0.1))
    t.join(2)
    assert isinstance(out["e"], TimeoutError)
    assert d.run_pending() == 0 and not ran  # stale call is not executed later


def test_shutdown_fails_pending_and_rejects_new():
    d = MainThreadDispatcher()
    t, out = _in_thread(lambda: d.call(lambda: 1, timeout=10))
    time.sleep(0.1)
    d.shutdown()
    t.join(2)
    assert not t.is_alive() and isinstance(out["e"], DispatcherError)
    t2, out2 = _in_thread(lambda: d.call(lambda: 1))
    t2.join(2)
    assert isinstance(out2["e"], DispatcherError)
    with pytest.raises(DispatcherError):
        d.call(lambda: 1)  # also rejected on the owner thread


def test_bounded_queue():
    d = MainThreadDispatcher(maxsize=2)
    threads = [_in_thread(lambda: d.call(lambda: 1, timeout=5)) for _ in range(2)]
    time.sleep(0.1)
    t, out = _in_thread(lambda: d.call(lambda: 1, timeout=5))
    t.join(2)
    assert isinstance(out["e"], DispatcherError)
    d.run_pending()
    for th, o in threads:
        th.join(2)
        assert o["v"] == 1


def test_run_pending_rejected_off_owner():
    d = MainThreadDispatcher()
    t, out = _in_thread(d.run_pending)
    t.join(2)
    assert isinstance(out["e"], DispatcherError)
