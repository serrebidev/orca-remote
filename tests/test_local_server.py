"""The bundled relay keeps serving while one peer stalls its handshake."""

from __future__ import annotations

import importlib.util
import json
import socket
import ssl
import time
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "orca-scripts" / "local_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("orca_remote_local_server", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    pytest.skip("relay did not start (no certificate tooling?)")


def _tls_join(port: int) -> dict:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=3) as raw:
        with ctx.wrap_socket(raw) as tls:
            tls.sendall(b'{"type":"protocol_version","version":2}\n'
                        b'{"type":"join","channel":"t","connection_type":"master"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


def test_silent_peer_does_not_block_other_clients():
    mod = _load()
    port = _free_port()
    server = mod.LocalRelayServer(port)
    server.start()
    try:
        _wait_listening(port, time.monotonic() + 15)
        # Opens TCP and never says anything, like a port scanner. Before
        # the fix the accept loop sat in this peer's handshake forever.
        silent = socket.create_connection(("127.0.0.1", port))
        try:
            time.sleep(0.2)
            started = time.monotonic()
            reply = _tls_join(port)
            assert reply["type"] == "channel_joined"
            assert time.monotonic() - started < mod.HANDSHAKE_TIMEOUT
        finally:
            silent.close()
    finally:
        server.stop()
