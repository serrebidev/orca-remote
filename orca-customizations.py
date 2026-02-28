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
    if text:
        transport.send(type="speak", sequence=text)
    return old_speak(self, text, acss, **kw)

def my_speak_character(self, character, acss=None):
    transport.send(type="speak", sequence=[character])
    return old_speakCharacter(self, character, acss)

def my_stop(self):
    transport.send(type="cancel")
    return old_stop(self)

SpeechServer._speak = my_speak
SpeechServer.speak_character = my_speak_character
SpeechServer.stop = my_stop

# ---- Key Event Interception for Remote Control ----

try:
    _original_process_key = input_event.KeyboardEvent.process

    def _patched_process_key(event_self):
        """Intercept key events when controlling remote machine.

        When in remote control mode, forward keys to NVDA instead of
        processing them locally -- except for the toggle gesture
        (Orca key + Alt + Tab) which always stays local.
        """
        if not controller.controlling_remote:
            return _original_process_key(event_self)

        # Always allow the toggle gesture through locally:
        # Orca modifier (Insert or CapsLock) + Alt + Tab
        event_string = event_self.event_string if hasattr(event_self, 'event_string') else ""
        modifiers = event_self.modifiers if hasattr(event_self, 'modifiers') else 0

        # Check for the toggle combo - let it pass through to Orca
        is_alt = bool(modifiers & (1 << 3))  # Mod1Mask = Alt
        is_tab = event_string == "Tab" or event_string == "ISO_Left_Tab"

        # Check if Orca modifier is active
        orca_mod_active = False
        try:
            from orca import orca_modifier_manager
            mgr = orca_modifier_manager.get_manager()
            orca_mod_active = mgr.is_orca_modifier_active() if hasattr(mgr, 'is_orca_modifier_active') else False
        except Exception:
            pass

        if orca_mod_active and is_alt and is_tab:
            # Toggle gesture - process locally
            return _original_process_key(event_self)

        # Forward all other keys to NVDA
        pressed = event_self.type == input_event.KeyboardEvent.TYPE_PRINTABLE or \
                  (hasattr(event_self, 'pressed') and event_self.pressed)
        if hasattr(event_self, 'is_pressed_key'):
            pressed = event_self.is_pressed_key()

        mod_names = []
        if modifiers & (1 << 0):  # Shift
            mod_names.append("shift")
        if modifiers & (1 << 2):  # Control
            mod_names.append("control")
        if modifiers & (1 << 3):  # Alt
            mod_names.append("alt")

        controller.send_key(
            key_name=event_string,
            pressed=pressed,
            modifiers=mod_names if mod_names else None
        )
        # Consume the event - don't process locally
        return True

    input_event.KeyboardEvent.process = _patched_process_key
except Exception:
    traceback.print_exc()
    print("Orca Remote: Could not patch key event processing. "
          "Remote control of NVDA will not work.")

# ---- Gesture Handlers ----

def _toggle_remote_control(script=None, inputEvent=None):
    """Toggle between controlling local Orca and remote NVDA.
    Gesture: Orca+Alt+Tab (mirrors NVDA Remote's F11)"""
    controller.toggle_control()
    return True

def _show_connect_dialog(script=None, inputEvent=None):
    """Show the connect dialog to connect to a remote NVDA machine.
    Gesture: Orca+Alt+PageUp (mirrors NVDA Remote's Alt+NVDA+PageUp)"""
    def _do_connect(result):
        if result is None:
            _speak_message("Connection cancelled")
            return
        host = result["host"]
        port = result["port"]
        key = result["key"]
        conn_type = result["connection_type"]
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
    controller.disconnect()
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
      Orca+Alt+PageDown  = Disconnect                   (NVDA: Alt+NVDA+PageDown)
      Ctrl+Shift+Orca+C  = Push clipboard               (NVDA: Ctrl+Shift+NVDA+C)
      Orca+Alt+M         = Toggle mute remote
      Orca+Shift+Delete  = Send Ctrl+Alt+Del to remote
    """
    try:
        # Get modifier mask constants, with fallbacks for different Orca versions
        orca_mod = getattr(keybindings, 'ORCA_MODIFIER_MASK', 1 << 14)
        alt_mod = getattr(keybindings, 'ALT_MODIFIER_MASK', 1 << 3)
        shift_mod = getattr(keybindings, 'SHIFT_MODIFIER_MASK', 1 << 0)
        ctrl_mod = getattr(keybindings, 'CTRL_MODIFIER_MASK', 1 << 2)
        default_mask = getattr(keybindings, 'defaultModifierMask', 0xFF)

        orca_alt = getattr(keybindings, 'ORCA_ALT_MODIFIER_MASK', orca_mod | alt_mod)
        orca_shift = getattr(keybindings, 'ORCA_SHIFT_MODIFIER_MASK', orca_mod | shift_mod)
        ctrl_shift_orca = getattr(keybindings, 'CTRL_SHIFT_ORCA_MODIFIER_MASK',
                                  ctrl_mod | shift_mod | orca_mod)

        gesture_bindings = [
            # (key, modifier_mask, required_modifiers, handler, description)
            ("Tab",       default_mask, orca_alt,       _toggle_remote_control,
             "Toggle local/remote control"),
            ("Page_Up",   default_mask, orca_alt,       _show_connect_dialog,
             "Connect to remote"),
            ("Page_Down", default_mask, orca_alt,       _disconnect,
             "Disconnect from remote"),
            ("c",         default_mask, ctrl_shift_orca, _push_clipboard,
             "Push clipboard to remote"),
            ("m",         default_mask, orca_alt,       _toggle_mute,
             "Toggle mute remote"),
            ("Delete",    default_mask, orca_shift,     _send_ctrl_alt_del,
             "Send Ctrl+Alt+Del to remote"),
        ]

        # Get the default script to register bindings
        default_script = None
        try:
            script_manager = orca.orca.getScriptManager()
            default_script = script_manager.getDefaultScript()
        except AttributeError:
            sm = getattr(orca.orca, '_scriptManager', None)
            if sm and hasattr(sm, 'getDefaultScript'):
                default_script = sm.getDefaultScript()

        if not default_script or not hasattr(default_script, 'keyBindings'):
            print("Orca Remote: Could not get default script for keybinding registration")
            return

        for key, mask, mods, handler, desc in gesture_bindings:
            binding = keybindings.KeyBinding(key, mask, mods, handler, 1)
            default_script.keyBindings.add(binding)

        if hasattr(default_script.keyBindings, 'setup'):
            default_script.keyBindings.setup()

        print("Orca Remote: Gestures registered:")
        for key, mask, mods, handler, desc in gesture_bindings:
            print("  %s" % desc)

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
