"""ASCII → US Computer Braille cell byte table.

NVDA Remote's `display` message carries raw braille cell bytes (one
per cell, each byte's low 8 bits encoding the 8 dots). The host has
the rendered braille TEXT (from braille.py via the braille_emitted
hook), so we need a text→cell mapping before we can send.

This table is the standard "US computer braille code" (BRF / North
American Braille Computer Code) for printable ASCII 0x20..0x7e.
It's lossy for non-Latin scripts but legible for English, which
covers the practical Orca-host → NVDA-master use case. Anything
not in the table is mapped to an empty cell.

If the future calls for richer translation (UEB Grade 2, language
tables, Unicode braille block passthrough), a liblouis-backed
translator can replace this module without touching the wire layer.

Cell-byte dot encoding (X11 / Unicode braille / BRF agreement):
    bit 0 = dot 1, bit 1 = dot 2, bit 2 = dot 3,
    bit 3 = dot 4, bit 4 = dot 5, bit 5 = dot 6,
    bit 6 = dot 7, bit 7 = dot 8
"""

from __future__ import annotations


# Map of printable ASCII -> cell byte (dots). Sourced from the
# standard US computer braille table.
_ASCII_TO_CELL: dict[str, int] = {
    " ":  0x00,
    "!":  0x16,  # dots 2,3,5
    '"':  0x10,  # dot 5
    "#":  0x3c,  # dots 3,4,5,6
    "$":  0x29,  # dots 1,4,6
    "%":  0x25,  # dots 1,3,6
    "&":  0x2f,  # dots 1,2,3,4,6
    "'":  0x04,  # dot 3
    "(":  0x36,  # dots 2,3,5,6
    ")":  0x36,  # dots 2,3,5,6 (BRF uses same for close; ambiguous OK)
    "*":  0x21,  # dots 1,6
    "+":  0x34,  # dots 3,4,6 (BRF)
    ",":  0x02,  # dot 2
    "-":  0x24,  # dots 3,6
    ".":  0x32,  # dots 2,5,6
    "/":  0x0c,  # dots 3,4
    "0":  0x34,  # dots 3,4,5,6  (BRF digit 0 = j-cell + #)
    "1":  0x01,  # dot 1
    "2":  0x03,  # dots 1,2
    "3":  0x09,  # dots 1,4
    "4":  0x19,  # dots 1,4,5
    "5":  0x11,  # dots 1,5
    "6":  0x0b,  # dots 1,2,4
    "7":  0x1b,  # dots 1,2,4,5
    "8":  0x13,  # dots 1,2,5
    "9":  0x0a,  # dots 2,4
    ":":  0x12,  # dots 2,5
    ";":  0x06,  # dots 2,3
    "<":  0x23,  # dots 1,2,6
    "=":  0x3f,  # dots 1,2,3,4,5,6
    ">":  0x1c,  # dots 3,4,5
    "?":  0x26,  # dots 2,3,6
    "@":  0x08,  # dot 4
    "A":  0x41,  # dots 1+7
    "B":  0x43,  # dots 1,2+7
    "C":  0x49,
    "D":  0x59,
    "E":  0x51,
    "F":  0x4b,
    "G":  0x5b,
    "H":  0x53,
    "I":  0x4a,
    "J":  0x5a,
    "K":  0x45,
    "L":  0x47,
    "M":  0x4d,
    "N":  0x5d,
    "O":  0x55,
    "P":  0x4f,
    "Q":  0x5f,
    "R":  0x57,
    "S":  0x4e,
    "T":  0x5e,
    "U":  0x65,
    "V":  0x67,
    "W":  0x7a,
    "X":  0x6d,
    "Y":  0x7d,
    "Z":  0x75,
    "[":  0x2f,  # dots 1,2,3,4,6
    "\\": 0x14,  # dots 3,5
    "]":  0x3d,  # dots 1,2,4,5,6
    "^":  0x18,  # dots 4,5
    "_":  0x20,  # dot 6
    "`":  0x04,  # dot 3 (BRF approximate)
    "a":  0x01,
    "b":  0x03,
    "c":  0x09,
    "d":  0x19,
    "e":  0x11,
    "f":  0x0b,
    "g":  0x1b,
    "h":  0x13,
    "i":  0x0a,
    "j":  0x1a,
    "k":  0x05,
    "l":  0x07,
    "m":  0x0d,
    "n":  0x1d,
    "o":  0x15,
    "p":  0x0f,
    "q":  0x1f,
    "r":  0x17,
    "s":  0x0e,
    "t":  0x1e,
    "u":  0x25,
    "v":  0x27,
    "w":  0x3a,
    "x":  0x2d,
    "y":  0x3d,
    "z":  0x35,
    "{":  0x2f,  # dots 1,2,3,4,6
    "|":  0x37,  # dots 1,2,3,5,6
    "}":  0x3d,  # dots 1,2,4,5,6
    "~":  0x18,  # dots 4,5
}


def text_to_cells(text: str) -> list[int]:
    """Translate a text string to a list of braille cell bytes.

    Unicode braille block characters (U+2800..U+28FF) pass through
    as their low byte (which IS the dot pattern by the standard).
    Printable ASCII is looked up in the US computer braille table.
    Tab, newline, and anything else not in either set becomes an
    empty cell (0x00).
    """

    cells: list[int] = []
    for ch in text:
        code = ord(ch)
        if 0x2800 <= code <= 0x28ff:
            cells.append(code & 0xff)
        else:
            cells.append(_ASCII_TO_CELL.get(ch, 0x00))
    return cells
