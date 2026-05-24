"""orca-ext-utils -- shared utilities for Orca user extensions.

A small library covering capabilities the upstream Orca controller
deliberately won't ship (per scope discipline: "Orca shouldn't carry
utilities it doesn't need itself"). Extensions that need them can
either install this as a package or vendor the relevant modules
into their own `.orca-ext` archive.

Each module is standalone -- import only the ones you need:

  screen_rect         -- screen-coordinate rect for an AT-SPI accessible.
  screen_capture      -- region capture (Gdk / ImageMagick / portal).
  mouse_input         -- mouse click synthesis at screen coords.
  keyboard_grab       -- batch wrapper around Atspi.Device.add_key_grab.
  window_info         -- the currently-focused toplevel window.
  compositor_query    -- multi-monitor geometry, DPI scaling, refresh.
  text_to_braille     -- text -> cell bytes (liblouis optional).
  notification        -- libnotify desktop-notification facade.
  process_supervisor  -- subprocess + timeout + GLib-friendly async.
  key_combo_helpers   -- keysym / modifier parsing and formatting.
  extension_settings  -- per-extension key/value store (GSettings or JSON).
  _backend            -- X11 vs Wayland session detection.

Per-backend coverage:

  X11:     all modules work via AT-SPI / XTest / Gdk.X11 paths.
  Wayland: screen_rect, window_info, and the synchronous
           screen_capture backends return None / no-op. Wayland-
           native paths (xdg-desktop-portal, libei) are used where
           implemented. Module docstrings document per-module
           Wayland coverage.

See docs/backends.md for the per-feature support matrix.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "screen_rect",
    "screen_capture",
    "mouse_input",
    "keyboard_grab",
    "window_info",
    "compositor_query",
    "text_to_braille",
    "notification",
    "process_supervisor",
    "key_combo_helpers",
    "extension_settings",
]
