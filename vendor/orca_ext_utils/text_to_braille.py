"""Text-to-braille-cells translation.

The motivating use case is orca-remote's outbound braille mirroring:
the local host generates speech and braille for the focused app,
and we forward the braille to a remote master (NVDA-Remote-compatible
or another Orca master) so a remote braille display can render it.
NVDA Remote v2.x's `display` message carries raw cell bytes; we
need to convert UTF-8 text into the appropriate cell bytes.

Two backends:

  1. liblouis -- the canonical free-software braille translator.
     Supports UEB Grade 1/2, computer braille, language-tagged
     contractions, and non-Latin scripts. Optional dep (heavy C
     library + Python bindings); detected at import time.
  2. Static ASCII -> US computer braille table -- always available,
     covers a-z / 0-9 / common punctuation, and renders blank cells
     for anything outside that set. Good enough for English-only
     environments where adding a liblouis dep isn't justified.

The module's primary entry point `text_to_cells` always returns
*some* cells, falling back from liblouis to the static table when
liblouis is unavailable or raises. Callers that need to know which
backend handled a given call can inspect `available_backends()`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Tuple


# US computer (8-dot) braille table for ASCII. Values are dot-bit
# masks where bit N is dot (N+1): bit 0 = dot 1, bit 1 = dot 2,
# bit 2 = dot 3, bit 3 = dot 4, bit 4 = dot 5, bit 5 = dot 6,
# bit 6 = dot 7, bit 7 = dot 8.
#
# This matches Unicode braille pattern encoding (U+2800 + mask),
# and matches what NVDA Remote / brltty / BrlAPI consume when they
# expect raw cell bytes.
#
# Characters not in this table get the blank cell (0). That's
# preferable to dropping characters (which would shift cell
# positions and break any cursor-cell pointer the master is
# rendering alongside).
_ASCII_TO_CELLS: dict[int, int] = {
    # Letters a-z (dots 1-3 per standard braille).
    ord("a"): 0x01, ord("b"): 0x03, ord("c"): 0x09, ord("d"): 0x19,
    ord("e"): 0x11, ord("f"): 0x0b, ord("g"): 0x1b, ord("h"): 0x13,
    ord("i"): 0x0a, ord("j"): 0x1a, ord("k"): 0x05, ord("l"): 0x07,
    ord("m"): 0x0d, ord("n"): 0x1d, ord("o"): 0x15, ord("p"): 0x0f,
    ord("q"): 0x1f, ord("r"): 0x17, ord("s"): 0x0e, ord("t"): 0x1e,
    ord("u"): 0x25, ord("v"): 0x27, ord("w"): 0x3a, ord("x"): 0x2d,
    ord("y"): 0x3d, ord("z"): 0x35,
    # Uppercase: lowercase pattern + dot 7 (capital indicator in 8-dot).
    ord("A"): 0x41, ord("B"): 0x43, ord("C"): 0x49, ord("D"): 0x59,
    ord("E"): 0x51, ord("F"): 0x4b, ord("G"): 0x5b, ord("H"): 0x53,
    ord("I"): 0x4a, ord("J"): 0x5a, ord("K"): 0x45, ord("L"): 0x47,
    ord("M"): 0x4d, ord("N"): 0x5d, ord("O"): 0x55, ord("P"): 0x4f,
    ord("Q"): 0x5f, ord("R"): 0x57, ord("S"): 0x4e, ord("T"): 0x5e,
    ord("U"): 0x65, ord("V"): 0x67, ord("W"): 0x7a, ord("X"): 0x6d,
    ord("Y"): 0x7d, ord("Z"): 0x75,
    # Digits 0-9 -- standard US computer braille (8-dot with dot 6).
    ord("0"): 0x34, ord("1"): 0x02, ord("2"): 0x06, ord("3"): 0x12,
    ord("4"): 0x32, ord("5"): 0x22, ord("6"): 0x16, ord("7"): 0x36,
    ord("8"): 0x26, ord("9"): 0x14,
    # Common punctuation -- US computer braille assignments.
    ord(" "): 0x00,
    ord("."): 0x32, ord(","): 0x02, ord(";"): 0x06, ord(":"): 0x12,
    ord("?"): 0x26, ord("!"): 0x16, ord("'"): 0x04, ord('"'): 0x36,
    ord("("): 0x37, ord(")"): 0x3e, ord("-"): 0x24, ord("/"): 0x0c,
    ord("\\"): 0x30, ord("&"): 0x2f, ord("*"): 0x21, ord("+"): 0x2b,
    ord("="): 0x3f, ord("<"): 0x23, ord(">"): 0x1c, ord("@"): 0x20,
    ord("#"): 0x3c, ord("$"): 0x2e, ord("%"): 0x29, ord("^"): 0x28,
    ord("_"): 0x38, ord("`"): 0x10, ord("|"): 0x33, ord("~"): 0x18,
    ord("["): 0x2c, ord("]"): 0x39, ord("{"): 0x06 | 0x40, ord("}"): 0x12 | 0x40,
    ord("\n"): 0x00, ord("\t"): 0x00,
}


def text_to_cells(text: str, table: str | None = None) -> bytes:
    """Convert UTF-8 text to braille cell bytes. Always returns bytes.

    `table` is the liblouis table name (e.g. "en-ueb-g2.ctb",
    "en-us-comp8.ctb"). When given, attempts liblouis translation
    first and falls back to the static ASCII table on failure.
    When omitted, uses the static ASCII table directly without
    even probing liblouis (cheaper for English-only callers).

    The output length is the number of cells, not the number of
    input chars -- a contraction can compress multiple chars into
    one cell. Callers that need positional correspondence (e.g. for
    cursor mapping) should request `table=None` (1:1 ASCII path).
    """

    if not text:
        return b""

    if table is not None and _liblouis_available():
        cells = _via_liblouis(text, table)
        if cells is not None:
            return cells

    return _via_ascii_table(text)


def text_to_unicode_braille(text: str, table: str | None = None) -> str:
    """Convert text to Unicode braille pattern chars (U+2800..U+28FF).

    Useful for callers that want to display braille in a normal text
    UI (logging, debug panels, test assertions). Each output char
    is a single Unicode codepoint corresponding to one cell.
    """

    cells = text_to_cells(text, table=table)
    return "".join(chr(0x2800 + b) for b in cells)


def available_backends() -> Tuple[str, ...]:
    """Returns the backends installed and usable right now.

    Always contains "ascii". Contains "liblouis" iff the louis
    Python module is importable. Use this to surface a clear "you
    need to install liblouis for UEB Grade 2" message to extension
    users.
    """

    backends = ["ascii"]
    if _liblouis_available():
        backends.append("liblouis")
    return tuple(backends)


@lru_cache(maxsize=1)
def _liblouis_available() -> bool:
    try:
        import louis  # pylint: disable=import-outside-toplevel,unused-import  # noqa: F401
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def _via_liblouis(text: str, table: str) -> bytes | None:
    try:
        import louis  # pylint: disable=import-outside-toplevel
        # louis.translate returns (translated_text, ...).
        # translatedString is the contracted form; we then convert
        # each char to its cell mask. louis ships a dotsIO mode that
        # returns the dot pattern directly via translateString with
        # mode=4 (compbrlAtCursor) or mode=8 (dotsIO).
        translated = louis.translateString([table], text, None, mode=8)
        # In dotsIO mode each output char is a Unicode braille
        # pattern (U+2800 + mask). Strip back down to mask bytes.
        return bytes((ord(c) - 0x2800) & 0xff for c in translated)
    except Exception:  # pylint: disable=broad-except
        return None


def _via_ascii_table(text: str) -> bytes:
    return bytes(_ASCII_TO_CELLS.get(ord(c), 0) for c in text)
