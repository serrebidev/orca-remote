"""Unit tests for braille_table.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import braille_table  # noqa: E402


class TestAsciiTable:
    def test_lowercase_letter_a(self) -> None:
        # 'a' = dot 1 = 0x01
        assert braille_table.text_to_cells("a") == [0x01]

    def test_lowercase_letter_b(self) -> None:
        # 'b' = dots 1,2 = 0x03
        assert braille_table.text_to_cells("b") == [0x03]

    def test_word(self) -> None:
        # "hi" -> h (dots 1,2,5 = 0x13) + i (dots 2,4 = 0x0a)
        assert braille_table.text_to_cells("hi") == [0x13, 0x0a]

    def test_uppercase_has_dot_7(self) -> None:
        # 'A' = dots 1+7 = 0x41
        assert braille_table.text_to_cells("A") == [0x41]

    def test_space_is_blank(self) -> None:
        assert braille_table.text_to_cells(" ") == [0x00]

    def test_digit(self) -> None:
        # '1' = dot 1 = 0x01 (BRF computer braille uses same as 'a')
        assert braille_table.text_to_cells("1") == [0x01]


class TestUnicodeBraillePassthrough:
    def test_unicode_braille_block_low_byte(self) -> None:
        # U+2820 = dot 6, low byte = 0x20
        assert braille_table.text_to_cells("⠠") == [0x20]
        # U+2811 = dots 1,5 = 0x11
        assert braille_table.text_to_cells("⠑") == [0x11]

    def test_mixed_braille_and_ascii(self) -> None:
        # 'a' (0x01) + U+2820 (0x20) + 'z' (0x35)
        result = braille_table.text_to_cells("a⠠z")
        assert result == [0x01, 0x20, 0x35]


class TestEdgeCases:
    def test_empty_string(self) -> None:
        assert braille_table.text_to_cells("") == []

    def test_non_ascii_non_braille_is_blank(self) -> None:
        # Cyrillic / CJK / emoji fall back to empty cell.
        assert braille_table.text_to_cells("é") == [0x00]
        assert braille_table.text_to_cells("世") == [0x00]
        assert braille_table.text_to_cells("🙂") == [0x00]

    def test_newline_and_tab_are_blank(self) -> None:
        # Not in the printable ASCII table; mapped to empty cell.
        assert braille_table.text_to_cells("\n") == [0x00]
        assert braille_table.text_to_cells("\t") == [0x00]

    def test_long_string_preserves_length(self) -> None:
        s = "abcdefghij"
        cells = braille_table.text_to_cells(s)
        assert len(cells) == len(s)
