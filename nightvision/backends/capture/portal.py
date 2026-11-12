# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Wayland capture: xdg-desktop-portal ScreenCast -> PipeWire -> GStreamer appsink.

NOT VERIFIED: this module could not be run in the development environment (no
Wayland compositor, portal, PyGObject or GStreamer). It was written against the
documented portal API (org.freedesktop.portal.ScreenCast v1+) and the
GStreamer `pipewiresrc` element. Expect to debug it on a real session.

Flow: CreateSession -> SelectSources -> Start -> OpenPipeWireRemote, each
request answered asynchronously through a Request.Response signal. The
compositor shows a permission dialog to the user at Start(); that explicit
consent is the intended, safe path (nothing is captured without it).
Requires optional PyGObject (`gi`) with Gio, GLib, Gst (+ pipewire gst plugin).
All imports are guarded so the module always imports.
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

import numpy as np

from .base import CaptureError, CaptureSource

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gio, GLib, Gst
    _GI_ERR = ""
except Exception as _e:  # noqa: BLE001
    gi = Gio = GLib = Gst = None
    _GI_ERR = f"PyGObject (gi) with Gio/GLib/Gst not available ({_e.__class__.__name__}: {_e})"

_PORTAL = "org.freedesktop.portal.Desktop"
_PATH = "/org/freedesktop/portal/desktop"
_SC = "org.freedesktop.portal.ScreenCast"
_REQ = "org.freedesktop.portal.Request"
_RESPONSE_TIMEOUT_S = 120  # the user may take a while to answer the dialog


