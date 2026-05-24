"""Key combo / keysym / modifier helpers.

orca-remote's `keymap.py` (Windows VK -> X11 keysym) and OCR's
modal-mode binding setup both reinvent slices of this. The
canonical operations are:

  - Convert a human-readable chord string ("Ctrl+Shift+a") to
    (modifier_mask, keysym).
  - Convert a (modifier_mask, keysym) back to a stable string for
    config-file persistence and log output.
  - Convert keysym numbers to readable names ("XK_a" or "a").
  - Parse modifier masks into a list of names ("Ctrl", "Shift").

This module is pure-Python with no Atspi / Gdk dependency -- it
uses a built-in name table for the common keysyms and modifiers.
For anything outside the table, callers can fall back to
Gdk.keyval_from_name / Gdk.keyval_name themselves; we expose
`name_to_keysym_via_gdk` as a convenience wrapper.

Modifier bits use the X11 standard mask values, matching
AT-SPI's Atspi.ModifierType (which is itself derived from X11):

    0x01 = Shift     0x04 = Control   0x08 = Alt (Mod1)
    0x10 = Mod2      0x20 = Mod3      0x40 = Super (Mod4)
    0x80 = Mod5

We name only the four commonly-bound bits; Mod2 / Mod3 / Mod5
get round-tripped through the mask without losing information
but don't appear in chord strings.
"""

from __future__ import annotations

from typing import Tuple


# Standard X11 modifier mask values. Bits beyond these are passed
# through unchanged but never emitted as named modifiers.
MOD_SHIFT = 0x01
MOD_CTRL = 0x04
MOD_ALT = 0x08
MOD_SUPER = 0x40

# Display order for modifiers in chord strings: matches the
# convention most documentation and configuration files use
# ("Ctrl+Shift+Alt+key" not "Alt+Shift+Ctrl+key").
_MODIFIER_ORDER: tuple[tuple[int, str], ...] = (
    (MOD_CTRL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_SUPER, "Super"),
)


# Names accepted on parse. Aliases let us be forgiving about user
# input from config files: "control"/"ctl"/"^"/"ctrl" all mean
# Control. Canonical name on output is always the first entry.
_MODIFIER_ALIASES: dict[str, int] = {
    "ctrl": MOD_CTRL, "control": MOD_CTRL, "ctl": MOD_CTRL, "^": MOD_CTRL,
    "shift": MOD_SHIFT, "shft": MOD_SHIFT,
    "alt": MOD_ALT, "meta": MOD_ALT, "mod1": MOD_ALT,
    "super": MOD_SUPER, "win": MOD_SUPER, "windows": MOD_SUPER,
    "cmd": MOD_SUPER, "mod4": MOD_SUPER,
}


# Compact built-in keysym table for the keys Orca commands actually
# bind to. Anything not here goes through Gdk.keyval_from_name; if
# both lookups fail, return None / 0.
_NAME_TO_KEYSYM: dict[str, int] = {
    # Letters (lowercase canonical).
    **{chr(0x61 + i): 0x61 + i for i in range(26)},
    # Digits.
    **{str(i): 0x30 + i for i in range(10)},
    # Function keys F1..F24.
    **{f"f{i}": 0xffbe + (i - 1) for i in range(1, 25)},
    # Common named keys.
    "space": 0x0020, "tab": 0xff09, "return": 0xff0d, "enter": 0xff0d,
    "escape": 0xff1b, "esc": 0xff1b, "backspace": 0xff08,
    "delete": 0xffff, "insert": 0xff63,
    "home": 0xff50, "end": 0xff57,
    "pageup": 0xff55, "pagedown": 0xff56,
    "left": 0xff51, "up": 0xff52, "right": 0xff53, "down": 0xff54,
    # Numeric keypad (Orca binds heavily to these on desktop layout).
    "kp_0": 0xffb0, "kp_1": 0xffb1, "kp_2": 0xffb2, "kp_3": 0xffb3,
    "kp_4": 0xffb4, "kp_5": 0xffb5, "kp_6": 0xffb6, "kp_7": 0xffb7,
    "kp_8": 0xffb8, "kp_9": 0xffb9,
    "kp_decimal": 0xffae, "kp_divide": 0xffaf, "kp_multiply": 0xffaa,
    "kp_subtract": 0xffad, "kp_add": 0xffab, "kp_enter": 0xff8d,
}


_KEYSYM_TO_NAME: dict[int, str] = {v: k for k, v in _NAME_TO_KEYSYM.items()}


