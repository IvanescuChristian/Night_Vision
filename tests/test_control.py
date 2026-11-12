# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
import json
import os
import socket
import stat
import sys
import time

import pytest

from nightvision.control import ControlServer, send_command

posix = pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX path tests")


class Handler:
    def __init__(self):
        self.calls = []

    def __call__(self, msg):
        self.calls.append(msg)
        if msg["cmd"] == "status":
            return {"preset": "off", "enabled": True}
        return {}


@pytest.fixture(params=["unix", "tcp"])
def server(request, tmp_path):
    if request.param == "unix" and sys.platform == "win32":
        pytest.skip("no AF_UNIX")
    h = Handler()
    s = ControlServer(h, path=str(tmp_path / "nv.sock"), transport=request.param)
    s.start()
    s.handler = h
    yield s
    s.stop()


def test_roundtrip_all_commands(server):
    kw = dict(path=server.path, transport=server.transport)
    assert send_command({"cmd": "preset", "name": "tarkov_dark"}, **kw)["ok"]
    assert send_command({"cmd": "next_preset"}, **kw)["ok"]
    assert send_command({"cmd": "toggle"}, **kw)["ok"]
    assert send_command({"cmd": "profile", "name": "Rust"}, **kw)["ok"]
    st = send_command({"cmd": "status"}, **kw)
    assert st == {"ok": True, "preset": "off", "enabled": True}
    assert send_command({"cmd": "quit"}, **kw)["ok"]
    assert [c["cmd"] for c in server.handler.calls] == [
        "preset", "next_preset", "toggle", "profile", "status", "quit"]
    assert "token" not in server.handler.calls[0]


def test_unknown_and_malformed_rejected(server):
    kw = dict(path=server.path, transport=server.transport)
    assert not send_command({"cmd": "rm -rf"}, **kw)["ok"]
    assert not send_command({"cmd": "preset"}, **kw)["ok"]
    assert not send_command({"cmd": "preset", "name": "x" * 500}, **kw)["ok"]
    assert server.handler.calls == []


def test_handler_exception_reported(tmp_path):
    def boom(_):
        raise RuntimeError("nope")
    s = ControlServer(boom, path=str(tmp_path / "s"), transport="tcp")
    s.start()
    try:
        r = send_command({"cmd": "toggle"}, path=s.path, transport="tcp")
        assert r == {"ok": False, "error": "nope"}
    finally:
        s.stop()


def test_oversize_rejected(server):
    r = send_command({"cmd": "preset", "name": "a", "pad": "x" * 10000},
                     path=server.path, transport=server.transport)
    assert r["ok"] is False and "large" in r["error"]
    assert server.handler.calls == []
    # still serving afterwards
    assert send_command({"cmd": "toggle"}, path=server.path, transport=server.transport)["ok"]


def test_stop_joins_fast_and_cleans_up(tmp_path):
    s = ControlServer(Handler(), path=str(tmp_path / "s.sock"))
    s.start()
    t0 = time.monotonic()
    s.stop()
    assert time.monotonic() - t0 < 2.0
    assert not os.path.exists(s.path)
    with pytest.raises(ConnectionError):
        send_command({"cmd": "toggle"}, path=s.path, timeout=0.5)


def test_silent_client_does_not_block_server(server):
    if server.transport != "unix":
        pytest.skip("covered on unix")
    idle = socket.socket(socket.AF_UNIX)
    idle.connect(server.path)
    try:
        t0 = time.monotonic()
        assert send_command({"cmd": "toggle"}, path=server.path, timeout=5)["ok"]
        assert time.monotonic() - t0 < 4.0  # waits at most the 2 s client timeout
    finally:
        idle.close()


@posix
def test_stale_socket_cleanup_and_live_refusal(tmp_path):
    p = str(tmp_path / "x.sock")
    dead = socket.socket(socket.AF_UNIX)
    dead.bind(p)
    dead.close()  # file remains, nobody listening
    assert os.path.exists(p)
    s = ControlServer(Handler(), path=p)
    s.start()
    try:
        assert send_command({"cmd": "toggle"}, path=p)["ok"]
        with pytest.raises(RuntimeError, match="already"):
            ControlServer(Handler(), path=p).start()
    finally:
        s.stop()


def test_permissions_0600(server):
    if sys.platform == "win32":
        pytest.skip("POSIX modes")
    assert stat.S_IMODE(os.stat(server.path).st_mode) == 0o600
    if server.transport == "tcp":
        assert "token" in json.load(open(server.path))


def test_tcp_requires_token(tmp_path):
    s = ControlServer(Handler(), path=str(tmp_path / "i.json"), transport="tcp")
    s.start()
    try:
        port = json.load(open(s.path))["port"]
        c = socket.create_connection(("127.0.0.1", port), 2)
        c.sendall(b'{"cmd":"toggle"}\n')
        assert json.loads(c.recv(1024))["ok"] is False
        c.close()
    finally:
        s.stop()
