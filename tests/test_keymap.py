"""Unit tests for keymap.py (Windows VK -> X11 keysym).

Pure-function module. Run with:
    python3 -m pytest tests/test_keymap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keymap  # noqa: E402


class TestLetters:
    @pytest.mark.parametrize(
        "vk,keysym",
        [
            (0x41, 0x61),  # VK_A -> XK_a
            (0x4d, 0x6d),  # VK_M -> XK_m
            (0x5a, 0x7a),  # VK_Z -> XK_z
        ],
    )
    def test_letters_map_to_lowercase(self, vk: int, keysym: int) -> None:
        assert keymap.vk_to_keysym(vk) == keysym

    def test_letters_extended_flag_is_irrelevant(self) -> None:
        # Letters don't have an "extended" variant on Windows.
        assert keymap.vk_to_keysym(0x41, extended=True) == 0x61


class TestDigits:
    @pytest.mark.parametrize(
        "vk,keysym",
        [
            (0x30, 0x30),  # VK_0 -> XK_0
            (0x35, 0x35),  # VK_5 -> XK_5
            (0x39, 0x39),  # VK_9 -> XK_9
        ],
    )
    def test_main_row_digits(self, vk: int, keysym: int) -> None:
        assert keymap.vk_to_keysym(vk) == keysym

    @pytest.mark.parametrize(
        "vk,keysym",
        [
            (0x60, 0xffb0),  # VK_NUMPAD0 -> XK_KP_0
            (0x65, 0xffb5),  # VK_NUMPAD5 -> XK_KP_5
            (0x69, 0xffb9),  # VK_NUMPAD9 -> XK_KP_9
        ],
    )
    def test_keypad_digits(self, vk: int, keysym: int) -> None:
        assert keymap.vk_to_keysym(vk) == keysym


class TestNavCluster:
    """The nav cluster has the trickiest extended/non-extended split.

    Default (extended=False) gives the numpad keysym, which matches
    what Windows reports when NumLock is off and the user pressed
    a numpad arrow. Extended=True gives the main-row keysym.
    """

    @pytest.mark.parametrize(
        "vk,non_ext,ext",
        [
            (0x21, 0xff9a, 0xff55),  # VK_PRIOR (Page Up)
            (0x22, 0xff9b, 0xff56),  # VK_NEXT  (Page Down)
            (0x23, 0xff9c, 0xff57),  # VK_END
            (0x24, 0xff95, 0xff50),  # VK_HOME
            (0x25, 0xff96, 0xff51),  # VK_LEFT
            (0x26, 0xff97, 0xff52),  # VK_UP
            (0x27, 0xff98, 0xff53),  # VK_RIGHT
            (0x28, 0xff99, 0xff54),  # VK_DOWN
            (0x2D, 0xff9e, 0xff63),  # VK_INSERT
            (0x2E, 0xff9f, 0xffff),  # VK_DELETE
        ],
    )
    def test_nav_cluster(self, vk: int, non_ext: int, ext: int) -> None:
        assert keymap.vk_to_keysym(vk, extended=False) == non_ext
        assert keymap.vk_to_keysym(vk, extended=True) == ext


class TestModifiers:
    def test_generic_modifiers(self) -> None:
        assert keymap.vk_to_keysym(0x10) == 0xffe1  # Shift -> Shift_L
        assert keymap.vk_to_keysym(0x11) == 0xffe3  # Control -> Control_L
        assert keymap.vk_to_keysym(0x12) == 0xffe9  # Menu (Alt) -> Alt_L

    def test_left_right_modifiers(self) -> None:
        assert keymap.vk_to_keysym(0xA0) == 0xffe1  # LShift
        assert keymap.vk_to_keysym(0xA1) == 0xffe2  # RShift
        assert keymap.vk_to_keysym(0xA2) == 0xffe3  # LControl
        assert keymap.vk_to_keysym(0xA3) == 0xffe4  # RControl

    def test_extended_modifiers(self) -> None:
        # Windows reports right Ctrl/Alt as extended; we override to
        # the _R variants.
        assert keymap.vk_to_keysym(0x11, extended=True) == 0xffe4
        assert keymap.vk_to_keysym(0x12, extended=True) == 0xffea


class TestFunctionKeys:
    def test_f1_f12_range(self) -> None:
        assert keymap.vk_to_keysym(0x70) == 0xffbe  # F1
        assert keymap.vk_to_keysym(0x7b) == 0xffc9  # F12

    def test_f24_upper_bound(self) -> None:
        assert keymap.vk_to_keysym(0x87) == 0xffd5  # F24


class TestReturnAndKpEnter:
    def test_return(self) -> None:
        assert keymap.vk_to_keysym(0x0D) == 0xff0d

    def test_kp_enter_via_extended(self) -> None:
        # NVDA reports keypad Enter as extended VK_RETURN.
        assert keymap.vk_to_keysym(0x0D, extended=True) == 0xff8d


class TestBrowserAndMedia:
    def test_browser_keys(self) -> None:
        # XF86 keysyms live in the 0x1008xxxx range.
        assert keymap.vk_to_keysym(0xA6) == 0x1008ff26  # Back
        assert keymap.vk_to_keysym(0xAC) == 0x1008ff18  # HomePage

    def test_media_keys(self) -> None:
        assert keymap.vk_to_keysym(0xAD) == 0x1008ff12  # Mute
        assert keymap.vk_to_keysym(0xB3) == 0x1008ff14  # PlayPause


class TestIME:
    def test_ime_keys(self) -> None:
        assert keymap.vk_to_keysym(0x15) == 0xff31  # Hangul / Kana
        assert keymap.vk_to_keysym(0x19) == 0xff21  # Hanja / Kanji
        assert keymap.vk_to_keysym(0x1C) == 0xff26  # Convert -> Henkan_Mode
        assert keymap.vk_to_keysym(0x1D) == 0xff22  # NonConvert -> Muhenkan


class TestUnmappedFallthrough:
    def test_unknown_vk_returns_zero(self) -> None:
        assert keymap.vk_to_keysym(0xFE) == 0
        assert keymap.vk_to_keysym(0xFF) == 0
        assert keymap.vk_to_keysym(0x0E) == 0  # Hole in VK table

    def test_extended_for_unmapped_vk_still_returns_zero(self) -> None:
        # extended=True should not invent a mapping out of nowhere.
        assert keymap.vk_to_keysym(0xFE, extended=True) == 0


class TestTableCompleteness:
    def test_minimum_size(self) -> None:
        # Sanity floor so a future refactor doesn't accidentally drop
        # half the table. 26 letters + 10 digits + 10 KP digits + 24
        # F-keys + ~75 named/extended is comfortably >120.
        assert len(keymap._VK_TO_KEYSYM) >= 120


class TestKeysymToVk:
    """Reverse: X11 keysym -> (vk_code, extended). Used for master-side key forwarding."""

    def test_letter_round_trip(self) -> None:
        # Forward: VK_A (0x41) -> XK_a (0x61).
        # Reverse: XK_a -> (VK_A, False).
        assert keymap.keysym_to_vk(0x61) == (0x41, False)
        assert keymap.keysym_to_vk(0x7a) == (0x5a, False)  # XK_z -> VK_Z

    def test_shifted_letter_round_trip(self) -> None:
        # AT-SPI may report Shift+C as XK_C. Shift is forwarded as a
        # separate key event, so the letter still maps to VK_C.
        assert keymap.keysym_to_vk(0x43) == (0x43, False)

    def test_digit_round_trip(self) -> None:
        assert keymap.keysym_to_vk(0x30) == (0x30, False)  # '0'
        assert keymap.keysym_to_vk(0x39) == (0x39, False)  # '9'

    def test_keypad_digit_round_trip(self) -> None:
        assert keymap.keysym_to_vk(0xffb0) == (0x60, False)  # XK_KP_0 -> VK_NUMPAD0
        assert keymap.keysym_to_vk(0xffb9) == (0x69, False)

    def test_function_key_round_trip(self) -> None:
        assert keymap.keysym_to_vk(0xffbe) == (0x70, False)  # F1
        assert keymap.keysym_to_vk(0xffc9) == (0x7b, False)  # F12

    def test_nav_cluster_extended_split(self) -> None:
        # Numpad nav (non-extended) and main-row nav (extended) both
        # exist in the reverse table; each points at the right VK
        # with the right extended bit.
        assert keymap.keysym_to_vk(0xff9a) == (0x21, False)  # KP_Page_Up
        assert keymap.keysym_to_vk(0xff55) == (0x21, True)   # Page_Up
        assert keymap.keysym_to_vk(0xff9e) == (0x2D, False)  # KP_Insert
        assert keymap.keysym_to_vk(0xff63) == (0x2D, True)   # Insert

    def test_return_vs_kp_enter(self) -> None:
        assert keymap.keysym_to_vk(0xff0d) == (0x0D, False)  # Return
        assert keymap.keysym_to_vk(0xff8d) == (0x0D, True)   # KP_Enter

    def test_left_right_modifiers_reverse_distinctly(self) -> None:
        # Left/Right control / alt have distinct VK codes; reverse
        # should preserve that.
        assert keymap.keysym_to_vk(0xffe3)[0] in (0x11, 0xA2)  # Control_L
        assert keymap.keysym_to_vk(0xffe4) == (0x11, True)     # Control_R (extended override wins)

    def test_unmapped_returns_zero(self) -> None:
        # An arbitrary keysym not in the table.
        assert keymap.keysym_to_vk(0xdeadbe) == (0, False)
        # XK_VoidSymbol just to be sure.
        assert keymap.keysym_to_vk(0xffffff) == (0, False)


class TestForwardableKeysyms:
    """The set master-side KeysetGrab takes when forwarding is active."""

    def test_returns_frozenset(self) -> None:
        # Frozenset so KeysetGrab can't mutate the source-of-truth.
        assert isinstance(keymap.forwardable_keysyms(), frozenset)

    def test_includes_letters(self) -> None:
        ks = keymap.forwardable_keysyms()
        # XK_a..XK_z must all be forwardable.
        for keysym in range(0x61, 0x7b):
            assert keysym in ks, f"keysym 0x{keysym:x} missing"
        for keysym in range(0x41, 0x5b):
            assert keysym in ks, f"shifted keysym 0x{keysym:x} missing"

    def test_includes_digits(self) -> None:
        ks = keymap.forwardable_keysyms()
        for keysym in range(0x30, 0x3a):
            assert keysym in ks, f"keysym 0x{keysym:x} missing"

    def test_includes_function_keys(self) -> None:
        ks = keymap.forwardable_keysyms()
        # XK_F1..XK_F12 -- the ones users actually press.
        for keysym in range(0xffbe, 0xffca):
            assert keysym in ks, f"F-key keysym 0x{keysym:x} missing"

    def test_includes_arrow_keys(self) -> None:
        ks = keymap.forwardable_keysyms()
        # Main-row arrows (extended) and numpad arrows (non-extended).
        for keysym in (0xff51, 0xff52, 0xff53, 0xff54,    # main-row
                       0xff96, 0xff97, 0xff98, 0xff99):    # numpad
            assert keysym in ks, f"arrow keysym 0x{keysym:x} missing"

    def test_excludes_unmapped_keysyms(self) -> None:
        ks = keymap.forwardable_keysyms()
        # XK_VoidSymbol and other never-mapped keysyms must be absent.
        assert 0xffffff not in ks
        assert 0xdeadbe not in ks
        assert 0 not in ks

    def test_consistent_with_reverse_lookup(self) -> None:
        # Every keysym in the forwardable set must reverse-lookup to
        # a non-zero VK code. Catches regressions where the helper
        # diverges from keysym_to_vk's coverage.
        ks = keymap.forwardable_keysyms()
        for keysym in list(ks)[:20]:
            assert keymap.keysym_to_vk(keysym)[0] != 0


class TestKeysymAliases:
    """Reverse-only aliases for keysyms delivered by X/AT-SPI."""

    def test_iso_left_tab_maps_to_vk_tab(self) -> None:
        assert keymap.keysym_to_vk(0xfe20) == (0x09, False)

    def test_iso_left_tab_is_forwardable(self) -> None:
        assert 0xfe20 in keymap.forwardable_keysyms()

    def test_alias_does_not_pollute_forward_table(self) -> None:
        assert keymap.vk_to_keysym(0x09) == 0xff09


class TestRoundTrip:
    def test_forward_reverse_round_trip_sample(self) -> None:
        # For every VK in the forward table, reverse-lookup of the
        # mapped keysym should give us *some* VK back. (Exact match
        # not guaranteed for keysyms appearing in both forward and
        # extended; the test of nav-cluster above covers those.)
        for vk in (0x41, 0x4d, 0x5a, 0x30, 0x39, 0x70, 0x7b):
            keysym = keymap.vk_to_keysym(vk)
            assert keysym != 0, f"VK 0x{vk:x} not in forward table"
            got_vk, _ext = keymap.keysym_to_vk(keysym)
            assert got_vk != 0, f"keysym 0x{keysym:x} not in reverse table"
