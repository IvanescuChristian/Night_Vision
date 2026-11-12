# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import threading

import pytest

from nightvision.backends.tone.null import NullToneBackend
from nightvision.platform_detect import PlatformInfo
from nightvision.backends.capture.synthetic import SyntheticCapture
from nightvision.processing import create_enhancer
from nightvision.sinks import LatestFrameSink

pytestmark = pytest.mark.gui
tk = pytest.importorskip("tkinter")

INFO = PlatformInfo("linux", "x11", "", False, True)


@pytest.fixture
def win(tmp_path):
    from nightvision.app import NightVisionApp
    from nightvision.ui.main_window import MainWindow
    app = NightVisionApp(tmp_path / "p.json", info=INFO, tone_backend=NullToneBackend(),
                         sink_factory=LatestFrameSink,
                         source_factory=lambda: SyntheticCapture(160, 90),
                         enhancer_factory=lambda: create_enhancer(prefer="cpu")[0],
                         enable_hotkeys=False,
                         enable_control=False, debounce_s=0.0)
    errors = []
    try:
        w = MainWindow(app)
    except tk.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    w.root.report_callback_exception = lambda *a: errors.append(a)
    yield w, app, errors
    w.close()
    assert not errors, errors
    assert not [t for t in threading.enumerate() if t.name.startswith("nv-")]


def test_preset_via_combobox(win):
    w, app, errors = win
    w.preset_box.set("night_vision")
    w.preset_box.event_generate("<<ComboboxSelected>>")
    w.root.update()
    assert app.preset == "night_vision"
    assert app.params.gamma == pytest.approx(1.7)
    assert w.vars["gamma"].get() == pytest.approx(1.7)


def test_slider_changes_app_params(win):
    w, app, errors = win
    w.scales["gamma"].set(2.0)
    w.root.update()
    assert app.params.gamma == pytest.approx(2.0)
    assert app.preset is None and w.preset_var.get() == ""
    # WHY: clahe_tiles is capture-only; the app starts in "off", where ttk ignores
    # Scale.set() on a disabled widget (value stays at the default 8). Assert the
    # precondition so a failure names the cause instead of "8 == 12".
    app.set_mode("preview")
    w._sync_from_app()
    assert app.mode == "preview"
    assert not w.scales["clahe_tiles"].instate(["disabled"])
    w.scales["clahe_tiles"].set(12.4)
    assert app.params.clahe_tiles == 12
    assert w.value_labels["clahe_tiles"].cget("text") == "12"


def test_capture_only_sliders_greyed_in_display_mode(win):
    w, app, errors = win
    app.set_mode("display")
    w._sync_from_app()
    assert w.scales["sharpen"].instate(["disabled"])
    assert "capture modes only" in w.notes["sharpen"].cget("text")
    assert not w.scales["gamma"].instate(["disabled"])
    app.set_mode("preview")
    w._sync_from_app()
    assert not w.scales["sharpen"].instate(["disabled"])
    assert w.notes["sharpen"].cget("text") == ""


def test_mode_radio_drives_app(win):
    w, app, errors = win
    w.mode_buttons["display"].invoke()
    assert app.mode == "display"
    w.mode_buttons["off"].invoke()
    assert app.mode == "off"


def test_diagnostics_populated_and_safety_note(win):
    from nightvision.ui.main_window import SAFETY_NOTE
    w, app, errors = win
    w.refresh_diagnostics()
    text = w.diag.get("1.0", "end")
    for key in ("tone:", "capture:", "process:", "present:", "hotkeys:"):
        assert key in text
    assert "null" in text
    assert "does not read or modify any game" in SAFETY_NOTE
    labels = [c.cget("text") for c in w.root.winfo_children() if c.winfo_class() == "TLabel"]
    assert SAFETY_NOTE in labels


def test_external_change_syncs_into_ui(win):
    w, app, errors = win
    app.post("apply_preset", "rust_night")
    w.root.update()
    w._tick()
    assert w.preset_var.get() == "rust_night"


def test_preview_canvas_renders_and_brightens(win):
    w, app, errors = win
    app.apply_preset("night_vision")
    w._render_preview()
    assert w._photo is not None and w._photo.width() == 2 * 256 + 4


def test_profile_save_new_delete_and_bad_hotkey(win):
    w, app, errors = win
    w.profile_var.set("Mine")
    w.exe_var.set("a.exe, b.exe")
    w.hotkey_var.set("ctrl+alt+8")
    w._on_save()
    assert app.store.get("Mine").exe_names == ["a.exe", "b.exe"]
    w.hotkey_var.set("x")
    w._on_save()
    assert "modifier" in w.status_var.get()
    w._on_new()
    assert app.profile.startswith("Profile ")
    w.profile_var.set("Mine")
    w._on_delete()
    assert app.store.get("Mine") is None


def test_close_calls_shutdown(win):
    w, app, errors = win
    calls = []
    orig = app.shutdown
    app.shutdown = lambda: (calls.append(1), orig())
    w.root.protocol("WM_DELETE_WINDOW")  # handler is registered
    w.close()
    assert calls == [1]
    w.close()  # idempotent
    assert calls == [1]
