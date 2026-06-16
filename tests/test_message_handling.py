"""Tests for RemoteExtension inbound message dispatch."""

from __future__ import annotations

import asyncio
from unittest import mock

from tests.test_bypass_chords import _load_remote_module


def _new_message_instance(mod):
    instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
    instance._say_async = mock.Mock()
    instance._log = mock.Mock()
    instance._current_role = mock.Mock(return_value="client")
    instance._focus_on_remote = False
    instance._inbound_speech_muted = False
    instance._announced_join = True
    instance._peer_not_connected_announced = False
    instance._dropped_nonstring_items = 0
    instance._handle_inbound_key = mock.AsyncMock()
    return instance


class TestConnectionMessages:
    def test_client_joined_is_announced(self):
        mod = _load_remote_module()
        instance = _new_message_instance(mod)

        asyncio.run(instance._on_message({"type": mod.protocol.MSG_CLIENT_JOINED}))

        instance._say_async.assert_called_once_with("Orca Remote: peer joined.")
        instance._log.assert_not_called()

    def test_nvda_not_connected_is_announced(self):
        mod = _load_remote_module()
        instance = _new_message_instance(mod)

        asyncio.run(
            instance._on_message({"type": mod.protocol.MSG_NVDA_NOT_CONNECTED})
        )

        instance._say_async.assert_called_once_with(
            "Orca Remote: peer is not connected."
        )
        instance._log.assert_not_called()

    def test_nvda_not_connected_repeats_are_suppressed(self):
        mod = _load_remote_module()
        instance = _new_message_instance(mod)
        message = {"type": mod.protocol.MSG_NVDA_NOT_CONNECTED}

        asyncio.run(instance._on_message(message))
        asyncio.run(instance._on_message(message))

        instance._say_async.assert_called_once_with(
            "Orca Remote: peer is not connected."
        )

    def test_client_joined_resets_not_connected_gate(self):
        mod = _load_remote_module()
        instance = _new_message_instance(mod)

        asyncio.run(
            instance._on_message({"type": mod.protocol.MSG_NVDA_NOT_CONNECTED})
        )
        asyncio.run(instance._on_message({"type": mod.protocol.MSG_CLIENT_JOINED}))
        asyncio.run(
            instance._on_message({"type": mod.protocol.MSG_NVDA_NOT_CONNECTED})
        )

        assert instance._say_async.call_args_list == [
            mock.call("Orca Remote: peer is not connected."),
            mock.call("Orca Remote: peer joined."),
            mock.call("Orca Remote: peer is not connected."),
        ]
