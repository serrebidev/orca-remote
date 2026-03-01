import sys
import os
import traceback
import threading

# Orca Remote Settings
YOUR_ORCA_SCRIPTS_FOLDER = os.path.expanduser("~/.local/share/orca/orca-scripts")
YOUR_NVDAREMOTE_SERVER_ADDRESS = "host"
YOUR_NVDAREMOTE_SERVER_PORT = 6837
YOUR_NVDAREMOTE_KEY = "key"

# Make the NVDA Remote modules importable
sys.path.insert(0, YOUR_ORCA_SCRIPTS_FOLDER)

from orca.speechdispatcherfactory import SpeechServer
import orca.orca
import orca.keybindings as keybindings
import orca.input_event as input_event
from serializer import JSONSerializer
from transport import RelayTransport
from remote_controller import RemoteController

_DBG_LOG = os.path.expanduser("~/.local/share/orca/orca-remote-debug.log")

def _dbg(msg):
    try:
        with open(_DBG_LOG, "a") as f:
            import time
            f.write("[%.3f] %s\n" % (time.time(), msg))
    except Exception:
        pass

# ---- X11 keyval name → Windows VK code ----
# Maps Gdk keyval_name strings to (vk_code, scan_code, extended).
# This is the format NVDA Remote's protocol expects.
_X11_TO_VK = {}
# Letters a-z (VK 0x41-0x5A, scan codes for US QWERTY)
for _ch, _sc in zip("abcdefghijklmnopqrstuvwxyz",
                     [0x1E,0x30,0x2E,0x20,0x12,0x21,0x22,0x23,0x17,
                      0x24,0x25,0x26,0x32,0x31,0x18,0x19,0x10,0x13,
                      0x1F,0x14,0x16,0x2F,0x11,0x2D,0x15,0x2C]):
    _vk = ord(_ch.upper())
    _X11_TO_VK[_ch] = (_vk, _sc, False)   # lowercase keyval
    _X11_TO_VK[_ch.upper()] = (_vk, _sc, False)  # uppercase (Shift held)
# Digits 0-9 (VK 0x30-0x39)
for _ch, _sc in zip("1234567890", [0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B]):
    _X11_TO_VK[_ch] = (ord(_ch), _sc, False)
_X11_TO_VK['0'] = (0x30, 0x0B, False)
# Function keys
for _i, _sc in enumerate([0x3B,0x3C,0x3D,0x3E,0x3F,0x40,0x41,0x42,0x43,0x44,0x57,0x58], 1):
    _X11_TO_VK['F%d' % _i] = (0x6F + _i, _sc, False)
