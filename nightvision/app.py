# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""UI-agnostic controller: the single owner of NightVision state.

Threading model
---------------
Every public mutator runs on the *owner* thread (the Tk main thread, or the
headless loop). Hotkey and control-socket callbacks arrive on their own
threads, so they only call `post()`, which enqueues into a bounded queue that
the owner drains in `pump()`. This keeps the tone backend (X11/Win32 handles),
the pipeline and the UI free of locks.

Modes are exclusive on purpose: the display tone ramp and the capture-side
enhancement both brighten shadows, so running both would double-process the
image and crush highlights.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Optional

from . import control as _control
from .backends.capture.select import select_capture_backend
from .backends.present.select import select_present_backend
from .backends.tone.select import select_tone_backend
from .core.curves import build_lut, lut_to_ramp
from .core.params import EnhanceParams
from .core.presets import PRESETS, get_preset
from .foreground import foreground_window_title, match_profile
from .hotkeys import create_hotkey_manager
from .hotkeys.base import parse_hotkey
from .mainthread import MainThreadDispatcher
from .pipeline import EnhancePipeline
from .platform_detect import detect
from .processing import create_enhancer
from .profiles import Profile, ProfileStore, default_config_path

log = logging.getLogger(__name__)

MODES = ("off", "display", "overlay", "preview")
CAPTURE_MODES = ("overlay", "preview")
DEFAULT_TOGGLE_HOTKEY = "ctrl+alt+n"
DEFAULT_NEXT_PRESET_HOTKEY = "ctrl+alt+p"
DEBOUNCE_S = 0.08
AUTO_POLL_S = 1.0
QUEUE_SIZE = 64


class AppError(RuntimeError):
    pass


