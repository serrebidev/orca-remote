"""Local machine handler for Orca Remote.

Handles incoming remote commands and executes them on the local
Orca machine - speech, tones, braille, clipboard, keys, etc.
This is the equivalent of NVDA Remote's LocalMachine class.
"""

import os
import subprocess
import threading
import time
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


class LocalMachine:
    """Executes remote commands on the local Orca/Linux machine."""

    def __init__(self):
        self.is_muted = False
        self._speech_paused = False

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

        Uses AT-SPI generateKeyboardEvent as the primary injection method so
        that keystrokes reach Wayland-native apps (GTK4, GNOME shell, etc.)
        as well as X11/XWayland apps.  Falls back to xdotool if AT-SPI fails.
        """
        if pressed is None:
            return
        key = self._resolve_key(key_name, vk_code, extended)
        if key is None:
            _dbg("send_key: NO MAPPING for name=%r vk=%s ext=%s" % (
                key_name, vk_code, extended))
            return
        _dbg("send_key: %s %s" % ("press" if pressed else "release", key))
        # xdotool (XTest) injection. AT-SPI generateKeyboardEvent was tried but
        # on this system (Asahi Linux / Wayland) it accepts the call silently
        # without actually delivering keys to apps.
        try:
            action = "keydown" if pressed else "keyup"
            result = subprocess.run(
                ["xdotool", action, key],
                capture_output=True,
            )
            if result.returncode != 0:
                _dbg("xdotool error: %s" % result.stderr.decode().strip())
        except FileNotFoundError:
            _dbg("xdotool not found")
            log.warning("xdotool not found for key injection")

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
            "numpadinsert": "Insert",
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
        return key_map.get(key_name.lower(), key_name)
