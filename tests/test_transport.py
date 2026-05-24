"""Unit tests for the asyncio transport helpers.

These tests avoid real sockets. They cover the pure TLS/fingerprint
helpers and the send-path behavior that protects Orca from unbounded
writer backlog.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import ssl
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PKG_NAME = "orca_remote_transport_test"


def _load_transport_module():
    """Load transport.py as a package module so relative imports work."""

    pkg = types.ModuleType(PKG_NAME)
    pkg.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules[PKG_NAME] = pkg

    protocol_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.protocol",
        str(ROOT / "protocol.py"),
    )
    protocol = importlib.util.module_from_spec(protocol_spec)
    sys.modules[f"{PKG_NAME}.protocol"] = protocol
    protocol_spec.loader.exec_module(protocol)

    transport_spec = importlib.util.spec_from_file_location(
        f"{PKG_NAME}.transport",
        str(ROOT / "transport.py"),
    )
    transport = importlib.util.module_from_spec(transport_spec)
    sys.modules[f"{PKG_NAME}.transport"] = transport
    transport_spec.loader.exec_module(transport)
    return transport


class FakeAsyncioTransport:
    def __init__(self, buffered: int = 0):
        self.buffered = buffered

    def get_write_buffer_size(self) -> int:
        return self.buffered


class FakeWriter:
    def __init__(self, buffered: int = 0):
        self.transport = FakeAsyncioTransport(buffered)
        self.writes: list[bytes] = []
        self.drains = 0
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        self.drains += 1

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeSSLObject:
    def __init__(self, der: bytes):
        self.der = der

    def getpeercert(self, binary_form=False):
        assert binary_form is True
        return self.der


class FakeFingerprintWriter:
    def __init__(self, ssl_object):
        self.ssl_object = ssl_object

    def get_extra_info(self, name):
        assert name == "ssl_object"
        return self.ssl_object


async def _noop_message(_message):
    return None


class TestFingerprintHelpers:
    def test_normalize_fingerprint(self):
        transport = _load_transport_module()
        assert transport._normalize_fingerprint("AA:bb cc-12") == "aabbcc12"

    def test_peer_fingerprint_hashes_der_cert(self):
        transport = _load_transport_module()
        der = b"certificate bytes"
        writer = FakeFingerprintWriter(FakeSSLObject(der))
        assert transport._peer_fingerprint(writer) == hashlib.sha256(der).hexdigest()

    def test_peer_fingerprint_without_ssl_object(self):
        transport = _load_transport_module()
        writer = FakeFingerprintWriter(None)
        assert transport._peer_fingerprint(writer) == ""

    def test_ssl_context_uses_pin_based_trust(self):
        transport = _load_transport_module()
        ctx = transport._make_ssl_context()
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


class TestRemoteTransportSend:
    def test_send_without_writer_is_noop(self):
        transport_mod = _load_transport_module()
        statuses: list[str] = []
        remote = transport_mod.RemoteTransport(
            host="example.invalid",
            port=6837,
            channel="key",
            fingerprint="aa:bb",
            connection_type="master",
            on_message=_noop_message,
            on_status=statuses.append,
        )

        asyncio.run(remote.send({"type": "ping"}))

        assert statuses == []
        assert remote._dropped_outbound == 0

    def test_send_writes_encoded_message_and_drains(self):
        transport_mod = _load_transport_module()
        remote = transport_mod.RemoteTransport(
            host="example.invalid",
            port=6837,
            channel="key",
            fingerprint="aa:bb",
            connection_type="master",
            on_message=_noop_message,
        )
        writer = FakeWriter(buffered=0)
        remote._writer = writer

        asyncio.run(remote.send({"type": "ping"}))

        assert writer.writes == [transport_mod.protocol.encode({"type": "ping"})]
        assert writer.drains == 1
        assert remote._dropped_outbound == 0

    def test_send_drops_when_writer_buffer_is_congested(self):
        transport_mod = _load_transport_module()
        statuses: list[str] = []
        remote = transport_mod.RemoteTransport(
            host="example.invalid",
            port=6837,
            channel="key",
            fingerprint="aa:bb",
            connection_type="master",
            on_message=_noop_message,
            on_status=statuses.append,
        )
        writer = FakeWriter(buffered=transport_mod._MAX_WRITE_BUFFER + 1)
        remote._writer = writer

        asyncio.run(remote.send({"type": "speak", "sequence": ["stale"]}))

        assert writer.writes == []
        assert writer.drains == 0
        assert remote._dropped_outbound == 1
        assert statuses
        assert "outbound buffer congested" in statuses[0]

    def test_constructor_normalizes_expected_fingerprint(self):
        transport_mod = _load_transport_module()
        remote = transport_mod.RemoteTransport(
            host="example.invalid",
            port=6837,
            channel="key",
            fingerprint="AA:BB CC",
            connection_type="master",
            on_message=_noop_message,
        )
        assert remote._expected_fp == "aabbcc"
