# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Local control channel so a running instance can be driven from the CLI,
desktop-environment shortcuts (essential on Wayland) or scripts.

Local-only IPC. It never touches games and never listens on a network
interface. The server only maps a fixed set of commands onto the handler;
it executes nothing from the message.

Transport choice
----------------
POSIX: AF_UNIX socket, 0600, in $XDG_RUNTIME_DIR (already per-user 0700).
Windows: localhost TCP on an ephemeral port plus a random token stored in a
per-user file. Python's socket module does not reliably expose AF_UNIX on
Windows (Win10 1803 kernel support, but not in CPython builds), so TCP is the
dependable option; the token stops other local users/processes that cannot
read the user's file from issuing commands.

Protocol: one JSON object per line, max 4 KiB, one reply line per request.
Handler runs on the server thread: marshal to the UI thread if needed.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import stat
import sys
import tempfile
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096
COMMANDS = {"preset", "next_preset", "toggle", "profile", "status", "quit"}
_NAME_CMDS = {"preset", "profile"}
_MAX_NAME = 128
_CLIENT_TIMEOUT = 2.0


def default_path() -> str:
    """Socket path (POSIX) or token-info file path (Windows)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        d = os.path.join(base, "NightVision")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "control.json")
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if rt and os.path.isdir(rt):
        return os.path.join(rt, "nightvision.sock")
    d = os.path.join(tempfile.gettempdir(), f"nightvision-{os.getuid()}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.stat(d)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise RuntimeError(f"unsafe control directory {d}")
    return os.path.join(d, "nightvision.sock")


def _validate(msg) -> Optional[str]:
    if not isinstance(msg, dict):
        return "message must be a JSON object"
    cmd = msg.get("cmd")
    if cmd not in COMMANDS:
        return f"unknown cmd {cmd!r}"
    if cmd in _NAME_CMDS:
        name = msg.get("name")
        if not isinstance(name, str) or not name or len(name) > _MAX_NAME:
            return "name must be a non-empty string"
    return None


def _read_line(conn: socket.socket) -> Optional[bytes]:
    """None if the message exceeds MAX_MESSAGE or the peer sent nothing."""
    buf = b""
    while b"\n" not in buf:
        if len(buf) > MAX_MESSAGE:
            return None
        chunk = conn.recv(1024)
        if not chunk:
            break
        buf += chunk
    line = buf.split(b"\n", 1)[0]
    return line if 0 < len(line) <= MAX_MESSAGE else None


def _drain(conn: socket.socket) -> None:
    # WHY: closing with unread data makes the kernel send RST, which can wipe
    # the error reply before an oversize sender reads it.
    conn.settimeout(0.2)
    try:
        for _ in range(64):
            if not conn.recv(4096):
                break
    except OSError:
        pass


class ControlServer:
    def __init__(self, handler: Callable[[dict], Optional[dict]], path: Optional[str] = None,
                 transport: Optional[str] = None) -> None:
        self._handler = handler
        self.path = path or default_path()
        self.transport = transport or ("tcp" if sys.platform == "win32" else "unix")
        if self.transport not in ("unix", "tcp"):
            raise ValueError("transport must be 'unix' or 'tcp'")
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._token = ""

    def start(self) -> None:
        self._stop.clear()
        if self.transport == "unix":
            self._sock = self._bind_unix()
        else:
            self._sock = self._bind_tcp()
        self._sock.listen(4)
        self._sock.settimeout(0.2)  # lets the loop notice the stop event
        self._thread = threading.Thread(target=self._serve, name="nv-control", daemon=True)
        self._thread.start()

    def _bind_unix(self) -> socket.socket:
        if os.path.exists(self.path):
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.5)
            try:
                probe.connect(self.path)
            except OSError:
                os.unlink(self.path)  # stale: nobody is listening
            else:
                raise RuntimeError(f"another instance is already listening on {self.path}")
            finally:
                probe.close()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)  # never exist world-accessible, even briefly
        try:
            s.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        return s

    def _tcp_instance_alive(self) -> bool:
        """True when the port recorded in an existing info file still accepts connections."""
        try:
            with open(self.path) as f:
                port = int(json.load(f)["port"])
            socket.create_connection(("127.0.0.1", port), 0.5).close()
        except (OSError, ValueError, KeyError, TypeError):
            return False  # missing/stale/corrupt file: nobody is listening
        return True

    def _bind_tcp(self) -> socket.socket:
        # WHY: unlike an AF_UNIX path, an ephemeral TCP port never collides, so without
        # this a second instance would silently overwrite the first one's port/token
        # file and both would run, with the first no longer reachable by `ctl`.
        if os.path.exists(self.path) and self._tcp_instance_alive():
            raise RuntimeError(f"another instance is already listening (see {self.path})")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        self._token = secrets.token_hex(16)
        info = json.dumps({"port": s.getsockname()[1], "token": self._token})
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(info)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return s

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(_CLIENT_TIMEOUT)  # a silent client must not block others
                    self._handle(conn)
                except Exception as exc:
                    log.debug("control client error: %s", exc)

    def _handle(self, conn: socket.socket) -> None:
        line = _read_line(conn)
        if line is None:
            reply = {"ok": False, "error": "message too large or empty"}
        else:
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                msg = None
                reply = {"ok": False, "error": "invalid JSON"}
            else:
                err = _validate(msg)
                if err is None and self.transport == "tcp" and not secrets.compare_digest(
                        str(msg.get("token", "")), self._token):
                    err = "bad token"
                if err:
                    reply = {"ok": False, "error": err}
                else:
                    msg.pop("token", None)
                    try:
                        res = self._handler(msg) or {}
                        reply = {"ok": True, **res}
                    except Exception as exc:
                        log.exception("control handler failed")
                        reply = {"ok": False, "error": str(exc)}
        conn.sendall(json.dumps(reply).encode() + b"\n")
        if line is None:
            _drain(conn)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


def send_command(cmd: dict, path: Optional[str] = None, timeout: float = 2,
                 transport: Optional[str] = None) -> dict:
    """Send one command; returns the reply dict. Raises ConnectionError if no
    instance is running."""
    path = path or default_path()
    transport = transport or ("tcp" if sys.platform == "win32" else "unix")
    payload = dict(cmd)
    try:
        if transport == "unix":
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(path)
        else:
            with open(path) as f:
                info = json.load(f)
            payload["token"] = info["token"]
            s = socket.create_connection(("127.0.0.1", int(info["port"])), timeout)
    except (OSError, ValueError, KeyError) as exc:
        raise ConnectionError(f"no running nightvision instance ({exc})") from exc
    with s:
        s.settimeout(timeout)
        s.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while b"\n" not in buf and len(buf) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    if not buf.strip():
        raise ConnectionError("empty reply")
    return json.loads(buf.split(b"\n", 1)[0])
