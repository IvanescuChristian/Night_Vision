# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import threading
import time

import numpy as np
import pytest

from nightvision.app import NightVisionApp
from nightvision.backends.capture.synthetic import SyntheticCapture
from nightvision.backends.tone.null import NullToneBackend
from nightvision.core.params import EnhanceParams
from nightvision.core.presets import PRESETS
from nightvision.hotkeys.base import HotkeyManager
from nightvision.platform_detect import PlatformInfo
from nightvision.processing import create_enhancer
from nightvision.profiles import Profile
from nightvision.sinks import LatestFrameSink

INFO = PlatformInfo("linux", "x11", "", False, True)


class FakeHotkeys(HotkeyManager):
    def __init__(self):
        super().__init__()
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout=2.0):
        self.stopped = True


def nv_threads():
    return [t for t in threading.enumerate() if t.name.startswith("nv-") and t.is_alive()]


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def make_app(tmp_path):
    apps = []

    def make(**kw):
        kw.setdefault("config_path", tmp_path / "profiles.json")
        kw.setdefault("info", INFO)
        kw.setdefault("tone_backend", NullToneBackend())
        kw.setdefault("source_factory", lambda: SyntheticCapture(160, 90, fps=120))
        kw.setdefault("sink_factory", LatestFrameSink)
        kw.setdefault("enhancer_factory", lambda: create_enhancer(prefer="cpu")[0])
        kw.setdefault("enable_hotkeys", False)
        kw.setdefault("enable_control", False)
        kw.setdefault("debounce_s", 0.0)
        a = NightVisionApp(**kw)
        apps.append(a)
        return a
    yield make
    for a in apps:
        a.shutdown()
    assert wait_for(lambda: not nv_threads()), nv_threads()


def test_display_mode_applies_ramp(make_app):
    app = make_app()
    tone = app.tone_backend
    app.apply_preset("night_vision")
    app.set_mode("display")
    assert tone.last_ramp is not None and tone.last_ramp.shape == (3, 256)
    # night_vision lifts shadows: ramp must sit above identity at the low end
    ident = np.linspace(0, 65535, 256)
    assert tone.last_ramp[0][32] > ident[32]
    assert app.pipeline is None


def test_preset_change_reapplies(make_app):
    app = make_app()
    app.set_mode("display")
    first = app.tone_backend.last_ramp.copy()
    app.apply_preset("night_vision")
    assert not np.array_equal(first, app.tone_backend.last_ramp)
    assert app.preset == "night_vision"
    with pytest.raises(KeyError):
        app.apply_preset("nope")


def test_set_params_is_debounced(make_app):
    app = make_app(debounce_s=0.05)
    app.set_mode("display")
    before = app.tone_backend.last_ramp.copy()
    app.set_params(EnhanceParams(gamma=2.0))
    assert app.params.gamma == 2.0
    assert np.array_equal(before, app.tone_backend.last_ramp)  # not yet applied
    assert wait_for(lambda: (app.pump(), not np.array_equal(
        before, app.tone_backend.last_ramp))[1])


def test_preview_starts_and_off_stops_pipeline(make_app):
    app = make_app()
    app.set_mode("preview")
    pl = app.pipeline
    assert pl is not None and wait_for(lambda: pl.stats()["frames_processed"] > 2)
    assert app.tone_backend.last_ramp is None
    app.set_mode("off")
    assert app.pipeline is None and not pl.running
    assert wait_for(lambda: not nv_threads())


def test_modes_exclusive_display_ramp_removed_in_capture(make_app):
    app = make_app()
    app.set_mode("display")
    tone = app.tone_backend
    assert tone.last_ramp is not None
    app.set_mode("overlay")
    assert tone.last_ramp is None and tone.restored == 1  # no double processing
    app.set_mode("display")
    assert app.pipeline is None and tone.last_ramp is not None


def test_capture_params_reach_pipeline(make_app):
    app = make_app()
    app.set_mode("preview")
    app.apply_preset("tarkov_dark")
    assert wait_for(lambda: app.pipeline.stats()["frames_processed"] > 1)
    assert app.pipeline._params == PRESETS["tarkov_dark"]