# Special keys
_X11_TO_VK.update({
    'Return':         (0x0D, 0x1C, False),
    'BackSpace':      (0x08, 0x0E, False),
    'Tab':            (0x09, 0x0F, False),
    'ISO_Left_Tab':   (0x09, 0x0F, False),   # Shift+Tab reports this keyval
    'Escape':         (0x1B, 0x01, False),
    'space':          (0x20, 0x39, False),
    'Delete':         (0x2E, 0x53, True),
    'Insert':         (0x2D, 0x52, True),
    'Home':           (0x24, 0x47, True),
    'End':            (0x23, 0x4F, True),
    'Prior':          (0x21, 0x49, True),   # Page Up
    'Next':           (0x22, 0x51, True),   # Page Down
    'Left':           (0x25, 0x4B, True),
    'Up':             (0x26, 0x48, True),
    'Right':          (0x27, 0x4D, True),
    'Down':           (0x28, 0x50, True),
    'Print':          (0x2C, 0x37, True),
    'Scroll_Lock':    (0x91, 0x46, False),
    'Pause':          (0x13, 0x45, False),
    'Caps_Lock':      (0x14, 0x3A, False),
    'Num_Lock':       (0x90, 0x45, True),
    # Modifiers
    'Shift_L':        (0x10, 0x2A, False),
    'Shift_R':        (0x10, 0x36, False),
    'Control_L':      (0x11, 0x1D, False),
    'Control_R':      (0x11, 0x1D, True),
    'Alt_L':          (0x12, 0x38, False),
    'Alt_R':          (0x12, 0x38, True),
    'Super_L':        (0x5B, 0x5B, True),
    'Super_R':        (0x5C, 0x5C, True),
    'Menu':           (0x5D, 0x5D, True),
    # Punctuation (US QWERTY)
    'minus':          (0xBD, 0x0C, False),
    'equal':          (0xBB, 0x0D, False),
    'bracketleft':    (0xDB, 0x1A, False),
    'bracketright':   (0xDD, 0x1B, False),
    'backslash':      (0xDC, 0x2B, False),
    'semicolon':      (0xBA, 0x27, False),
    'apostrophe':     (0xDE, 0x28, False),
    'grave':          (0xC0, 0x29, False),
    'comma':          (0xBC, 0x33, False),
    'period':         (0xBE, 0x34, False),
    'slash':          (0xBF, 0x35, False),
    # Numpad
    'KP_0':           (0x60, 0x52, False),
    'KP_1':           (0x61, 0x4F, False),
    'KP_2':           (0x62, 0x50, False),
    'KP_3':           (0x63, 0x51, False),
    'KP_4':           (0x64, 0x4B, False),
    'KP_5':           (0x65, 0x4C, False),
    'KP_6':           (0x66, 0x4D, False),
    'KP_7':           (0x67, 0x47, False),
    'KP_8':           (0x68, 0x48, False),
    'KP_9':           (0x69, 0x49, False),
    'KP_Decimal':     (0x6E, 0x53, False),
    'KP_Multiply':    (0x6A, 0x37, False),
    'KP_Add':         (0x6B, 0x4E, False),
    'KP_Subtract':    (0x6D, 0x4A, False),
    'KP_Divide':      (0x6F, 0x35, True),
    'KP_Enter':       (0x0D, 0x1C, True),
    'KP_Insert':      (0x60, 0x52, False),
    'KP_Delete':      (0x6E, 0x53, False),
    'KP_Home':        (0x67, 0x47, False),
    'KP_End':         (0x61, 0x4F, False),
    'KP_Prior':       (0x69, 0x49, False),
    'KP_Next':        (0x63, 0x51, False),
    'KP_Up':          (0x68, 0x48, False),
    'KP_Down':        (0x62, 0x50, False),
    'KP_Left':        (0x64, 0x4B, False),
    'KP_Right':       (0x66, 0x4D, False),
    'KP_Begin':       (0x65, 0x4C, False),
})

def _key_to_vk(keyval_name):
    """Return (vk_code, scan_code, extended) for a Gdk keyval_name, or None."""
    return _X11_TO_VK.get(keyval_name)

# ---- Transport Setup ----

mySerializer = JSONSerializer()
transport = RelayTransport(
    mySerializer,
    (YOUR_NVDAREMOTE_SERVER_ADDRESS, YOUR_NVDAREMOTE_SERVER_PORT),
    channel=YOUR_NVDAREMOTE_KEY,
    connection_type="slave"
)

def _speak_message(text):
    """Speak a message through Orca's speech system."""
    try:
        import orca.speech as speech
        speech.speak(text)
    except Exception:
        print("Orca Remote: %s" % text)

# ---- Remote Controller ----

controller = RemoteController(transport, speech_callback=_speak_message)


# ---- Connection Thread ----
# Only auto-connect if real connection details were provided (not the
# install-time sentinel values). Otherwise the user connects via the
# connect dialog (Orca+Alt+PageUp).

_has_config = (YOUR_NVDAREMOTE_SERVER_ADDRESS != "host"
               and YOUR_NVDAREMOTE_KEY != "key")

if _has_config:
    def try_run_thread():
        try:
            transport.run()
        except Exception:
            print("Error in thread")
            traceback.print_exc()

    t = threading.Thread(target=try_run_thread)
    t.daemon = True
    t.start()

# ---- Patch Orca Speech Functions (Slave: forward local speech to remote) ----

old_speak = SpeechServer._speak
old_speakCharacter = SpeechServer.speak_character
old_stop = SpeechServer.stop

def my_speak(self, text, acss, **kw):
    # Only forward Orca's own speech when acting as slave (being controlled).
    # In master mode we receive NVDA's speech; forwarding it back causes a loop.
    if text and transport.connected and transport.connection_type == "slave":
        transport.send(type="speak", sequence=text)
    return old_speak(self, text, acss, **kw)

def my_speak_character(self, character, acss=None):
    if transport.connected and transport.connection_type == "slave":
        transport.send(type="speak", sequence=[character])
    return old_speakCharacter(self, character, acss)

def my_stop(self):
    # Only forward cancel when acting as slave; in master mode this echoes
    # our cancel back to NVDA and cuts off its speech.
    if transport.connected and transport.connection_type == "slave":
        transport.send(type="cancel")
    return old_stop(self)

SpeechServer._speak = my_speak
SpeechServer.speak_character = my_speak_character
SpeechServer.stop = my_stop

