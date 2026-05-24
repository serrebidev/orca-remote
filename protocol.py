"""NVDA Remote v2.x wire protocol -- clean-room implementation.

The wire is newline-delimited JSON over TLS. Each message is a JSON
object with a `type` field plus type-specific keys. This module
defines the constants we use, the (de)serializers, and small helpers
for building the outbound messages Stage 1 needs (protocol_version,
join, ping reply). Inbound messages are returned as plain dicts and
dispatched by the extension.

Compatibility target: NVDA Remote v2.x. The relay (e.g.
nvdaremote.com) just forwards bytes between peers on the same
channel; it does not interpret most message types.
"""

from __future__ import annotations

import json
from typing import Any, Iterable


# Protocol version we announce on connect. NVDA Remote v2 servers
# accept v2; the constant lives here so Stage 2+ can bump it if/when
# we adopt later protocol revisions.
PROTOCOL_VERSION = 2

# Line terminator used by the wire framing. NVDA Remote uses LF only.
LINE_TERMINATOR = b"\n"


# Outbound message types we send from the client.
MSG_PROTOCOL_VERSION = "protocol_version"
MSG_JOIN = "join"
MSG_PING = "ping"

# Inbound message types Stage 1 cares about. Anything else is logged
# and ignored.
MSG_CHANNEL_JOINED = "channel_joined"
MSG_CLIENT_JOINED = "client_joined"
MSG_CLIENT_LEFT = "client_left"
MSG_MOTD = "motd"
MSG_SPEAK = "speak"
MSG_CANCEL = "cancel"
MSG_PAUSE_SPEECH = "pause_speech"
MSG_NVDA_NOT_CONNECTED = "nvda_not_connected"
MSG_KEY = "key"
MSG_SET_CLIPBOARD_TEXT = "set_clipboard_text"
MSG_SET_BRAILLE_INFO = "set_braille_info"
MSG_DISPLAY = "display"


# Connection type sent in the join message. "master" = we are the
# controller (we receive speech/braille, we send keys). "slave" = we
# are being controlled (we broadcast speech/braille, we accept keys).
# Stage 1 is always "master".
CONNECTION_TYPE_MASTER = "master"
CONNECTION_TYPE_SLAVE = "slave"


class ProtocolError(Exception):
    """Raised when a frame is not valid JSON or lacks a `type` field."""


def encode(message: dict[str, Any]) -> bytes:
    """Serialize a message dict to a single newline-terminated JSON line."""

    return json.dumps(message, separators=(",", ":")).encode("utf-8") + LINE_TERMINATOR


def decode(line: bytes) -> dict[str, Any]:
    """Parse one wire line into a message dict.

    The trailing newline is tolerated but not required. Raises
    ProtocolError on malformed JSON or a missing `type` field; the
    transport layer logs and drops such lines rather than aborting
    the connection.
    """

    try:
        obj = json.loads(line.rstrip(b"\r\n").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError(f"invalid JSON: {error}") from error
    if not isinstance(obj, dict) or "type" not in obj:
        raise ProtocolError("message lacks 'type' field")
    return obj


def build_protocol_version() -> dict[str, Any]:
    """Build the protocol_version handshake message."""

    return {"type": MSG_PROTOCOL_VERSION, "version": PROTOCOL_VERSION}


def build_join(channel: str, connection_type: str = CONNECTION_TYPE_MASTER) -> dict[str, Any]:
    """Build the join message that subscribes us to a channel.

    `channel` is the shared key passphrase. `connection_type` is
    "master" for Stage 1 (we want to receive speech from the remote).
    """

    return {
        "type": MSG_JOIN,
        "channel": channel,
        "connection_type": connection_type,
    }


def build_ping() -> dict[str, Any]:
    """Build a ping message (used to reply to server pings if any)."""

    return {"type": MSG_PING}


def extract_speech_text(speak_message: dict[str, Any]) -> tuple[str, int]:
    """Pull human-readable text and a dropped-item count from a `speak`.

    NVDA Remote's `speak` payload is `{"sequence": [...]}` where each
    element is either a plain string (text to speak) or a dict
    describing a speech command (IndexCommand, LangChangeCommand,
    CharacterModeCommand, etc.). We ignore commands and concatenate
    the plain-string fragments, which is what the user actually hears.

    Returns (text, dropped) where `dropped` is the number of sequence
    items that were NOT plain strings. Callers can surface a counter
    so the user can see when we're losing structure (e.g. language
    switches, index marks) -- silent drops were previously invisible.
    """

    sequence: Iterable[Any] = speak_message.get("sequence", []) or []
    parts: list[str] = []
    dropped = 0
    for item in sequence:
        if isinstance(item, str):
            parts.append(item)
        else:
            dropped += 1
    return "".join(parts).strip(), dropped
