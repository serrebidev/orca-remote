"""Local machine handler for Orca Remote.

Handles incoming remote commands and executes them on the local
Orca machine - speech, tones, braille, clipboard, keys, etc.
This is the equivalent of NVDA Remote's LocalMachine class.
"""

import subprocess
from logging import getLogger
log = getLogger('local_machine')


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

    def send_key(self, key_name=None, pressed=None, modifiers=None, **kwargs):
        """Inject a key event from the remote machine."""
        if key_name is None or pressed is None:
            return
        try:
            if pressed:
                action = "keydown"
            else:
                action = "keyup"
            mapped = self._map_key_name(key_name)
            subprocess.Popen(
                ["xdotool", action, "--clearmodifiers", mapped],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
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

    @staticmethod
    def _map_key_name(key_name):
        """Map NVDA Remote key names to xdotool key names."""
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
