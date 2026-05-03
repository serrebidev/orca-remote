"""Local machine handler for Orca Remote.

Handles incoming remote commands and executes them on the local
Orca machine - speech, tones, braille, clipboard, keys, etc.
This is the equivalent of NVDA Remote's LocalMachine class.
"""

import os
import shutil
import subprocess
import threading
import time
import uuid
from logging import getLogger
log = getLogger('local_machine')

# ---- AT-SPI injection tracking ----
# When send_key() injects via AT-SPI, it marks the keysym here so that
# _patched_process_key in orca-customizations.py can recognise and pass through
# the event without consuming it (Orca's listener would otherwise swallow it).
_injected = {}          # (keysym: int, pressed: bool) → pending count
_injected_lock = threading.Lock()


def _mark_injected(keysym, pressed):
    """Record that one AT-SPI event with this keysym is about to arrive."""
    token = (int(keysym), bool(pressed))
    with _injected_lock:
        _injected[token] = _injected.get(token, 0) + 1


def is_injected_event(keysym, pressed):
    """Consume one injection marker for (keysym, pressed). Returns True if found.

    Called from orca-customizations._patched_process_key to detect and pass
    through events that we ourselves generated via generateKeyboardEvent.
    """
    token = (int(keysym), bool(pressed))
    with _injected_lock:
        count = _injected.get(token, 0)
        if count > 0:
            if count == 1:
                del _injected[token]
            else:
                _injected[token] = count - 1
            return True
    return False

_DBG_LOG = os.path.expanduser("~/.local/share/orca/orca-remote-debug.log")

def _dbg(msg):
    try:
        with open(_DBG_LOG, "a") as f:
            f.write("[%.3f] LM: %s\n" % (time.time(), msg))
    except Exception:
        pass


# Windows VK code → xdotool key name
# Used when NVDA (or a master Orca) sends key events via vk_code.
_VK_TO_XDOTOOL = {
    0x08: "BackSpace",
    0x09: "Tab",
    0x0D: "Return",
    0x1B: "Escape",
    0x20: "space",
    0x21: "Prior",       # Page Up
    0x22: "Next",        # Page Down
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print",
    0x2D: "Insert",
    0x2E: "Delete",
    # Digits 0-9
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
    0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
    # Letters A-Z (xdotool uses lowercase)
    0x41: "a", 0x42: "b", 0x43: "c", 0x44: "d", 0x45: "e",
    0x46: "f", 0x47: "g", 0x48: "h", 0x49: "i", 0x4A: "j",
    0x4B: "k", 0x4C: "l", 0x4D: "m", 0x4E: "n", 0x4F: "o",
    0x50: "p", 0x51: "q", 0x52: "r", 0x53: "s", 0x54: "t",
    0x55: "u", 0x56: "v", 0x57: "w", 0x58: "x", 0x59: "y", 0x5A: "z",
    # Windows/Super/Menu
    0x5B: "Super_L", 0x5C: "Super_R", 0x5D: "Menu",
    # Numpad (with Num Lock on)
    0x60: "KP_0", 0x61: "KP_1", 0x62: "KP_2", 0x63: "KP_3",
    0x64: "KP_4", 0x65: "KP_5", 0x66: "KP_6", 0x67: "KP_7",
    0x68: "KP_8", 0x69: "KP_9",
    0x6A: "KP_Multiply", 0x6B: "KP_Add", 0x6D: "KP_Subtract",
    0x6E: "KP_Decimal",  0x6F: "KP_Divide",
    # Function keys
    0x70: "F1",  0x71: "F2",  0x72: "F3",  0x73: "F4",
    0x74: "F5",  0x75: "F6",  0x76: "F7",  0x77: "F8",
    0x78: "F9",  0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    # Locks / misc
    0x13: "Pause", 0x14: "Caps_Lock",
    0x90: "Num_Lock", 0x91: "Scroll_Lock",
    # Modifiers — generic (sent by some clients)
    0x10: "Shift_L", 0x11: "Control_L", 0x12: "Alt_L",
    # Modifiers — left/right specific (sent by NVDA Remote)
    0xA0: "Shift_L",   0xA1: "Shift_R",
    0xA2: "Control_L", 0xA3: "Control_R",
    0xA4: "Alt_L",     0xA5: "Alt_R",
    # Punctuation — US QWERTY
    0xBA: "semicolon",   0xBB: "equal",        0xBC: "comma",
    0xBD: "minus",       0xBE: "period",       0xBF: "slash",
    0xC0: "grave",
    0xDB: "bracketleft", 0xDC: "backslash",    0xDD: "bracketright",
    0xDE: "apostrophe",
}

