"""Windows Virtual-Key code -> X11 keysym translation table.

NVDA Remote v2.x `key` messages carry the originating machine's
Windows virtual key code (`vk_code`). To replay the keystroke on a
Linux slave we have to map that into an X11 keysym that
Atspi.generate_keyboard_event understands.

The table below covers the keys real users actually press:
letters, digits, arrows, function keys, modifiers, common
punctuation, and the dead keys that interact with Orca. Anything
unmapped returns 0; the caller logs and drops it rather than
guessing.

Keysym values are the standard X.org constants from
/usr/include/X11/keysymdef.h. We hardcode rather than depending on
python-xlib so the extension stays stdlib-only.
"""

from __future__ import annotations


# Letters: Windows VK_A..VK_Z (0x41..0x5A) map to lowercase X keysyms
# (a..z, 0x61..0x7a). NVDA Remote sends VK_SHIFT separately when the
# user is holding shift, so we never emit the uppercase keysym here.
_LETTERS: dict[int, int] = {
    0x41 + i: 0x61 + i for i in range(26)
}

# Digits: VK_0..VK_9 (0x30..0x39) -> X keysyms 0x30..0x39 (same).
_DIGITS: dict[int, int] = {
    0x30 + i: 0x30 + i for i in range(10)
}

# Numeric keypad digits: VK_NUMPAD0..VK_NUMPAD9 (0x60..0x69) ->
# XK_KP_0..XK_KP_9 (0xffb0..0xffb9).
_KEYPAD_DIGITS: dict[int, int] = {
    0x60 + i: 0xffb0 + i for i in range(10)
}

# Function keys: VK_F1..VK_F24 (0x70..0x87) -> XK_F1..XK_F24
# (0xffbe..0xffd5).
_FUNCTION_KEYS: dict[int, int] = {
    0x70 + i: 0xffbe + i for i in range(24)
}

