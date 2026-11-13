"""Clipboard push and fingerprint-pin handling regressions.

Two live findings on Orca 51.alpha (perf build, rev 92a603f):

- Its ClipboardPresenter has no get_text, so the controller's
  get_clipboard_text() always returns "", and clipboard push always
  claimed the clipboard was empty. The extension now falls back to the
  Gtk clipboard.
- Pasting a fingerprint into the pre-filled settings field appends to
  the stored value, producing a pin that can never match; the connect
  loop then reported only "fingerprint did not match". Malformed pins
  are now normalized/rejected with a spoken reason instead.
"""

from __future__ import annotations

from unittest import mock

from tests.test_bypass_chords import _load_remote_module

GOOD_PIN = "431acb53822c437de4a5fb774955c73a94c3040ccc571373955665696b6f4594"


class _FakeClipboard:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.written: list[str] = []

    def wait_for_text(self) -> str:
        return self.text

    def set_text(self, text: str, _length: int) -> None:
        self.written.append(text)


def _instance(mod):
    instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
    instance._settings = {"role": "client"}
    instance._schedule_send = mock.Mock()
    instance._say = mock.Mock()
    instance._log = mock.Mock()
    instance._save_settings = mock.Mock()
    instance._restart_transport = mock.Mock()
    instance._disabled = False
    return instance


