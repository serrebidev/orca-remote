"""Mouse input synthesis at screen coords.

OCR uses this to click on a recognized text region. Other extensions
could use it for "navigate to here" gestures, pointer-led
demonstrations, etc.

Backend order:

  1. `Atspi.generate_mouse_event(x, y, kind)` -- the canonical
     AT-SPI path. On X11 this calls XTest under the covers and
     works for any X-server-attached process. Coverage on Wayland
     varies by compositor (mostly broken because compositors
     haven't implemented the AT-SPI input-synth interface).

  2. Not yet implemented: xdg-desktop-portal `RemoteDesktop` for
     Wayland. Will land in v0.2 with the explicit user-permission
     dialog handshake. Tracked in docs/architecture.md.

Returns True on best-effort success, False if no backend could
synthesize the event. Callers should check the return value before
assuming the click landed (Wayland will silently fail).
"""

from __future__ import annotations

from typing import Literal


# Atspi.generate_mouse_event uses these short string codes for the
# kind argument. Documented here so callers don't have to look them
# up in the AT-SPI source.
_BUTTON_KIND_PRESS = {
    "left":   "b1p",
    "middle": "b2p",
    "right":  "b3p",
}
_BUTTON_KIND_RELEASE = {
    "left":   "b1r",
    "middle": "b2r",
    "right":  "b3r",
}
_BUTTON_KIND_CLICK = {
    "left":   "b1c",
    "middle": "b2c",
    "right":  "b3c",
}
_BUTTON_KIND_DOUBLE_CLICK = {
    "left":   "b1d",
    "middle": "b2d",
    "right":  "b3d",
}


Button = Literal["left", "middle", "right"]


def click_at(x: int, y: int, button: Button = "left") -> bool:
    """Synthesize a single mouse click at screen coords (x, y).

    `button` is one of "left", "middle", "right" (default "left").
    Returns True on best-effort success; False if no backend could
    synthesize the event.

    Note: True does NOT guarantee the click was actually delivered
    to a window -- AT-SPI's generate_mouse_event is fire-and-forget
    on X11. Wayland coverage is compositor-dependent and may
    silently fail even when this returns True.
    """

    kind = _BUTTON_KIND_CLICK.get(button)
    if kind is None:
        return False
    return _generate(x, y, kind)


def double_click_at(x: int, y: int, button: Button = "left") -> bool:
    """Synthesize a mouse double-click at screen coords (x, y)."""

    kind = _BUTTON_KIND_DOUBLE_CLICK.get(button)
    if kind is None:
        return False
    return _generate(x, y, kind)


def press_at(x: int, y: int, button: Button = "left") -> bool:
    """Synthesize a mouse-button PRESS (without release) at (x, y).

    Useful for drag-style operations. Pair with `release_at`.
    """

    kind = _BUTTON_KIND_PRESS.get(button)
    if kind is None:
        return False
    return _generate(x, y, kind)


def release_at(x: int, y: int, button: Button = "left") -> bool:
    """Synthesize a mouse-button RELEASE at (x, y).

    Pair with `press_at` to complete a drag. Without a preceding
    press, behavior is system-dependent (usually a no-op).
    """

    kind = _BUTTON_KIND_RELEASE.get(button)
    if kind is None:
        return False
    return _generate(x, y, kind)


def move_to(x: int, y: int) -> bool:
    """Move the pointer to screen coords (x, y) without clicking."""

    return _generate(x, y, "abs")


def _generate(x: int, y: int, kind: str) -> bool:
    """Calls Atspi.generate_mouse_event; returns True on success."""

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # pylint: disable=import-outside-toplevel
        Atspi.generate_mouse_event(int(x), int(y), kind)
        return True
    except Exception:  # pylint: disable=broad-except
        return False