def _get_speech_server():
    """Return the active Orca SpeechServer instance, or None."""
    try:
        import orca.speech as speech
        return speech._state.server
    except Exception:
        return None

def _local_only_stop():
    """Stop local Orca speech directly via the speech server (no relay forwarding).

    speech.stop() does not exist in this version of Orca; we must call old_stop
    on the SpeechServer instance directly.
    """
    server = _get_speech_server()
    if server is not None:
        old_stop(server)

def _local_only_speak(text):
    """Speak text locally via the speech server (no relay forwarding).

    Bypasses my_speak entirely so the text is not echoed back to the relay.
    """
    server = _get_speech_server()
    if server is not None:
        old_speak(server, text, None)
    else:
        # Fallback if server not yet initialised
        try:
            import orca.speech as speech
            speech.speak(text)
        except Exception:
            pass

controller.local_machine._local_stop = _local_only_stop
controller.local_machine._local_speak = _local_only_speak

# ---- Key Event Interception for Remote Control ----

# Tracks (vk_code, scan_code, extended) for keys currently forwarded as
# "pressed" to the remote.  Cleared on return to LOCAL so we can send
# synthetic key-up events and un-stick them on the remote machine.
_forwarded_keys_down = set()

def _release_all_forwarded_keys():
    """Send key-up for every key still marked as down on the remote."""
    for vk_code, scan_code, extended in list(_forwarded_keys_down):
        controller.transport.send(
            type="key",
            vk_code=vk_code,
            scan_code=scan_code,
            extended=extended,
            pressed=False,
        )
    _forwarded_keys_down.clear()

try:
    _original_process_key = input_event.KeyboardEvent.process

    def _patched_process_key(event_self):
        """Intercept key events when controlling remote machine.

        When in remote control mode, forward keys to NVDA instead of
        processing them locally -- except for the toggle gesture
        (Orca key + Alt + Tab) which always stays local.
        """
        # Pass through events that local_machine.send_key injected via AT-SPI.
        # Orca's own AT-SPI listener sees these events too; without this check
        # it would consume them and apps would never receive the keystroke.
        try:
            from local_machine import is_injected_event
            if is_injected_event(event_self.id, event_self.is_pressed_key()):
                _dbg("passthrough injected keysym=%d" % event_self.id)
                return False
        except Exception:
            pass

        if not controller.controlling_remote:
            return _original_process_key(event_self)

        _dbg("_patched_process_key: in REMOTE mode")
        # Always allow the toggle gesture through locally:
        # Orca modifier (Insert or CapsLock) + Alt + Tab
        key_name = event_self.keyval_name if hasattr(event_self, 'keyval_name') else ""
        modifiers = event_self.modifiers if hasattr(event_self, 'modifiers') else 0

        # Always allow the toggle gesture (Orca+Alt+Tab) through to local Orca
        is_orca = bool(modifiers & keybindings.ORCA_MODIFIER_MASK)
        is_alt = bool(modifiers & keybindings.ALT_MODIFIER_MASK)
        is_tab = key_name == "Tab" or key_name == "ISO_Left_Tab"

        if is_orca and is_alt and is_tab:
            # On the key-press event, release any keys still held down on
            # the remote (Insert, Alt, etc.) before we switch to LOCAL mode.
            # On key-release we just let it through without further action.
            if event_self.is_pressed_key():
                _release_all_forwarded_keys()
            return _original_process_key(event_self)

        # Forward all other keys to NVDA in NVDA Remote VK-code format
        pressed = event_self.is_pressed_key()
        vk_info = _key_to_vk(key_name)
        _dbg("REMOTE key: name=%r pressed=%s vk=%s connected=%s" % (
            key_name, pressed, vk_info, controller.transport.connected))
        if vk_info is not None:
            vk_code, scan_code, extended = vk_info
            controller.transport.send(
                type="key",
                vk_code=vk_code,
                scan_code=scan_code,
                extended=extended,
                pressed=pressed,
            )
            if pressed:
                _forwarded_keys_down.add((vk_code, scan_code, extended))
            else:
                _forwarded_keys_down.discard((vk_code, scan_code, extended))
        # Consume the event - don't process locally
        return True

    input_event.KeyboardEvent.process = _patched_process_key
except Exception:
    traceback.print_exc()
    print("Orca Remote: Could not patch key event processing. "
          "Remote control of NVDA will not work.")

# ---- Gesture Handlers ----

