# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Tk control panel. Main thread only: every app call happens here, and
background threads reach the app through its action queue (see app.pump)."""
from __future__ import annotations

import base64
import tkinter as tk
from dataclasses import fields, replace
from tkinter import ttk
from typing import Optional

import cv2
import numpy as np

from ..app import MODES, NightVisionApp
from ..backends.capture.synthetic import SyntheticCapture
from ..core.enhance import FrameEnhancer
from ..core.params import PARAM_RANGES, TONE_FIELDS, EnhanceParams
from ..core.presets import PRESETS

SAFETY_NOTE = "Display-side image enhancement only — does not read or modify any game."
PUMP_MS = 50
PUMP_MS_GL = 15  # GL window events/main-thread requests are serviced by this pump
DIAG_MS = 500
PREVIEW_DEBOUNCE_MS = 120
PREVIEW_W, PREVIEW_H = 256, 144
CAPTURE_ONLY = tuple(f.name for f in fields(EnhanceParams) if f.name not in TONE_FIELDS)
_INT_FIELDS = ("clahe_tiles",)


def _png_photo(bgr: np.ndarray) -> tk.PhotoImage:
    ok, buf = cv2.imencode(".png", bgr)
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes()))


class MainWindow:
    def __init__(self, app: NightVisionApp, root: Optional[tk.Tk] = None) -> None:
        self.app = app
        self.root = root or tk.Tk()
        self.root.title("Night Vision")
        self._closed = False
        self._suppress = False
        self._seen_version = -1
        self._preview_job = None
        self._photo: Optional[tk.PhotoImage] = None
        self.scales: dict[str, ttk.Scale] = {}
        self.value_labels: dict[str, ttk.Label] = {}
        self.vars: dict[str, tk.DoubleVar] = {}
        self.notes: dict[str, ttk.Label] = {}
        # One synthetic dark frame, built once: the preview only re-runs the enhancer.
        self._scene = SyntheticCapture(PREVIEW_W, PREVIEW_H, realtime=False).frame_at(60)[:, :, :3]
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._sync_from_app()
        self.root.after(PUMP_MS, self._tick)
        self.root.after(DIAG_MS, self._diag_tick)

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        r = self.root
        r.columnconfigure(0, weight=1)
        ttk.Label(r, text=SAFETY_NOTE, foreground="#2a6f2a").grid(
            row=0, column=0, sticky="we", padx=8, pady=(6, 2))
        body = ttk.Frame(r)
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        left, right = ttk.Frame(body), ttk.Frame(body)
        left.grid(row=0, column=0, sticky="n")
        right.grid(row=0, column=1, sticky="n", padx=(12, 0))

        # mode
        mf = ttk.LabelFrame(left, text="Mode")
        mf.grid(row=0, column=0, sticky="we", pady=2)
        self.mode_var = tk.StringVar(value=self.app.mode)
        self.mode_buttons = {}
        for i, m in enumerate(MODES):
            b = ttk.Radiobutton(mf, text=m, value=m, variable=self.mode_var,
                                command=self._on_mode)
            b.grid(row=0, column=i, padx=4)
            self.mode_buttons[m] = b

        # presets and sliders
        pf = ttk.LabelFrame(left, text="Preset and parameters")
        pf.grid(row=1, column=0, sticky="we", pady=2)
        ttk.Label(pf, text="Preset").grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(pf, textvariable=self.preset_var, state="readonly",
                                       values=list(PRESETS), width=18)
        self.preset_box.grid(row=0, column=1, columnspan=2, sticky="we")
        self.preset_box.bind("<<ComboboxSelected>>", self._on_preset)
        for i, f in enumerate(fields(EnhanceParams), start=1):
            lo, hi = PARAM_RANGES[f.name]
            ttk.Label(pf, text=f.name).grid(row=i, column=0, sticky="w")
            var = tk.DoubleVar(value=getattr(self.app.params, f.name))
            sc = ttk.Scale(pf, from_=lo, to=hi, variable=var, length=170,
                           command=lambda v, n=f.name: self._on_slider(n, v))
            sc.grid(row=i, column=1, sticky="we")
            lab = ttk.Label(pf, text="", width=6)
            lab.grid(row=i, column=2, sticky="e")
            note = ttk.Label(pf, text="", foreground="#888888")
            note.grid(row=i, column=3, sticky="w")
            self.vars[f.name], self.scales[f.name] = var, sc
            self.value_labels[f.name], self.notes[f.name] = lab, note

        # profiles
        prf = ttk.LabelFrame(left, text="Profile")
        prf.grid(row=2, column=0, sticky="we", pady=2)
        self.profile_var = tk.StringVar(value=self.app.profile)
        self.profile_box = ttk.Combobox(prf, textvariable=self.profile_var, width=22)
        self.profile_box.grid(row=0, column=0, columnspan=3, sticky="we")
        self.profile_box.bind("<<ComboboxSelected>>", self._on_profile)
        ttk.Button(prf, text="Save", command=self._on_save).grid(row=1, column=0)
        ttk.Button(prf, text="New", command=self._on_new).grid(row=1, column=1)
        ttk.Button(prf, text="Delete", command=self._on_delete).grid(row=1, column=2)
        ttk.Label(prf, text="Match window titles").grid(row=2, column=0, columnspan=3, sticky="w")
        self.exe_var = tk.StringVar()
        ttk.Entry(prf, textvariable=self.exe_var).grid(row=3, column=0, columnspan=3, sticky="we")
        ttk.Label(prf, text="Hotkey (e.g. ctrl+alt+1)").grid(row=4, column=0, columnspan=3,
                                                             sticky="w")
        self.hotkey_var = tk.StringVar()
        ttk.Entry(prf, textvariable=self.hotkey_var).grid(row=5, column=0, columnspan=3,
                                                          sticky="we")
        self.auto_var = tk.BooleanVar(value=self.app.auto_switch)
        ttk.Checkbutton(prf, text="Auto-switch profile by focused window",
                        variable=self.auto_var, command=self._on_auto).grid(
            row=6, column=0, columnspan=3, sticky="w")
        self.status_var = tk.StringVar()
        ttk.Label(left, textvariable=self.status_var, foreground="#b03030",
                  wraplength=360).grid(row=3, column=0, sticky="we")

        # preview
        pv = ttk.LabelFrame(right, text="Dark-scene preview (before / after)")
        pv.grid(row=0, column=0, sticky="we")
        self.canvas = tk.Canvas(pv, width=PREVIEW_W * 2 + 4, height=PREVIEW_H,
                                highlightthickness=0, background="#000000")
        self.canvas.grid(row=0, column=0)

        # diagnostics
        df = ttk.LabelFrame(right, text="Diagnostics")
        df.grid(row=1, column=0, sticky="we", pady=4)
        self.diag = tk.Text(df, width=62, height=17, wrap="word", state="disabled",
                            background="#1e1e1e", foreground="#d8d8d8")
        self.diag.grid(row=0, column=0)

    # ------------------------------------------------------------ app -> UI
    def _sync_from_app(self) -> None:
        self._suppress = True
        try:
            p = self.app.params.to_dict()
            for name, var in self.vars.items():
                var.set(p[name])
                self._show_value(name)
            self.preset_var.set(self.app.preset or "")
            self.mode_var.set(self.app.mode)
            names = sorted(self.app.store.profiles)
            self.profile_box["values"] = names
            self.profile_var.set(self.app.profile)
            prof = self.app.store.get(self.app.profile)
            self.exe_var.set(", ".join(prof.exe_names) if prof else "")
            self.hotkey_var.set((prof.hotkey or "") if prof else "")
            self.auto_var.set(self.app.auto_switch)
            self._update_greying()
            self.status_var.set(self.app.last_error or "")
        finally:
            self._suppress = False
        self._seen_version = self.app.version
        self._schedule_preview()

    def _update_greying(self) -> None:
        display = self.app.mode in ("display", "off")
        for name in CAPTURE_ONLY:
            self.scales[name].state(["disabled"] if display else ["!disabled"])
            self.notes[name].config(text="capture modes only" if display else "")

    def _show_value(self, name: str) -> None:
        v = self.vars[name].get()
        self.value_labels[name].config(
            text=f"{int(round(v))}" if name in _INT_FIELDS else f"{v:.2f}")

    # ------------------------------------------------------------ UI -> app
    def _on_slider(self, name: str, raw) -> None:
        if self._suppress:
            return
        v = float(raw)
        if name in _INT_FIELDS:
            v = int(round(v))
        self._show_value(name)
        self.app.set_params(replace(self.app.params, **{name: v}))
        self.preset_var.set(self.app.preset or "")
        self._seen_version = self.app.version
        self._schedule_preview()

    def _on_preset(self, _evt=None) -> None:
        self._guard(lambda: self.app.apply_preset(self.preset_var.get()))

    def _on_mode(self) -> None:
        self._guard(lambda: self.app.set_mode(self.mode_var.get()))

    def _on_profile(self, _evt=None) -> None:
        self._guard(lambda: self.app.set_profile(self.profile_var.get()))

    def _on_save(self) -> None:
        exes = [e.strip() for e in self.exe_var.get().split(",") if e.strip()]
        hk = self.hotkey_var.get().strip()
        self._guard(lambda: self.app.save_profile(self.profile_var.get(), exes, hk))

    def _on_new(self) -> None:
        self._guard(lambda: self.app.new_profile())

    def _on_delete(self) -> None:
        self._guard(lambda: self.app.delete_profile(self.profile_var.get()))

    def _on_auto(self) -> None:
        self.app.set_auto_switch(self.auto_var.get())

    def _guard(self, fn) -> None:
        msg = ""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - show it, keep the window alive
            msg = str(exc)
        self._sync_from_app()
        self.status_var.set(msg or self.app.last_error or "")

    # ------------------------------------------------------------ preview
    def _schedule_preview(self) -> None:
        if self._preview_job is not None or self._closed:
            return
        self._preview_job = self.root.after(PREVIEW_DEBOUNCE_MS, self._render_preview)

    def _render_preview(self) -> None:
        self._preview_job = None
        if self._closed:
            return
        out = FrameEnhancer(self.app.params).process(self._scene)
        both = np.concatenate([self._scene, np.zeros((PREVIEW_H, 4, 3), np.uint8), out], axis=1)
        self._photo = _png_photo(both)  # keep a reference or Tk drops the image
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")

    # ------------------------------------------------------------ periodic
    def _tick(self) -> None:
        if self._closed:
            return
        self.app.pump()
        if self.app.quit_requested.is_set():
            self.close()
            return
        if self.app.version != self._seen_version:
            self._sync_from_app()
        self.root.after(PUMP_MS_GL if self.app.mode in ("preview", "overlay") else PUMP_MS,
                        self._tick)

    def _diag_tick(self) -> None:
        if self._closed:
            return
        self.refresh_diagnostics()
        self.root.after(DIAG_MS, self._diag_tick)

    def refresh_diagnostics(self) -> None:
        d = self.app.diagnostics()
        lines = [f"mode: {d['mode']}   profile: {d['profile']}   preset: {d['preset']}",
                 f"platform: {d['platform']['os']}/{d['platform']['session']}"
                 f" ({d['platform']['desktop'] or 'n/a'})"]
        for key in ("tone", "capture", "process", "present"):
            sect = d[key]
            lines.append(f"{key}: {sect.get('backend') or sect.get('mode') or 'none'}")
            lines += [f"    - {r}" for r in sect.get("reasons", [])]
        if d.get("gpu_renderer"):
            lines.append(f"GPU renderer: {d['gpu_renderer']}")
        lines.append("hotkeys: " + "; ".join(d["hotkeys"]["reasons"]))
        lines += [f"    ! {e}" for e in d["hotkeys"]["errors"]]
        if d["control"]["error"]:
            lines.append(f"control: {d['control']['error']}")
        st = d["stats"]
        lines.append("live: " + (f"{st['fps']:.1f} fps, {st['avg_process_ms']:.1f} ms/frame, "
                                 f"{st['frames_dropped']} dropped" if st else "idle"))
        if d["error"]:
            lines.append(f"error: {d['error']}")
        self.diag.config(state="normal")
        self.diag.delete("1.0", "end")
        self.diag.insert("1.0", "\n".join(lines))
        self.diag.config(state="disabled")

    # ------------------------------------------------------------ lifecycle
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.app.shutdown()
        finally:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def run(self) -> None:
        self.root.mainloop()