# Extended-key overrides: right-side modifiers and numpad Enter.
# NVDA Remote sets extended=True for these VK codes.
_VK_TO_XDOTOOL_EXT = {
    0x10: "Shift_R",
    0x11: "Control_R",
    0x12: "Alt_R",
    0x0D: "KP_Enter",
}

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
PORTAL_REMOTE_DESKTOP_IFACE = "org.freedesktop.portal.RemoteDesktop"
PORTAL_REQUEST_IFACE = "org.freedesktop.portal.Request"
PORTAL_KEYBOARD = 1

KEYSYM_BY_KEY_NAME = {
    "BackSpace": 0xff08,
    "Tab": 0xff09,
    "ISO_Left_Tab": 0xfe20,
    "Return": 0xff0d,
    "Escape": 0xff1b,
    "space": 0x20,
    "Delete": 0xffff,
    "Insert": 0xff63,
    "Home": 0xff50,
    "End": 0xff57,
    "Prior": 0xff55,
    "Next": 0xff56,
    "Left": 0xff51,
    "Up": 0xff52,
    "Right": 0xff53,
    "Down": 0xff54,
    "Print": 0xff61,
    "Scroll_Lock": 0xff14,
    "Pause": 0xff13,
    "Caps_Lock": 0xffe5,
    "Num_Lock": 0xff7f,
    "Shift_L": 0xffe1,
    "Shift_R": 0xffe2,
    "Control_L": 0xffe3,
    "Control_R": 0xffe4,
    "Alt_L": 0xffe9,
    "Alt_R": 0xffea,
    "Super_L": 0xffeb,
    "Super_R": 0xffec,
    "Menu": 0xff67,
    "minus": ord("-"),
    "equal": ord("="),
    "bracketleft": ord("["),
    "bracketright": ord("]"),
    "backslash": ord("\\"),
    "semicolon": ord(";"),
    "apostrophe": ord("'"),
    "grave": ord("`"),
    "comma": ord(","),
    "period": ord("."),
    "slash": ord("/"),
    "KP_Enter": 0xff8d,
    "KP_Home": 0xff95,
    "KP_Left": 0xff96,
    "KP_Up": 0xff97,
    "KP_Right": 0xff98,
    "KP_Down": 0xff99,
    "KP_Prior": 0xff9a,
    "KP_Next": 0xff9b,
    "KP_End": 0xff9c,
    "KP_Begin": 0xff9d,
    "KP_Insert": 0xff9e,
    "KP_Delete": 0xff9f,
    "KP_Multiply": 0xffaa,
    "KP_Add": 0xffab,
    "KP_Subtract": 0xffad,
    "KP_Decimal": 0xffae,
    "KP_Divide": 0xffaf,
}
for _index in range(1, 13):
    KEYSYM_BY_KEY_NAME["F%d" % _index] = 0xffbd + _index
for _index in range(10):
    KEYSYM_BY_KEY_NAME["KP_%d" % _index] = 0xffb0 + _index

YDO_KEYCODE_BY_KEY_NAME = {
    "Escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "minus": 12,
    "equal": 13,
    "BackSpace": 14,
    "Tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20,
    "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "bracketleft": 26,
    "bracketright": 27,
    "Return": 28,
    "Control_L": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34,
    "h": 35, "j": 36, "k": 37, "l": 38,
    "semicolon": 39,
    "apostrophe": 40,
    "grave": 41,
    "Shift_L": 42,
    "backslash": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48,
    "n": 49, "m": 50,
    "comma": 51,
    "period": 52,
    "slash": 53,
    "Shift_R": 54,
    "KP_Multiply": 55,
    "Alt_L": 56,
    "space": 57,
    "Caps_Lock": 58,
    "Num_Lock": 69,
    "Scroll_Lock": 70,
    "KP_Home": 71,
    "KP_Up": 72,
    "KP_Prior": 73,
    "KP_Subtract": 74,
    "KP_Left": 75,
    "KP_Begin": 76,
    "KP_Right": 77,
    "KP_Add": 78,
    "KP_End": 79,
    "KP_Down": 80,
    "KP_Next": 81,
    "KP_Insert": 82,
    "KP_Delete": 83,
    "KP_Enter": 96,
    "Control_R": 97,
    "KP_Divide": 98,
    "Print": 99,
    "Alt_R": 100,
    "Home": 102,
    "Up": 103,
    "Prior": 104,
    "Left": 105,
    "Right": 106,
    "End": 107,
    "Down": 108,
    "Next": 109,
    "Insert": 110,
    "Delete": 111,
    "Pause": 119,
    "Super_L": 125,
    "Super_R": 126,
    "Menu": 127,
}
for _index in range(1, 11):
    YDO_KEYCODE_BY_KEY_NAME["F%d" % _index] = 58 + _index