class NightVisionApp:
    def __init__(self, config_path=None, *, dry_run: bool = False, gpu: str = "auto",
                 fps: float = 30.0, synthetic: bool = False, info=None,
                 store: Optional[ProfileStore] = None,
                 tone_backend=None,
                 source_factory: Optional[Callable[[], Any]] = None,
                 sink_factory: Optional[Callable[[], Any]] = None,
                 enhancer_factory: Optional[Callable[[], Any]] = None,
                 hotkey_factory: Optional[Callable[[Any], tuple]] = None,
                 enable_hotkeys: bool = True, enable_control: bool = True,
                 control_path: Optional[str] = None,
                 foreground_fn: Optional[Callable[[], Optional[str]]] = None,
                 auto_switch: bool = False, debounce_s: float = DEBOUNCE_S) -> None:
        self.info = info or detect()
        self.dry_run = dry_run
        self.gpu = gpu
        self.fps = fps
        self.synthetic = synthetic
        self.store = store or ProfileStore(config_path or default_config_path()).load()
        self._tone = tone_backend
        self._tone_reasons: list[str] = []
        self._source_factory = source_factory
        self._sink_factory = sink_factory
        self._enhancer_factory = enhancer_factory
        self._hotkey_factory = hotkey_factory or create_hotkey_manager
        self._enable_hotkeys = enable_hotkeys
        self._enable_control = enable_control
        self._control_path = control_path
        self._foreground_fn = foreground_fn
        self._debounce = debounce_s

        # Owner = the constructing (main) thread: GLFW window calls are marshalled here.
        self._dispatcher = MainThreadDispatcher()
        self._q: "queue.Queue[tuple[str, tuple]]" = queue.Queue(maxsize=QUEUE_SIZE)
        self.quit_requested = threading.Event()
        self._closed = False
        self._mode = "off"
        self._last_mode = "display"
        self.version = 0  # bumped on every visible state change; UIs poll it

        active = self.store.get(self.store.active)
        self._profile = active.name if active else "off"
        self._params = (active.params if active else EnhanceParams()).validated()
        self._preset: Optional[str] = self._preset_for(self._params)
        self._applied_params: Optional[EnhanceParams] = None
        self._pending_since: Optional[float] = None

        self._pipeline: Optional[EnhancePipeline] = None
        self._sink = None
        self._enh_info: dict[str, Any] = {"name": None, "reasons": [], "gl_renderer": None}
        self._enh_ref = None  # live enhancer, for diagnostics only
        self.last_run_summary: Optional[dict[str, Any]] = None
        self._src_reasons: list[str] = []
        self._capture_diag: Optional[dict[str, Any]] = None
        self._present_info: dict[str, Any] = {}
        self._hotkeys = None
        self._hotkey_reasons: list[str] = []
        self._hotkey_errors: list[str] = []
        self._control: Optional[_control.ControlServer] = None
        self._control_error: Optional[str] = None
        self.last_error: Optional[str] = None
        self.auto_switch = auto_switch
        self._next_poll = 0.0
        self._status: dict[str, Any] = {}
        self._refresh_status()

    # ------------------------------------------------------------ properties
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def params(self) -> EnhanceParams:
        """Latest requested params (may still be waiting out the debounce)."""
        return self._params

    @property
    def preset(self) -> Optional[str]:
        return self._preset

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def pipeline(self) -> Optional[EnhancePipeline]:
        return self._pipeline

    @property
    def tone_backend(self):
        if self._tone is None:
            self._tone, self._tone_reasons = select_tone_backend(
                self.info, dry_run=self.dry_run)
        return self._tone

    @staticmethod
    def _preset_for(p: EnhanceParams) -> Optional[str]:
        for n, v in PRESETS.items():
            if v == p:
                return n
        return None

    # ------------------------------------------------------------ owner-thread queue
    def post(self, action: str, *args) -> bool:
        """Thread-safe: callable from hotkey/control threads. False if full."""
        try:
            self._q.put_nowait((action, args))
            return True
        except queue.Full:
            log.warning("action queue full; dropping %s", action)
            return False

    _ACTIONS = {"toggle", "next_preset", "apply_preset", "set_profile", "set_mode"}

    def pump(self) -> int:
        """Drain queued actions, apply debounced params, run periodic checks."""
        if self._closed:
            return 0
        self._dispatcher.run_pending()
        # Only if a GL sink was ever imported: keeps glfw out of headless runs.
        gl = sys.modules.get("nightvision.backends.present.overlay_gl")
        # getattr: the render thread may still be mid-import of that module.
        poll = getattr(gl, "poll_events", None)
        if poll is not None:
            poll()
        n = 0
        while n < QUEUE_SIZE:
            try:
                action, args = self._q.get_nowait()
            except queue.Empty:
                break
            n += 1
            if action not in self._ACTIONS:
                continue
            try:
                getattr(self, action)(*args)
            except Exception as exc:  # noqa: BLE001 - a bad action must not kill the loop
                self._set_error(f"{action}: {exc}")
        self._flush_if_due()
        self._check_pipeline()
        self._poll_foreground()
        return n

    # ------------------------------------------------------------ modes
    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; choose from {', '.join(MODES)}")
        self._ensure_open()
        if mode == self._mode:
            return
        if self._mode != "off":
            self._last_mode = self._mode
        self._flush_params()
        try:
            if mode in CAPTURE_MODES:
                self._tone_restore()
                self._start_pipeline(mode)
            else:
                self._stop_pipeline()
                if mode == "display":
                    self._apply_display(self._params)
                else:
                    self._tone_restore()
        except Exception as exc:
            self._stop_pipeline()
            self._tone_restore()
            self._mode = "off"
            self._set_error(f"cannot enter {mode} mode: {exc}")
            raise
        self._mode = mode
        self.last_error = None
        self._changed()

    def toggle(self) -> None:
        self.set_mode(self._last_mode if self._mode == "off" else "off")

    # ------------------------------------------------------------ params / presets
    def set_params(self, params: EnhanceParams, immediate: bool = False) -> None:
        """Request new params. Slider drags call this per tick; the actual
        apply is debounced because a ramp write can flicker the whole display."""
        self._ensure_open()
        self._params = params.validated()
        self._preset = self._preset_for(self._params)
        self._pending_since = time.monotonic()
        if immediate:
            self._flush_params()
        self._changed()

    def apply_preset(self, name: str) -> None:
        self.set_params(get_preset(name), immediate=True)

    def next_preset(self) -> None:
        names = list(PRESETS)
        i = names.index(self._preset) if self._preset in names else -1
        self.apply_preset(names[(i + 1) % len(names)])

    def _flush_if_due(self) -> None:
        if self._pending_since is not None and \
                time.monotonic() - self._pending_since >= self._debounce:
            self._flush_params()

    def _flush_params(self) -> None:
        self._pending_since = None
        p = self._params
        if p == self._applied_params:
            return
        try:
            if self._mode == "display":
                self._apply_display(p)
            elif self._pipeline is not None and self._mode in CAPTURE_MODES:
                self._pipeline.set_params(p)
            self._applied_params = p
        except Exception as exc:  # noqa: BLE001
            self._set_error(f"apply failed: {exc}")

    def flush(self) -> None:
        """Apply pending params now (tests, shutdown)."""
        self._flush_params()

    def _apply_display(self, p: EnhanceParams) -> None:
        ramp = lut_to_ramp(build_lut(p))
        self.tone_backend.apply(ramp)
        self._applied_params = p

    def _tone_restore(self) -> None:
        if self._tone is not None:
            try:
                self._tone.restore()
            except Exception as exc:  # noqa: BLE001
                self._set_error(f"gamma restore failed: {exc}")
        self._applied_params = None

    # ------------------------------------------------------------ pipeline
    def _enhancer_make(self, sink=None):
        if self._enhancer_factory is not None:
            enh = self._enhancer_factory()
            self._enh_info["name"] = getattr(enh, "name", type(enh).__name__)
            self._enh_ref = enh
            return enh
        # WHY: a GL sink (GLFW/GLX on Linux) must share its context with the
        # enhancer; a second standalone EGL context in the same process makes
        # Xlib abort with BadAccess. Non-GL sinks have no ctx, so the enhancer
        # opens its own as before.
        ctx = getattr(sink, "ctx", None) if hasattr(sink, "present_texture") else None
        enh, name, reasons = create_enhancer(params=self._params, prefer=self.gpu,
                                             ctx=ctx)
        renderer = self._renderer_of(enh)
        self._enh_info.update(name=name, reasons=list(reasons), gl_renderer=renderer)
        self._enh_ref = enh
        return enh

    def _enh_path(self) -> Optional[str]:
        """Human-readable processing path, live (reflects a runtime GPU drop)."""
        enh = self._enh_ref
        if enh is None:
            return None
        if getattr(enh, "backend", None) != "gpu":
            return "cpu"
        if getattr(enh, "zero_copy", False):
            return "gpu (shared context, zero-copy)"
        return "gpu (standalone context, readback)"

    @staticmethod
    def _renderer_of(enh) -> Optional[str]:
        caps = getattr(enh, "caps", None)
        if caps is not None and getattr(caps, "renderer", None):
            return str(caps.renderer)
        try:
            return enh.ctx.info.get("GL_RENDERER")
        except Exception:  # noqa: BLE001
            return None

    def _source_make(self):
        if self._source_factory is not None:
            return self._source_factory()
        src, reasons = select_capture_backend(self.info, synthetic=self.synthetic)
        self._src_reasons = list(reasons)
        return src

    def _sink_factory_for(self, mode: str) -> Callable[[], Any]:
        if self._sink_factory is not None:
            base = self._sink_factory
            self._present_info = {"mode": "injected", "reasons": []}
        else:
            fac, pmode, reasons = select_present_backend(
                self.info, prefer="overlay" if mode == "overlay" else "window")
            self._present_info = {"mode": pmode, "reasons": reasons}
            if fac is None:
                raise AppError("no presentation backend: " + "; ".join(reasons))
            if mode == "overlay" and pmode != "overlay":
                raise AppError("overlay unavailable on this platform: " + "; ".join(reasons))
            dispatcher = self._dispatcher

            def base():
                return fac(dispatcher=dispatcher)

        def make():
            self._sink = base()
            return self._sink
        return make

    def _start_pipeline(self, mode: str) -> None:
        self._stop_pipeline()
        sink_factory = self._sink_factory_for(mode)
        self._pipeline = EnhancePipeline(self._source_make, self._enhancer_make, sink_factory,
                                         self._params, self.fps,
                                         dispatcher=self._dispatcher)
        self._applied_params = self._params
        self._pipeline.start()

    def _stop_pipeline(self) -> None:
        pl, self._pipeline = self._pipeline, None
        if pl is not None:
            self.last_run_summary = {"path": self._enh_path(), "stats": pl.stats(),
                                     "renderer": self._enh_info.get("gl_renderer")}
            if not pl.stop(3.0):
                log.warning("pipeline threads did not exit in time")
        self._sink = None
        self._enh_ref = None

    def _check_pipeline(self) -> None:
        pl = self._pipeline
        if pl is None or self._mode not in CAPTURE_MODES:
            return
        if pl.last_error is not None:
            err = pl.last_error
            self._stop_pipeline()
            self._mode = "off"
            self._set_error(f"{type(err).__name__}: {err}")
            self._changed()
            return
        if self._mode == "preview" and self._sink is not None \
                and pl.stats()["frames_processed"] > 0 and getattr(self._sink, "closed", False):
            # The user closed the preview window: that means "stop".
            self._stop_pipeline()
            self._mode = "off"
            self._changed()

    # ------------------------------------------------------------ profiles
    def set_profile(self, name: str) -> None:
        self._ensure_open()
        prof = self.store.get(name)
        if prof is None:
            raise KeyError(f"unknown profile {name!r}")
        self._profile = name
        self.store.active = name
        self.set_params(prof.params, immediate=True)

    def save_profile(self, name: str, exe_names: Optional[list[str]] = None,
                     hotkey: Optional[str] = None) -> Profile:
        """Create/update `name` with the current params. Raises ValueError on a bad hotkey."""
        name = name.strip()
        if not name:
            raise ValueError("profile name is empty")
        if hotkey:
            parse_hotkey(hotkey)
        old = self.store.get(name)
        prof = Profile(name, self._params,
                       exe_names if exe_names is not None else (old.exe_names if old else []),
                       (hotkey or None) if (hotkey is not None or old is None)
                       else old.hotkey)
        self.store.upsert(prof)
        self._profile = name
        self.store.active = name
        self.store.save()
        self.reload_hotkeys()
        self._changed()
        return prof

    def new_profile(self, name: Optional[str] = None) -> Profile:
        if not name:
            i = 1
            while f"Profile {i}" in self.store.profiles:
                i += 1
            name = f"Profile {i}"
        if name in self.store.profiles:
            raise ValueError(f"profile {name!r} already exists")
        return self.save_profile(name, [], None)

    def delete_profile(self, name: str) -> bool:
        if len(self.store.profiles) <= 1:
            raise ValueError("cannot delete the last profile")
        removed = self.store.delete(name)
        if removed:
            if self._profile == name:
                self._profile = self.store.active
            self.store.save()
            self.reload_hotkeys()
            self._changed()
        return removed

    def set_auto_switch(self, on: bool) -> None:
        self.auto_switch = bool(on)
        self._next_poll = 0.0
        self._changed()

    def _poll_foreground(self) -> None:
        if not self.auto_switch:
            return
        now = time.monotonic()
        if now < self._next_poll:
            return
        self._next_poll = now + AUTO_POLL_S
        fn = self._foreground_fn or foreground_window_title
        try:
            prof = match_profile(fn(), self.store.profiles.values())
        except Exception as exc:  # noqa: BLE001
            log.debug("foreground poll failed: %s", exc)
            return
        # No match keeps the current profile: alt-tabbing to a browser should
        # not silently drop the game's settings.
        if prof is not None and prof.name != self._profile:
            self.set_profile(prof.name)

    # ------------------------------------------------------------ hotkeys / control
    def start_services(self) -> None:
        """Start hotkeys and the control server (call once, on the owner thread)."""
        self._ensure_open()
        if self._enable_hotkeys:
            self.reload_hotkeys()
        if self._enable_control and self._control is None:
            srv = _control.ControlServer(self._handle_command, path=self._control_path)
            try:
                srv.start()
                self._control = srv
            except Exception as exc:  # noqa: BLE001 - e.g. another instance is running
                self._control_error = str(exc)
                log.warning("control server not started: %s", exc)

    @property
    def control_error(self) -> Optional[str]:
        return self._control_error

    def reload_hotkeys(self) -> None:
        if not self._enable_hotkeys:
            return
        if self._hotkeys is not None:
            try:
                self._hotkeys.stop()
            except Exception:  # noqa: BLE001
                pass
        mgr, reasons = self._hotkey_factory(self.info)
        self._hotkey_reasons = list(reasons)
        self._hotkey_errors = []
        seen: set[str] = set()

        def reg(hk: Optional[str], cb: Callable[[], Any], what: str) -> None:
            if not hk:
                return
            try:
                key = parse_hotkey(hk)
                canon = repr((sorted(key[0]), key[1]))
                if canon in seen:
                    self._hotkey_errors.append(f"{hk} ({what}): duplicate binding")
                    return
                mgr.register(hk, cb)
                seen.add(canon)
            except ValueError as exc:
                self._hotkey_errors.append(f"{hk} ({what}): {exc}")

        reg(DEFAULT_TOGGLE_HOTKEY, lambda: self.post("toggle"), "toggle")
        reg(DEFAULT_NEXT_PRESET_HOTKEY, lambda: self.post("next_preset"), "next preset")
        for prof in list(self.store.profiles.values()):
            reg(prof.hotkey, (lambda n=prof.name: self.post("set_profile", n)),
                f"profile {prof.name}")
        try:
            mgr.start()
        except Exception as exc:  # noqa: BLE001
            self._hotkey_errors.append(f"hotkey manager failed to start: {exc}")
        self._hotkeys = mgr

    def _handle_command(self, msg: dict) -> dict:
        """Runs on the control server thread: validate, enqueue, never touch state."""
        cmd = msg.get("cmd")
        if cmd == "status":
            st = dict(self._status)
            pl = self._pipeline
            if pl is not None:
                st["stats"] = pl.stats()
            return {"status": st}
        if cmd == "quit":
            self.quit_requested.set()
            return {}
        if cmd == "preset":
            get_preset(msg["name"])  # KeyError -> {"ok": false, "error": ...}
            action, args = "apply_preset", (msg["name"],)
        elif cmd == "profile":
            if self.store.get(msg["name"]) is None:
                raise KeyError(f"unknown profile {msg['name']!r}")
            action, args = "set_profile", (msg["name"],)
        elif cmd == "toggle":
            action, args = "toggle", ()
        elif cmd == "next_preset":
            action, args = "next_preset", ()
        else:
            raise ValueError(f"unsupported command {cmd!r}")
        if not self.post(action, *args):
            raise RuntimeError("busy: action queue full")
        return {}

    # ------------------------------------------------------------ diagnostics
    def diagnostics(self, probe: bool = False) -> dict[str, Any]:
        """Snapshot for the UI and --list-backends. `probe=True` additionally
        instantiates (and immediately closes) an enhancer to learn which
        process backend would be chosen; the UI skips it to stay cheap."""
        tone = self.tone_backend
        d: dict[str, Any] = {
            "platform": {"os": self.info.os, "session": self.info.session,
                         "desktop": self.info.desktop, "wlroots": self.info.wlroots},
            "mode": self._mode, "profile": self._profile, "preset": self._preset,
            "tone": {"backend": getattr(tone, "name", type(tone).__name__),
                     "reasons": list(self._tone_reasons)},
            "error": self.last_error,
        }
        if self._capture_diag is None:  # probing may touch D-Bus/X: do it once
            try:
                src, reasons = select_capture_backend(self.info, synthetic=self.synthetic)
                self._capture_diag = {"backend": getattr(src, "name", type(src).__name__),
                                      "reasons": list(reasons)}
                try:
                    src.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001 - CaptureError when nothing is usable
                self._capture_diag = {"backend": None, "reasons": [str(exc)]}
        d["capture"] = dict(self._capture_diag)
        pf, pmode, preasons = select_present_backend(self.info, prefer=None)
        d["present"] = {"backend": "gl" if pf else None, "mode": pmode, "reasons": preasons}
        if self._present_info:
            d["present"].update(self._present_info)
        enh = dict(self._enh_info)
        if probe and enh["name"] is None:
            try:
                e, name, reasons = create_enhancer(params=self._params, prefer=self.gpu)
                enh.update(name=name, reasons=list(reasons), gl_renderer=self._renderer_of(e))
                e.close()
            except Exception as exc:  # noqa: BLE001
                enh["reasons"] = [f"probe failed: {exc}"]
        reasons = list(enh["reasons"])
        if self._enh_ref is not None:  # runtime fallbacks append to the live list
            reasons = list(getattr(self._enh_ref, "reasons", reasons)) or reasons
        d["process"] = {"backend": enh["name"], "reasons": reasons,
                        "path": self._enh_path()}
        d["gpu_renderer"] = enh["gl_renderer"]
        if self._hotkeys is not None:
            hk_reasons = self._hotkey_reasons
        else:
            try:
                _, hk_reasons = self._hotkey_factory(self.info)
            except Exception as exc:  # noqa: BLE001
                hk_reasons = [f"unavailable: {exc}"]
        d["hotkeys"] = {"reasons": list(hk_reasons), "errors": list(self._hotkey_errors)}
        d["control"] = {"error": self._control_error,
                        "path": getattr(self._control, "path", None)}
        pl = self._pipeline
        d["stats"] = pl.stats() if pl is not None else None
        return d

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    # ------------------------------------------------------------ internals
    def _ensure_open(self) -> None:
        if self._closed:
            raise AppError("application is shut down")

    def _set_error(self, msg: str) -> None:
        self.last_error = msg
        log.warning("%s", msg)
        self._changed()

    def _changed(self) -> None:
        self.version += 1
        self._refresh_status()

    def _refresh_status(self) -> None:
        self._status = {"mode": self._mode, "preset": self._preset, "profile": self._profile,
                        "auto_switch": self.auto_switch, "error": self.last_error,
                        "params": self._params.to_dict(),
                        "tone_backend": getattr(self._tone, "name", None)}

    def shutdown(self) -> None:
        """Idempotent. Always ends with the display gamma restored."""
        if self._closed:
            return
        self._closed = True
        self.quit_requested.set()
        for stop in (lambda: self._hotkeys and self._hotkeys.stop(),
                     lambda: self._control and self._control.stop(),
                     self._stop_pipeline,
                     self._dispatcher.shutdown):
            try:
                stop()
            except Exception as exc:  # noqa: BLE001 - keep tearing down
                log.warning("shutdown step failed: %s", exc)
        if self._tone is not None:
            try:
                self._tone.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("gamma restore failed: %s", exc)
        self._mode = "off"
        self._refresh_status()
        if os.environ.get("NIGHTVISION_DIAG") and self.last_run_summary:
            # Opt-in one-liner (the CLI has no verbose flag): lets scripts and
            # CI see which processing path actually ran.
            s = self.last_run_summary
            print(f"nightvision-diag: path={s['path']} renderer={s['renderer']} "
                  f"fps={s['stats']['fps']:.1f} frames={s['stats']['frames_processed']}",
                  file=sys.stderr, flush=True)
        try:
            self.store.save()
        except OSError as exc:
            log.warning("could not save profiles: %s", exc)
