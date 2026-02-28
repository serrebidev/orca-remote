"""GTK Connect Dialog for Orca Remote.

Provides an accessible GTK dialog for entering connection details
to connect to an NVDA Remote server. Mirrors the NVDA Remote
connect dialog with all the same fields and options.
"""

import random
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib


class ConnectDialog:
    """An accessible GTK dialog for entering NVDA Remote connection details.

    Fields mirror NVDA Remote's DirectConnectDialog:
    - Server address (host)
    - Port (default 6837)
    - Channel key (with generate button)
    - Connection type (control another machine / allow to be controlled)
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
        """Generate a random 7-digit channel key (same as NVDA Remote)."""
        key = str(random.randint(1000000, 9999999))
        self.key_entry.set_text(key)

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

        # Connection type (radio buttons like NVDA Remote)
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

        # Server address
        host_label = Gtk.Label(label="Server _address:")
        host_label.set_use_underline(True)
        host_label.set_halign(Gtk.Align.START)
        host_label.get_accessible().set_name("Server address")
        self.host_entry = Gtk.Entry()
        self.host_entry.set_text(self.default_host)
        self.host_entry.set_activates_default(True)
        self.host_entry.get_accessible().set_name("Server address")
        host_label.set_mnemonic_widget(self.host_entry)
        content.pack_start(host_label, False, False, 0)
        content.pack_start(self.host_entry, False, False, 0)

        # Port
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

        # Channel key with generate button
        key_label = Gtk.Label(label="Channel _key:")
        key_label.set_use_underline(True)
        key_label.set_halign(Gtk.Align.START)
        key_label.get_accessible().set_name("Channel key")
        content.pack_start(key_label, False, False, 0)

        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.key_entry = Gtk.Entry()
        self.key_entry.set_text(self.default_key)
        self.key_entry.set_activates_default(True)
        self.key_entry.set_hexpand(True)
        self.key_entry.get_accessible().set_name("Channel key")
        key_label.set_mnemonic_widget(self.key_entry)
        key_box.pack_start(self.key_entry, True, True, 0)

        generate_button = Gtk.Button(label="_Generate key")
        generate_button.set_use_underline(True)
        generate_button.get_accessible().set_name("Generate key")
        generate_button.connect("clicked", self._generate_key)
        key_box.pack_start(generate_button, False, False, 0)
        content.pack_start(key_box, False, False, 0)

        dialog.show_all()
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            host = self.host_entry.get_text().strip()
            port = self.port_entry.get_text().strip()
            key = self.key_entry.get_text().strip()
            if self.radio_master.get_active():
                conn_type = "master"
            else:
                conn_type = "slave"
            if host and port and key:
                self.result = {
                    "host": host,
                    "port": int(port),
                    "key": key,
                    "connection_type": conn_type,
                }
        dialog.destroy()
        return self.result