YDO_KEYCODE_BY_KEY_NAME["F11"] = 87
YDO_KEYCODE_BY_KEY_NAME["F12"] = 88
YDO_KEYCODE_BY_KEY_NAME.update({
    "KP_0": 82,
    "KP_1": 79,
    "KP_2": 80,
    "KP_3": 81,
    "KP_4": 75,
    "KP_5": 76,
    "KP_6": 77,
    "KP_7": 71,
    "KP_8": 72,
    "KP_9": 73,
})


class PortalKeyboardInjector:
    """Keyboard injection through the XDG Remote Desktop portal."""

    def __init__(self):
        self._lock = threading.RLock()
        self._bus = None
        self._session_handle = None
        self._started = False
        self.denied = False
        self.unavailable = False
        self._warned_failure = False

    def send_key(self, key_name, pressed):
        if self.unavailable:
            return False
        keycode = self._keycode_from_key_name(key_name)
        keysym = None if keycode is not None else self._keysym_from_key_name(key_name)
        if keycode is None and keysym is None:
            _dbg("portal: cannot map key %r" % key_name)
            return False
        try:
            if not self._ensure_session():
                return False
        except Exception:
            self.unavailable = True
            self._log_failure("Remote Desktop portal setup failed")
            return False

        method = "NotifyKeyboardKeycode"
        key_value = keycode
        if key_value is None:
            method = "NotifyKeyboardKeysym"
            key_value = keysym
        try:
            GLib, Gio = self._load_gio()
            self._bus.call_sync(
                PORTAL_BUS_NAME,
                PORTAL_OBJECT_PATH,
                PORTAL_REMOTE_DESKTOP_IFACE,
                method,
                GLib.Variant(
                    "(oa{sv}iu)",
                    (self._session_handle, {}, int(key_value),
                     1 if pressed else 0)
                ),
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None
            )
            return True
        except Exception:
            self._started = False
            self._log_failure("Remote Desktop portal key injection failed")
            return False

    def _ensure_session(self):
        with self._lock:
            if self._started:
                return True
            if self.denied:
                return False

            GLib, Gio = self._load_gio()
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            session_token = self._new_token("session")
            create_results = self._portal_request(
                "CreateSession",
                lambda token: GLib.Variant("(a{sv})", ({
                    "handle_token": GLib.Variant("s", token),
                    "session_handle_token": GLib.Variant("s", session_token),
                },))
            )
            self._session_handle = self._variant_value(
                create_results.get("session_handle")
            )
            if not self._session_handle:
                raise RuntimeError("Portal did not return a session handle")

            self._portal_request(
                "SelectDevices",
                lambda token: GLib.Variant("(oa{sv})", (
                    self._session_handle,
                    {
                        "handle_token": GLib.Variant("s", token),
                        "types": GLib.Variant("u", PORTAL_KEYBOARD),
                    }
                ))
            )
            start_results = self._portal_request(
                "Start",
                lambda token: GLib.Variant("(osa{sv})", (
                    self._session_handle,
                    "",
                    {"handle_token": GLib.Variant("s", token)}
                ))
            )
            devices = self._variant_value(start_results.get("devices"), 0)
            if not devices & PORTAL_KEYBOARD:
                self.denied = True
                _dbg("portal: keyboard access was not granted")
                return False
            self._started = True
            return True

    def _portal_request(self, method, params_factory):
        GLib, Gio = self._load_gio()
        token = self._new_token(method.lower())
        expected_path = self._request_path(token)
        response_data = {"done": False, "response": None, "results": None}
        loop = GLib.MainLoop()

        def on_response(
                connection, sender_name, object_path, interface_name,
                signal_name, parameters, user_data):
            response, results = parameters.unpack()
            response_data["done"] = True
            response_data["response"] = response
            response_data["results"] = results
            loop.quit()

        sub_ids = [
            self._bus.signal_subscribe(
                PORTAL_BUS_NAME,
                PORTAL_REQUEST_IFACE,
                "Response",
                expected_path,
                None,
                Gio.DBusSignalFlags.NONE,
                on_response,
                None
            )
        ]
        timeout_id = GLib.timeout_add_seconds(300, loop.quit)
        try:
            reply = self._bus.call_sync(
                PORTAL_BUS_NAME,
                PORTAL_OBJECT_PATH,
                PORTAL_REMOTE_DESKTOP_IFACE,
                method,
                params_factory(token),
                GLib.VariantType.new("(o)"),
                Gio.DBusCallFlags.NONE,
                30000,
                None
            )
            request_path = reply.unpack()[0]
            if request_path != expected_path:
                sub_ids.append(self._bus.signal_subscribe(
                    PORTAL_BUS_NAME,
                    PORTAL_REQUEST_IFACE,
                    "Response",
                    request_path,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    on_response,
                    None
                ))
            if not response_data["done"]:
                loop.run()
        finally:
            try:
                GLib.source_remove(timeout_id)
            except Exception:
                pass
            for sub_id in sub_ids:
                self._bus.signal_unsubscribe(sub_id)

        if not response_data["done"]:
            raise RuntimeError("Timed out waiting for portal %s response" % method)
        if response_data["response"] == 1:
            self.denied = True
            _dbg("portal: request denied for %s" % method)
            return {}
        if response_data["response"] != 0:
            raise RuntimeError(
                "Portal %s failed with response %s"
                % (method, response_data["response"])
            )
        return response_data["results"] or {}

    def _request_path(self, token):
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        return "/org/freedesktop/portal/desktop/request/%s/%s" % (
            sender, token
        )

    def _log_failure(self, message):
        if self._warned_failure:
            return
        self._warned_failure = True
        log.exception(message)
        _dbg(message)

    @staticmethod
    def _new_token(prefix):
        return "orcaremote_%s_%s" % (prefix, uuid.uuid4().hex)

    @staticmethod
    def _variant_value(value, default=None):
        if value is None:
            return default
        if hasattr(value, "unpack"):
            return value.unpack()
        return value

    @staticmethod
    def _load_gio():
        import gi
        from gi.repository import GLib, Gio
        return GLib, Gio

    @staticmethod
    def _keycode_from_key_name(key_name):
        if not key_name:
            return None
        if len(key_name) == 1:
            key_name = key_name.lower()
        return YDO_KEYCODE_BY_KEY_NAME.get(key_name)

    @staticmethod
    def _keysym_from_key_name(key_name):
        if not key_name:
            return None
        if len(key_name) == 1:
            return ord(key_name)
        keysym = KEYSYM_BY_KEY_NAME.get(key_name)
        if keysym is not None:
            return keysym
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk
            keysym = Gdk.keyval_from_name(key_name)
            return keysym or None
        except Exception:
            return None