# Named keys -- the catalogue of everything that isn't a letter,
# digit, or F-key. Keysyms taken from keysymdef.h.
_NAMED: dict[int, int] = {
    0x08: 0xff08,  # VK_BACK        -> XK_BackSpace
    0x09: 0xff09,  # VK_TAB         -> XK_Tab
    0x0C: 0xff0b,  # VK_CLEAR       -> XK_Clear
    0x0D: 0xff0d,  # VK_RETURN      -> XK_Return
    0x10: 0xffe1,  # VK_SHIFT       -> XK_Shift_L
    0x11: 0xffe3,  # VK_CONTROL     -> XK_Control_L
    0x12: 0xffe9,  # VK_MENU (Alt)  -> XK_Alt_L
    0x13: 0xff13,  # VK_PAUSE       -> XK_Pause
    0x14: 0xffe5,  # VK_CAPITAL     -> XK_Caps_Lock
    # IME keys. The X server side usually has these bound to
    # ibus / fcitx triggers; mapping them lets a CJK-input user on a
    # Windows master drive the matching IME on a Linux slave.
    0x15: 0xff31,  # VK_HANGUL/KANA -> XK_Hangul / XK_Kanji_Bangou (same)
    0x17: 0xff39,  # VK_JUNJA       -> XK_Hangul_Jeonja
    0x18: 0xff37,  # VK_FINAL       -> XK_Codeinput (closest)
    0x19: 0xff21,  # VK_HANJA/KANJI -> XK_Kanji
    0x1B: 0xff1b,  # VK_ESCAPE      -> XK_Escape
    0x1C: 0xff26,  # VK_CONVERT     -> XK_Henkan_Mode
    0x1D: 0xff22,  # VK_NONCONVERT  -> XK_Muhenkan
    0x1E: 0xff23,  # VK_ACCEPT      -> XK_Henkan
    0x1F: 0xff24,  # VK_MODECHANGE  -> XK_Romaji
    0x20: 0x0020,  # VK_SPACE       -> XK_space
    # Nav-cluster keys default to the *numpad* keysym (extended=False
    # is what Windows reports when NumLock is off and the user pressed
    # the numpad nav keys -- e.g. numpad-0 for Insert). The main-row
    # variants land in _EXTENDED_OVERRIDES below. This matters because
    # Orca's desktop layout binds flat review, screen-reader commands,
    # etc. to the XK_KP_* keysyms; mapping a remote numpad press to
    # the main-row keysym would silently miss every Orca binding.
    0x21: 0xff9a,  # VK_PRIOR       -> XK_KP_Page_Up
    0x22: 0xff9b,  # VK_NEXT        -> XK_KP_Page_Down
    0x23: 0xff9c,  # VK_END         -> XK_KP_End
    0x24: 0xff95,  # VK_HOME        -> XK_KP_Home
    0x25: 0xff96,  # VK_LEFT        -> XK_KP_Left
    0x26: 0xff97,  # VK_UP          -> XK_KP_Up
    0x27: 0xff98,  # VK_RIGHT       -> XK_KP_Right
    0x28: 0xff99,  # VK_DOWN        -> XK_KP_Down
    0x2C: 0xff61,  # VK_SNAPSHOT    -> XK_Print
    0x2D: 0xff9e,  # VK_INSERT      -> XK_KP_Insert
    0x2E: 0xff9f,  # VK_DELETE      -> XK_KP_Delete
    0x5B: 0xffeb,  # VK_LWIN        -> XK_Super_L
    0x5C: 0xffec,  # VK_RWIN        -> XK_Super_R
    0x5D: 0xff67,  # VK_APPS        -> XK_Menu
    0x6A: 0xffaa,  # VK_MULTIPLY    -> XK_KP_Multiply
    0x6B: 0xffab,  # VK_ADD         -> XK_KP_Add
    0x6C: 0xffac,  # VK_SEPARATOR   -> XK_KP_Separator
    0x6D: 0xffad,  # VK_SUBTRACT    -> XK_KP_Subtract
    0x6E: 0xffae,  # VK_DECIMAL     -> XK_KP_Decimal
    0x6F: 0xffaf,  # VK_DIVIDE      -> XK_KP_Divide
    0x90: 0xff7f,  # VK_NUMLOCK     -> XK_Num_Lock
    0x91: 0xff14,  # VK_SCROLL      -> XK_Scroll_Lock
    0xA0: 0xffe1,  # VK_LSHIFT      -> XK_Shift_L
    0xA1: 0xffe2,  # VK_RSHIFT      -> XK_Shift_R
    0xA2: 0xffe3,  # VK_LCONTROL    -> XK_Control_L
    0xA3: 0xffe4,  # VK_RCONTROL    -> XK_Control_R
    0xA4: 0xffe9,  # VK_LMENU       -> XK_Alt_L
    0xA5: 0xffea,  # VK_RMENU       -> XK_Alt_R
    # Browser keys (VK 0xA6..0xAC). XF86 keysyms; modern desktops bind
    # them to the matching application actions.
    0xA6: 0x1008ff26,  # VK_BROWSER_BACK       -> XF86XK_Back
    0xA7: 0x1008ff27,  # VK_BROWSER_FORWARD    -> XF86XK_Forward
    0xA8: 0x1008ff73,  # VK_BROWSER_REFRESH    -> XF86XK_Refresh
    0xA9: 0x1008ff28,  # VK_BROWSER_STOP       -> XF86XK_Stop
    0xAA: 0x1008ff1b,  # VK_BROWSER_SEARCH     -> XF86XK_Search
    0xAB: 0x1008ff30,  # VK_BROWSER_FAVORITES  -> XF86XK_Favorites
    0xAC: 0x1008ff18,  # VK_BROWSER_HOME       -> XF86XK_HomePage
    # Volume / media keys (VK 0xAD..0xB7).
    0xAD: 0x1008ff12,  # VK_VOLUME_MUTE        -> XF86XK_AudioMute
    0xAE: 0x1008ff11,  # VK_VOLUME_DOWN        -> XF86XK_AudioLowerVolume
    0xAF: 0x1008ff13,  # VK_VOLUME_UP          -> XF86XK_AudioRaiseVolume
    0xB0: 0x1008ff17,  # VK_MEDIA_NEXT_TRACK   -> XF86XK_AudioNext
    0xB1: 0x1008ff16,  # VK_MEDIA_PREV_TRACK   -> XF86XK_AudioPrev
    0xB2: 0x1008ff15,  # VK_MEDIA_STOP         -> XF86XK_AudioStop
    0xB3: 0x1008ff14,  # VK_MEDIA_PLAY_PAUSE   -> XF86XK_AudioPlay
    0xB4: 0x1008ff19,  # VK_LAUNCH_MAIL        -> XF86XK_Mail
    0xB5: 0x1008ff32,  # VK_LAUNCH_MEDIA_SELECT-> XF86XK_AudioMedia
    0xB6: 0x1008ff1c,  # VK_LAUNCH_APP1        -> XF86XK_MyComputer
    0xB7: 0x1008ff5d,  # VK_LAUNCH_APP2        -> XF86XK_Calculator
    0xBA: 0x003b,  # VK_OEM_1       -> XK_semicolon
    0xBB: 0x003d,  # VK_OEM_PLUS    -> XK_equal
    0xBC: 0x002c,  # VK_OEM_COMMA   -> XK_comma
    0xBD: 0x002d,  # VK_OEM_MINUS   -> XK_minus
    0xBE: 0x002e,  # VK_OEM_PERIOD  -> XK_period
    0xBF: 0x002f,  # VK_OEM_2       -> XK_slash
    0xC0: 0x0060,  # VK_OEM_3       -> XK_grave
    0xDB: 0x005b,  # VK_OEM_4       -> XK_bracketleft
    0xDC: 0x005c,  # VK_OEM_5       -> XK_backslash
    0xDD: 0x005d,  # VK_OEM_6       -> XK_bracketright
    0xDE: 0x0027,  # VK_OEM_7       -> XK_apostrophe
}


_VK_TO_KEYSYM: dict[int, int] = {
    **_LETTERS,
    **_DIGITS,
    **_KEYPAD_DIGITS,
    **_FUNCTION_KEYS,
    **_NAMED,
}


