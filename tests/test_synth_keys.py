"""Tests for host-side key synthesis (`_synthesize_key_idle_cb`).

Regression target: on X11 a synthesized PRESS without a matching
RELEASE leaves the key "held" in the server, and X then auto-repeats
real Pressed events at ~30 Hz. One inbound 'H' frame from an NVDA
master became a flood of host-side H presses, saturating Orca's main
loop ("everything freezes" once a remote connects). The fix taps
non-modifier keys (PRESS + immediate RELEASE) while keeping modifier
keysyms sticky so chords still work.
"""

from __future__ import annotations

from unittest import mock

from tests.test_bypass_chords import _load_remote_module


def _new_instance(mod):
    instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
    instance._pressed_keysyms = set()
    instance._chord_matches_own_command = mock.Mock(return_value=False)
    instance._log = mock.Mock()
    instance.controller = mock.Mock()
    instance.controller.synthesize_key_event = mock.Mock(return_value=True)
    return instance


class TestTapMode:
    """Non-modifier PRESS taps (PRESS + auto-RELEASE in same callback)."""

    def test_letter_press_taps(self):
        mod = _load_remote_module()
        instance = _new_instance(mod)
        # H = 0x48 (XK_H). Not in _STICKY_KEYSYMS.
        instance._synthesize_key_idle_cb(0x48, True)
        calls = instance.controller.synthesize_key_event.call_args_list
        assert calls == [mock.call(0x48, True), mock.call(0x48, False)]
        # Still tracked for dedupe until the wire RELEASE arrives.
        assert 0x48 in instance._pressed_keysyms

    def test_duplicate_press_drops(self):
        """NVDA-style repeated PRESS frames for one physical tap collapse."""

        mod = _load_remote_module()
        instance = _new_instance(mod)
        instance._synthesize_key_idle_cb(0x48, True)
        instance.controller.synthesize_key_event.reset_mock()
        # Second PRESS for same keysym before wire RELEASE: drop.
        instance._synthesize_key_idle_cb(0x48, True)
        instance.controller.synthesize_key_event.assert_not_called()

    def test_wire_release_clears_dedupe_without_resynth(self):
        """Letter RELEASE on wire is a no-op for X (already released)."""

        mod = _load_remote_module()
        instance = _new_instance(mod)
        instance._synthesize_key_idle_cb(0x48, True)
        instance.controller.synthesize_key_event.reset_mock()
        # Wire RELEASE: should NOT re-synth (the auto-tap above already
        # released it in X). Should remove from dedupe set.
        instance._synthesize_key_idle_cb(0x48, False)
        instance.controller.synthesize_key_event.assert_not_called()
        assert 0x48 not in instance._pressed_keysyms

    def test_second_physical_tap_after_release_works(self):
        """After wire RELEASE clears dedupe, a fresh PRESS taps again."""

        mod = _load_remote_module()
        instance = _new_instance(mod)
        instance._synthesize_key_idle_cb(0x48, True)
        instance._synthesize_key_idle_cb(0x48, False)
        instance.controller.synthesize_key_event.reset_mock()
        instance._synthesize_key_idle_cb(0x48, True)
        calls = instance.controller.synthesize_key_event.call_args_list
        assert calls == [mock.call(0x48, True), mock.call(0x48, False)]


class TestStickyMode:
    """Modifier keysyms stay held until the wire RELEASE arrives."""

    def test_control_press_holds(self):
        mod = _load_remote_module()
        instance = _new_instance(mod)
        # Control_L = 0xffe3.
        instance._synthesize_key_idle_cb(0xffe3, True)
        # Single synth, no auto-release.
        instance.controller.synthesize_key_event.assert_called_once_with(
            0xffe3, True,
        )
        assert 0xffe3 in instance._pressed_keysyms

    def test_control_release_synths_and_clears(self):
        mod = _load_remote_module()
        instance = _new_instance(mod)
        instance._synthesize_key_idle_cb(0xffe3, True)
        instance.controller.synthesize_key_event.reset_mock()
        instance._synthesize_key_idle_cb(0xffe3, False)
        instance.controller.synthesize_key_event.assert_called_once_with(
            0xffe3, False,
        )
        assert 0xffe3 not in instance._pressed_keysyms

    def test_insert_orca_mod_is_sticky(self):
        """Insert (Orca modifier) must hold so Insert+H chord works."""

        mod = _load_remote_module()
        instance = _new_instance(mod)
        # Insert = 0xff63.
        instance._synthesize_key_idle_cb(0xff63, True)
        # One synth, no auto-release.
        instance.controller.synthesize_key_event.assert_called_once_with(
            0xff63, True,
        )
        assert 0xff63 in instance._pressed_keysyms

    def test_chord_held_during_letter_tap(self):
        """Ctrl+Tab: Ctrl stays held while Tab taps."""

        mod = _load_remote_module()
        instance = _new_instance(mod)
        # Ctrl PRESS (sticky).
        instance._synthesize_key_idle_cb(0xffe3, True)
        # Tab PRESS (Tab = 0xff09, not in _STICKY_KEYSYMS) -> tap.
        instance._synthesize_key_idle_cb(0xff09, True)
        # Ctrl RELEASE.
        instance._synthesize_key_idle_cb(0xffe3, False)
        # Wire RELEASE for Tab (no-op).
        instance._synthesize_key_idle_cb(0xff09, False)

        calls = instance.controller.synthesize_key_event.call_args_list
        assert calls == [
            mock.call(0xffe3, True),   # Ctrl down
            mock.call(0xff09, True),   # Tab down
            mock.call(0xff09, False),  # Tab up (auto-release)
            mock.call(0xffe3, False),  # Ctrl up
        ]
        assert instance._pressed_keysyms == set()


class TestOwnChordRefusal:
    """Chord refusal still wins over the synth path."""

    def test_refused_chord_does_not_synth_or_track(self):
        mod = _load_remote_module()
        instance = _new_instance(mod)
        instance._chord_matches_own_command = mock.Mock(return_value=True)
        # 'r' arriving while Insert+Ctrl held -> own-command, refuse.
        instance._synthesize_key_idle_cb(0x72, True)
        instance.controller.synthesize_key_event.assert_not_called()
        assert 0x72 not in instance._pressed_keysyms


class TestSynthFailure:
    """If the controller refuses to synth, do not track or auto-release."""

    def test_failed_press_skips_auto_release(self):
        mod = _load_remote_module()
        instance = _new_instance(mod)
        instance.controller.synthesize_key_event = mock.Mock(
            return_value=False,
        )
        instance._synthesize_key_idle_cb(0x48, True)
        # Single call (the failed PRESS); no auto-release attempted.
        instance.controller.synthesize_key_event.assert_called_once_with(
            0x48, True,
        )
        # Not tracked because the synth said no.
        assert 0x48 not in instance._pressed_keysyms