class LocalMachine:
    """Executes remote commands on the local Orca/Linux machine."""

    def __init__(self):
        self.is_muted = False
        self._speech_paused = False
        self._portal_injector = None
        self._warned_no_key_backend = False
        self._warned_ydotool = False

    # ---- Speech ----

    def speak(self, sequence=None, priority=None, **kwargs):
        """Speak text received from the remote machine."""
        if self.is_muted or sequence is None:
            return
        try:
            if isinstance(sequence, list):
                text = " ".join(str(s) for s in sequence if isinstance(s, str))
            else:
                text = str(sequence)
            if text:
                # Stop current speech before speaking to allow interruption,
                # just like Orca does on each navigation event.
                # NVDA SPRI_NEXT (priority 0) means queue; all others interrupt.
                if priority != 0:
                    local_stop = getattr(self, '_local_stop', None)
                    if local_stop is not None:
                        local_stop()
                local_speak = getattr(self, '_local_speak', None)
                if local_speak is not None:
                    local_speak(text)
                else:
                    import orca.speech as speech
                    speech.speak(text)
        except Exception:
            log.exception("Failed to speak remote text")

    def cancel_speech(self, **kwargs):
        """Cancel current speech."""
        if self.is_muted:
            return
        try:
            local_stop = getattr(self, '_local_stop', None)
            if local_stop is not None:
                local_stop()
            else:
                import orca.speech as speech
                speech.stop()
        except Exception:
            log.exception("Failed to cancel speech")

    def pause_speech(self, switch=None, **kwargs):
        """Pause or resume speech."""
        if self.is_muted or switch is None:
            return
        self._speech_paused = bool(switch)
        # Orca doesn't have a direct pause API, so we stop speech on pause
        if self._speech_paused:
            self.cancel_speech()

    # ---- Audio ----

    def beep(self, hz=None, length=None, left=50, right=50, **kwargs):
        """Play a tone/beep using the local audio system."""
        if self.is_muted or hz is None or length is None:
            return
        try:
            # Use paplay or sox to generate tones on Linux
            duration_sec = length / 1000.0
            # Volume from 0-100 to 0-1
            vol = max(left, right) / 100.0
            subprocess.Popen(
                ["play", "-qn", "synth", str(duration_sec),
                 "sine", str(hz), "vol", str(vol)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            # sox/play not installed, try python beep
            try:
                self._python_beep(hz, length / 1000.0)
            except Exception:
                log.debug("No audio backend for tones (install sox)")

    @staticmethod
    def _python_beep(frequency, duration):
        """Generate a beep using pure Python (fallback)."""
        import struct
        import wave
        import tempfile
        import os
        import math
        sample_rate = 22050
        n_samples = int(sample_rate * duration)
        buf = b''
        for i in range(n_samples):
            t = i / sample_rate
            sample = int(16000 * math.sin(2 * math.pi * frequency * t))
            buf += struct.pack('<h', sample)
        fd, path = tempfile.mkstemp(suffix='.wav')
        try:
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(buf)
            subprocess.Popen(
                ["paplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        finally:
            # Give paplay time to read before cleanup
            import threading
            def _cleanup():
                import time
                time.sleep(duration + 0.5)
                try:
                    os.unlink(path)
                except OSError:
                    pass
            t = threading.Thread(target=_cleanup)
            t.daemon = True
            t.start()

    def play_wave(self, fileName=None, **kwargs):
        """Play a wave file."""
        if self.is_muted or fileName is None:
            return
        import os
        if not os.path.exists(fileName):
            log.debug("Wave file not found: %s" % fileName)
            return
        try:
            subprocess.Popen(
                ["paplay", fileName],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            try:
                subprocess.Popen(
                    ["aplay", fileName],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                log.debug("No audio player found (install pulseaudio-utils)")

    # ---- Clipboard ----

    def set_clipboard_text(self, text=None, **kwargs):
        """Set the local clipboard text from remote."""
        if text is None:
            return
        try:
            # Try GTK clipboard first
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, Gdk
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            clipboard.store()
            log.info("Clipboard set from remote")
        except Exception:
            # Fallback to xclip
            try:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                proc.communicate(text.encode('utf-8'))
            except FileNotFoundError:
                log.warning("Cannot set clipboard (install xclip)")

    def get_clipboard_text(self):
        """Get the local clipboard text for sending to remote."""
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, Gdk
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            text = clipboard.wait_for_text()
            return text or ""
        except Exception:
            try:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, text=True, timeout=2
                )
                return result.stdout
            except Exception:
                log.warning("Cannot read clipboard")
                return ""

    # ---- Keys ----

    def send_key(self, key_name=None, pressed=None, modifiers=None,
                 vk_code=None, scan_code=None, extended=None, **kwargs):
        """Inject a key event from the remote machine.

        Accepts either key_name (Orca-to-Orca path) or vk_code/extended
        (NVDA-to-Orca path, Windows Virtual Key codes).

        On GNOME Wayland, uses the XDG Remote Desktop portal so the
        compositor can authorize keyboard control. On X11, uses xdotool.
        ydotool is an optional fallback when configured.
        """
        if pressed is None:
            return
        key = self._resolve_key(key_name, vk_code, extended)
        if key is None:
            _dbg("send_key: NO MAPPING for name=%r vk=%s ext=%s" % (
                key_name, vk_code, extended))
            return
        _dbg("send_key: %s %s" % ("press" if pressed else "release", key))

        if self._is_wayland_session():
            if self._send_key_portal(key, pressed):
                return
            if self._portal_denied():
                return
            if self._send_key_ydotool(key, pressed):
                return
            self._warn_no_key_backend()
            return

        if self._send_key_xdotool(key, pressed):
            return
        if self._send_key_ydotool(key, pressed):
            return
        if self._send_key_portal(key, pressed):
            return
        self._warn_no_key_backend()

    def _send_key_xdotool(self, key, pressed):
        if shutil.which("xdotool") is None:
            return False
        try:
            action = "keydown" if pressed else "keyup"
            result = subprocess.run(
                ["xdotool", action, key],
                capture_output=True,
            )
            if result.returncode != 0:
                _dbg("xdotool error: %s" % result.stderr.decode().strip())
                return False
            return True
        except FileNotFoundError:
            _dbg("xdotool not found")
            return False

    def _send_key_portal(self, key, pressed):
        if self._portal_injector is None:
            self._portal_injector = PortalKeyboardInjector()
        return self._portal_injector.send_key(key, pressed)

    def _send_key_ydotool(self, key, pressed):
        keycode = PortalKeyboardInjector._keycode_from_key_name(key)
        if keycode is None or shutil.which("ydotool") is None:
            return False
        try:
            result = subprocess.run(
                ["ydotool", "key", "%d:%d" % (keycode, 1 if pressed else 0)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            if result.returncode == 0:
                return True
            if not self._warned_ydotool:
                self._warned_ydotool = True
                _dbg("ydotool failed; is ydotoold running?")
            return False
        except Exception:
            if not self._warned_ydotool:
                self._warned_ydotool = True
                log.exception("ydotool key injection failed")
            return False

    def _warn_no_key_backend(self):
        if self._warned_no_key_backend:
            return
        self._warned_no_key_backend = True
        _dbg(
            "No usable key injection backend. On GNOME Wayland, approve the "
            "Remote Desktop portal prompt or configure ydotoold. On X11, "
            "install xdotool."
        )
        log.warning(
            "No usable key injection backend. On GNOME Wayland, approve the "
            "Remote Desktop portal prompt or configure ydotoold. On X11, "
            "install xdotool."
        )

    def _portal_denied(self):
        return bool(self._portal_injector and self._portal_injector.denied)

    def send_sas(self, **kwargs):
        """Handle Ctrl+Alt+Del request (not applicable on Linux)."""
        log.info("Received SAS request (Ctrl+Alt+Del) - not applicable on Linux")

    # ---- Braille ----

    def braille_display(self, cells=None, **kwargs):
        """Display braille cells from remote."""
        if cells is None:
            return
        try:
            import orca.braille as braille
            # Write cells to the local braille display if available
            if hasattr(braille, 'writeCells'):
                braille.writeCells(cells)
        except Exception:
            log.debug("Braille display not available")

    def set_braille_info(self, name=None, numCells=None, **kwargs):
        """Receive remote braille display info."""
        log.info("Remote braille display: %s (%s cells)" % (name, numCells))

    # ---- Mute ----

    def toggle_mute(self):
        """Toggle mute state for remote output."""
        self.is_muted = not self.is_muted
        return self.is_muted

    # ---- Helpers ----

    @staticmethod
    def _is_wayland_session():
        return (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )

    @staticmethod
    def _resolve_key(key_name, vk_code, extended):
        """Return an xdotool key name or None if the key is unmapped.

        Priority:
          1. key_name string (Orca-to-Orca path, already in X11 key name form)
          2. vk_code with extended flag (NVDA-to-Orca path)
        """
        if key_name:
            return LocalMachine._map_key_name(key_name)
        if vk_code is not None:
            if extended and vk_code in _VK_TO_XDOTOOL_EXT:
                return _VK_TO_XDOTOOL_EXT[vk_code]
            return _VK_TO_XDOTOOL.get(vk_code)
        return None

    @staticmethod
    def _map_key_name(key_name):
        """Map Orca/NVDA Remote key_name strings to xdotool key names."""
        key_map = {
            "back": "BackSpace",
            "apps": "Menu",
            "win": "Super_L",
            "return": "Return",
            "enter": "Return",
            "space": "space",
            "tab": "Tab",
            "escape": "Escape",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "home": "Home",
            "end": "End",
            "pageup": "Prior",
            "pagedown": "Next",
            "delete": "Delete",
            "insert": "Insert",
            "numpadinsert": "KP_Insert",
            "capslock": "Caps_Lock",
            "numlock": "Num_Lock",
            "scrolllock": "Scroll_Lock",
            "shift": "Shift_L",
            "control": "Control_L",
            "alt": "Alt_L",
            "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
            "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
            "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
        }
        for index in range(10):
            key_map["numpad%d" % index] = "KP_%d" % index
        return key_map.get(key_name.lower(), key_name)