# Windows reports `extended=True` for keys on the "enhanced keyboard"
# extended set: right Ctrl/Alt, numpad Enter and slash, and the
# main-row nav cluster to the left of the numeric keypad (Insert,
# Delete, Home, End, Page Up, Page Down, and arrow keys). For the
# nav keys this is the *inverse* of how the _NAMED defaults are set
# up -- the defaults give the numpad keysym, and an extended press
# overrides it to the main-row keysym.
_EXTENDED_OVERRIDES: dict[int, int] = {
    0x0D: 0xff8d,  # extended VK_RETURN  -> XK_KP_Enter
    0x11: 0xffe4,  # extended VK_CONTROL -> XK_Control_R
    0x12: 0xffea,  # extended VK_MENU    -> XK_Alt_R
    0x21: 0xff55,  # extended VK_PRIOR   -> XK_Page_Up
    0x22: 0xff56,  # extended VK_NEXT    -> XK_Page_Down
    0x23: 0xff57,  # extended VK_END     -> XK_End
    0x24: 0xff50,  # extended VK_HOME    -> XK_Home
    0x25: 0xff51,  # extended VK_LEFT    -> XK_Left
    0x26: 0xff52,  # extended VK_UP      -> XK_Up
    0x27: 0xff53,  # extended VK_RIGHT   -> XK_Right
    0x28: 0xff54,  # extended VK_DOWN    -> XK_Down
    0x2D: 0xff63,  # extended VK_INSERT  -> XK_Insert
    0x2E: 0xffff,  # extended VK_DELETE  -> XK_Delete
}


def vk_to_keysym(vk_code: int, extended: bool = False) -> int:
    """Translate a Windows VK code (+ extended flag) to an X11 keysym.

    Returns 0 if the VK code is not in the table; callers should
    log and skip the event rather than synthesize a bogus keysym.
    """

    if extended and vk_code in _EXTENDED_OVERRIDES:
        return _EXTENDED_OVERRIDES[vk_code]
    return _VK_TO_KEYSYM.get(vk_code, 0)


# Reverse: X11 keysym -> (vk_code, extended_flag). Built once at
# module init from the forward tables. Extended-flag bias: if a
# keysym appears in _EXTENDED_OVERRIDES (e.g. XK_Page_Up for main-
# row Page Up vs XK_KP_Page_Up for numpad Page Up), the override
# wins and the table records extended=True. The non-extended source
# keysym still maps to extended=False because both sides of the
# nav-cluster split are real X11 keysyms in their own right.
def _build_reverse_table() -> dict[int, tuple[int, bool]]:
    table: dict[int, tuple[int, bool]] = {}
    # Forward (non-extended) first so extended overrides them by
    # winning the same keysym key when it matches one. In practice
    # the extended keysyms (XK_Page_Up etc.) are DIFFERENT from the
    # non-extended ones (XK_KP_Page_Up), so both sides coexist in
    # this table -- one points back at the extended VK, the other at
    # the non-extended VK.
    for vk, keysym in _VK_TO_KEYSYM.items():
        if keysym != 0 and keysym not in table:
            table[keysym] = (vk, False)
    # Some AT-SPI events report shifted letter keys as uppercase
    # keysyms. Shift is forwarded separately, so map XK_A..XK_Z back
    # to the same VK codes as XK_a..XK_z instead of dropping them.
    for index in range(26):
        table[0x41 + index] = (0x41 + index, False)
    for vk, keysym in _EXTENDED_OVERRIDES.items():
        if keysym != 0:
            table[keysym] = (vk, True)
    return table


_KEYSYM_TO_VK: dict[int, tuple[int, bool]] = _build_reverse_table()


def keysym_to_vk(keysym: int) -> tuple[int, bool]:
    """Translate an X11 keysym to (vk_code, extended) for outbound keys.

    Returns (0, False) if the keysym isn't in our table; callers
    should drop the event rather than guess. Used by master-side key
    forwarding (Linux Orca master -> NVDA / Orca slave): when the
    extension consumes a local key and forwards it on the wire,
    NVDA Remote v2's `key` frame requires the originating-machine
    Windows VK code, which is what this returns.
    """

    return _KEYSYM_TO_VK.get(keysym, (0, False))


def forwardable_keysyms() -> frozenset[int]:
    """Returns the keysyms master-side forwarding can send.

    This is the set the KeysetGrab on the master should cover so
    that, while focused-on-remote is True, the focused local app
    stops receiving keys we're already forwarding to the slave.
    Anything outside this set isn't forwardable (keysym_to_vk
    returns 0) so the focused app SHOULD still receive it -- that
    matches the "unmapped: pass through" semantics in
    `_on_keyboard_event`.

    Orca Remote's own command chords are handled by the bypass list
    in RemoteExtension._on_keyboard_event, so they dispatch locally
    while ordinary screen-reader commands continue to forward.
    """

    return frozenset(_KEYSYM_TO_VK.keys())