class PortalPipeWireCapture(CaptureSource):
    name = "portal-pipewire"

    def __init__(self, env=None, cursor: bool = False):
        super().__init__()
        self._env = os.environ if env is None else env
        self._cursor = cursor
        self._conn = None
        self._session: Optional[str] = None
        self._fd: Optional[int] = None
        self._pipe = None
        self._sink = None
        self._size = (0, 0)
        self._first: Optional[np.ndarray] = None

    # -- availability ---------------------------------------------------
    def available(self) -> bool:
        return not self.unavailable_reason()

    def unavailable_reason(self) -> str:
        try:
            if _GI_ERR:
                return _GI_ERR
            if not (self._env.get("WAYLAND_DISPLAY")
                    or self._env.get("XDG_SESSION_TYPE", "").lower() == "wayland"):
                return "not a Wayland session"
            if not self._env.get("DBUS_SESSION_BUS_ADDRESS") and not self._env.get("XDG_RUNTIME_DIR"):
                return "no D-Bus session bus"
            Gst.init(None)
            if Gst.ElementFactory.find("pipewiresrc") is None:
                return "GStreamer element pipewiresrc missing (install gst-plugin-pipewire)"
            return ""
        except Exception as e:  # noqa: BLE001 - contract: never raise
            return f"portal probe failed: {e}"

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    # -- portal handshake -------------------------------------------------
    def _request(self, method: str, args, options: dict, *, with_handle_prefix=()):
        """Call a portal method that returns a Request handle and wait for its
        Response signal. Returns the response 'results' dict (unpacked)."""
        sender = self._conn.get_unique_name()[1:].replace(".", "_")
        token = "nv" + secrets.token_hex(6)
        options = dict(options, handle_token=GLib.Variant("s", token))
        req_path = f"{_PATH}/request/{sender}/{token}"
        loop = GLib.MainLoop()
        out: dict = {}

        def on_resp(_c, _s, _p, _i, _sig, params):
            out["code"], out["results"] = params.unpack()
            loop.quit()

        # Subscribe BEFORE the call: the response can arrive before the call returns.
        sub = self._conn.signal_subscribe(_PORTAL, _REQ, "Response", req_path, None,
                                          Gio.DBusSignalFlags.NONE, on_resp)
        GLib.timeout_add_seconds(_RESPONSE_TIMEOUT_S, lambda: (out.setdefault("timeout", True), loop.quit(), False)[2])
        try:
            variant_args = list(with_handle_prefix) + list(args) + [GLib.Variant("a{sv}", options)]
            self._conn.call_sync(_PORTAL, _PATH, _SC, method,
                                 GLib.Variant.new_tuple(*variant_args),
                                 None, Gio.DBusCallFlags.NONE, -1, None)
            if "code" not in out:
                loop.run()
        finally:
            self._conn.signal_unsubscribe(sub)
        if out.get("timeout"):
            raise CaptureError(f"portal: {method} timed out waiting for user/compositor")
        if out.get("code") != 0:
            raise CaptureError(f"portal: {method} was denied or cancelled (code {out.get('code')})")
        return out["results"]

    def _open(self) -> None:
        reason = self.unavailable_reason()
        if reason:
            raise CaptureError(f"portal: {reason}")
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = self._request("CreateSession", [], {
                "session_handle_token": GLib.Variant("s", "nv" + secrets.token_hex(6))})
            self._session = res["session_handle"]
            sess = GLib.Variant("o", self._session)
            self._request("SelectSources", [], {
                "types": GLib.Variant("u", 1),            # MONITOR
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", 2 if self._cursor else 1),  # embedded / hidden
            }, with_handle_prefix=[sess])
            res = self._request("Start", [GLib.Variant("s", "")], {}, with_handle_prefix=[sess])
            streams = res.get("streams") or []
            if not streams:
                raise CaptureError("portal: Start returned no streams")
            node_id = int(streams[0][0])
            self._fd = self._open_remote()
            self._start_pipeline(node_id)
        except CaptureError:
            self._close()
            raise
        except Exception as e:  # noqa: BLE001
            self._close()
            raise CaptureError(f"portal: {e}") from e

    def _open_remote(self) -> int:
        ret, fds = self._conn.call_with_unix_fd_list_sync(
            _PORTAL, _PATH, _SC, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self._session, {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, -1, None, None)
        return fds.get(ret.unpack()[0])  # fd is ours (dup'd by GIO)

    def _start_pipeline(self, node_id: int) -> None:
        Gst.init(None)
        desc = (f"pipewiresrc fd={self._fd} path={node_id} ! videoconvert ! "
                "video/x-raw,format=BGRA ! "
                "appsink name=sink max-buffers=1 drop=true sync=false")
        self._pipe = Gst.parse_launch(desc)
        self._sink = self._pipe.get_by_name("sink")
        if self._pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise CaptureError("portal: GStreamer pipeline failed to start")
        # Wait for the first sample so size is known before the pipeline reads it.
        sample = self._sink.try_pull_sample(10 * Gst.SECOND)
        if sample is None:
            raise CaptureError("portal: no frame from PipeWire within 10 s")
        s = sample.get_caps().get_structure(0)
        self._size = (s.get_value("width"), s.get_value("height"))
        self._first = self._to_array(sample)

    def _to_array(self, sample) -> Optional[np.ndarray]:
        buf = sample.get_buffer()
        ok, mi = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            w, h = self._size
            raw = np.frombuffer(mi.data, dtype=np.uint8)
            stride = raw.size // h  # rows may be padded
            return raw.reshape(h, stride)[:, : w * 4].reshape(h, w, 4).copy()
        finally:
            buf.unmap(mi)

    def _grab(self) -> Optional[np.ndarray]:
        first = self._first
        if first is not None:
            self._first = None
            return first
        sample = self._sink.try_pull_sample(50 * Gst.MSECOND)  # short wait keeps stop() responsive
        return None if sample is None else self._to_array(sample)

    def _close(self) -> None:
        pipe, self._pipe, self._sink, self._first = self._pipe, None, None, None
        if pipe is not None:
            try:
                pipe.set_state(Gst.State.NULL)
            except Exception:  # noqa: BLE001
                pass
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._conn is not None and self._session:
            try:
                self._conn.call_sync(_PORTAL, self._session, "org.freedesktop.portal.Session",
                                     "Close", None, None, Gio.DBusCallFlags.NONE, 2000, None)
            except Exception:  # noqa: BLE001
                pass
        self._session = None
        self._conn = None
