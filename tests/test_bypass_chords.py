"""Tests for the master-side bypass-chord list.

Loads remote.py with Orca stubs and checks that every chord
RemoteExtension binds via _get_commands also appears in the
bypass list (_OWN_CTRL_CHORD_KEYSYMS / _OWN_ALT_CHORD_KEYSYMS).

The bypass list lives in `_on_keyboard_event` and tells the
master-side forwarder which chords are orca-remote's own
commands (so they dispatch locally instead of being forwarded
to the remote machine).

Regression target: adding a new KeyboardCommand without also
updating the bypass list would silently send the chord to the
remote -- the user would press it, nothing local would happen,
and they'd be confused.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest


def _load_remote_module():
    """Load remote.py with stubbed Orca imports.

    Returns the module so callers can inspect constants + methods.
    """

    # Stub the orca packages remote.py imports at module load.
    for name in (
        "gi",
        "gi.repository",
        "gi.repository.GLib",
        "gi.repository.Gtk",
        "orca",
        "orca.debug",
        "orca.keybindings",
        "orca.command",
        "orca.extension",
    ):
        sys.modules[name] = types.ModuleType(name)

    sys.modules["gi"].require_version = lambda *a, **kw: None
    sys.modules["gi"].repository = sys.modules["gi.repository"]
    sys.modules["gi.repository"].GLib = sys.modules["gi.repository.GLib"]
    sys.modules["gi.repository"].Gtk = sys.modules["gi.repository.Gtk"]
    sys.modules["gi.repository.GLib"].get_user_data_dir = lambda: "/tmp"

    # KeyboardCommand: capture name + binding so tests can assert
    # on what was registered without spinning up Orca's binding
    # machinery.
    captured: list[dict] = []

    def fake_keyboard_command(
        name, function, group_label, description="",
        desktop_keybinding=None, laptop_keybinding=None,
        enabled=True, suspended=False, is_group_toggle=False,
    ):
        rec = dict(
            name=name, function=function, group_label=group_label,
            description=description,
            desktop_keybinding=desktop_keybinding,
            laptop_keybinding=laptop_keybinding,
        )
        captured.append(rec)
        return rec

    sys.modules["orca.command"].Command = object
    sys.modules["orca.command"].KeyboardCommand = fake_keyboard_command
    sys.modules["orca.extension"].Extension = object

    # KeyBinding records its constructor args so the test can ask
    # "what keysymstring + modifiers did the binding declare?"
    class FakeKeyBinding:
        def __init__(self, keysymstring, modifiers, click_count=1):
            self.keysymstring = keysymstring
            self.modifiers = modifiers
            self.click_count = click_count

    sys.modules["orca.keybindings"].KeyBinding = FakeKeyBinding
    # Modifier-mask constants. The values aren't load-bearing for
    # this test; we only inspect the keysymstring + which mask
    # constant was used.
    sys.modules["orca.keybindings"].ORCA_MODIFIER_MASK = 0x100
    sys.modules["orca.keybindings"].ORCA_CTRL_MODIFIER_MASK = 0x104
    sys.modules["orca.keybindings"].ORCA_ALT_MODIFIER_MASK = 0x108
    sys.modules["orca.keybindings"].ORCA_SHIFT_MODIFIER_MASK = 0x101
    sys.modules["orca.keybindings"].SHIFT_MODIFIER_MASK = 0x01
    sys.modules["orca.keybindings"].CTRL_MODIFIER_MASK = 0x04
    sys.modules["orca.keybindings"].ALT_MODIFIER_MASK = 0x08
    sys.modules["orca.keybindings"].NO_MODIFIER_MASK = 0
    sys.modules["orca.debug"].print_message = lambda *a, **kw: None
    sys.modules["orca.debug"].LEVEL_WARNING = 0
    sys.modules["orca.debug"].LEVEL_INFO = 1

    # Synthesize the parent package shim remote.py expects.
    pkg = types.ModuleType("orca_user_extension")
    pkg.__path__ = [str(Path(__file__).resolve().parent.parent)]
    sys.modules["orca_user_extension"] = pkg

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "orca_user_extension.remote",
        str(Path(__file__).resolve().parent.parent / "remote.py"),
        submodule_search_locations=[str(Path(__file__).resolve().parent.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["orca_user_extension.remote"] = mod
    spec.loader.exec_module(mod)
    mod._test_captured_commands = captured  # type: ignore[attr-defined]
    return mod


# Map from KeyBinding keysymstring to X11 keysym int, for the keys
# orca-remote actually binds. Lets us check binding keysyms against
# the bypass-list integer set.
_KEYSYM_NAME_TO_INT: dict[str, int] = {
    "r": 0x72,
    "m": 0x6d,
    "c": 0x63,
    "Page_Up": 0xff55,
    "Page_Down": 0xff56,
    "Tab": 0xff09,
}


class TestBypassCoverage:
    """Every chord in _get_commands must appear in the bypass list."""

    def test_every_bound_chord_in_bypass_set(self):
        mod = _load_remote_module()
        # Construct without __init__ so we can call _get_commands
        # without running through Extension's __init__ side effects.
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        commands = instance._get_commands()
        assert commands, "no commands registered -- nothing to verify"

        for cmd in commands:
            keybinding = cmd["desktop_keybinding"]
            assert keybinding is not None, (
                f"{cmd['name']}: desktop_keybinding is None"
            )
            keysym_name = keybinding.keysymstring
            keysym_int = _KEYSYM_NAME_TO_INT.get(keysym_name)
            assert keysym_int is not None, (
                f"{cmd['name']}: binding uses keysym {keysym_name!r} "
                f"which isn't in test's translation map -- update _KEYSYM_NAME_TO_INT"
            )
            # Modifier sniff: which of {ORCA_CTRL, ORCA_ALT} did this
            # binding use? We compare to the constants we stubbed.
            modifiers = keybinding.modifiers
            ctrl_mask = sys.modules["orca.keybindings"].ORCA_CTRL_MODIFIER_MASK
            alt_mask = sys.modules["orca.keybindings"].ORCA_ALT_MODIFIER_MASK
            ctrl_shift_mask = (
                sys.modules["orca.keybindings"].ORCA_MODIFIER_MASK
                | sys.modules["orca.keybindings"].CTRL_MODIFIER_MASK
                | sys.modules["orca.keybindings"].SHIFT_MODIFIER_MASK
            )

            if modifiers == ctrl_mask:
                assert keysym_int in mod._OWN_CTRL_CHORD_KEYSYMS, (
                    f"{cmd['name']}: bound to Orca+Ctrl+{keysym_name} but "
                    f"keysym 0x{keysym_int:x} is missing from "
                    f"_OWN_CTRL_CHORD_KEYSYMS -- master-side bypass "
                    f"won't fire and the chord will be forwarded to the remote"
                )
            elif modifiers == ctrl_shift_mask:
                assert keysym_int in mod._OWN_CTRL_SHIFT_CHORD_KEYSYMS, (
                    f"{cmd['name']}: bound to Orca+Ctrl+Shift+{keysym_name} but "
                    f"keysym 0x{keysym_int:x} is missing from "
                    f"_OWN_CTRL_SHIFT_CHORD_KEYSYMS -- master-side bypass "
                    f"won't fire and the chord will be forwarded to the remote"
                )
            elif modifiers == alt_mask:
                assert keysym_int in mod._OWN_ALT_CHORD_KEYSYMS, (
                    f"{cmd['name']}: bound to Orca+Alt+{keysym_name} but "
                    f"keysym 0x{keysym_int:x} is missing from "
                    f"_OWN_ALT_CHORD_KEYSYMS -- master-side bypass "
                    f"won't fire and the chord will be forwarded to the remote"
                )
            else:
                pytest.fail(
                    f"{cmd['name']}: unrecognized modifier mask {modifiers!r}. "
                    f"Update the test if you've added a new modifier kind."
                )


class TestBypassPredicate:
    """Direct unit tests for the bypass-chord predicate logic."""

    def test_no_orca_mod_held_means_no_bypass(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 0
        # Even with a matching keysym + modifier, no Orca-mod held
        # means this isn't a local command chord.
        assert not instance._is_local_bypass_chord(0x72, mod._AT_SPI_MOD_CTRL)
        assert not instance._is_local_bypass_chord(0xff09, mod._AT_SPI_MOD_ALT)

    def test_orca_mod_plus_ctrl_chord_bypasses(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        for keysym in (0x72, 0x6d, 0xff55, 0xff56):  # r, m, PgUp, PgDn
            assert instance._is_local_bypass_chord(
                keysym, mod._AT_SPI_MOD_CTRL,
            ), f"keysym 0x{keysym:x} should bypass under Orca+Ctrl"

    def test_orca_mod_plus_ctrl_shift_c_bypasses(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        modifiers = mod._AT_SPI_MOD_CTRL | mod._AT_SPI_MOD_SHIFT
        assert instance._is_local_bypass_chord(0x63, modifiers)
        assert instance._is_local_bypass_chord(0x43, modifiers)

    def test_orca_mod_plus_alt_tab_bypasses(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        assert instance._is_local_bypass_chord(0xff09, mod._AT_SPI_MOD_ALT)

    def test_non_chord_letter_does_not_bypass(self):
        """Insert+Ctrl+other-letter should NOT bypass -- forwards normally."""

        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        # 'a' / 'z' aren't in the bypass list; would forward to remote.
        assert not instance._is_local_bypass_chord(0x61, mod._AT_SPI_MOD_CTRL)
        assert not instance._is_local_bypass_chord(0x7a, mod._AT_SPI_MOD_CTRL)

    def test_orca_mod_plus_down_does_not_bypass(self):
        """Insert+Down (sayAll on remote NVDA) must NOT be bypassed."""

        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        # XK_Down = 0xff54. Not in either OWN_*_CHORD set even though
        # Insert is held; this is a remote-side NVDA command and must
        # reach the slave.
        assert not instance._is_local_bypass_chord(0xff54, 0)
        # With Ctrl: still not in the list.
        assert not instance._is_local_bypass_chord(0xff54, mod._AT_SPI_MOD_CTRL)

    def test_wrong_modifier_does_not_bypass(self):
        """Insert+Ctrl+m (mute) only bypasses when Ctrl bit set.

        Insert+Alt+m (not a real command) should forward, not bypass.
        """

        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        # Mute keysym 'm' is in OWN_CTRL_CHORD_KEYSYMS but the check
        # requires Ctrl-bit. Without it, no bypass.
        assert not instance._is_local_bypass_chord(0x6d, 0)
        assert not instance._is_local_bypass_chord(0x6d, mod._AT_SPI_MOD_ALT)

    def test_clipboard_requires_shift(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 1
        assert not instance._is_local_bypass_chord(0x63, mod._AT_SPI_MOD_CTRL)
        assert not instance._is_local_bypass_chord(0x63, mod._AT_SPI_MOD_SHIFT)


class TestMuteToggle:
    """mute_inbound_toggle flips the flag and speaks state."""

    def test_toggle_flips_flag(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._inbound_speech_muted = False
        instance._say = mock.Mock()
        # Force role=client so the toggle isn't no-op.
        instance._current_role = mock.Mock(return_value="client")

        result = instance.mute_inbound_toggle()
        assert result is True
        assert instance._inbound_speech_muted is True
        # Toggle again: flips back.
        instance.mute_inbound_toggle()
        assert instance._inbound_speech_muted is False

    def test_toggle_speaks_state(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._inbound_speech_muted = False
        instance._say = mock.Mock()
        instance._current_role = mock.Mock(return_value="client")

        instance.mute_inbound_toggle()
        # First call (now muted): speaks "remote muted" sentence.
        first_call_text = instance._say.call_args[0][0]
        assert "muted" in first_call_text.lower()
        assert "unmuted" not in first_call_text.lower()

        instance.mute_inbound_toggle()
        # Second call (now unmuted): speaks unmuted.
        second_call_text = instance._say.call_args[0][0]
        assert "unmuted" in second_call_text.lower()

    def test_toggle_silent_noop_on_host(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._inbound_speech_muted = False
        instance._say = mock.Mock()
        instance._current_role = mock.Mock(return_value="host")

        result = instance.mute_inbound_toggle()
        assert result is True
        # Flag must NOT change; host has no inbound to mute.
        assert instance._inbound_speech_muted is False
        # No speech either.
        instance._say.assert_not_called()


class TestOrcaModTracking:
    """Insert / Caps_Lock press/release update the held counter."""

    def test_press_increments(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 0
        # Construct a minimal call-chain: only the tracking branch
        # of _on_keyboard_event runs because role / transport guards
        # short-circuit.
        instance._current_role = mock.Mock(return_value="client")
        instance._focus_on_remote = False  # guard returns False after tracking
        instance._transport = None
        # Insert press.
        instance._on_keyboard_event(
            pressed=True, keycode=118, keysym=0xff63,
            modifiers=0, text="",
        )
        assert instance._master_orca_mod_held == 1
        # Insert release.
        instance._on_keyboard_event(
            pressed=False, keycode=118, keysym=0xff63,
            modifiers=0, text="",
        )
        assert instance._master_orca_mod_held == 0

    def test_release_clamped_to_zero(self):
        """Stray release without a matching press doesn't go negative."""

        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 0
        instance._current_role = mock.Mock(return_value="client")
        instance._focus_on_remote = False
        instance._transport = None
        # Release without preceding press (Orca-mod transition we missed).
        instance._on_keyboard_event(
            pressed=False, keycode=118, keysym=0xff63,
            modifiers=0, text="",
        )
        assert instance._master_orca_mod_held == 0

    def test_two_orca_mod_keys_balance(self):
        """Insert + KP_Insert held together net to 2, decrement to 0 cleanly."""

        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._master_orca_mod_held = 0
        instance._current_role = mock.Mock(return_value="client")
        instance._focus_on_remote = False
        instance._transport = None
        # Press both.
        instance._on_keyboard_event(True, 0, 0xff63, 0, "")  # Insert
        instance._on_keyboard_event(True, 0, 0xff9e, 0, "")  # KP_Insert
        assert instance._master_orca_mod_held == 2
        # Release both.
        instance._on_keyboard_event(False, 0, 0xff63, 0, "")
        instance._on_keyboard_event(False, 0, 0xff9e, 0, "")
        assert instance._master_orca_mod_held == 0


class TestOwnChordRefusalPredicate:
    """Host-side inbound synth refusal for Orca Remote command chords."""

    def test_ctrl_shift_c_refused_when_modifiers_held(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._pressed_keysyms = {
            0xff63,  # Insert
            0xffe3,  # Control_L
            0xffe1,  # Shift_L
        }
        assert instance._chord_matches_own_command(0x63)
        assert instance._chord_matches_own_command(0x43)

    def test_ctrl_shift_c_not_refused_without_shift(self):
        mod = _load_remote_module()
        instance = mod.RemoteExtension.__new__(mod.RemoteExtension)
        instance._pressed_keysyms = {
            0xff63,  # Insert
            0xffe3,  # Control_L
        }
        assert not instance._chord_matches_own_command(0x63)