class TestClipboardPush:
    def test_receiving_clipboard_uses_gtk_when_controller_has_no_helper(self, monkeypatch):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance.controller = mock.Mock()
        instance.controller.set_clipboard_text.side_effect = AttributeError("no clipboard helper")
        clipboard = _FakeClipboard()
        monkeypatch.setattr(mod, "_gtk_clipboard", lambda: clipboard)

        instance._set_clipboard_idle_cb("remote clipboard")

        assert clipboard.written == ["remote clipboard"]

    """push_clipboard must not depend on the controller's read helper."""

    def test_falls_back_to_gtk_when_the_controller_returns_nothing(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._transport = object()
        instance.controller = mock.Mock()
        instance.controller.get_clipboard_text.return_value = ""
        clipboard = _FakeClipboard("text from gtk")

        with mock.patch.object(mod, "_gtk_clipboard", return_value=clipboard):
            instance.push_clipboard()

        assert instance._schedule_send.call_args.args[0] == {
            "type": mod.protocol.MSG_SET_CLIPBOARD_TEXT,
            "text": "text from gtk",
        }
        assert "pushed clipboard" in instance._say.call_args.args[0]

    def test_still_reports_an_empty_clipboard(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._transport = object()
        instance.controller = mock.Mock()
        instance.controller.get_clipboard_text.return_value = ""

        with mock.patch.object(mod, "_gtk_clipboard", return_value=_FakeClipboard("")):
            instance.push_clipboard()

        instance._schedule_send.assert_not_called()
        assert "empty" in instance._say.call_args.args[0]

    def test_controller_text_wins_when_present(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._transport = object()
        instance.controller = mock.Mock()
        instance.controller.get_clipboard_text.return_value = "controller text"

        with mock.patch.object(mod, "_gtk_clipboard", return_value=_FakeClipboard("gtk")):
            instance.push_clipboard()

        assert instance._schedule_send.call_args.args[0]["text"] == "controller text"


class TestFingerprintPin:
    """Malformed pins must be refused loudly, not looped on."""

    def test_rejects_a_paste_appended_to_the_old_value(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        result = {mod.SETTING_FINGERPRINT: "00" * _FINGERPRINT_LEN(mod) + GOOD_PIN}

        rejected = instance._reject_bad_fingerprint(result)

        assert rejected == 128
        assert mod.SETTING_FINGERPRINT not in result

    def test_normalizes_separators_and_case(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        upper = GOOD_PIN.upper()
        colonized = ":".join(upper[i:i + 2] for i in range(0, len(upper), 2))
        result = {mod.SETTING_FINGERPRINT: colonized}

        rejected = instance._reject_bad_fingerprint(result)

        assert rejected == 0
        assert result[mod.SETTING_FINGERPRINT] == GOOD_PIN

    def test_empty_pin_is_allowed(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        result = {mod.SETTING_FINGERPRINT: ""}

        assert instance._reject_bad_fingerprint(result) == 0
        assert result[mod.SETTING_FINGERPRINT] == ""

    def test_settings_save_keeps_the_old_pin_and_says_why(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._settings = {mod.SETTING_FINGERPRINT: GOOD_PIN, mod.SETTING_CHANNEL: "old"}
        instance._settings_dialog = None

        instance._on_settings_result({
            mod.SETTING_FINGERPRINT: "ff" * _FINGERPRINT_LEN(mod) + GOOD_PIN,
            mod.SETTING_CHANNEL: "new-channel",
        })

        assert instance._settings[mod.SETTING_FINGERPRINT] == GOOD_PIN
        assert instance._settings[mod.SETTING_CHANNEL] == "new-channel"
        assert "not 64" in instance._say.call_args.args[0]
        instance._restart_transport.assert_called_once()

    def test_mismatch_is_handed_to_the_glib_thread(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        with mock.patch.object(mod.GLib, "idle_add", create=True) as idle_add:
            instance._on_fingerprint_mismatch(GOOD_PIN)
        idle_add.assert_called_once_with(instance._fingerprint_prompt_idle_cb, GOOD_PIN)

    def test_without_a_dialog_the_user_gets_manual_instructions(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._trust_dialog = None
        instance._settings = {mod.SETTING_HOST: "relay", mod.SETTING_PORT: 6837}
        instance._write_fingerprint_file = mock.Mock(return_value="")
        instance._copy_fingerprint_idle_cb = mock.Mock()

        with mock.patch.object(mod, "build_trust_dialog", side_effect=RuntimeError("no gtk")), \
                mock.patch.object(mod, "_fingerprint_file_path", return_value="/home/u/fp.txt"):
            instance._fingerprint_prompt_idle_cb(GOOD_PIN)

        instance._copy_fingerprint_idle_cb.assert_called_once_with(GOOD_PIN)
        announcement = instance._say.call_args.args[0]
        assert "Control+A" in announcement
        assert "/home/u/fp.txt" in announcement

    def test_fingerprint_copy_also_writes_through_gtk(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance.controller = mock.Mock()
        clipboard = _FakeClipboard()

        with mock.patch.object(mod, "_gtk_clipboard", return_value=clipboard), \
                mock.patch.object(mod, "_fingerprint_file_path", return_value="unused"):
            instance._write_fingerprint_file = mock.Mock(return_value="unused")
            instance._copy_fingerprint_idle_cb(GOOD_PIN)

        instance.controller.set_clipboard_text.assert_called_once_with(GOOD_PIN)
        assert clipboard.written == [GOOD_PIN]

    def test_fingerprint_is_saved_to_a_text_file(self, tmp_path):
        mod = _load_remote_module()
        instance = _instance(mod)
        path = tmp_path / "orca-remote-fingerprint.txt"

        with mock.patch.object(mod, "_fingerprint_file_path", return_value=str(path)):
            written = instance._write_fingerprint_file(GOOD_PIN)

        assert written == str(path)
        assert path.read_text(encoding="utf-8") == GOOD_PIN + "\n"

    def test_fingerprint_file_failure_is_logged(self, tmp_path):
        mod = _load_remote_module()
        instance = _instance(mod)
        missing = tmp_path / "missing-dir" / "orca-remote-fingerprint.txt"

        with mock.patch.object(mod, "_fingerprint_file_path", return_value=str(missing)):
            assert instance._write_fingerprint_file(GOOD_PIN) == ""

        instance._log.assert_called_once()
        assert "could not write" in instance._log.call_args.args[0]

    def test_transport_refuses_to_dial_with_a_malformed_pin(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._loop = object()
        instance._transport = None
        instance._settings = {
            mod.SETTING_HOST: "127.0.0.1",
            mod.SETTING_PORT: 6837,
            mod.SETTING_CHANNEL: "chan",
            mod.SETTING_FINGERPRINT: "ab" * 40,
        }
        instance._stop_transport = mock.Mock()

        # Call the real implementation (the helper used elsewhere in this
        # file replaces it with a Mock).
        mod.RemoteExtension._restart_transport(instance)

        instance._stop_transport.assert_called_once()
        assert "not 64 hex characters" in instance._say.call_args.args[0]
        assert instance._transport is None


def _FINGERPRINT_LEN(mod) -> int:
    """Number of 2-char groups in a valid pin, for building bad values."""

    return mod._FINGERPRINT_CHARS // 2


class TestSettingsSaveRemembersConnect:
    def test_save_after_disconnect_turns_auto_connect_back_on(self):
        # Orca+Ctrl+Page Down clears auto_connect; a settings save then
        # dials again. Without re-arming the flag the session is live
        # now but stays offline after the next Orca start.
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._settings = {mod.SETTING_CHANNEL: "old", mod.SETTING_AUTO_CONNECT: False}
        instance._settings_dialog = None

        instance._on_settings_result({mod.SETTING_CHANNEL: "yoga"})

        assert instance._settings[mod.SETTING_AUTO_CONNECT] is True
        instance._restart_transport.assert_called_once()

    def test_cancel_leaves_auto_connect_alone(self):
        mod = _load_remote_module()
        instance = _instance(mod)
        instance._settings = {mod.SETTING_AUTO_CONNECT: False}
        instance._settings_dialog = None

        instance._on_settings_result(None)

        assert instance._settings[mod.SETTING_AUTO_CONNECT] is False
        instance._restart_transport.assert_not_called()


class TestTrustPrompt:
    """First connect and changed certificates ask instead of failing."""

    def _prompt(self, mod, saved_pin):
        instance = _instance(mod)
        instance._trust_dialog = None
        instance._settings = {
            mod.SETTING_HOST: "relay.example", mod.SETTING_PORT: 6837,
            mod.SETTING_FINGERPRINT: saved_pin, mod.SETTING_AUTO_CONNECT: False,
        }
        instance._write_fingerprint_file = mock.Mock(return_value="")
        with mock.patch.object(mod, "build_trust_dialog") as build:
            instance._fingerprint_prompt_idle_cb(GOOD_PIN.upper())
        return instance, build

    def test_blank_pin_asks_as_a_first_connection(self):
        mod = _load_remote_module()
        _instance_, build = self._prompt(mod, "")
        host, port, fingerprint, changed, _cb = build.call_args.args
        assert (host, port, fingerprint, changed) == ("relay.example", 6837, GOOD_PIN, False)

    def test_different_saved_pin_warns_that_it_changed(self):
        mod = _load_remote_module()
        _instance_, build = self._prompt(mod, "ab" * 32)
        assert build.call_args.args[3] is True

    def test_trusting_saves_the_pin_and_connects(self):
        mod = _load_remote_module()
        instance, build = self._prompt(mod, "")
        build.call_args.args[4](True)
        assert instance._settings[mod.SETTING_FINGERPRINT] == GOOD_PIN
        assert instance._settings[mod.SETTING_AUTO_CONNECT] is True
        instance._restart_transport.assert_called_once()

    def test_declining_keeps_the_old_pin(self):
        mod = _load_remote_module()
        old = "ab" * 32
        instance, build = self._prompt(mod, old)
        build.call_args.args[4](False)
        assert instance._settings[mod.SETTING_FINGERPRINT] == old
        instance._restart_transport.assert_not_called()

    def test_pin_is_not_saved_if_the_server_setting_moved(self):
        mod = _load_remote_module()
        instance, build = self._prompt(mod, "")
        instance._settings[mod.SETTING_HOST] = "other.example"
        build.call_args.args[4](True)
        assert instance._settings[mod.SETTING_FINGERPRINT] == ""
        instance._restart_transport.assert_not_called()