def _update_keyboard_grab():
    """Grab the full keyboard in remote mode so keystrokes don't also fire locally."""
    try:
        from orca import input_event_manager
        iem = input_event_manager.get_manager()
        if controller.controlling_remote:
            iem.grab_keyboard("orca_remote")
        else:
            iem.ungrab_keyboard("orca_remote")
        _dbg("keyboard grab updated: remote=%s" % controller.controlling_remote)
    except Exception:
        _dbg("grab_keyboard failed: %s" % traceback.format_exc())

def _toggle_remote_control(script=None, inputEvent=None):
    """Toggle between controlling local Orca and remote NVDA.
    Gesture: Orca+Alt+Tab (mirrors NVDA Remote's F11)"""
    controller.toggle_control()
    _dbg("toggle_remote_control: now controlling_remote=%s transport.connected=%s" % (
        controller.controlling_remote, controller.transport.connected))
    _update_keyboard_grab()
    return True

_local_server = None


def _start_local_server(port):
    """Start the built-in relay server on *port*, stopping any existing one."""
    global _local_server
    try:
        from local_server import LocalRelayServer
        if _local_server is not None:
            _local_server.stop()
        _local_server = LocalRelayServer(port=port)
        _local_server.start()
        _dbg("Local relay server started on port %d" % port)
    except Exception:
        traceback.print_exc()
        _speak_message("Could not start local server")


def _stop_local_server():
    """Stop the built-in relay server if running."""
    global _local_server
    if _local_server is not None:
        _local_server.stop()
        _local_server = None


def _show_connect_dialog(script=None, inputEvent=None):
    """Show the connect dialog to connect to a remote NVDA machine.
    Gesture: Orca+Alt+PageUp (mirrors NVDA Remote's Alt+NVDA+PageUp)"""
    def _do_connect(result):
        if result is None:
            _speak_message("Connection cancelled")
            return
        conn_type = result["connection_type"]
        server_mode = result["server_mode"]
        port = result["port"]
        key = result["key"]

        if server_mode == "local":
            if conn_type == "slave":
                # Start a built-in relay server, then join it as slave
                _start_local_server(port)
                host = "localhost"
            else:
                # Master connecting to a local server already running here
                host = "localhost"
        else:
            host = result["host"]
            _stop_local_server()

        _speak_message("Connecting to %s" % host)
        transport.reconnect(
            address=(host, port),
            channel=key,
            connection_type=conn_type,
        )

    try:
        from connect_dialog import ConnectDialog
        dialog = ConnectDialog(
            default_host=YOUR_NVDAREMOTE_SERVER_ADDRESS,
            default_port=str(YOUR_NVDAREMOTE_SERVER_PORT),
            default_key=YOUR_NVDAREMOTE_KEY,
        )
        dialog.run_threadsafe(_do_connect)
    except Exception:
        traceback.print_exc()
        _speak_message("Could not open connect dialog")
    return True

def _disconnect(script=None, inputEvent=None):
    """Disconnect from the remote session.
    Gesture: Orca+Alt+PageDown (mirrors NVDA Remote's Alt+NVDA+PageDown)"""
    _release_all_forwarded_keys()
    controller.disconnect()
    _update_keyboard_grab()
    _stop_local_server()
    return True

def _push_clipboard(script=None, inputEvent=None):
    """Push local clipboard to remote machine.
    Gesture: Ctrl+Shift+Orca+C (mirrors NVDA Remote's Ctrl+Shift+NVDA+C)"""
    controller.push_clipboard()
    return True

def _toggle_mute(script=None, inputEvent=None):
    """Toggle mute for remote speech and sounds.
    Gesture: Orca+Alt+M (avoids laptop flat review conflict on CapsLock+M)"""
    controller.toggle_mute()
    return True

def _send_ctrl_alt_del(script=None, inputEvent=None):
    """Send Ctrl+Alt+Del to the remote machine.
    Gesture: Orca+Shift+Delete"""
    controller.send_sas()
    _speak_message("Sent Ctrl Alt Delete")
    return True

# ---- Gesture Registration ----