def test_pipeline_failure_returns_to_off(make_app):
    def boom():
        raise RuntimeError("no capture")
    app = make_app(source_factory=boom)
    app.set_mode("preview")
    assert wait_for(lambda: (app.pump(), app.mode == "off")[1])
    assert "no capture" in app.last_error


def test_queued_actions_processed_by_pump_only(make_app):
    app = make_app()
    done = threading.Event()

    def from_other_thread():
        app.post("apply_preset", "balanced")
        app.post("toggle")
        done.set()
    threading.Thread(target=from_other_thread).start()
    assert done.wait(2)
    assert app.preset != "balanced" and app.mode == "off"  # nothing ran yet
    assert app.pump() == 2
    assert app.preset == "balanced" and app.mode == "display"


def test_queue_is_bounded_and_bad_actions_survive(make_app):
    app = make_app()
    assert all(app.post("toggle") for _ in range(64))
    assert app.post("toggle") is False
    app.pump()
    app.post("apply_preset", "does-not-exist")
    app.post("os.system", "x")  # not whitelisted
    app.pump()
    assert "apply_preset" in app.last_error


def test_toggle_returns_to_last_mode(make_app):
    app = make_app()
    app.toggle()
    assert app.mode == "display"
    app.set_mode("preview")
    app.toggle()
    assert app.mode == "off"
    app.toggle()
    assert app.mode == "preview"


def test_next_preset_cycles(make_app):
    app = make_app()
    names = list(PRESETS)
    seen = []
    for _ in range(len(names) + 1):
        app.next_preset()
        seen.append(app.preset)
    assert seen[:len(names)] == names[1:] + names[:1]
    assert seen[-1] == names[1]  # wraps around


def test_shutdown_restores_gamma_and_is_idempotent(make_app):
    app = make_app()
    tone = app.tone_backend
    app.apply_preset("night_vision")
    app.set_mode("display")
    app.set_mode("preview")
    app.set_mode("display")
    app.start_services()
    app.shutdown()
    app.shutdown()
    assert tone.last_ramp is None and tone._applied is False
    assert wait_for(lambda: not nv_threads())
    with pytest.raises(Exception):
        app.set_mode("display")
    assert app.pump() == 0


def test_shutdown_stops_running_pipeline_and_control(make_app, tmp_path):
    app = make_app(enable_control=True, control_path=str(tmp_path / "c.sock"))
    app.start_services()
    app.set_mode("overlay")
    assert app.control_error is None
    assert any(t.name == "nv-control" for t in nv_threads())
    app.shutdown()
    assert wait_for(lambda: not nv_threads())


def test_hotkeys_registered_and_marshalled(make_app):
    mgr = FakeHotkeys()
    app = make_app(enable_hotkeys=True, hotkey_factory=lambda info: (mgr, ["fake"]))
    app.store.upsert(Profile("Game", PRESETS["rust_night"], ["game.exe"], "ctrl+alt+1"))
    app.start_services()
    keys = {b[0]: b[3] for b in mgr._bindings}
    assert {"ctrl+alt+n", "ctrl+alt+p", "ctrl+alt+1"} <= set(keys)
    assert mgr.started
    t = threading.Thread(target=keys["ctrl+alt+1"])
    t.start()
    t.join()
    assert app.profile != "Game"          # callback thread must not mutate state
    app.pump()
    assert app.profile == "Game" and app.params == PRESETS["rust_night"]
    keys["ctrl+alt+n"]()
    app.pump()
    assert app.mode == "display"


def test_bad_or_duplicate_profile_hotkey_reported(make_app):
    mgr = FakeHotkeys()
    app = make_app(enable_hotkeys=True, hotkey_factory=lambda info: (mgr, []))
    app.store.upsert(Profile("A", EnhanceParams(), [], "ctrl+alt+n"))
    app.store.upsert(Profile("B", EnhanceParams(), [], "F5"))
    app.start_services()
    errs = app.diagnostics()["hotkeys"]["errors"]
    assert any("duplicate" in e for e in errs) and any("modifier" in e for e in errs)


