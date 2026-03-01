"""Remote controller state machine for Orca Remote.

Manages the state of local vs remote control, handles all NVDA Remote
protocol message types, session events, and audio notification cues.
"""

import os
import threading
import time
from logging import getLogger
log = getLogger('remote_controller')

from local_machine import LocalMachine

_DBG_LOG = os.path.expanduser("~/.local/share/orca/orca-remote-debug.log")

def _dbg(msg):
    try:
        with open(_DBG_LOG, "a") as f:
            f.write("[%.3f] RC: %s\n" % (time.time(), msg))
    except Exception:
        pass

# Audio cue definitions: list of (frequency_hz, duration_ms) tuples
CUES = {
    "connected":           [(440, 60), (660, 60)],
    "disconnected":        [(660, 60), (440, 60)],
    "control_server":      [(720, 100), (0, 50), (720, 100), (0, 50), (720, 100)],
    "client_connected":    [(1000, 300)],
    "client_disconnected": [(108, 300)],
    "clipboard_pushed":    [(500, 100), (600, 100)],
    "clipboard_received":  [(600, 100), (500, 100)],
}


class RemoteController:
    """Manages bidirectional control state between Orca and NVDA.

    Handles all NVDA Remote protocol message types and mirrors
    the master/slave session architecture of NVDA Remote.

    States:
        - LOCAL: All keystrokes processed locally by Orca (default)
        - REMOTE: Keystrokes are forwarded to the remote NVDA machine
    """

    LOCAL = "local"
    REMOTE = "remote"

    def __init__(self, transport, speech_callback=None):
        self.transport = transport
        self.control_state = self.LOCAL
        self.speech_callback = speech_callback
        self.local_machine = LocalMachine()
        self.connected_clients = {}
        self.play_sounds = True

        # Register all incoming message handlers
        cb = self.transport.callback_manager
        # Session events
        cb.register_callback('transport_connected', self._on_transport_connected)
        cb.register_callback('transport_disconnected', self._on_transport_disconnected)
        cb.register_callback('msg_channel_joined', self._on_channel_joined)
        cb.register_callback('msg_client_joined', self._on_client_joined)
        cb.register_callback('msg_client_left', self._on_client_left)
        cb.register_callback('msg_ping', self._on_ping)
        # Error/info messages
        cb.register_callback('msg_motd', self._on_motd)
        cb.register_callback('msg_version_mismatch', self._on_version_mismatch)
        cb.register_callback('msg_error', self._on_error)
        cb.register_callback('msg_nvda_not_connected', self._on_nvda_not_connected)
        # Speech & audio
        cb.register_callback('msg_speak', self._on_remote_speak)
        cb.register_callback('msg_cancel', self._on_remote_cancel)
        cb.register_callback('msg_pause_speech', self._on_remote_pause_speech)
        cb.register_callback('msg_tone', self._on_remote_tone)
        cb.register_callback('msg_wave', self._on_remote_wave)
        cb.register_callback('msg_index', self._on_remote_index)
        # Input
        cb.register_callback('msg_key', self._on_remote_key)
        cb.register_callback('msg_send_SAS', self._on_remote_sas)
        # Braille
        cb.register_callback('msg_display', self._on_remote_braille_display)
        cb.register_callback('msg_braille_input', self._on_remote_braille_input)
        cb.register_callback('msg_set_braille_info', self._on_remote_braille_info)
        cb.register_callback('msg_set_display_size', self._on_remote_display_size)
        # Clipboard
        cb.register_callback('msg_set_clipboard_text', self._on_remote_clipboard)

    # ---- Properties ----

    @property
    def controlling_remote(self):
        return self.control_state == self.REMOTE

    @property
    def is_muted(self):
        return self.local_machine.is_muted

    # ---- Control State ----

    def toggle_control(self):
        """Toggle between controlling local and remote machine."""
        if self.control_state == self.LOCAL:
            self.control_state = self.REMOTE
            msg = "Controlling remote machine"
        else:
            self.control_state = self.LOCAL
            msg = "Controlling local machine"
        log.info(msg)
        if self.speech_callback:
            self.speech_callback(msg)
        return self.control_state

    def toggle_mute(self):
        """Toggle mute state for remote speech/audio."""
        muted = self.local_machine.toggle_mute()
        msg = "Remote muted" if muted else "Remote unmuted"
        log.info(msg)
        if self.speech_callback:
            self.speech_callback(msg)
        return muted

    def disconnect(self):
        """Disconnect from the remote server."""
        if self.control_state == self.REMOTE:
            self.control_state = self.LOCAL
        self.transport.close()
        if self.speech_callback:
            self.speech_callback("Disconnected")

    # ---- Outbound: Send to Remote NVDA ----

    def send_key(self, key_name, pressed, modifiers=None):
        """Send a key event to the remote NVDA machine."""
        if not self.transport.connected:
            return
        event = {"key_name": key_name, "pressed": pressed}
        if modifiers:
            event["modifiers"] = modifiers
        self.transport.send(type="key", **event)

    def push_clipboard(self):
        """Send local clipboard text to remote machine."""
        text = self.local_machine.get_clipboard_text()
        if text:
            self.transport.send(type="set_clipboard_text", text=text)
            self._play_cue("clipboard_pushed")
            if self.speech_callback:
                self.speech_callback("Clipboard sent")
        else:
            if self.speech_callback:
                self.speech_callback("Clipboard is empty")

    def send_sas(self):
        """Send Ctrl+Alt+Del to the remote machine."""
        if self.transport.connected:
            self.transport.send(type="send_SAS")

    def send_braille_info(self, name, num_cells):
        """Send local braille display info to remote."""
        if self.transport.connected:
            self.transport.send(
                type="set_braille_info", name=name, numCells=num_cells
            )

    # ---- Session Event Handlers ----

    def _on_transport_connected(self):
        log.info("Transport connected")
        self._play_cue("connected")
        if self.speech_callback:
            self.speech_callback("Connected")

    def _on_transport_disconnected(self):
        log.info("Transport disconnected")
        self.connected_clients.clear()
        if self.control_state == self.REMOTE:
            self.control_state = self.LOCAL
            if self.speech_callback:
                self.speech_callback("Controlling local machine")
        self._play_cue("disconnected")

    def _on_channel_joined(self, channel=None, user_ids=None, clients=None, **kwargs):
        log.info("Joined channel: %s" % channel)
        if clients:
            for client in clients:
                client_id = client.get('id')
                conn_type = client.get('connection_type')
                if client_id:
                    self.connected_clients[client_id] = {
                        'connection_type': conn_type
                    }
            log.info("Clients in channel: %d" % len(self.connected_clients))

    def _on_client_joined(self, client=None, **kwargs):
        if client:
            client_id = client.get('id')
            conn_type = client.get('connection_type')
            if client_id:
                self.connected_clients[client_id] = {
                    'connection_type': conn_type
                }
                log.info("Client joined: %s (%s)" % (client_id, conn_type))
                self._play_cue("client_connected")
                if self.speech_callback:
                    if conn_type == "master":
                        self.speech_callback("Controller connected")
                    else:
                        self.speech_callback("Client connected")

    def _on_client_left(self, client=None, **kwargs):
        if client:
            client_id = client.get('id')
            if client_id and client_id in self.connected_clients:
                del self.connected_clients[client_id]
                log.info("Client left: %s" % client_id)
                self._play_cue("client_disconnected")

    def _on_ping(self, **kwargs):
        # Ping is just a keep-alive, no response needed
        pass

    # ---- Error/Info Handlers ----

    def _on_motd(self, motd=None, **kwargs):
        if motd:
            log.info("Server MOTD: %s" % motd)

    def _on_version_mismatch(self, **kwargs):
        log.error("Protocol version mismatch with server")
        if self.speech_callback:
            self.speech_callback("Protocol version mismatch. Connection may not work correctly.")

    def _on_error(self, message=None, **kwargs):
        log.error("Server error: %s" % message)
        if message == "incorrect_password":
            if self.speech_callback:
                self.speech_callback("Incorrect channel key")
        elif self.speech_callback:
            self.speech_callback("Connection error: %s" % message)

    def _on_nvda_not_connected(self, **kwargs):
        log.warning("Remote NVDA is not connected")
        if self.speech_callback:
            self.speech_callback("Remote NVDA is not connected")

    # ---- Incoming Speech & Audio ----

    def _on_remote_speak(self, sequence=None, priority=None, **kwargs):
        self.local_machine.speak(sequence=sequence, priority=priority)

    def _on_remote_cancel(self, **kwargs):
        self.local_machine.cancel_speech()

    def _on_remote_pause_speech(self, switch=None, **kwargs):
        self.local_machine.pause_speech(switch=switch)

    def _on_remote_tone(self, hz=None, length=None, left=50, right=50, **kwargs):
        self.local_machine.beep(hz=hz, length=length, left=left, right=right)

    def _on_remote_wave(self, fileName=None, **kwargs):
        self.local_machine.play_wave(fileName=fileName)

    def _on_remote_index(self, index=None, **kwargs):
        # Speech index markers - logged but not acted on
        log.debug("Speech index: %s" % index)

    # ---- Incoming Input ----

    def _on_remote_key(self, key_name=None, pressed=None, modifiers=None,
                       vk_code=None, scan_code=None, extended=None, **kwargs):
        if pressed is None:
            _dbg("_on_remote_key: pressed is None, dropping (kwargs=%s)" % kwargs)
            return
        _dbg("_on_remote_key: name=%r vk=%s ext=%s pressed=%s" % (
            key_name, vk_code, extended, pressed))
        self.local_machine.send_key(
            key_name=key_name, pressed=pressed, modifiers=modifiers,
            vk_code=vk_code, scan_code=scan_code, extended=extended,
        )

    def _on_remote_sas(self, **kwargs):
        self.local_machine.send_sas()

    # ---- Incoming Braille ----

    def _on_remote_braille_display(self, cells=None, **kwargs):
        self.local_machine.braille_display(cells=cells)

    def _on_remote_braille_input(self, **kwargs):
        log.debug("Remote braille input: %s" % kwargs)

    def _on_remote_braille_info(self, name=None, numCells=None, **kwargs):
        self.local_machine.set_braille_info(name=name, numCells=numCells)

    def _on_remote_display_size(self, sizes=None, **kwargs):
        log.debug("Remote display sizes: %s" % sizes)

    # ---- Incoming Clipboard ----

    def _on_remote_clipboard(self, text=None, **kwargs):
        self.local_machine.set_clipboard_text(text=text)
        self._play_cue("clipboard_received")
        if self.speech_callback:
            self.speech_callback("Clipboard received")

    # ---- Audio Cues ----

    def _play_cue(self, cue_name):
        """Play an audio notification cue (sequence of beeps)."""
        if not self.play_sounds:
            return
        tones = CUES.get(cue_name)
        if not tones:
            return

        def _play():
            import time
            for hz, duration_ms in tones:
                if hz > 0:
                    self.local_machine.beep(
                        hz=hz, length=duration_ms, left=50, right=50
                    )
                # Wait for the tone duration before playing next
                time.sleep(duration_ms / 1000.0)

        t = threading.Thread(target=_play)
        t.daemon = True
        t.start()
