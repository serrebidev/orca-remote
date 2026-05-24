"""Desktop notification façade (libnotify).

Speech and braille aren't the right channel for every extension
event. Examples where a desktop notification is the better fit:

  - "Tesseract isn't installed" first-run error for orca-ocr
  - "Remote session disconnected: certificate mismatch" for
    orca-remote (the user may not be at the speech-output device)
  - Sighted-dev debug mirrors during extension development
  - Persistent error states the user should see on the desktop
    even after Orca's speech moves on

This is a thin wrapper over Notify.Notification (gi-libnotify).
Extensions can use libnotify directly; this just gives them a
consistent app-name string ("Orca extension: <name>"), sane
defaults (LOW urgency, 5-second timeout), and a helper for the
common "replace the previous notification rather than stacking"
pattern.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Urgency(Enum):
    """Notification urgency hint to the desktop environment."""

    LOW = 0
    NORMAL = 1
    CRITICAL = 2  # CRITICAL notifications typically persist until dismissed.


_INITIALIZED: dict[str, bool] = {}


def notify(
    extension_name: str,
    summary: str,
    body: str = "",
    *,
    urgency: Urgency = Urgency.LOW,
    timeout_ms: int = 5000,
    icon: str = "",
    replaces_id: int | None = None,
) -> int | None:
    """Show a desktop notification. Returns the notification id, or None.

    `extension_name` becomes the app-name displayed by the desktop
    (e.g. "Orca extension: ocr"). `summary` is the bold headline;
    `body` is the longer text (HTML markup tolerated by most
    notification daemons).

    `replaces_id` is the integer returned from a previous `notify`
    call; passing it causes the new notification to replace the
    old one in-place rather than stacking a second toast (useful
    for progress updates).

    Returns None if libnotify isn't available or the show call
    failed; otherwise returns the notification id that can later
    be passed as `replaces_id` to update this notification.
    """

    app_name = f"Orca extension: {extension_name}"
    notif = _build(app_name, summary, body, urgency, timeout_ms, icon, replaces_id)
    if notif is None:
        return None
    try:
        notif.show()
        # libnotify exposes the id via get_id() after show().
        return int(notif.get_id() or 0) or None
    except Exception:  # pylint: disable=broad-except
        return None


def close(extension_name: str, notification_id: int) -> bool:
    """Close a notification we previously showed. Best-effort.

    Returns True on success, False if libnotify isn't available or
    the close call raised (the notification may have already been
    dismissed or replaced by the user).
    """

    if not _ensure_init(f"Orca extension: {extension_name}"):
        return False
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify  # pylint: disable=import-outside-toplevel
        # libnotify exposes a "close by id" via constructing an
        # empty Notification with the id set, then calling close().
        notif = Notify.Notification.new("", "", "")
        # The id field is private in some GIR generations; defensive
        # setattr so we don't fail on missing attribute.
        try:
            notif.set_property("id", int(notification_id))
        except Exception:  # pylint: disable=broad-except
            pass
        notif.close()
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def is_available() -> bool:
    """Returns True if libnotify is importable on this system."""

    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify  # pylint: disable=import-outside-toplevel,unused-import  # noqa: F401
        return True
    except Exception:  # pylint: disable=broad-except
        return False


def _build(
    app_name: str, summary: str, body: str,
    urgency: Urgency, timeout_ms: int, icon: str,
    replaces_id: int | None,
) -> Any:
    if not _ensure_init(app_name):
        return None
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify  # pylint: disable=import-outside-toplevel
        notif = Notify.Notification.new(summary, body, icon)
        notif.set_urgency(urgency.value)
        notif.set_timeout(int(timeout_ms))
        if replaces_id is not None:
            try:
                notif.set_property("id", int(replaces_id))
            except Exception:  # pylint: disable=broad-except
                pass
        return notif
    except Exception:  # pylint: disable=broad-except
        return None


def _ensure_init(app_name: str) -> bool:
    # Notify.init() must be called once per app-name. We cache the
    # init status per app-name string to allow several extensions
    # in the same Orca process to use different names without
    # interfering.
    if _INITIALIZED.get(app_name):
        return True
    try:
        import gi  # pylint: disable=import-outside-toplevel
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify  # pylint: disable=import-outside-toplevel
        if not Notify.init(app_name):
            return False
        _INITIALIZED[app_name] = True
        return True
    except Exception:  # pylint: disable=broad-except
        return False
