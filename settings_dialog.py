"""Custom Gtk settings dialog for Orca Remote.

The Orca preferences framework only ships boolean / range / enum /
color / selection PreferenceControls -- there is no string control.
Rather than pad the framework for one extension, Stage 1 owns its
settings UI directly: a non-blocking Gtk.Dialog with labeled entries
for host, port, channel key, and server fingerprint. The extension
binds Orca+Ctrl+R to open it.

`build_settings_dialog(initial, on_result)` shows the dialog and
returns immediately; `on_result(dict | None)` is invoked from the
GLib main loop when the user closes the dialog. Non-blocking is
critical because the dialog can be opened by a remote master
synthesizing the chord -- a blocking Dialog.run() would suspend the
GLib loop until the local user clicked something.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


# Setting keys -- also the JSON keys persisted by the extension.
SETTING_HOST = "host"
SETTING_PORT = "port"
SETTING_CHANNEL = "channel"
SETTING_FINGERPRINT = "fingerprint"
SETTING_ROLE = "role"
# Internal: not exposed in the dialog. Flipped True by an explicit
# connect command and False by an explicit disconnect, so the
# extension can remember the user's last intent across Orca restarts
# instead of always auto-dialing when settings look valid.
SETTING_AUTO_CONNECT = "auto_connect"

ROLE_CLIENT = "client"  # Receive speech from a remote (we are master).
ROLE_HOST = "host"      # Broadcast our speech (we are slave/host).

_ROLE_LABELS: list[tuple[str, str]] = [
    (ROLE_CLIENT, "Receive speech (control a remote machine)"),
    (ROLE_HOST,   "Broadcast speech (let a remote machine control us)"),
]


DEFAULT_SETTINGS: dict[str, Any] = {
    SETTING_HOST: "nvdaremote.com",
    SETTING_PORT: 6837,
    SETTING_CHANNEL: "",
    SETTING_FINGERPRINT: "",
    SETTING_ROLE: ROLE_CLIENT,
    SETTING_AUTO_CONNECT: True,
}


# Kept as a thin shim so the extension's get_preference_controls can
# stay empty for Stage 1 (manifest does not declare style="dialog").
# When Stage 2 lands a real StringPreferenceControl in the framework,
# this returns the proper list and the manifest gets the [preferences]
# block back.
def build_preference_controls(getter, setter) -> list[Any]:  # noqa: ARG001
    return []


def build_settings_dialog(
    initial: dict[str, Any],
    on_result: Callable[[Optional[dict[str, Any]]], None],
) -> Gtk.Dialog:
    """Show the settings dialog without blocking.

    Returns the Gtk.Dialog immediately. When the user closes the
    dialog, `on_result` is invoked from the GLib main loop with the
    new settings (OK) or None (cancel / window-close). The callback
    is always invoked exactly once, and the dialog is destroyed
    before the callback runs.
    """

    dialog = Gtk.Dialog(
        title="Orca Remote Settings",
        modal=True,
    )
    dialog.add_button("_Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("_Save", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)

    content = dialog.get_content_area()
    content.set_spacing(8)
    content.set_border_width(12)

    grid = Gtk.Grid()
    grid.set_row_spacing(8)
    grid.set_column_spacing(12)
    content.pack_start(grid, True, True, 0)

    host_entry = _add_text_row(
        grid, 0, "_Relay host:", str(initial.get(SETTING_HOST, "")),
        accessible_name="Relay host",
    )
    port_entry = _add_text_row(
        grid, 1, "Relay _port:", str(initial.get(SETTING_PORT, "")),
        accessible_name="Relay port",
    )
    channel_entry = _add_channel_row(
        grid, 2, str(initial.get(SETTING_CHANNEL, "")),
    )
    fingerprint_entry = _add_text_row(
        grid, 3, "Server _fingerprint (SHA-256):",
        str(initial.get(SETTING_FINGERPRINT, "")),
        accessible_name="Server fingerprint SHA-256",
    )
    role_combo = _add_role_row(
        grid, 4, "_Role:", str(initial.get(SETTING_ROLE, ROLE_CLIENT)),
    )

    def _on_response(_dialog: Gtk.Dialog, response: int) -> None:
        # Snapshot values BEFORE destroying the widgets, then destroy,
        # then hand off. This keeps the callback simple (no live widget
        # references survive into application code) and guarantees the
        # dialog is gone if the callback raises.
        if response != Gtk.ResponseType.OK:
            _dialog.destroy()
            on_result(None)
            return

        try:
            port_value = int(port_entry.get_text().strip())
        except ValueError:
            port_value = int(
                initial.get(SETTING_PORT, DEFAULT_SETTINGS[SETTING_PORT])
            )

        role_id = role_combo.get_active_id() or ROLE_CLIENT
        if role_id not in (ROLE_CLIENT, ROLE_HOST):
            role_id = ROLE_CLIENT

        result: dict[str, Any] = {
            SETTING_HOST:
                host_entry.get_text().strip() or DEFAULT_SETTINGS[SETTING_HOST],
            SETTING_PORT: port_value,
            SETTING_CHANNEL: channel_entry.get_text(),
            SETTING_FINGERPRINT: fingerprint_entry.get_text().strip(),
            SETTING_ROLE: role_id,
        }
        _dialog.destroy()
        on_result(result)

    dialog.connect("response", _on_response)
    dialog.show_all()
    return dialog


def _add_text_row(
    grid: Gtk.Grid,
    row: int,
    label_text: str,
    initial_value: str,
    masked: bool = False,
    accessible_name: str = "",
) -> Gtk.Entry:
    """Add a label + Gtk.Entry row to the grid and return the entry."""

    label = Gtk.Label(label=label_text, xalign=0.0)
    label.set_use_underline(True)
    label.set_hexpand(False)
    grid.attach(label, 0, row, 1, 1)

    entry = Gtk.Entry()
    entry.set_text(initial_value)
    entry.set_hexpand(True)
    entry.set_activates_default(True)
    if masked:
        entry.set_visibility(False)
        entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    if accessible_name:
        label.get_accessible().set_name(accessible_name)
        entry.get_accessible().set_name(accessible_name)
    grid.attach(entry, 1, row, 1, 1)

    label.set_mnemonic_widget(entry)
    return entry


def _add_channel_row(
    grid: Gtk.Grid,
    row: int,
    initial_value: str,
) -> Gtk.Entry:
    """Add the channel key row with a generate button."""

    label = Gtk.Label(label="_Channel key:", xalign=0.0)
    label.set_use_underline(True)
    label.set_hexpand(False)
    label.get_accessible().set_name("Channel key")
    grid.attach(label, 0, row, 1, 1)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.set_hexpand(True)

    entry = Gtk.Entry()
    entry.set_text(initial_value)
    entry.set_hexpand(True)
    entry.set_activates_default(True)
    entry.set_visibility(False)
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    entry.get_accessible().set_name("Channel key")
    label.set_mnemonic_widget(entry)
    box.pack_start(entry, True, True, 0)

    button = Gtk.Button.new_with_mnemonic("_Generate")
    button.get_accessible().set_name("Generate channel key")

    def _on_generate(_button: Gtk.Button) -> None:
        entry.set_text(str(random.randint(1000000, 9999999)))
        entry.grab_focus()

    button.connect("clicked", _on_generate)
    box.pack_start(button, False, False, 0)

    grid.attach(box, 1, row, 1, 1)
    return entry


def _add_role_row(
    grid: Gtk.Grid,
    row: int,
    label_text: str,
    initial_value: str,
) -> Gtk.ComboBoxText:
    """Add a label + role ComboBoxText row, return the combo."""

    label = Gtk.Label(label=label_text, xalign=0.0)
    label.set_use_underline(True)
    label.set_hexpand(False)
    label.get_accessible().set_name("Role")
    grid.attach(label, 0, row, 1, 1)

    combo = Gtk.ComboBoxText()
    for role_id, role_label in _ROLE_LABELS:
        combo.append(role_id, role_label)
    if initial_value not in (ROLE_CLIENT, ROLE_HOST):
        initial_value = ROLE_CLIENT
    combo.set_active_id(initial_value)
    combo.set_hexpand(True)
    combo.get_accessible().set_name("Role")
    grid.attach(combo, 1, row, 1, 1)

    label.set_mnemonic_widget(combo)
    return combo