def parse_chord(chord: str) -> Tuple[int, int]:
    """Parse "Ctrl+Shift+a" into (modifier_mask, keysym).

    Tokens are split on "+" or whitespace and matched case-insensitively
    against the modifier and keysym tables. The last unparsed token is
    treated as the keysym. Returns (0, 0) if the chord can't be parsed
    (no keysym match).

    Tolerant of trailing/leading whitespace and mixed separators:
    "Ctrl + Shift + A", "ctrl+a", and "CONTROL+SHIFT+A" all work.
    """

    if not chord:
        return (0, 0)
    tokens = [t.strip() for t in chord.replace(" ", "+").split("+") if t.strip()]
    if not tokens:
        return (0, 0)
    mask = 0
    key_token = tokens[-1]
    for token in tokens[:-1]:
        m = _MODIFIER_ALIASES.get(token.lower())
        if m is not None:
            mask |= m
    keysym = name_to_keysym(key_token)
    if keysym == 0:
        return (0, 0)
    return (mask, keysym)


def format_chord(modifier_mask: int, keysym: int) -> str:
    """Inverse of parse_chord: returns a canonical chord string.

    Examples:
        format_chord(0x04, 0x61)         -> "Ctrl+a"
        format_chord(0x05, 0xff52)       -> "Ctrl+Shift+Up"
        format_chord(0,    0xff1b)       -> "Escape"

    The keysym name uses the built-in table when available, falling
    back to "0x<hex>" for unknown values (so chord strings round-
    trip losslessly through parse_chord even for exotic keysyms,
    which name_to_keysym will accept as hex).
    """

    parts: list[str] = []
    for bit, name in _MODIFIER_ORDER:
        if modifier_mask & bit:
            parts.append(name)
    key_name = keysym_to_name(keysym) or f"0x{int(keysym):x}"
    # Title-case named keys for readability (Up, Left, Escape, F1).
    # Single-char keys (letters, digits, punctuation) stay as-is.
    if len(key_name) > 1 and key_name.isalpha():
        key_name = key_name.title()
    parts.append(key_name)
    return "+".join(parts)


def name_to_keysym(name: str) -> int:
    """Returns the X11 keysym for a key name, or 0 if unknown.

    Tries the built-in table first (covers all keys Orca commands
    bind to). Falls back to Gdk.keyval_from_name if available.
    Accepts "0x<hex>" form as a last resort for forward-compatibility
    with chord strings that mention keysyms outside the table.
    """

    if not name:
        return 0
    lowered = name.lower()
    if lowered in _NAME_TO_KEYSYM:
        return _NAME_TO_KEYSYM[lowered]
    if name.startswith("0x") or name.startswith("0X"):
        try:
            return int(name, 16)
        except ValueError:
            return 0
    return name_to_keysym_via_gdk(name)


def keysym_to_name(keysym: int) -> str | None:
    """Returns the human-readable name for a keysym, or None.

    Built-in table first, Gdk.keyval_name as fallback. Returns None
    if neither knows the keysym.
    """

    if keysym in _KEYSYM_TO_NAME:
        return _KEYSYM_TO_NAME[keysym]
    return keysym_to_name_via_gdk(keysym)


def modifier_names(modifier_mask: int) -> Tuple[str, ...]:
    """Returns the named modifiers set in `modifier_mask`, in display order.

    Bits without canonical names (Mod2, Mod3, Mod5) are silently
    omitted -- they don't appear in user-facing strings.
    """

    return tuple(name for bit, name in _MODIFIER_ORDER if modifier_mask & bit)


_GDK_KEY_VOID_SYMBOL = 0xffffff
"""Gdk.keyval_from_name returns this when the name doesn't map.

Treat it as "unknown" (return 0) rather than passing it through:
0xffffff is a sentinel, not a real keysym, and downstream code
calling Atspi.generate_keyboard_event with VoidSymbol would emit
a key event for nothing.
"""


def name_to_keysym_via_gdk(name: str) -> int:
    """Gdk.keyval_from_name wrapper. Returns 0 on failure."""

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel
        value = int(Gdk.keyval_from_name(name) or 0)
        if value == _GDK_KEY_VOID_SYMBOL:
            return 0
        return value
    except Exception:  # pylint: disable=broad-except
        return 0


def keysym_to_name_via_gdk(keysym: int) -> str | None:
    """Gdk.keyval_name wrapper. Returns None on failure."""

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel
        return Gdk.keyval_name(int(keysym)) or None
    except Exception:  # pylint: disable=broad-except
        return None