def test_control_commands_map_to_actions(make_app, tmp_path):
    from nightvision.control import send_command
    path = str(tmp_path / "c.sock")
    app = make_app(enable_control=True, control_path=path)
    app.start_services()
    assert send_command({"cmd": "preset", "name": "balanced"}, path)["ok"]
    assert send_command({"cmd": "toggle"}, path)["ok"]
    assert app.mode == "off"  # queued, not yet executed
    app.pump()
    assert app.preset == "balanced" and app.mode == "display"
    r = send_command({"cmd": "preset", "name": "bogus"}, path)
    assert not r["ok"]
    r = send_command({"cmd": "status"}, path)
    assert r["ok"] and r["status"]["mode"] == "display"
    assert send_command({"cmd": "quit"}, path)["ok"]
    assert app.quit_requested.is_set()


def test_auto_switch_from_foreground(make_app):
    title = {"v": "Some Browser"}
    app = make_app(foreground_fn=lambda: title["v"])
    app.set_auto_switch(True)
    app.pump()
    assert app.profile == "off"
    title["v"] = "EscapeFromTarkov | UnityWndClass"
    app._next_poll = 0  # skip the 1 s poll interval
    app.pump()
    assert app.profile == "Escape from Tarkov"
    assert app.params == PRESETS["tarkov_dark"]
    title["v"] = "Some Browser"
    app._next_poll = 0
    app.pump()
    assert app.profile == "Escape from Tarkov"  # no match keeps current


def test_auto_switch_off_by_default(make_app):
    app = make_app(foreground_fn=lambda: "EscapeFromTarkov")
    app.pump()
    assert app.profile == "off"


def test_profile_save_delete_roundtrip(make_app, tmp_path):
    app = make_app()
    app.set_params(EnhanceParams(gamma=1.4), immediate=True)
    app.save_profile("MyGame", ["mygame.exe"], "ctrl+alt+7")
    p = app.store.get("MyGame")
    assert p.params.gamma == 1.4 and p.hotkey == "ctrl+alt+7"
    with pytest.raises(ValueError):
        app.save_profile("X", [], "n")
    assert app.delete_profile("MyGame")
    assert app.store.get("MyGame") is None


def test_diagnostics_shape(make_app):
    d = make_app().diagnostics()
    for k in ("platform", "tone", "capture", "process", "present", "hotkeys", "control"):
        assert k in d


class _GlLikeSink(LatestFrameSink):
    ctx = object()

    def present_texture(self, tex):
        pass


def test_gl_sink_context_is_shared_with_enhancer_and_gpu_choice_respected(
        make_app, monkeypatch):
    import nightvision.app as appmod
    calls = []

    def spy(params=None, prefer="auto", frame_size=None, ctx=None):
        calls.append((prefer, ctx))
        return create_enhancer(params, prefer="cpu")

    monkeypatch.setattr(appmod, "create_enhancer", spy)
    sink = _GlLikeSink()
    app = make_app(enhancer_factory=None, sink_factory=lambda: sink, gpu="gpu")
    app.set_mode("preview")
    assert wait_for(lambda: app.pipeline and app.pipeline.stats()["frames_processed"] >= 2)
    # No forced-CPU workaround: the user's choice reaches create_enhancer, with the sink ctx.
    assert calls == [("gpu", _GlLikeSink.ctx)]
    d = app.diagnostics()
    assert d["process"]["path"] == "cpu"


def test_plain_sink_gets_no_ctx(make_app, monkeypatch):
    import nightvision.app as appmod
    calls = []

    def spy(params=None, prefer="auto", frame_size=None, ctx=None):
        calls.append(ctx)
        return create_enhancer(params, prefer="cpu")

    monkeypatch.setattr(appmod, "create_enhancer", spy)
    app = make_app(enhancer_factory=None)
    app.set_mode("preview")
    assert wait_for(lambda: app.pipeline and app.pipeline.stats()["frames_processed"] >= 2)
    assert calls == [None]
