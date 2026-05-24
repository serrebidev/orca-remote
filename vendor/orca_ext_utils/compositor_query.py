"""Compositor and monitor geometry queries.

Extensions doing overlay positioning, region capture, or
"highlight on screen" gestures all need to know things AT-SPI
doesn't carry: how many monitors are connected, where each one
sits in the virtual screen, what the DPI scaling is, which monitor
the focused window is on.

Backend coverage:

  X11:      All queries work via Gdk.Display / Gdk.Monitor (Xrandr
            under the covers). Returns real values for all helpers.
  Wayland:  Same Gdk API works; values come from the compositor's
            xdg_output protocol. May return None for individual
            monitors during compositor restart / hot-plug; callers
            should treat None as transient and retry.

All helpers return None / empty tuples on error rather than
raising. Cached values are not used -- monitor layout changes
(hot-plug, rotation, resolution change) need to be visible
immediately.
"""

from __future__ import annotations

from typing import NamedTuple, Tuple


class MonitorInfo(NamedTuple):
    """A single connected monitor's geometry and properties."""

    index: int
    """Position of this monitor in `monitors()`'s return list (0-based)."""

    rect: Tuple[int, int, int, int]
    """(x, y, width, height) in virtual screen coords. Logical pixels."""

    scale_factor: int
    """HiDPI scale factor (1 = no scaling, 2 = 2x, 3 = 3x). Integer per Gdk."""

    refresh_hz: float
    """Refresh rate in Hz. 0.0 if Gdk doesn't know."""

    model: str
    """Manufacturer-reported model name, or "" if unknown."""

    is_primary: bool
    """True if this is the user's designated primary monitor."""


def monitors() -> Tuple[MonitorInfo, ...]:
    """Returns all connected monitors as MonitorInfo tuples.

    Empty tuple on error (no display, Gdk unavailable). Order is
    Gdk's enumeration order, which is typically the order the user
    arranged them in the display settings panel but is not
    guaranteed.
    """

    display = _get_display()
    if display is None:
        return ()
    try:
        n = display.get_n_monitors()
    except Exception:  # pylint: disable=broad-except
        return ()
    result = []
    for i in range(n):
        info = _monitor_info(display, i)
        if info is not None:
            result.append(info)
    return tuple(result)


def primary_monitor() -> MonitorInfo | None:
    """Returns the user's primary monitor, or None.

    Returns None when no monitor is flagged primary (some compositor
    configurations don't designate one) -- callers wanting "any
    monitor" should fall back to `monitors()[0]`.
    """

    display = _get_display()
    if display is None:
        return None
    try:
        primary = display.get_primary_monitor()
    except Exception:  # pylint: disable=broad-except
        return None
    if primary is None:
        return None
    # Find its index in the enumeration so the returned MonitorInfo
    # has the right `index` for use with other queries.
    try:
        n = display.get_n_monitors()
    except Exception:  # pylint: disable=broad-except
        return None
    for i in range(n):
        if display.get_monitor(i) == primary:
            return _monitor_info(display, i)
    return None


def monitor_at_point(x: int, y: int) -> MonitorInfo | None:
    """Returns the monitor containing virtual-screen coords (x, y).

    None when the coords fall outside any connected monitor (gaps
    in non-rectangular multi-monitor layouts are real and common).
    """

    display = _get_display()
    if display is None:
        return None
    try:
        monitor = display.get_monitor_at_point(x, y)
    except Exception:  # pylint: disable=broad-except
        return None
    if monitor is None:
        return None
    try:
        n = display.get_n_monitors()
    except Exception:  # pylint: disable=broad-except
        return None
    for i in range(n):
        if display.get_monitor(i) == monitor:
            return _monitor_info(display, i)
    return None


def virtual_screen_rect() -> Tuple[int, int, int, int] | None:
    """Returns (x, y, width, height) of the union of all monitors.

    This is the "canvas" coordinate space mouse and screen coords
    live in. Useful for clamping overlay positions to known-visible
    regions, or for full-screen capture extent. None on error.
    """

    mons = monitors()
    if not mons:
        return None
    x0 = min(m.rect[0] for m in mons)
    y0 = min(m.rect[1] for m in mons)
    x1 = max(m.rect[0] + m.rect[2] for m in mons)
    y1 = max(m.rect[1] + m.rect[3] for m in mons)
    return (x0, y0, x1 - x0, y1 - y0)


def _get_display():
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # pylint: disable=import-outside-toplevel
        return Gdk.Display.get_default()
    except Exception:  # pylint: disable=broad-except
        return None


def _monitor_info(display, index: int) -> MonitorInfo | None:
    try:
        monitor = display.get_monitor(index)
        if monitor is None:
            return None
        geom = monitor.get_geometry()
        rect = (int(geom.x), int(geom.y), int(geom.width), int(geom.height))
        scale = int(monitor.get_scale_factor() or 1)
        # refresh_rate returns millihertz per the Gdk docs; divide to Hz.
        try:
            refresh = float(monitor.get_refresh_rate()) / 1000.0
        except Exception:  # pylint: disable=broad-except
            refresh = 0.0
        model = monitor.get_model() or ""
        try:
            primary = bool(monitor.is_primary())
        except Exception:  # pylint: disable=broad-except
            primary = False
        return MonitorInfo(
            index=index, rect=rect, scale_factor=scale,
            refresh_hz=refresh, model=model, is_primary=primary,
        )
    except Exception:  # pylint: disable=broad-except
        return None
