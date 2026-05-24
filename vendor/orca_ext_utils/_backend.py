"""Backend detection: X11 vs Wayland.

The session-type detection is fiddly because environment variables
lie under common configurations -- in particular, `XDG_SESSION_TYPE`
is set to `wayland` on some MATE / XFCE installs where the user is
actually running X11 (Xorg) the whole time. We probe several signals
and prefer the most authoritative.

Order of precedence (most to least authoritative):

  1. `Gdk.Display.get_default()` is a `Gdk.X11.Display` -> X11.
     This is what actually matters for X11-only APIs like XTest.
  2. `Gdk.Display.get_default()` is a `Gdk.Wayland.Display` -> Wayland.
  3. `WAYLAND_DISPLAY` env var is set and non-empty -> Wayland.
  4. `DISPLAY` env var is set and non-empty -> X11.
  5. Fall back to "unknown" (treated as not-X11 to be conservative).
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def is_x11() -> bool:
    """Returns True iff the current session is X11 (or XWayland).

    Cached: the session type doesn't change for the life of the
    process. Cheap on every subsequent call.
    """

    if _gdk_says_x11():
        return True
    # If Gdk says Wayland, trust it.
    if _gdk_says_wayland():
        return False
    # Gdk unavailable or undecided: fall back to env vars.
    if os.environ.get("WAYLAND_DISPLAY", ""):
        return False
    if os.environ.get("DISPLAY", ""):
        return True
    return False


@lru_cache(maxsize=1)
def is_wayland() -> bool:
    """Returns True iff the current session is Wayland.

    Note this is NOT simply `not is_x11()`: on headless / unknown
    environments both return False. An extension that needs to know
    "do I have a display server at all" should check both.
    """

    if _gdk_says_wayland():
        return True
    if _gdk_says_x11():
        return False
    if os.environ.get("WAYLAND_DISPLAY", ""):
        return True
    return False


def _gdk_says_x11() -> bool:
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel
        display = Gdk.Display.get_default()
        if display is None:
            return False
        # The class name check works whether or not gi has imported
        # the X11 typelib. Cheap and string-stable.
        return type(display).__name__ == "X11Display"
    except Exception:  # pylint: disable=broad-except
        return False


def _gdk_says_wayland() -> bool:
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel
        display = Gdk.Display.get_default()
        if display is None:
            return False
        return type(display).__name__ == "WaylandDisplay"
    except Exception:  # pylint: disable=broad-except
        return False
