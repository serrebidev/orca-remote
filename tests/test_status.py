"""Orca+Alt+S speaks one accurate summary of the session."""

from __future__ import annotations

from unittest import mock

from tests.test_bypass_chords import _load_remote_module


class _Transport:
    def __init__(self, connected: bool) -> None:
        self.connected = connected


def _ext(mod, role="client", connected=True, host="95alive.net", port=6837):
    instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
    instance._settings = {
        mod.SETTING_HOST: host, mod.SETTING_PORT: port,
        mod.SETTING_CHANNEL: "yoga", mod.SETTING_ROLE: role,
    }
    instance._transport = _Transport(connected)
    instance._trust_dialog = None
    instance._channel_peers = {}
    instance._last_transport_status = ""
    instance._focus_on_remote = False
    instance._inbound_speech_muted = False
    instance._say = mock.Mock()
    return instance


def test_not_set_up():
    mod = _load_remote_module()
    ext = _ext(mod, host="")
    assert "not set up" in ext._status_report()


def test_disconnected_says_how_to_connect():
    mod = _load_remote_module()
    ext = _ext(mod)
    ext._transport = None
    report = ext._status_report()
    assert "disconnected from 95alive.net" in report
    assert "Page Up" in report


def test_retrying_reports_the_last_transport_status():
    mod = _load_remote_module()
    ext = _ext(mod, connected=False)
    ext._on_status("reconnecting in 30s")
    assert ext._status_report() == (
        "Orca Remote is not connected to 95alive.net. Last status: reconnecting in 30s."
    )


def test_waiting_on_the_trust_dialog():
    mod = _load_remote_module()
    ext = _ext(mod, connected=False)
    ext._trust_dialog = object()
    assert "waiting for you to trust" in ext._status_report()


def test_non_default_port_is_spoken():
    mod = _load_remote_module()
    ext = _ext(mod, port=7000)
    assert "95alive.net port 7000" in ext._status_report()


def test_host_counts_controllers_from_relay_frames():
    mod = _load_remote_module()
    ext = _ext(mod, role="host")
    ext._note_channel_joined({"type": "channel_joined", "user_ids": [5],
                              "clients": [{"id": 5, "connection_type": "master"}]})
    assert ext._status_report() == (
        "Orca Remote is connected to 95alive.net as host. "
        "One person can control this machine."
    )
    ext._note_client_joined({"type": "client_joined", "user_id": 6,
                             "client": {"id": 6, "connection_type": "master"}})
    assert "2 people can control" in ext._status_report()
    ext._note_client_left({"type": "client_left", "user_id": 5})
    ext._note_client_left({"type": "client_left", "user_id": 6})
    assert "No one is controlling this machine." in ext._status_report()


def test_client_reports_remote_focus_and_mute():
    mod = _load_remote_module()
    ext = _ext(mod)
    ext._note_client_joined({"type": "client_joined", "user_id": 9,
                             "client": {"id": 9, "connection_type": "slave"}})
    ext._focus_on_remote = True
    ext._inbound_speech_muted = True
    assert ext._status_report() == (
        "Orca Remote is connected to 95alive.net as client. "
        "The remote machine is connected. "
        "You are controlling the remote machine. Remote speech is muted."
    )


def test_client_with_no_host_on_the_channel():
    mod = _load_remote_module()
    ext = _ext(mod)
    assert "The remote machine is not connected." in ext._status_report()


def test_speak_status_speaks_the_report():
    mod = _load_remote_module()
    ext = _ext(mod)
    assert ext.speak_status() is True
    ext._say.assert_called_once_with(ext._status_report())


def test_orca_alt_s_stays_local_while_controlling():
    mod = _load_remote_module()
    instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
    instance._master_orca_mods_down = {0xffe5}
    assert instance._is_local_bypass_chord(0x73, mod._AT_SPI_MOD_ALT)
    assert not instance._is_local_bypass_chord(0x73, 0)
