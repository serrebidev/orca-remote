"""GTK Connect Dialog for Orca Remote.

Provides an accessible GTK dialog for entering connection details.
Mirrors the NVDA Remote connect dialog with all the same fields and options,
plus a server-type sub-option (host locally vs. use a remote relay server).
"""

import random
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib


class ConnectDialog:
    """An accessible GTK dialog for entering Orca Remote connection details.

    Connection mode (top radio group):
      - Allow this machine to be controlled (slave)
      - Control the remote machine (master)

    Server type (second radio group, dynamic):
      - Host locally  — Orca starts a built-in relay server on this machine.
                        Only port and connection code are needed.
      - Use remote server — Connect to an existing NVDA Remote relay server.
                        Host address, port, and connection code are needed.

    Both modes share the same server-type sub-radio; "host locally" means
    the server lives on localhost (port + key only), "use remote server"
    means an external relay (host + port + key).

    Result dict keys:
      connection_type : "slave" | "master"
      server_mode     : "local" | "remote"
      host            : str  (empty string when server_mode == "local")
      port            : int
      key             : str
    """

    def __init__(self, default_host="", default_port="6837", default_key=""):
        self.result = None
        self.default_host = default_host
        self.default_port = default_port
        self.default_key = default_key

    def run(self):
        """Show the dialog and return connection params or None if cancelled."""
        GLib.idle_add(self._show_dialog)
        return self.result

    def run_threadsafe(self, callback):
        """Show dialog from a non-GTK thread. Calls callback(result) when done."""
        def _show():
            self._show_dialog()
            callback(self.result)
            return False
        GLib.idle_add(_show)

    def _generate_key(self, button):
        """Generate a random 7-digit connection code (same format as NVDA Remote)."""
        key = str(random.randint(1000000, 9999999))
        self.key_entry.set_text(key)

    def _on_server_type_changed(self, button):
        """Update host field visibility when server-type radio changes."""
        self._update_field_visibility()

    def _update_field_visibility(self):
        """Show or hide the host address row based on the server-type selection."""
        is_local = self.radio_local.get_active()
        self.host_label.set_visible(not is_local)
        self.host_entry.set_visible(not is_local)

    def _show_dialog(self):
        dialog = Gtk.Dialog(
            title="Orca Remote - Connect",
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_CONNECT, Gtk.ResponseType.OK,
        )
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_border_width(10)

        content = dialog.get_content_area()
        content.set_spacing(8)

        # ── Connection mode ───────────────────────────────────────────────
        type_frame = Gtk.Frame(label="Connection mode")
        type_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        type_box.set_border_width(6)

        self.radio_slave = Gtk.RadioButton.new_with_mnemonic(
            None, "Allow this machine to be _controlled"
        )
        self.radio_slave.get_accessible().set_name(
            "Allow this machine to be controlled"
        )
        self.radio_master = Gtk.RadioButton.new_with_mnemonic_from_widget(
            self.radio_slave, "Control the _remote machine"
        )
        self.radio_master.get_accessible().set_name(
            "Control the remote machine"
        )
        type_box.pack_start(self.radio_slave, False, False, 0)
        type_box.pack_start(self.radio_master, False, False, 0)
        type_frame.add(type_box)
        content.pack_start(type_frame, False, False, 0)

        # ── Server type ───────────────────────────────────────────────────
        server_frame = Gtk.Frame(label="Server")
        server_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        server_box.set_border_width(6)

        self.radio_local = Gtk.RadioButton.new_with_mnemonic(
            None, "Host _locally (start a server on this machine)"
        )
        self.radio_local.get_accessible().set_name(
            "Host locally, start a server on this machine"
        )
        self.radio_remote_server = Gtk.RadioButton.new_with_mnemonic_from_widget(
            self.radio_local, "Use remote relay _server"
        )
        self.radio_remote_server.get_accessible().set_name(
            "Use remote relay server"
        )
        # Default: use a remote relay server
        self.radio_remote_server.set_active(True)

        server_box.pack_start(self.radio_local, False, False, 0)
        server_box.pack_start(self.radio_remote_server, False, False, 0)
        server_frame.add(server_box)
        content.pack_start(server_frame, False, False, 0)

        # ── Host address (hidden when "host locally" is selected) ─────────
        self.host_label = Gtk.Label(label="Server _address:")
        self.host_label.set_use_underline(True)
        self.host_label.set_halign(Gtk.Align.START)
        self.host_label.get_accessible().set_name("Server address")

        self.host_entry = Gtk.Entry()
        self.host_entry.set_text(self.default_host)
        self.host_entry.set_activates_default(True)
        self.host_entry.get_accessible().set_name("Server address")
        self.host_label.set_mnemonic_widget(self.host_entry)

        content.pack_start(self.host_label, False, False, 0)
        content.pack_start(self.host_entry, False, False, 0)

        # ── Port ──────────────────────────────────────────────────────────
        port_label = Gtk.Label(label="_Port:")
        port_label.set_use_underline(True)
        port_label.set_halign(Gtk.Align.START)
        port_label.get_accessible().set_name("Port")

        self.port_entry = Gtk.Entry()
        self.port_entry.set_text(self.default_port)
        self.port_entry.set_activates_default(True)
        self.port_entry.get_accessible().set_name("Port")
        port_label.set_mnemonic_widget(self.port_entry)

        content.pack_start(port_label, False, False, 0)
        content.pack_start(self.port_entry, False, False, 0)

        # ── Connection code ───────────────────────────────────────────────
        key_label = Gtk.Label(label="Connection _code:")
        key_label.set_use_underline(True)
        key_label.set_halign(Gtk.Align.START)
        key_label.get_accessible().set_name("Connection code")
        content.pack_start(key_label, False, False, 0)

        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.key_entry = Gtk.Entry()
        self.key_entry.set_text(self.default_key)
        self.key_entry.set_activates_default(True)
        self.key_entry.set_hexpand(True)
        self.key_entry.get_accessible().set_name("Connection code")
        key_label.set_mnemonic_widget(self.key_entry)
        key_box.pack_start(self.key_entry, True, True, 0)

        generate_button = Gtk.Button(label="_Generate code")
        generate_button.set_use_underline(True)
        generate_button.get_accessible().set_name("Generate connection code")
        generate_button.connect("clicked", self._generate_key)
        key_box.pack_start(generate_button, False, False, 0)
        content.pack_start(key_box, False, False, 0)

        # Connect server-type signal after all widgets exist
        self.radio_local.connect("toggled", self._on_server_type_changed)

        dialog.show_all()
        # Apply initial visibility (show_all shows everything first)
        self._update_field_visibility()

        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            is_local = self.radio_local.get_active()
            is_master = self.radio_master.get_active()
            host = "" if is_local else self.host_entry.get_text().strip()
            port_text = self.port_entry.get_text().strip()
            key = self.key_entry.get_text().strip()
            conn_type = "master" if is_master else "slave"
            server_mode = "local" if is_local else "remote"

            valid = bool(key) and bool(port_text)
            if not is_local:
                valid = valid and bool(host)

            if valid:
                try:
                    port = int(port_text)
                    self.result = {
                        "host": host,
                        "port": port,
                        "key": key,
                        "connection_type": conn_type,
                        "server_mode": server_mode,
                    }
                except ValueError:
                    pass

        dialog.destroy()
        return self.result
