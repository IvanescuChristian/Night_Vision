# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Monitor selection: index -> rect mapping, errors, and the overlay covering the capture."""
import pytest

from nightvision import __main__ as cli
from nightvision.app import AppError, NightVisionApp
from nightvision.backends.capture import mss_capture
from nightvision.backends.capture import select as cap_select
from nightvision.backends.capture.base import CaptureError
from nightvision.platform_detect import PlatformInfo

INFO = PlatformInfo("linux", "x11", "", False, True)
MONS = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]


@pytest.fixture
def two_monitors(monkeypatch):
    monkeypatch.setattr(cap_select, "list_monitors", lambda: list(MONS))


def test_index_maps_to_rect(two_monitors):
    assert cap_select.monitor_rect(1) == (0, 0, 1920, 1080)
    assert cap_select.monitor_rect(2) == (1920, 0, 1280, 1024)


@pytest.mark.parametrize("bad", [0, 3, -1])
def test_invalid_index_lists_monitors(two_monitors, bad):
    with pytest.raises(CaptureError) as e:
        cap_select.monitor_rect(bad)
    msg = str(e.value)
    assert f"monitor {bad} not found" in msg
    assert "1: 1920x1080 at (0,0)" in msg and "2: 1280x1024 at (1920,0)" in msg


def test_list_monitors_skips_virtual_screen(monkeypatch):
    class FakeSct:
        monitors = [{"left": 0, "top": 0, "width": 3200, "height": 1080},
                    {"left": 0, "top": 0, "width": 1920, "height": 1080},
                    {"left": 1920, "top": 0, "width": 1280, "height": 1024}]
        closed = False

        def close(self):
            FakeSct.closed = True

    class FakeMss:
        @staticmethod
        def MSS():
            return FakeSct()

    monkeypatch.setattr(mss_capture, "_mss", FakeMss)
    assert mss_capture.list_monitors() == MONS
    assert FakeSct.closed


def test_selected_monitor_reaches_capture_backends(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":9")  # never opened: only constructed and probed
    src, _ = cap_select.select_capture_backend(INFO, monitor=2)
    assert src.name == "mss" and src._monitor == 2
    src, _ = cap_select.select_capture_backend(INFO)
    assert src._monitor == 1  # default keeps the old behaviour
    assert cap_select._make("dxgi", INFO, 3)._output == 2


def _app(tmp_path, **kw):
    return NightVisionApp(tmp_path / "p.json", info=INFO, dry_run=True,
                          enable_hotkeys=False, enable_control=False, **kw)


def test_rect_reaches_overlay_sink_factory(tmp_path, monkeypatch, two_monitors):
    calls = []

    def fac(**kw):
        calls.append(kw)
        return object()

    monkeypatch.setattr("nightvision.app.select_present_backend",
                        lambda info, prefer=None: (fac, "overlay", []))
    app = _app(tmp_path, monitor=2)
    try:
        app._sink_factory_for("overlay")()
        assert calls[-1]["monitor_rect"] == (1920, 0, 1280, 1024)
        monkeypatch.setattr("nightvision.app.select_present_backend",
                            lambda info, prefer=None: (fac, "window", []))
        app._sink_factory_for("preview")()
        assert "monitor_rect" not in calls[-1]  # preview window keeps its own size
    finally:
        app.shutdown()


def test_invalid_monitor_is_an_apperror(tmp_path, monkeypatch, two_monitors):
    app = _app(tmp_path, monitor=5)
    try:
        with pytest.raises(AppError, match="available monitors"):
            app.monitor_rect()
    finally:
        app.shutdown()


def test_cli_parses_monitor():
    p = cli._parser()
    assert p.parse_args([]).monitor == 1
    assert p.parse_args(["--monitor", "2"]).monitor == 2
    for bad in ("0", "-1", "x"):
        with pytest.raises(SystemExit):
            p.parse_args(["--monitor", bad])


def test_cli_bad_monitor_exit_code_and_message(tmp_path, monkeypatch, capsys, two_monitors):
    rc = cli.main(["--headless", "--dry-run", "--monitor", "7",
                   "--config", str(tmp_path / "p.json")])
    assert rc == cli.EXIT_USAGE
    assert "available monitors" in capsys.readouterr().err
    assert cli.main(["--monitor", "0"]) == cli.EXIT_USAGE


@pytest.mark.gui
def test_overlay_window_matches_monitor_rect():
    import glfw

    from nightvision.backends.present.overlay_gl import GlOverlaySink
    rect = cap_select.monitor_rect(1)
    sink = GlOverlaySink(monitor_rect=rect, mode="overlay")
    sink.open()
    try:
        x, y, w, h = rect
        assert tuple(glfw.get_window_pos(sink._win)) == (x, y)
        assert tuple(glfw.get_window_size(sink._win)) == (w, h)
    finally:
        sink.close()