def _register_gestures():
    """Register Orca keybindings for all remote control features.

    Gesture map (mirrors NVDA Remote where possible):
      Orca+Alt+Tab       = Toggle local/remote control  (NVDA: F11)
      Orca+Alt+PageUp    = Connect dialog               (NVDA: Alt+NVDA+PageUp)
      Orca+Alt+C         = Connect dialog (alt binding for keyboards without PageUp)
      Orca+Alt+PageDown  = Disconnect                   (NVDA: Alt+NVDA+PageDown)
      Orca+Alt+D         = Disconnect (alt binding for keyboards without PageDown)
      Ctrl+Shift+Orca+C  = Push clipboard               (NVDA: Ctrl+Shift+NVDA+C)
      Orca+Alt+M         = Toggle mute remote
      Orca+Shift+Delete  = Send Ctrl+Alt+Del to remote
    """
    try:
        from orca import command_manager as cmd_mgr, orca_modifier_manager

        orca_mod = getattr(keybindings, 'ORCA_MODIFIER_MASK', 1 << 14)
        alt_mod = getattr(keybindings, 'ALT_MODIFIER_MASK', 1 << 3)
        shift_mod = getattr(keybindings, 'SHIFT_MODIFIER_MASK', 1 << 0)
        ctrl_mod = getattr(keybindings, 'CTRL_MODIFIER_MASK', 1 << 2)

        orca_alt = getattr(keybindings, 'ORCA_ALT_MODIFIER_MASK', orca_mod | alt_mod)
        orca_shift = getattr(keybindings, 'ORCA_SHIFT_MODIFIER_MASK', orca_mod | shift_mod)
        ctrl_shift_orca = ctrl_mod | shift_mod | orca_mod

        print("Orca Remote: Modifier masks: orca=%s alt=%s shift=%s ctrl=%s"
              % (hex(orca_mod), hex(alt_mod), hex(shift_mod), hex(ctrl_mod)))
        print("Orca Remote: Combined masks: orca_alt=%s orca_shift=%s ctrl_shift_orca=%s"
              % (hex(orca_alt), hex(orca_shift), hex(ctrl_shift_orca)))

        # (name, key, modifiers, handler, description)
        all_bindings = [
            ("orca_remote_toggle",         "Tab",          orca_alt,        _toggle_remote_control,
             "Toggle local/remote control"),
            ("orca_remote_connect",        "Page_Up",      orca_alt,        _show_connect_dialog,
             "Connect to remote (PageUp)"),
            ("orca_remote_connect_kp",     "KP_Page_Up",   orca_alt,        _show_connect_dialog,
             "Connect to remote (Keypad PageUp)"),
            ("orca_remote_disconnect",     "Page_Down",    orca_alt,        _disconnect,
             "Disconnect from remote (PageDown)"),
            ("orca_remote_disconnect_kp",  "KP_Page_Down", orca_alt,        _disconnect,
             "Disconnect from remote (Keypad PageDown)"),
            ("orca_remote_clipboard",      "c",            ctrl_shift_orca, _push_clipboard,
             "Push clipboard to remote"),
            ("orca_remote_mute",           "m",            orca_alt,        _toggle_mute,
             "Toggle mute remote"),
            ("orca_remote_ctrl_alt_del",   "Delete",       orca_shift,      _send_ctrl_alt_del,
             "Send Ctrl+Alt+Del to remote"),
            # Alternative bindings for keyboards without PageUp/PageDown (e.g. Mac laptops)
            ("orca_remote_connect_alt",    "c",            orca_alt,        _show_connect_dialog,
             "Connect to remote (Alt: Orca+Alt+C)"),
            ("orca_remote_disconnect_alt", "d",            orca_alt,        _disconnect,
             "Disconnect from remote (Alt: Orca+Alt+D)"),
        ]

        mgr = cmd_mgr.get_manager()
        orca_modifiers = orca_modifier_manager.get_manager().get_orca_modifier_keys()

        for name, key, mods, handler, desc in all_bindings:
            try:
                cmd = mgr.register_command(name, handler, desc, key, mods)
                kb = cmd.get_keybinding()
                if kb and not kb.has_grabs():
                    kb.add_grabs(orca_modifiers)
                print("Orca Remote:   Registered: %s (key=%s mods=%s)" % (desc, key, hex(mods)))
            except Exception as e:
                print("Orca Remote:   FAILED to register %s: %s" % (desc, e))

        print("Orca Remote: All gestures registered successfully")

    except Exception:
        traceback.print_exc()
        print("Orca Remote: Could not register gestures. "
              "You may need to configure them in Orca's key bindings dialog.")

# Register gestures after a short delay to ensure Orca is fully initialized
def _delayed_init():
    import time
    time.sleep(2)
    try:
        from gi.repository import GLib
        GLib.idle_add(_register_gestures)
    except Exception:
        _register_gestures()

init_thread = threading.Thread(target=_delayed_init)
init_thread.daemon = True
init_thread.start()

print("Orca Remote loaded")
