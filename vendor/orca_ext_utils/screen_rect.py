"""Screen-coord rect for an AT-SPI accessible object.

OCR's central problem: "I just got user-invoked OCR on a focused
text region; where on screen do I render the recognition results,
and where do I click to navigate to a chosen word?" This module
answers both.

Backend order:

  1. `Atspi.Component.get_extents(SCREEN)` -- the canonical AT-SPI
     path. Works for GTK3 / Qt5 / native X apps. Returns
     (x, y, width, height) directly in screen coords.

  2. Fallback for the case Atspi returns invalid extents (typically
     GTK4-on-X11 or XWayland apps that don't populate the
     screen-coord bridge correctly): find the toplevel window's
     screen origin via Gdk, then translate using the WINDOW-relative
     extents AT-SPI does provide.

  3. Wayland: AT-SPI extents commonly return -1,-1 or zero, and
     the Gdk fallback returns None because get_origin() can't
     determine screen coords without compositor cooperation. We
     return None and document the limitation.

The function never raises; it returns None when it can't produce
a usable rect. Callers should degrade gracefully (e.g. OCR could
fall back to "render the result in a dialog instead of overlaid
at the source").
"""

from __future__ import annotations

from typing import Any, Tuple

from . import _backend, window_info


def for_accessible(obj: Any) -> Tuple[int, int, int, int] | None:
    """Returns (x, y, width, height) in screen coords for `obj`, or None.

    `obj` is an Atspi.Accessible. The function tries the AT-SPI
    component-extents path first; if that returns garbage or the
    object has no Component interface, falls back to the toplevel-
    window + window-relative-offset path on X11. Returns None on
    Wayland and on any error.
    """

    if obj is None:
        return None

    screen_rect = _try_atspi_screen_extents(obj)
    if screen_rect is not None:
        return screen_rect

    if not _backend.is_x11():
        return None

    return _try_window_relative_fallback(obj)


def for_active_window() -> Tuple[int, int, int, int] | None:
    """Returns (x, y, width, height) of the currently active toplevel.

    Convenience wrapper for the common "where is the user right now"
    query. Same Wayland limitations as `for_accessible` and
    `window_info.active_window_rect` (returns None there).
    """

    return window_info.active_window_rect()


def _try_atspi_screen_extents(obj: Any) -> Tuple[int, int, int, int] | None:
    """First-line: ask AT-SPI directly for screen-coord extents."""

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # pylint: disable=import-outside-toplevel

        # The component interface is per-object; some accessibles
        # don't have one (window-less abstract groupings, etc.).
        # Defensive: catch AttributeError too.
        rect = obj.get_extents(Atspi.CoordType.SCREEN)
    except Exception:  # pylint: disable=broad-except
        return None

    if rect is None:
        return None
    # Atspi returns -1, -1 (or 0-width / 0-height) when it can't
    # determine the coords. These are sentinel values, not real
    # extents -- never return them.
    if rect.width <= 0 or rect.height <= 0:
        return None
    if rect.x < 0 or rect.y < 0:
        return None
    return (int(rect.x), int(rect.y), int(rect.width), int(rect.height))


def _try_window_relative_fallback(obj: Any) -> Tuple[int, int, int, int] | None:
    """Fallback: toplevel window's screen origin + AT-SPI WINDOW extents.

    Walks the accessibility tree up to a top-level window, finds its
    screen origin via Gdk (X11 only), then offsets by the object's
    extents *within* that window (which Atspi reliably reports even
    when SCREEN coords are broken).

    X11 only. Returns None when:
      - we can't find a toplevel ancestor for `obj`
      - Gdk can't tell us the toplevel's screen origin
      - AT-SPI doesn't have WINDOW-relative extents for `obj`
    """

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # pylint: disable=import-outside-toplevel
    except Exception:  # pylint: disable=broad-except
        return None

    # Get WINDOW-relative extents for the object itself.
    try:
        window_rect = obj.get_extents(Atspi.CoordType.WINDOW)
    except Exception:  # pylint: disable=broad-except
        return None
    if window_rect is None or window_rect.width <= 0 or window_rect.height <= 0:
        return None

    # Find the active X11 window's screen origin. This is a best-
    # effort: we assume `obj` belongs to whatever window is currently
    # active. For OCR's call pattern -- "OCR what's focused right
    # now" -- that's typically correct. Callers with different
    # semantics should compute the origin themselves.
    active = window_info.active_window_rect()
    if active is None:
        return None
    ox, oy, _ow, _oh = active

    return (
        int(ox + window_rect.x),
        int(oy + window_rect.y),
        int(window_rect.width),
        int(window_rect.height),
    )
