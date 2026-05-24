"""Active window info.

Find the currently-focused toplevel window. Used by `screen_rect`
as a fallback when AT-SPI's screen-coord query returns junk, and
exposed directly for extensions that just want "where is the user?"

Backend coverage:

  X11:      Gdk active-window query gives a Gdk.X11.Window whose
            geometry + origin reflect the screen position. Works
            for native X apps and XWayland-hosted apps.
  Wayland:  no portable way to get the active window's screen
            position without a portal session. Returns None.
            The portal RemoteDesktop interface CAN return cursor
            position, but window-of-focus + rect is not exposed.
"""

from __future__ import annotations

from typing import Tuple

from . import _backend


def active_window_rect() -> Tuple[int, int, int, int] | None:
    """Returns (x, y, width, height) of the active toplevel, or None.

    Returns coordinates in the X server's screen space on X11.
    Returns None on Wayland and on errors (no display, no active
    window, Gdk unavailable).
    """

    if not _backend.is_x11():
        return None
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel

        screen = Gdk.Screen.get_default()
        if screen is None:
            return None
        window = screen.get_active_window()
        if window is None:
            return None
        # get_origin returns (success, x, y) -- the success bool was
        # added in GTK3 to signal "we couldn't determine origin",
        # primarily for the Wayland case. On X11 it's always True.
        ok, ox, oy = window.get_origin()
        if not ok:
            return None
        width = window.get_width()
        height = window.get_height()
        if width <= 0 or height <= 0:
            return None
        return (int(ox), int(oy), int(width), int(height))
    except Exception:  # pylint: disable=broad-except
        return None


def active_window_xid() -> int | None:
    """Returns the X11 window ID of the active toplevel, or None.

    X11 only. Returns None on Wayland and when the active window
    can't be determined.
    """

    if not _backend.is_x11():
        return None
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel

        screen = Gdk.Screen.get_default()
        if screen is None:
            return None
        window = screen.get_active_window()
        if window is None:
            return None
        # get_xid only exists on Gdk.X11.Window. Defensive call.
        get_xid = getattr(window, "get_xid", None)
        if get_xid is None:
            return None
        return int(get_xid())
    except Exception:  # pylint: disable=broad-except
        return None
