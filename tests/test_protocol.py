"""Unit tests for protocol.py.

Pure-function module; no Orca / Gtk / asyncio imports needed. Run
with `python3 -m pytest tests/test_protocol.py` from the repo root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make repo root importable so `import protocol` works without
# packaging gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import protocol  # noqa: E402


class TestEncode:
    def test_roundtrip_simple(self) -> None:
        msg = {"type": "speak", "sequence": ["hello"]}
        encoded = protocol.encode(msg)
        assert encoded.endswith(b"\n")
        decoded = protocol.decode(encoded)
        assert decoded == msg

    def test_encode_is_compact(self) -> None:
        # No spaces in separators -- matters for wire efficiency
        # under heavy speak load.
        encoded = protocol.encode({"type": "ping"})
        assert b": " not in encoded
        assert b", " not in encoded

    def test_encode_unicode(self) -> None:
        msg = {"type": "speak", "sequence": ["héllo, 世界"]}
        encoded = protocol.encode(msg)
        decoded = protocol.decode(encoded)
        assert decoded == msg


class TestDecode:
    def test_decode_strips_lf(self) -> None:
        line = b'{"type":"ping"}\n'
        assert protocol.decode(line) == {"type": "ping"}

    def test_decode_strips_crlf(self) -> None:
        line = b'{"type":"ping"}\r\n'
        assert protocol.decode(line) == {"type": "ping"}

    def test_decode_no_newline(self) -> None:
        # Trailing newline tolerated but not required.
        assert protocol.decode(b'{"type":"ping"}') == {"type": "ping"}

    def test_decode_missing_type_raises(self) -> None:
        with pytest.raises(protocol.ProtocolError):
            protocol.decode(b'{"foo":"bar"}\n')

    def test_decode_not_an_object_raises(self) -> None:
        with pytest.raises(protocol.ProtocolError):
            protocol.decode(b'[]\n')

    def test_decode_malformed_json_raises(self) -> None:
        with pytest.raises(protocol.ProtocolError):
            protocol.decode(b'{this is not json\n')

    def test_decode_invalid_utf8_raises(self) -> None:
        with pytest.raises(protocol.ProtocolError):
            protocol.decode(b'\xff\xfe\n')


class TestBuilders:
    def test_protocol_version(self) -> None:
        msg = protocol.build_protocol_version()
        assert msg["type"] == "protocol_version"
        assert msg["version"] == protocol.PROTOCOL_VERSION

    def test_join_master(self) -> None:
        msg = protocol.build_join("chankey", protocol.CONNECTION_TYPE_MASTER)
        assert msg == {
            "type": "join",
            "channel": "chankey",
            "connection_type": "master",
        }

    def test_join_slave(self) -> None:
        msg = protocol.build_join("chankey", protocol.CONNECTION_TYPE_SLAVE)
        assert msg["connection_type"] == "slave"

    def test_join_defaults_to_master(self) -> None:
        msg = protocol.build_join("chankey")
        assert msg["connection_type"] == "master"


class TestExtractSpeechText:
    def test_string_only(self) -> None:
        msg = {"type": "speak", "sequence": ["hello", " world"]}
        text, dropped = protocol.extract_speech_text(msg)
        assert text == "hello world"
        assert dropped == 0

    def test_mixed_sequence_counts_drops(self) -> None:
        # Real NVDA-Remote speak frames mix strings with dicts for
        # LangChangeCommand, IndexCommand, etc.
        msg = {
            "type": "speak",
            "sequence": [
                {"type": "LangChangeCommand", "language": "en_US"},
                "hello ",
                {"type": "IndexCommand", "index": 1},
                "world",
            ],
        }
        text, dropped = protocol.extract_speech_text(msg)
        assert text == "hello world"
        assert dropped == 2

    def test_empty_sequence(self) -> None:
        text, dropped = protocol.extract_speech_text(
            {"type": "speak", "sequence": []}
        )
        assert text == ""
        assert dropped == 0

    def test_missing_sequence(self) -> None:
        # Defensive: a malformed peer could omit `sequence` entirely.
        text, dropped = protocol.extract_speech_text({"type": "speak"})
        assert text == ""
        assert dropped == 0

    def test_null_sequence(self) -> None:
        text, dropped = protocol.extract_speech_text(
            {"type": "speak", "sequence": None}
        )
        assert text == ""
        assert dropped == 0

    def test_trims_whitespace(self) -> None:
        msg = {"type": "speak", "sequence": ["  hello  "]}
        text, _ = protocol.extract_speech_text(msg)
        assert text == "hello"


class TestConstants:
    def test_message_constants_match_nvda_remote_v2(self) -> None:
        # Pin the wire vocabulary so a refactor doesn't silently
        # rename a constant in a way that breaks NVDA Remote interop.
        assert protocol.MSG_PROTOCOL_VERSION == "protocol_version"
        assert protocol.MSG_JOIN == "join"
        assert protocol.MSG_SPEAK == "speak"
        assert protocol.MSG_CANCEL == "cancel"
        assert protocol.MSG_PAUSE_SPEECH == "pause_speech"
        assert protocol.MSG_KEY == "key"
        assert protocol.MSG_CHANNEL_JOINED == "channel_joined"
        assert protocol.MSG_CLIENT_LEFT == "client_left"
        assert protocol.MSG_MOTD == "motd"
        assert protocol.MSG_SET_CLIPBOARD_TEXT == "set_clipboard_text"
        assert protocol.MSG_SET_BRAILLE_INFO == "set_braille_info"
        assert protocol.MSG_DISPLAY == "display"
        assert protocol.CONNECTION_TYPE_MASTER == "master"
        assert protocol.CONNECTION_TYPE_SLAVE == "slave"
        assert protocol.PROTOCOL_VERSION == 2
