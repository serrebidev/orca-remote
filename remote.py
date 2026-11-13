"""RemoteExtension -- the user-extension entry point.

Stage 1 scope: client-only receive-speech mirror. On enable, spin up
an asyncio loop in a daemon thread that holds an outbound TLS
connection to an NVDA Remote v2.x relay. Inbound `speak` messages
are routed back to Orca's main thread and spoken via the controller.

No keyboard commands and no host (be-controlled) mode in Stage 1.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import wave
from typing import Any

# Workaround: orca's extension loader synthesizes the child package
# (`orca_user_extension.remote`) and registers it in sys.modules, but
# not the top-level parent `orca_user_extension`. Without that
# top-level entry, our relative imports below trigger
# `ModuleNotFoundError: No module named 'orca_user_extension'` because
# Python's import machinery walks parent names. Create a stub parent
# package if one isn't already present.
if "orca_user_extension" not in sys.modules:
    _parent = types.ModuleType("orca_user_extension")
    _parent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["orca_user_extension"] = _parent

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from orca import debug, keybindings  # noqa: E402
from orca.command import Command, KeyboardCommand  # noqa: E402
from orca.extension import Extension  # noqa: E402

from . import protocol
from . import braille_table
from .settings_dialog import (
    DEFAULT_SETTINGS,
    ROLE_CLIENT,
    ROLE_HOST,
    SETTING_AUTO_CONNECT,
    SETTING_CHANNEL,
    SETTING_FINGERPRINT,
    SETTING_HOST,
    SETTING_PORT,
    SETTING_ROLE,
    build_settings_dialog,
    build_trust_dialog,
)
from .keymap import forwardable_keysyms, keysym_to_vk, vk_to_keysym
from .protocol import CONNECTION_TYPE_MASTER, CONNECTION_TYPE_SLAVE
from .transport import RemoteTransport

# Both modules need the same normalization rule, so the helper lives
# with the transport that compares the pin.
from .transport import _normalize_fingerprint
from .inject import KeyInjector, is_wayland_session, resolve_keysym

# Vendored orca-ext-utils (see vendor/UPDATE.md for sync notes). The
# import is guarded so an extension built without the vendor tree
# (developer running from a source checkout that hasn't synced yet)
# degrades to "Orca-dispatch consume only" rather than crashing on
# load.
try:
    from .vendor.orca_ext_utils.keyboard_grab import KeysetGrab
    _HAVE_KEYSET_GRAB = True
except Exception:  # pylint: disable=broad-except
    KeysetGrab = None  # type: ignore[assignment,misc]
    _HAVE_KEYSET_GRAB = False


_SETTINGS_FILENAME = "orca-remote-settings.json"

# Last fingerprint seen on a mismatch, saved as plain text so it can be
# copied by hand (nano ~/orca-remote-fingerprint.txt). Clipboard
# handling is the fragile part across sessions: X selections with no
# clipboard manager, GPaste/Klipper on Wayland, wl-copy, and a paste
# that lands next to the old value all fail in different ways.
_FINGERPRINT_FILENAME = "orca-remote-fingerprint.txt"


def _fingerprint_file_path() -> str:
    """Path of the saved fingerprint in the user's home directory."""

    return os.path.join(os.path.expanduser("~"), _FINGERPRINT_FILENAME)


# A SHA-256 fingerprint is 64 hex characters. Anything else can never
# match a certificate, so a malformed pin is worth refusing loudly
# instead of dialing and reporting a mismatch forever.
_FINGERPRINT_CHARS = 64


def _gtk_clipboard() -> Any:
    """Return the Gtk CLIPBOARD object, or None when GTK is unusable here.

    The clipboard is read and written directly on some paths: builds
    whose clipboard presenter has no get_text make
    controller.get_clipboard_text() always return "", and
    set_clipboard_text is a silent no-op when no clipboard manager is
    active. Gtk's own clipboard is the fallback Orca's presenter uses
    too, so this is the same mechanism, minus the presenter's state.
    """

    try:
        import gi  # pylint: disable=import-outside-toplevel

        gi.require_version("Gdk", "3.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gtk  # pylint: disable=import-outside-toplevel

        return Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    except Exception:  # pylint: disable=broad-except
        return None


# Orca builds report outbound speech two different ways. The perf
# branch (and the builds this project is live-tested on) has the
# controller hook `subscribe_speech_emitted`. Released Orca 51.1 has
# no such controller method; its Extension API calls
# `on_speech_output` instead. We use whichever the running Orca
# provides -- never both, or the peer would hear every utterance
# twice.
_HAVE_ON_SPEECH_OUTPUT = hasattr(Extension, "on_speech_output")

# Same split for braille output: perf builds report refreshes through
# the controller hook `subscribe_braille_emitted`; released Orca 51.1
# has no such method and calls `Extension.on_braille_output` instead.
_HAVE_ON_BRAILLE_OUTPUT = hasattr(Extension, "on_braille_output")


# Modifier keysyms whose press/release we use to recognize when an
# inbound key sequence is about to complete one of our own command
# chords. Tracked against `_pressed_keysyms`, which records what we've
# synthesized PRESS for and not yet RELEASED.
_ORCA_MOD_KEYSYMS: frozenset[int] = frozenset({
    0xff63,  # XK_Insert       (desktop-layout Orca modifier)
    0xff9e,  # XK_KP_Insert    (numpad variant)
    0xffe5,  # XK_Caps_Lock    (laptop-layout Orca modifier)
    0xffe6,  # XK_Shift_Lock   (laptop-layout alternative)
})
_CTRL_KEYSYMS: frozenset[int] = frozenset({0xffe3, 0xffe4})
_SHIFT_KEYSYMS: frozenset[int] = frozenset({0xffe1, 0xffe2})
_ALT_KEYSYMS: frozenset[int] = frozenset({0xffe9, 0xffea})

# Keysyms we hold across multiple inbound frames so chords work (Orca-mod
# + Ctrl + letter style). Every other keysym is tapped -- PRESS plus an
# immediate matching RELEASE in the same idle callback -- to keep X11's
# own auto-repeat from kicking in. NVDA Remote forwards one PRESS frame
# per physical tap on the master; on X11, a synthesized PRESS without a
# RELEASE leaves the key in the server's "held" state, at which point
# the X server itself starts dispatching real key events at ~30 Hz, one
# inbound H frame becomes a flood of host-side H presses, and Orca's
# main loop saturates trying to keep up. Holding only the modifier set
# below preserves chord semantics while everything else taps cleanly.
_STICKY_KEYSYMS: frozenset[int] = frozenset({
    0xffe1, 0xffe2,  # Shift_L, Shift_R
    0xffe3, 0xffe4,  # Control_L, Control_R
    0xffe7, 0xffe8,  # Meta_L, Meta_R
    0xffe9, 0xffea,  # Alt_L, Alt_R
    0xffeb, 0xffec,  # Super_L, Super_R
    0xffed, 0xffee,  # Hyper_L, Hyper_R
    0xfe03,          # ISO_Level3_Shift
    0xfe04,          # ISO_Level5_Shift
    0xff63,          # Insert       (Orca mod, desktop layout)
    0xff9e,          # KP_Insert    (Orca mod, numpad variant)
    # Caps_Lock stays a tap on purpose: held, the X server autorepeats
    # it before the late wire RELEASE lands and caps state flips again
    # per repeat (seen live on XWayland: one tap -> on, off, on).
})

# Keysyms that, when pressed while Orca-mod + Ctrl are held, fire one
# of our own commands. Used in two places:
#   1. Host (slave) side: refuses inbound synthesis so a remote master
#      can't pop our settings dialog or bounce the transport.
#   2. Master (client) side: skips forwarding so the user can fire
#      orca-remote's own commands locally even while focused on remote
#      (the bypass list replaces the older single-key escape).
_OWN_CTRL_CHORD_KEYSYMS: frozenset[int] = frozenset({
    0x72,    # XK_r          -> open_settings
    0xff55,  # XK_Page_Up    -> connect
    0xff56,  # XK_Page_Down  -> disconnect_session
    0xff9a,  # XK_KP_Page_Up
    0xff9b,  # XK_KP_Page_Down
})
_OWN_CTRL_SHIFT_CHORD_KEYSYMS: frozenset[int] = frozenset({
    0x63,    # XK_c          -> push_clipboard
    0x43,    # XK_C          -> push_clipboard on layouts reporting shifted keysyms
    # Mute is Orca+Ctrl+Shift+M, not Orca+Ctrl+M: in the laptop layout
    # Orca+Ctrl+M is flat review "end of line", and Orca refuses an
    # extension binding that collides with a built-in one.
    0x6d,    # XK_m          -> mute_inbound_toggle
    0x4d,    # XK_M          -> mute_inbound_toggle (shifted keysym)
})
_OWN_ALT_CHORD_KEYSYMS: frozenset[int] = frozenset({
    0xff09,  # XK_Tab        -> switch_side
    0x73,    # XK_s          -> speak_status
    0x53,    # XK_S          -> speak_status (shifted keysym)
})

# AT-SPI key-event modifier bit positions (standard X11 layout).
# Used by the master-side bypass check to distinguish our own command
# chords from regular typing that should be forwarded to the remote.
_AT_SPI_MOD_CTRL: int = 0x04
_AT_SPI_MOD_SHIFT: int = 0x01
_AT_SPI_MOD_ALT: int = 0x08

# Modifier-mask combinations used for master-mode grabs on Orca's own
# AT-SPI device. Plain 0x00 is required for unmodified arrows and Tab.
_MASTER_GRAB_MODIFIERS: tuple[int, ...] = (
    0x00,
    0x01,                 # Shift
    0x04,                 # Ctrl
    0x08,                 # Alt
    0x40,                 # Super
    0x01 | 0x04,          # Shift+Ctrl
    0x01 | 0x08,          # Shift+Alt
    0x04 | 0x08,          # Ctrl+Alt
    0x01 | 0x04 | 0x08,   # Shift+Ctrl+Alt
)


# Identical host utterances closer together than this are one Orca
# event reported twice (focus + name change), not two utterances.
_SPEECH_DEDUPE_SECONDS: float = 0.3


def _settings_path() -> str:
    """Absolute path to the JSON settings file."""

    return os.path.join(
        GLib.get_user_data_dir(),  # pylint: disable=no-value-for-parameter
        "orca",
        _SETTINGS_FILENAME,
    )


def _play_tone_blocking(
    hz: int, length_ms: int, left: int, right: int, log: Any
) -> None:
    """Play one NVDA `tone` frame on the local audio system.

    Mirrors the legacy implementation's backends: sox `play` when
    present, else a generated WAV through paplay. Runs on a worker
    thread (spawned by RemoteExtension._play_tone) because it spawns
    subprocesses and, in the fallback path, writes a temp file.
    """

    volume = max(left, right) / 100.0
    if volume <= 0:
        return
    play = shutil.which("play")
    if play is not None:
        try:
            subprocess.Popen(
                [
                    play, "-qn", "synth", f"{length_ms / 1000.0:.3f}",
                    "sine", str(hz), "vol", f"{volume:.2f}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            pass  # fall through to the WAV fallback
    paplay = shutil.which("paplay")
    if paplay is None:
        log("tone: no audio backend (install sox or pulseaudio-utils)")
        return
    sample_rate = 22050
    n_samples = int(sample_rate * length_ms / 1000.0)
    amplitude = int(16000 * volume)
    frames = b"".join(
        struct.pack(
            "<h",
            int(amplitude * math.sin(2 * math.pi * hz * i / sample_rate)),
        )
        for i in range(n_samples)
    )
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as handle:
            with wave.open(handle, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(frames)
        subprocess.Popen(
            [paplay, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, wave.Error):
        try:
            os.unlink(path)
        except OSError:
            pass
        return

    # paplay reads the file asynchronously; remove it once the tone
    # has had time to finish.
    def _cleanup() -> None:
        time.sleep(length_ms / 1000.0 + 0.5)
        try:
            os.unlink(path)
        except OSError:
            pass

    threading.Thread(target=_cleanup, daemon=True).start()


class RemoteExtension(Extension):
    """NVDA Remote v2.x receive-speech mirror."""

    GROUP_LABEL = "Orca Remote"

    def __init__(self) -> None:
        self._settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._load_settings()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._transport: RemoteTransport | None = None
        # Master-only "focus" flag: when True, the master is driving
        # the remote machine -- keystrokes get forwarded (via
        # KeysetGrab + subscribe_keyboard_event) AND inbound speech
        # is spoken locally. When False, keys go to the master's own
        # apps and inbound speech is dropped. Toggled by Insert+Alt+Tab.
        # Default False so the extension loads in a known-quiet state;
        # the first switch_side enables both grab + hearing together.
        # Has no effect when we're the host (a slave never receives
        # speak messages it needs to mute and never grabs keyboard).
        self._focus_on_remote: bool = False
        # Master-only mute: independent of focus_on_remote. When True,
        # inbound speech is dropped regardless of focus state. Lets
        # the user keep the connection up + the focus on remote (so
        # keystrokes still forward) while silencing the speech feed --
        # "stop hearing them move around while I work." Toggled by
        # Orca+Ctrl+Shift+M.
        self._inbound_speech_muted: bool = False
        # Master-only: which Orca-modifier keys (Insert / Caps_Lock)
        # are currently held. A set, not a counter: holding Insert
        # autorepeats PRESS with a single RELEASE, which left a counter
        # stuck above zero and later Ctrl+M / Ctrl+PgDn treated as
        # local chords. Read by the bypass check in _on_keyboard_event
        # to decide whether the incoming chord is one of our local
        # commands (which dispatch locally via Orca) versus regular
        # typing (which gets forwarded so the remote NVDA/Orca can act
        # on it).
        self._master_orca_mods_down: set[int] = set()
        # Master-only: (vk_code, extended) pairs forwarded as PRESS and
        # not yet RELEASE. Leaving the remote (Orca+Alt+Tab) or
        # disconnecting stops forwarding before the user lets go of
        # Insert and Alt, so these are released explicitly; otherwise
        # they stay held down on the remote machine.
        self._forwarded_keys_down: set[tuple[int, bool]] = set()
        # Host-side: keysyms we've synthesized PRESS for but not yet
        # RELEASE. If the transport drops between a press/release pair
        # the X server would otherwise believe the key is still held,
        # which survives Orca restart (Atspi.generate_keyboard_event
        # uses XTEST). On disconnect / disable we drain this set with
        # synthetic releases so a force-killed VM is never the only
        # way out of a stuck modifier.
        self._pressed_keysyms: set[int] = set()
        # Host-side injection backends beyond XTEST: the RemoteDesktop
        # portal, ydotool, xdotool (see inject.py). It runs on its own
        # worker thread because the portal consent prompt and subprocess
        # calls must not block Orca's GLib main loop.
        self._injector = KeyInjector(self._log)
        # Set True the first time we see channel_joined for the current
        # session intent; reset by explicit connect / disconnect /
        # disable so user-initiated transitions re-announce but silent
        # auto-reconnects do not. Without this, a flaky link to the
        # relay produced a "connected in host mode" announcement every
        # 30s (the backoff cap), which is what the VM crash session
        # heard as "repeating things over and over."
        self._announced_join: bool = False
        # Relay-side "peer not connected" status can repeat while the
        # channel is empty. Announce it once until a peer joins or the
        # user starts a fresh connection intent.
        self._peer_not_connected_announced: bool = False
        # Host-side de-duplication of consecutive identical speech.
        # Orca emits the same string twice in rapid succession in a
        # few legitimate code paths (caret-moved + name-changed,
        # focus-of-focus, etc.). On the wire each duplicate becomes a
        # separate NVDA speak frame that the master's NVDA queues and
        # speaks. The user reported this as the master hearing
        # everything twice. Compare last-sent text and skip a repeat.
        self._last_outbound_speech: str = ""
        self._last_outbound_speech_at: float = 0.0
        # Same idea for braille: a long-running line refresh produces
        # many braille_emitted with identical text+cursor; drop repeats.
        self._last_outbound_braille: tuple[str, int] = ("", -2)
        # Peer-reported braille display width. Set from inbound
        # set_braille_info; informational for now (display_braille_text
        # auto-sizes to our local display, not the peer's).
        self._peer_braille_cells: int = 0
        # Set True on the first set_braille_info we send per session
        # so we re-send the dimensions only when the master would have
        # forgotten (a fresh channel_joined). Reset on disconnect.
        self._sent_braille_info: bool = False
        # Counters surfaced via _log to give the user a way to see if
        # we're silently dropping things. Reset on disable.
        self._dropped_nonstring_items: int = 0
        # Master-side: reasons an inbound `speak` frame was dropped,
        # already logged this session. A peer that sends nothing and a
        # gate that drops everything both sound like silence; one log
        # line per reason tells the two apart in orca-debug.txt.
        self._speak_ignored_reasons: set[str] = set()
        # User toggles surfaced via the remote menu. Defaults match
        # "everything on" so first-time users get the full experience;
        # menu items flip these without restarting the transport.
        self._mirror_speech: bool = True
        self._mirror_braille: bool = True
        # Singleton settings-dialog ref. open_settings always destroys
        # and rebuilds rather than presenting an existing dialog --
        # focus-loss on the existing one tends to leave it alive but
        # unreachable on marco/wayland-flagged sessions, and present()
        # doesn't reliably re-raise. Destroy-rebuild is predictable.
        self._settings_dialog: Any = None
        self._trust_dialog: Any = None
        # Other clients on our channel, id -> connection_type ("master"
        # or "slave"), kept from channel_joined / client_joined /
        # client_left for the spoken status. Written on the asyncio
        # thread, read on GLib; whole-object swaps keep reads safe.
        self._channel_peers: dict[int, str] = {}
        # Latest transport status line ("reconnecting in 30s", ...).
        self._last_transport_status: str = ""
        # (SpeechServer class, original speak_character, original
        # speak_key_event) while _hook_character_speech is active.
        self._char_speech_patch: "tuple[Any, Any, Any] | None" = None
        self._keyboard_patch: "tuple[Any, Any] | None" = None
        # Master-mode grab state, active while focused-on-remote.
        # Prefer Orca's own AT-SPI device because those grabs consume
        # reliably on X11; fallback to vendored KeysetGrab.
        self._master_grab_ids: "list[int] | None" = None
        self._master_grab: "KeysetGrab | None" = None
        super().__init__()

    # ---- lifecycle -------------------------------------------------
    #
    # The framework calls set_up_commands() at load time (and again
    # when the extension is re-enabled via the prefs panel, after
    # clearing _disabled / _commands_initialized). disable() is
    # called when the user toggles us off. There is no enable() hook
    # in the base class, so all startup work hangs off
    # set_up_commands.

    def set_up_commands(self) -> None:
        """Register commands, then start the transport if the user
        was connected at last shutdown.

        We always spin up the asyncio thread so a later Orca+Ctrl+
        Page Up can dial without re-entering setup, but we only
        auto-dial when the persisted auto-connect flag says the user
        had an active session before quitting. Explicit disconnect
        (Orca+Ctrl+Page Down) flips that flag off and it stays off
        across Orca restarts.
        """

        super().set_up_commands()
        if self._disabled:
            return
        self._start_loop_thread()
        if bool(self._settings.get(SETTING_AUTO_CONNECT, True)):
            self._log("auto-connect enabled; starting transport")
            self._restart_transport()
        else:
            self._log("auto-connect disabled; waiting for explicit connect")
        # Mirror outbound speech in host mode. Which mechanism exists
        # depends on the Orca build: perf-branch builds expose the
        # subscribe_speech_emitted controller hook, released 51.1+ call
        # Extension.on_speech_output (implemented below). Never both,
        # or the master hears every utterance twice.
        if _HAVE_ON_SPEECH_OUTPUT:
            self._log("host speech mirroring via Extension.on_speech_output")
        else:
            try:
                self.controller.subscribe_speech_emitted(self._on_speech_emitted)
            except AttributeError:
                self._log(
                    "this Orca has neither on_speech_output nor "
                    "subscribe_speech_emitted; host mode will not "
                    "mirror speech"
                )
        self._hook_character_speech()
        # Same split for braille: perf builds expose the controller
        # hook, released 51.1 calls Extension.on_braille_output
        # (implemented below). Never both, or the master would see
        # every refresh twice.
        if _HAVE_ON_BRAILLE_OUTPUT:
            self._log("host braille mirroring via Extension.on_braille_output")
        else:
            try:
                self.controller.subscribe_braille_emitted(self._on_braille_emitted)
            except AttributeError:
                self._log(
                    "this Orca has neither on_braille_output nor "
                    "subscribe_braille_emitted; host mode will not "
                    "mirror braille"
                )
        # And keyboard events for master-side key forwarding. The
        # controller hook exists only on perf builds; released Orca
        # (51.1) gives extensions no keyboard hook at all, so fall
        # back to patching KeyboardEvent.process -- the interception
        # point the legacy implementation uses -- and feed events to
        # _on_keyboard_event ourselves.
        try:
            self.controller.subscribe_keyboard_event(self._on_keyboard_event)
        except AttributeError:
            if not self._hook_keyboard_process():
                self._log(
                    "controller has no subscribe_keyboard_event and "
                    "KeyboardEvent.process could not be patched; "
                    "master-side keys unavailable"
                )

    def disable(self) -> None:
        """Stop the transport, then deregister commands."""

        self._log("disabling")
        if self._dropped_nonstring_items:
            self._log(
                f"dropped {self._dropped_nonstring_items} non-string "
                f"sequence item(s) over this session (LangChange / "
                f"IndexCommand / etc.)"
            )
            self._dropped_nonstring_items = 0
        self._announced_join = False
        self._peer_not_connected_announced = False
        self._last_outbound_speech = ""
        self._last_outbound_braille = ("", -2)
        self._sent_braille_info = False
        try:
            self.controller.unsubscribe_speech_emitted(self._on_speech_emitted)
        except AttributeError:
            pass
        self._unhook_character_speech()
        try:
            self.controller.unsubscribe_braille_emitted(self._on_braille_emitted)
        except AttributeError:
            pass
        try:
            self.controller.unsubscribe_keyboard_event(self._on_keyboard_event)
        except AttributeError:
            pass
        self._unhook_keyboard_process()
        # Release the master-mode KeysetGrab if one is held. Safe to
        # call when nothing's held (no-op).
        self._disable_master_grab()
        self._stop_transport()
        # Belt-and-braces: _stop_transport already drains held keys,
        # but if there was no transport to stop (e.g. extension toggled
        # off without ever connecting) we still want this to fire.
        self._release_held_keys()
        self._stop_loop_thread()
        super().disable()

    def _localized_character_names(self) -> "dict[str, str] | None":
        """This Orca's localized chnames table, or None when unavailable.

        Cached after the first lookup (success or failure): the table
        is static for the life of the Orca process.
        """

        cached = getattr(self, "_character_names_cache", None)
        if cached is not None:
            return cached or None
        names: dict[str, str] = {}
        try:
            from orca import chnames  # pylint: disable=import-outside-toplevel

            table = getattr(chnames, "chnames", None)
            if isinstance(table, dict):
                names = {k: v for k, v in table.items() if v}
        except Exception:  # pylint: disable=broad-except
            names = {}
        self._character_names_cache = names
        return names or None

    def _on_speech_emitted(self, text: str, voice_type: str, language: str) -> None:
        """Forward Orca's outbound speech to the remote master when in host mode.

        Called from whatever thread SpeechServer.speak() ran on
        (typically the GLib main thread). Marshals the actual send
        onto the asyncio loop where the transport lives.
        """

        # No-op unless we're a host and the transport is live.
        if self._current_role() != ROLE_HOST:
            return
        if self._transport is None or self._loop is None:
            return
        if not text:
            return
        # Issue #5: a lone character (character navigation, key echo)
        # must go out as its spoken name -- NVDA is silent for a raw
        # " " or lone punctuation. Localized via this Orca's own
        # chnames table when it has one.
        text = protocol.expand_single_character(
            text, self._localized_character_names()
        )
        # User toggle: when the menu mutes mirroring, hold the
        # transport open but stop emitting speak frames to the master.
        if not self._mirror_speech:
            return

        # Coalesce identical back-to-back utterances. Orca legitimately
        # emits the same string twice in some flows (focus-of-focus,
        # caret-moved followed by name-changed). Each duplicate becomes
        # a separate NVDA speak frame on the master, which speaks both.
        # Done here on the GLib thread so we don't race with the
        # asyncio side.
        # Only within a short window: a later identical utterance is
        # real (typing "aa", arrowing over two blank lines) and must
        # reach the master.
        now = time.monotonic()
        if (
            text == self._last_outbound_speech
            and now - self._last_outbound_speech_at < _SPEECH_DEDUPE_SECONDS
        ):
            return
        self._last_outbound_speech = text
        self._last_outbound_speech_at = now

        # NVDA Remote's speak message: {"type":"speak","sequence":[...]}
        # where sequence entries are either strings (text) or dicts
        # (speech commands). We forward a single text fragment; richer
        # ACSS / index marks can come later.
        message = {"type": protocol.MSG_SPEAK, "sequence": [text]}
        self._schedule_send(message, what="speech")

    def on_speech_output(self, output: Any) -> None:
        """Mirror an utterance on Orca builds with the extension output hook.

        Released Orca (51.1+) calls this on every enabled Extension
        before speech reaches the provider; it stands in for the
        perf-branch `subscribe_speech_emitted` path. Returning None
        leaves local speech untouched -- we only observe.
        """

        self._on_speech_emitted(str(getattr(output, "text", "") or ""), "", "")
        return None

    def on_braille_output(self, output: Any) -> None:
        """Mirror a braille refresh on Orca builds with the output hook.

        Released Orca (51.1+) calls this on every enabled Extension
        before braille reaches the display; it stands in for the
        perf-branch `subscribe_braille_emitted` path. The output's
        regions carry the line's text in order; the cursor cell is
        the focused region's offset within the joined line. Returning
        None leaves local braille untouched -- we only observe.
        """

        regions = list(getattr(output, "regions", ()) or ())
        focused = getattr(output, "focused_region", None)
        text = ""
        cursor = -1
        for region in regions:
            if region is focused:
                cursor = len(text) + int(getattr(region, "cursor_offset", 0) or 0)
            text += str(getattr(region, "string", "") or "")
        self._on_braille_emitted(text, cursor)
        return None

    def _hook_keyboard_process(self) -> bool:
        """Feed master-side key forwarding on Orca builds without the hook.

        Released Orca (51.1) gives extensions no keyboard-event
        subscription: `subscribe_keyboard_event` exists only on perf
        builds, so without this fallback a released-Orca master could
        connect and hear the host but never send it a single key.
        Patch `KeyboardEvent.process` -- the interception point the
        legacy implementation uses -- and route each event through
        `_on_keyboard_event`, whose return value keeps its perf-hook
        meaning: True consumes the event (the original process()
        never runs, so Orca does not dispatch it locally), False lets
        the original run. Returns False when the patch cannot be
        installed.
        """

        if getattr(self, "_keyboard_patch", None) is not None:
            return True
        try:
            from orca import input_event  # pylint: disable=import-outside-toplevel

            cls = input_event.KeyboardEvent
            orig_process = cls.process
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"keyboard forwarding patch unavailable: {error}")
            return False
        ext = self

        def process(event_self: Any) -> Any:
            try:
                consumed = ext._on_keyboard_event(
                    bool(event_self.is_pressed_key()),
                    int(getattr(event_self, "hw_code", 0) or 0),
                    int(getattr(event_self, "id", 0) or 0),
                    int(getattr(event_self, "modifiers", 0) or 0),
                    str(getattr(event_self, "_text", "") or ""),
                )
            except Exception:  # pylint: disable=broad-except
                consumed = False
            if consumed:
                return True
            return orig_process(event_self)

        cls.process = process  # type: ignore[method-assign]
        self._keyboard_patch = (cls, orig_process)
        self._log(
            "master-side keys via KeyboardEvent.process patch "
            "(this Orca has no subscribe_keyboard_event)"
        )
        return True

    def _unhook_keyboard_process(self) -> None:
        if getattr(self, "_keyboard_patch", None) is None:
            return
        cls, orig_process = self._keyboard_patch
        cls.process = orig_process
        self._keyboard_patch = None

    def _hook_character_speech(self) -> None:
        """Mirror single characters, which bypass speech_emitted.

        Orca emits speech_emitted only from SpeechServer.speak().
        speak_character() and one-character speak_key_event() go
        straight to speech-dispatcher's char(), so the master heard
        nothing while the host typed or arrowed by character (seen
        live on orca-perf 51.alpha). If Orca starts emitting these
        itself, the 0.3s dedupe swallows the second copy.
        """

        if self._char_speech_patch is not None:
            return
        try:
            from orca import speechdispatcherfactory  # pylint: disable=import-outside-toplevel
            cls = speechdispatcherfactory.SpeechServer
            orig_char = cls.speak_character
            orig_key = cls.speak_key_event
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"character speech mirroring unavailable: {error}")
            return
        ext = self

        def speak_character(server: Any, character: str, *args: Any, **kwargs: Any) -> None:
            orig_char(server, character, *args, **kwargs)
            if character:
                ext._on_speech_emitted(character, "", "")

        def speak_key_event(server: Any, event: Any, *args: Any, **kwargs: Any) -> None:
            orig_key(server, event, *args, **kwargs)
            try:
                name = f"{event.get_key_name()} {event.get_locking_state_string()}".strip()
            except Exception:  # pylint: disable=broad-except
                return
            if len(name) == 1:  # longer names already went through speak()
                ext._on_speech_emitted(name, "", "")

        cls.speak_character = speak_character
        cls.speak_key_event = speak_key_event
        self._char_speech_patch = (cls, orig_char, orig_key)

    def _unhook_character_speech(self) -> None:
        if self._char_speech_patch is None:
            return
        cls, orig_char, orig_key = self._char_speech_patch
        cls.speak_character = orig_char
        cls.speak_key_event = orig_key
        self._char_speech_patch = None

    def _on_keyboard_event(
        self,
        pressed: bool,
        keycode: int,
        keysym: int,
        modifiers: int,
        text: str,
    ) -> bool:
        """Master-side key forwarding hook.

        Called from perf-branch input_event_manager BEFORE Orca's
        own command dispatch. Returns True to consume from Orca's
        perspective (Orca skips event.process() for this event).

        Behavior:
        - Track Orca-modifier (Insert / Caps_Lock) press/release into
          self._master_orca_mod_held so the bypass check below can tell
          a local Orca chord from regular typing. The modifier keys
          themselves are still forwarded so the remote screen reader
          sees Insert/Caps_Lock state come through.
        - Inactive unless role=client AND _focus_on_remote AND a
          live transport. Otherwise returns False (passthrough).
        - Bypass-chord list: when the user holds Orca-mod and presses
          one of our own command chords (Insert+Ctrl+R / Ctrl+Shift+M /
          Ctrl+PgUp / Ctrl+PgDn / Alt+Tab), return False so Orca
          dispatches the binding locally. The component modifier
          keystrokes (Insert, Ctrl, Alt) are STILL forwarded; only
          the trigger keysym is suppressed. The remote screen reader
          sees the modifier prefix without the trigger and treats it
          as a no-op.
        - Everything else maps via keysym_to_vk and is sent as an
          NVDA Remote v2 `key` frame with (vk_code, extended,
          pressed). Unmapped keysyms (keysym_to_vk returns (0, ...))
          pass through to Orca so an exotic key isn't silently
          dropped.

        Full-consume model (0.7.0+): while focused-on-remote is
        active, `_enable_master_grab` registers a KeysetGrab over
        `forwardable_keysyms()`. That grab takes the keys off the
        focused application's AT-SPI delivery, so forwarded keys
        only act on the remote machine -- not also on the focused
        local app. The grab's callback is a no-op consume; the
        actual forwarding still happens here (input_event_manager
        still fires this hook even for AT-SPI-grabbed events).

        Compositor coverage: KeysetGrab works on X11 (Xorg). On
        Wayland it works for compositors that honor AT-SPI grabs;
        for those that don't, `_master_grab.failed_keysyms` lists
        the rejected pairs and the focused app continues to see
        those keys (degrades to pre-0.7.0 Orca-dispatch-only
        consume for the unaccepted subset).
        """

        # Track Orca-mod hold state. Counter (not bool) so two
        # Orca-mod keys held together (Insert + KP_Insert) balance.
        # Fall through to the normal forwarding path: the Orca-mod
        # keys themselves SHOULD reach the remote so its screen
        # reader sees Insert/Caps_Lock in its own modifier state.
        if pressed:
            # A new key is a new utterance even if it repeats the last
            # one (fast "ll", Return Return); end the dedupe window.
            self._last_outbound_speech_at = 0.0
        if keysym in _ORCA_MOD_KEYSYMS:
            if pressed:
                self._master_orca_mods_down.add(keysym)
            else:
                self._master_orca_mods_down.discard(keysym)

        # Cheap guards: non-forwarding sessions pay no further cost.
        if self._current_role() != ROLE_CLIENT:
            return False
        if not self._focus_on_remote:
            return False
        if self._transport is None:
            return False

        # Bypass: if the user is holding Orca-mod and this is one of
        # our own command chords, don't forward -- Orca's dispatcher
        # will fire the matching binding locally. Everything else
        # (including Insert+Down for sayAll on the remote, etc.) is
        # forwarded so the remote screen reader can act on it.
        if self._is_local_bypass_chord(keysym, modifiers):
            return False

        vk_code, extended = keysym_to_vk(keysym)
        if vk_code == 0:
            # Unmapped; pass through to Orca so an exotic key isn't
            # silently dropped.
            return False

        key = (vk_code, extended)
        if pressed:
            self._forwarded_keys_down.add(key)
        elif key in self._forwarded_keys_down:
            self._forwarded_keys_down.discard(key)
        else:
            # Pressed while focused local (e.g. the Insert+Alt that
            # entered remote mode); the remote never saw the PRESS.
            return True

        self._schedule_send(
            {
                "type": protocol.MSG_KEY,
                "vk_code": vk_code,
                "extended": extended,
                "pressed": pressed,
                "scan_code": 0,
            },
            what="key",
        )
        return True

    def _is_local_bypass_chord(self, keysym: int, modifiers: int) -> bool:
        """True iff (Orca-mod held) + (modifier) + (keysym) names a local command.

        Master-side bypass for orca-remote's own keybindings. When
        the user holds Insert and presses Ctrl+R / Ctrl+Shift+M / Ctrl+PgUp
        / Ctrl+PgDn / Alt+Tab, we don't want to forward those to the
        remote -- they're local control commands and should fire
        Orca's binding for the registered handler instead.

        Modifier bits come from the AT-SPI event: Ctrl = 0x04,
        Alt = 0x08. The Orca-modifier (Insert / Caps_Lock) isn't a
        true X11 modifier bit; we track its press/release ourselves
        in _master_orca_mods_down.
        """

        if not self._master_orca_mods_down:
            return False
        if (
            keysym in _OWN_CTRL_SHIFT_CHORD_KEYSYMS
            and (modifiers & _AT_SPI_MOD_CTRL)
            and (modifiers & _AT_SPI_MOD_SHIFT)
        ):
            return True
        if keysym in _OWN_CTRL_CHORD_KEYSYMS and (modifiers & _AT_SPI_MOD_CTRL):
            return True
        if keysym in _OWN_ALT_CHORD_KEYSYMS and (modifiers & _AT_SPI_MOD_ALT):
            return True
        return False

    def _on_braille_emitted(self, text: str, cursor_cell: int) -> None:
        """Forward Orca's braille refresh to the remote master.

        Host-mode only. `text` is the rendered braille string,
        `cursor_cell` is 0-based (or -1 if no cursor).

        NVDA Remote's `display` payload is a list of cell bytes (one
        per cell, low byte = dots 1..8). We translate text -> cells
        via braille_table.text_to_cells; see that module's docstring
        for the limitations (English-only, lossy for other scripts).
        """

        if self._current_role() != ROLE_HOST:
            return
        if self._transport is None or self._loop is None:
            return
        if not self._mirror_braille:
            return

        cells = braille_table.text_to_cells(text)
        # Skip empty refreshes (e.g. a paint with no content yet).
        if not cells:
            return
        # Drop unchanged frames. braille_emitted fires on EVERY paint
        # including ones where nothing changed (e.g. caret-but-same-line
        # refreshes). The master sees no value in identical frames.
        key = (text, cursor_cell)
        if key == self._last_outbound_braille:
            return
        self._last_outbound_braille = key

        # Send dimensions to the master once per session so its braille
        # viewer knows the column count. set_braille_info is sticky on
        # NVDA's side; resending on every paint would just be noise.
        if not self._sent_braille_info:
            self._schedule_send(
                {
                    "type": protocol.MSG_SET_BRAILLE_INFO,
                    "name": "orca",
                    "numCells": len(cells),
                },
                what="braille-info",
            )
            self._sent_braille_info = True

        self._schedule_send(
            {"type": protocol.MSG_DISPLAY, "cells": cells},
            what="braille",
        )

    def _schedule_send(self, message: dict, *, what: str) -> None:
        """Schedule a fire-and-forget send onto the asyncio loop.

        `what` is a short tag used in the failure log line so a user
        looking at the Orca debug log can tell which kind of message
        failed (speech vs cancel vs key vs clipboard). The future is
        watched via add_done_callback so transport-side exceptions
        aren't silently swallowed.
        """

        if self._transport is None or self._loop is None:
            return
        transport = self._transport

        async def _send() -> None:
            await transport.send(message)

        try:
            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        except RuntimeError:
            # Loop was closed between the check and the schedule. Drop.
            return

        def _on_done(fut: Any) -> None:
            try:
                exc = fut.exception()
            except Exception:  # pylint: disable=broad-except
                return
            if exc is not None:
                self._log(f"outbound {what} send failed: {exc!r}")

        future.add_done_callback(_on_done)

    # ---- command registration -------------------------------------
    #
    # Orca+Ctrl+R       open Orca Remote settings dialog
    # Orca+Ctrl+Shift+M toggle inbound speech mute (master-side)
    # Orca+Ctrl+PageUp  connect (no-op if already connected)
    # Orca+Ctrl+PageDn  disconnect (no-op if already disconnected)
    # Orca+Ctrl+Shift+C push local clipboard to the peer
    # Orca+Alt+S        speak connection status
    # Orca+Alt+Tab      switch_side -- toggle focus_on_remote (grab +
    #                   hearing together)
    #
    # The menu-based action list was removed in 0.8.0; the bypass-chord
    # mechanism in _on_keyboard_event ensures these five chords still
    # reach Orca's dispatcher even while master-mode forwarding is on.

    def _get_commands(self) -> list[Command]:
        # Not every Orca build exports the combined Orca+Ctrl / Orca+Alt
        # masks; fall back to composing them like the ctrl_shift case
        # below, so command registration never raises during startup.
        ctrl = getattr(
            keybindings,
            "ORCA_CTRL_MODIFIER_MASK",
            keybindings.ORCA_MODIFIER_MASK | keybindings.CTRL_MODIFIER_MASK,
        )
        alt = getattr(
            keybindings,
            "ORCA_ALT_MODIFIER_MASK",
            keybindings.ORCA_MODIFIER_MASK | keybindings.ALT_MODIFIER_MASK,
        )
        ctrl_shift = getattr(
            keybindings,
            "ORCA_CTRL_SHIFT_MODIFIER_MASK",
            (
                keybindings.ORCA_MODIFIER_MASK
                | keybindings.CTRL_MODIFIER_MASK
                | keybindings.SHIFT_MODIFIER_MASK
            ),
        )
        return [
            KeyboardCommand(
                "orcaRemoteOpenSettingsHandler",
                self.open_settings,
                self.GROUP_LABEL,
                "Open Orca Remote settings",
                desktop_keybinding=keybindings.KeyBinding("r", ctrl),
                laptop_keybinding=keybindings.KeyBinding("r", ctrl),
            ),
            KeyboardCommand(
                "orcaRemoteMuteInboundHandler",
                self.mute_inbound_toggle,
                self.GROUP_LABEL,
                "Toggle Orca Remote inbound mute",
                desktop_keybinding=keybindings.KeyBinding("m", ctrl_shift),
                laptop_keybinding=keybindings.KeyBinding("m", ctrl_shift),
            ),
            KeyboardCommand(
                "orcaRemoteConnectHandler",
                self.connect,
                self.GROUP_LABEL,
                "Connect Orca Remote",
                desktop_keybinding=keybindings.KeyBinding("Page_Up", ctrl),
                laptop_keybinding=keybindings.KeyBinding("Page_Up", ctrl),
            ),
            KeyboardCommand(
                "orcaRemoteDisconnectHandler",
                self.disconnect_session,
                self.GROUP_LABEL,
                "Disconnect Orca Remote",
                desktop_keybinding=keybindings.KeyBinding("Page_Down", ctrl),
                laptop_keybinding=keybindings.KeyBinding("Page_Down", ctrl),
            ),
            KeyboardCommand(
                "orcaRemotePushClipboardHandler",
                self.push_clipboard,
                self.GROUP_LABEL,
                "Push Orca Remote clipboard",
                desktop_keybinding=keybindings.KeyBinding("c", ctrl_shift),
                laptop_keybinding=keybindings.KeyBinding("c", ctrl_shift),
            ),
            KeyboardCommand(
                "orcaRemoteStatusHandler",
                self.speak_status,
                self.GROUP_LABEL,
                "Speak Orca Remote connection status",
                desktop_keybinding=keybindings.KeyBinding("s", alt),
                laptop_keybinding=keybindings.KeyBinding("s", alt),
            ),
            KeyboardCommand(
                "orcaRemoteSwitchSideHandler",
                self.switch_side,
                self.GROUP_LABEL,
                "Switch between host and remote machine",
                desktop_keybinding=keybindings.KeyBinding("Tab", alt),
                laptop_keybinding=keybindings.KeyBinding("Tab", alt),
            ),
        ]

    def connect(self) -> bool:
        # Explicit connect: remember the intent so the next Orca
        # restart re-dials automatically.
        self._set_auto_connect(True)
        if self._transport is not None and self._transport.connected:
            self._say("Orca Remote already connected.")
            return True
        # Not connected, or still retrying / parked on a fingerprint
        # mismatch: dial again now instead of claiming success.
        # User asked to connect -- they should hear the next join.
        self._announced_join = False
        self._peer_not_connected_announced = False
        self._say("Orca Remote: connecting.")
        self._restart_transport()
        return True

    def disconnect_session(self) -> bool:
        # Explicit disconnect: clear the intent so the next Orca
        # restart stays offline until the user reconnects.
        self._set_auto_connect(False)
        # Whatever happens next, the next join should announce again.
        self._announced_join = False
        self._peer_not_connected_announced = False
        if self._transport is None:
            self._say("Orca Remote already disconnected.")
            return True
        self._say("Orca Remote: disconnecting.")
        self._stop_transport()
        return True

    def _set_auto_connect(self, value: bool) -> None:
        if bool(self._settings.get(SETTING_AUTO_CONNECT, True)) == value:
            return
        self._settings[SETTING_AUTO_CONNECT] = value
        self._save_settings()

    def switch_side(self) -> bool:
        """Toggle the master's focus between remote and local.

        Client (master) only. Entering focused-on-remote mode:
          - activates KeysetGrab (forwarded keys stop hitting local apps)
          - inbound speech becomes audible (unless _inbound_speech_muted)
        Leaving focused-on-remote mode:
          - releases the KeysetGrab
          - inbound speech is dropped regardless of mute state

        Bound to Insert+Alt+Tab. On the slave (host), this is a
        silent no-op -- matching how NVDA Remote ignores F11-
        equivalents on the controlled machine.

        Announcement always reflects current mute state when
        entering remote so the user knows whether they'll hear
        anything ("focused on remote machine, muted" vs "focused
        on remote machine"). Leaving doesn't include mute because
        the mute flag is independent and persists across toggles.
        """

        if self._current_role() != ROLE_CLIENT:
            return True  # slave: silent no-op

        self._focus_on_remote = not self._focus_on_remote
        if self._focus_on_remote:
            self._enable_master_grab()
            if self._inbound_speech_muted:
                self._say("Orca Remote: focused on remote machine. Muted.")
            else:
                self._say("Orca Remote: focused on remote machine.")
        else:
            self._release_forwarded_keys()
            self._disable_master_grab()
            self._say("Orca Remote: focused on local machine.")
        return True

    def _release_forwarded_keys(self) -> None:
        """Send RELEASE for every key the remote still thinks is held."""

        for vk_code, extended in sorted(self._forwarded_keys_down):
            self._schedule_send(
                {
                    "type": protocol.MSG_KEY,
                    "vk_code": vk_code,
                    "extended": extended,
                    "pressed": False,
                    "scan_code": 0,
                },
                what="key-release",
            )
        self._forwarded_keys_down.clear()

    def _leave_remote_focus(self) -> None:
        """Drop focused-on-remote silently because the transport is going away.

        The grab would otherwise outlive the connection and swallow
        every forwardable key from local apps until the user pressed
        Orca+Alt+Tab again.
        """

        self._release_forwarded_keys()
        self._disable_master_grab()
        self._focus_on_remote = False

    def speak_status(self) -> bool:
        """Speak the connection state. Bound to Orca+Alt+S."""

        self._say(self._status_report())
        return True

    def _status_report(self) -> str:
        """One spoken summary: connection, role, peers, focus, mute."""

        host = str(self._settings.get(SETTING_HOST, "") or "")
        port = int(self._settings.get(SETTING_PORT, 0) or 0)
        channel = str(self._settings.get(SETTING_CHANNEL, "") or "")
        role = self._current_role()
        if not host or port <= 0 or not channel:
            return "Orca Remote is not set up. Press Orca+Ctrl+R to open settings."
        server = host if port == DEFAULT_SETTINGS.get(SETTING_PORT) else f"{host} port {port}"
        role_text = "host" if role == ROLE_HOST else "client"

        transport = self._transport
        if transport is None:
            return (
                f"Orca Remote is disconnected from {server}. "
                "Press Orca+Ctrl+Page Up to connect."
            )
        if self._trust_dialog is not None:
            return f"Orca Remote is waiting for you to trust {server}."
        if not transport.connected:
            status = self._last_transport_status
            detail = f" Last status: {status}." if status else ""
            return f"Orca Remote is not connected to {server}.{detail}"

        parts = [f"Orca Remote is connected to {server} as {role_text}."]
        peers = list(self._channel_peers.values())
        if role == ROLE_HOST:
            masters = sum(1 for kind in peers if kind == CONNECTION_TYPE_MASTER)
            if masters == 0:
                parts.append("No one is controlling this machine.")
            elif masters == 1:
                parts.append("One person can control this machine.")
            else:
                parts.append(f"{masters} people can control this machine.")
        else:
            slaves = sum(1 for kind in peers if kind == CONNECTION_TYPE_SLAVE)
            if slaves == 0:
                parts.append("The remote machine is not connected.")
            elif slaves == 1:
                parts.append("The remote machine is connected.")
            else:
                parts.append(f"{slaves} remote machines are connected.")
            if self._focus_on_remote:
                parts.append("You are controlling the remote machine.")
            else:
                parts.append("You are controlling this machine.")
            if self._inbound_speech_muted:
                parts.append("Remote speech is muted.")
        return " ".join(parts)

    def _note_channel_joined(self, message: dict) -> None:
        peers: dict[int, str] = {}
        for client in message.get("clients") or []:
            if isinstance(client, dict) and isinstance(client.get("id"), int):
                peers[client["id"]] = str(client.get("connection_type", ""))
        if not peers:
            for user_id in message.get("user_ids") or []:
                if isinstance(user_id, int):
                    peers[user_id] = ""
        self._channel_peers = peers

    def _note_client_joined(self, message: dict) -> None:
        client = message.get("client") if isinstance(message.get("client"), dict) else {}
        user_id = client.get("id", message.get("user_id"))
        if isinstance(user_id, int):
            peers = dict(self._channel_peers)
            peers[user_id] = str(client.get("connection_type", ""))
            self._channel_peers = peers

    def _note_client_left(self, message: dict) -> None:
        client = message.get("client") if isinstance(message.get("client"), dict) else {}
        user_id = message.get("user_id", client.get("id"))
        if isinstance(user_id, int) and user_id in self._channel_peers:
            peers = dict(self._channel_peers)
            peers.pop(user_id, None)
            self._channel_peers = peers

    def mute_inbound_toggle(self) -> bool:
        """Toggle the master-side inbound-speech mute. Bound to Orca+Ctrl+Shift+M.

        Independent of focus_on_remote: muting while focused-on-local
        is a no-op for hearing (we already weren't), but the flag
        sticks so the next switch_side back to remote stays quiet.
        Client only; host has no inbound speech to mute (silent no-op).
        """

        if self._current_role() != ROLE_CLIENT:
            return True
        self._inbound_speech_muted = not self._inbound_speech_muted
        if self._inbound_speech_muted:
            self._say("Orca Remote: remote muted.")
        else:
            self._say("Orca Remote: remote unmuted.")
        return True

    def _enable_master_grab(self) -> None:
        """Take ownership of forwardable keys at the AT-SPI level."""

        if self._master_grab_ids is not None or self._master_grab is not None:
            return
        if self._enable_master_grab_via_orca():
            return
        if not _HAVE_KEYSET_GRAB or KeysetGrab is None:
            self._log(
                "no AT-SPI grab available (orca device unreachable and "
                "vendored ext-utils missing); forwarded keys will also "
                "reach the focused local app"
            )
            return
        keysyms = forwardable_keysyms()
        grab = KeysetGrab(keysyms)
        try:
            grab.__enter__()
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"KeysetGrab.__enter__ raised: {error}")
            return
        # The grab callback fires on AT-SPI key events for grabbed
        # keys. Forwarding still happens via `_on_keyboard_event`
        # (which Orca's input_event_manager calls); the grab's only
        # job here is to prevent the focused local app from also
        # seeing the key, so the callback just consumes. Returning
        # True from the registered callback marks the event as
        # consumed at the AT-SPI level.
        grab.register(lambda _event: True)
        self._master_grab = grab
        held = len(keysyms) * len(grab._modifier_combos) - len(grab.failed_keysyms)
        refused = len(grab.failed_keysyms)
        self._log(
            f"master grab active (fallback KeysetGrab): {held} held, "
            f"{refused} refused"
        )
        # Partial coverage is common on Wayland compositors that don't
        # fully honor AT-SPI key grabs. The refused count goes to the
        # log but NOT to speech -- speaking it on every switch_side
        # was too chatty (and the count varies between activations
        # depending on transient grab-table state, which is confusing).
        # Users who want to know their coverage can grep the log.

    def _enable_master_grab_via_orca(self) -> bool:
        """Grab forwardable keysyms on Orca's own AT-SPI device."""

        try:
            from orca import ax_device_manager  # pylint: disable=import-outside-toplevel
            import gi  # pylint: disable=import-outside-toplevel
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # pylint: disable=import-outside-toplevel
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"orca-device grab unavailable: {error}")
            return False

        try:
            manager = ax_device_manager.get_manager()
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"orca-device grab: get_manager failed: {error}")
            return False
        try:
            active = bool(manager is not None and manager.is_active())
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"orca-device grab: is_active failed: {error}")
            return False
        if not active:
            self._log("orca-device grab: device manager not active")
            return False

        grab_ids: list[int] = []
        refused = 0
        for keysym in forwardable_keysyms():
            for modifier in _MASTER_GRAB_MODIFIERS:
                kd = Atspi.KeyDefinition()
                kd.keysym = keysym
                kd.modifiers = modifier
                kd.keycode = 0
                try:
                    grab_id = manager.add_key_grab(kd)
                except Exception:  # pylint: disable=broad-except
                    grab_id = 0
                if grab_id:
                    grab_ids.append(grab_id)
                else:
                    refused += 1
        if not grab_ids:
            self._log("orca-device grab: no grabs accepted; falling back")
            return False
        self._master_grab_ids = grab_ids
        self._log(
            f"master grab active (orca device): {len(grab_ids)} held, "
            f"{refused} refused"
        )
        return True

    def _disable_master_grab(self) -> None:
        """Release the master-mode grab if one is held."""

        if self._master_grab_ids is not None:
            try:
                from orca import ax_device_manager  # pylint: disable=import-outside-toplevel
                manager = ax_device_manager.get_manager()
                for grab_id in self._master_grab_ids:
                    try:
                        manager.remove_key_grab(grab_id)
                    except Exception:  # pylint: disable=broad-except
                        pass
            except Exception as error:  # pylint: disable=broad-except
                self._log(f"orca-device grab release failed: {error}")
            self._master_grab_ids = None

        if self._master_grab is not None:
            try:
                self._master_grab.release()
            except Exception as error:  # pylint: disable=broad-except
                self._log(f"KeysetGrab.release raised: {error}")
            self._master_grab = None

    def open_settings(self) -> bool:
        """Show the settings dialog. Bound to Insert+Ctrl+R.

        Destroy-and-rebuild rather than present-existing: focus loss
        on the existing dialog tends to leave it alive but unreachable
        on marco / wayland-flagged sessions, and Gtk.Window.present()
        doesn't reliably re-raise it. Destroying first guarantees the
        user sees a freshly-mapped, focused window every time.
        """

        if self._settings_dialog is not None:
            try:
                self._settings_dialog.destroy()
            except Exception:  # pylint: disable=broad-except
                pass
            self._settings_dialog = None
        self._settings_dialog = build_settings_dialog(
            dict(self._settings), self._on_settings_result,
        )
        return True

    def _on_settings_result(self, result: dict[str, Any] | None) -> None:
        """Apply the dict the settings dialog returned (or do nothing).

        Invoked from the GLib main loop when the user closes the
        dialog. `result` is None on cancel / window-close.
        """

        # Clear singleton ref so the next Orca+Ctrl+R opens a fresh
        # dialog. settings_dialog.build_settings_dialog destroys the
        # widget before invoking us, so the ref is guaranteed stale.
        self._settings_dialog = None
        if result is None:
            return
        result = dict(result)
        rejected_fingerprint = self._reject_bad_fingerprint(result)
        changed = False
        for key, value in result.items():
            if self._settings.get(key) != value:
                self._settings[key] = value
                changed = True
        if changed:
            self._save_settings()
            if rejected_fingerprint:
                self._say(
                    "Orca Remote settings saved, except the fingerprint: it "
                    f"was {rejected_fingerprint} characters, not 64. Select the "
                    "old value with Control+A before pasting the new one."
                )
            else:
                self._say("Orca Remote settings saved.")
            if not self._disabled:
                # Settings change -> connect-affecting restart; the
                # user deserves to hear the new join announce. Saving
                # dials, so remember that intent too: otherwise a save
                # made after Orca+Ctrl+Page Down leaves a live session
                # that silently stays offline after the next Orca start.
                self._set_auto_connect(True)
                self._announced_join = False
                self._peer_not_connected_announced = False
                self._restart_transport()

    def _reject_bad_fingerprint(self, result: dict[str, Any]) -> int:
        """Normalize the dialog's fingerprint, dropping a malformed value.

        Returns the length of a rejected value, or 0 when the value is
        usable (or empty). The dialog pre-fills the field with the
        stored pin, so pasting without selecting first appends to it and
        yields a value that can never match; without this check the
        connection just reported "fingerprint did not match" forever.
        """

        normalized = _normalize_fingerprint(str(result.get(SETTING_FINGERPRINT, "")))
        if normalized and len(normalized) != _FINGERPRINT_CHARS:
            result.pop(SETTING_FINGERPRINT, None)
            return len(normalized)
        result[SETTING_FINGERPRINT] = normalized
        return 0

    def _get_setting(self, key: str) -> Any:
        return self._settings.get(key, DEFAULT_SETTINGS.get(key))

    def _set_setting(self, key: str, value: Any) -> None:
        if self._settings.get(key) == value:
            return
        self._settings[key] = value
        self._save_settings()
        # Reconnect if the change affects the transport. Role is
        # connect-affecting because the join message's connection_type
        # changes (master vs slave).
        if key in (
            SETTING_HOST, SETTING_PORT, SETTING_CHANNEL,
            SETTING_FINGERPRINT, SETTING_ROLE,
        ):
            if not self._disabled:
                self._set_auto_connect(True)
                self._announced_join = False
                self._peer_not_connected_announced = False
                self._restart_transport()

    # ---- settings persistence -------------------------------------

    def _load_settings(self) -> None:
        path = _settings_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            self._log(f"settings load failed ({error}); using defaults")
            return
        if isinstance(loaded, dict):
            for key, default in DEFAULT_SETTINGS.items():
                value = loaded.get(key, default)
                # Coerce port back to int in case the JSON stored a string.
                if key == SETTING_PORT:
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        value = default
                self._settings[key] = value

    def _save_settings(self) -> None:
        path = _settings_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Write with restrictive perms BEFORE writing the content,
            # so the channel key (a shared passphrase) is never on disk
            # at default umask. os.open + fdopen avoids the window
            # where open(path,"w") would create with 0644.
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(path, flags, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._settings, handle, indent=2)
            except Exception:
                # fdopen owns the fd on success; on failure we close.
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            # Tighten perms on a pre-existing file that might have been
            # created at a wider umask before this safeguard landed.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except OSError as error:
            self._log(f"settings save failed: {error}")

    # ---- asyncio thread plumbing ----------------------------------

    def _start_loop_thread(self) -> None:
        if self._loop_thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop_thread_main,
            name="orca-remote-asyncio",
            daemon=True,
        )
        self._loop_thread.start()

    def _loop_thread_main(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception:  # pylint: disable=broad-except
                pass

    def _stop_loop_thread(self) -> None:
        if self._loop is None or self._loop_thread is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=2.0)
        self._loop = None
        self._loop_thread = None

    # ---- transport orchestration ----------------------------------

    def _restart_transport(self) -> None:
        """Stop any current transport and start a fresh one."""

        if self._loop is None:
            return
        # Validate enough is configured to even try.
        channel = str(self._settings.get(SETTING_CHANNEL, "") or "")
        host = str(self._settings.get(SETTING_HOST, "") or "")
        port = int(self._settings.get(SETTING_PORT, 0) or 0)
        fingerprint = _normalize_fingerprint(
            str(self._settings.get(SETTING_FINGERPRINT, "") or ""),
        )
        role = str(self._settings.get(SETTING_ROLE, ROLE_CLIENT) or ROLE_CLIENT)
        connection_type = (
            CONNECTION_TYPE_SLAVE if role == ROLE_HOST else CONNECTION_TYPE_MASTER
        )

        # Stop existing transport (if any) before starting a new one.
        # Done in the loop thread so we don't race with its read loop.
        self._stop_transport()

        if not host or port <= 0 or not channel:
            self._say("Orca Remote not configured. Open Settings to set host, port, and channel key.")
            return
        if fingerprint and len(fingerprint) != _FINGERPRINT_CHARS:
            # A malformed pin can never match. Dialing would just loop on
            # "fingerprint did not match" with nothing to act on.
            self._log(f"stored fingerprint has {len(fingerprint)} characters, not 64")
            self._say(
                "Orca Remote: the saved fingerprint is not 64 hex characters, "
                "so the relay would refuse it. Open Settings with Orca+Ctrl+R "
                "and paste the fingerprint again, selecting the old value with "
                "Control+A first."
            )
            return

        # Assign on this thread, start on the loop. Assigning from a
        # scheduled coroutine left _transport None for a moment, so a
        # quick second restart or a disconnect missed it and the first
        # transport kept running with nothing able to stop it.
        transport = RemoteTransport(
            host=host,
            port=port,
            channel=channel,
            fingerprint=fingerprint,
            connection_type=connection_type,
            on_message=self._on_message,
            on_status=self._on_status,
            on_fingerprint_mismatch=self._on_fingerprint_mismatch,
            on_disconnected=self._on_transport_disconnected,
        )
        self._transport = transport
        self._loop.call_soon_threadsafe(transport.start)

    def _stop_transport(self) -> None:
        # Queued before the teardown below, so the releases go out first.
        self._leave_remote_focus()
        # A new session should report its own dropped-speech reasons.
        self._speak_ignored_reasons.clear()
        self._channel_peers = {}
        self._last_transport_status = ""
        if self._loop is None or self._transport is None:
            # No live transport, but we may still have a stale held-key
            # set from a previous session. Drain it anyway.
            self._release_held_keys()
            return
        transport = self._transport
        self._transport = None

        async def _teardown() -> None:
            await transport.stop()

        future = asyncio.run_coroutine_threadsafe(_teardown(), self._loop)
        try:
            future.result(timeout=2.0)
        except Exception:  # pylint: disable=broad-except
            pass
        # Release anything the remote master pressed before the link
        # went down. Idle-scheduled so it runs on the GLib thread that
        # owns AT-SPI synthesis.
        self._release_held_keys()

    # ---- callbacks (run on asyncio thread) ------------------------

    async def _on_message(self, message: dict) -> None:
        msg_type = message.get("type")
        if msg_type == protocol.MSG_SPEAK:
            # Only speak inbound utterances when we're a client, the
            # master is focused on the remote session, and the user
            # hasn't muted the remote (Orca+Ctrl+Shift+M). Mute is
            # independent of focus so a muted state persists across
            # switch_side toggles -- coming back to remote stays quiet
            # until the user explicitly unmutes.
            reason = self._inbound_speech_drop_reason()
            if reason:
                self._log_speak_ignored(reason)
                return
            text, dropped = protocol.extract_speech_text(message)
            if dropped:
                self._dropped_nonstring_items += dropped
            if text:
                self._say_async(text)
        elif msg_type in (protocol.MSG_CANCEL, protocol.MSG_PAUSE_SPEECH):
            # The peer is asking us to flush queued speech (cancel) or
            # pause (pause_speech). Screen-reader use cases treat both
            # as "shut up right now"; we don't have a pause-and-resume
            # surface so cancel-equivalent is the right behavior.
            if not self._inbound_speech_drop_reason():
                GLib.idle_add(self._interrupt_speech_idle_cb)
        elif msg_type == protocol.MSG_CHANNEL_JOINED:
            self._note_channel_joined(message)
            if self._announced_join:
                return
            self._announced_join = True
            self._peer_not_connected_announced = False
            role = self._current_role()
            if role == ROLE_HOST:
                self._say_async("Orca Remote connected in host mode.")
            else:
                self._say_async("Orca Remote connected.")
        elif msg_type == protocol.MSG_CLIENT_JOINED:
            self._note_client_joined(message)
            self._peer_not_connected_announced = False
            # A new master has no braille yet; send the next frame
            # even if it matches what the previous peer was shown.
            self._last_outbound_braille = ("", -2)
            self._sent_braille_info = False
            self._say_async("Orca Remote: peer joined.")
        elif msg_type == protocol.MSG_CLIENT_LEFT:
            self._note_client_left(message)
            self._peer_not_connected_announced = False
            # A master that drops mid-chord never sends its releases;
            # without this its Ctrl/Insert stay held on this machine.
            self._release_held_keys()
            self._say_async("Orca Remote: peer left.")
        elif msg_type == protocol.MSG_NVDA_NOT_CONNECTED:
            if not self._peer_not_connected_announced:
                self._peer_not_connected_announced = True
                self._say_async("Orca Remote: peer is not connected.")
        elif msg_type == protocol.MSG_PING:
            pass  # relay keepalive every 5 minutes; nothing to answer
        elif msg_type == protocol.MSG_MOTD:
            motd = str(message.get("motd", "")).strip()
            if motd:
                self._log(f"motd: {motd}")
        elif msg_type == protocol.MSG_KEY:
            await self._handle_inbound_key(message)
        elif msg_type == protocol.MSG_SET_CLIPBOARD_TEXT:
            text = str(message.get("text", "") or "")
            if text:
                GLib.idle_add(self._set_clipboard_idle_cb, text)
        elif msg_type == protocol.MSG_SET_BRAILLE_INFO:
            # Track peer's display dimensions so we can resize our
            # local render if/when the host machine swaps displays
            # mid-session.
            try:
                num_cells = int(message.get("numCells", 0) or 0)
            except (TypeError, ValueError):
                num_cells = 0
            name = str(message.get("name", "") or "")
            self._peer_braille_cells = num_cells
            self._log(f"peer braille info: name={name!r} cells={num_cells}")
        elif msg_type == protocol.MSG_DISPLAY:
            # Inbound braille from the peer. We render cells as the
            # equivalent Unicode braille block characters (U+2800 +
            # cell_byte) and push via controller.display_braille_text.
            # Unicode braille passthrough is the standard
            # interpretation; the local BrlAPI driver will produce
            # exactly the dot pattern the peer intended.
            #
            # Client mode only, and only while the master is
            # focused-on-remote AND not muted. Mute is meant for
            # speech but inbound braille follows the same gate for
            # consistency -- if the user can't hear the remote, the
            # remote braille shouldn't bleed onto their display
            # either.
            if (
                self._current_role() == ROLE_CLIENT
                and self._focus_on_remote
                and not self._inbound_speech_muted
            ):
                cells = message.get("cells") or []
                if isinstance(cells, list) and cells:
                    text = "".join(
                        chr(0x2800 + (int(c) & 0xff))
                        for c in cells
                        if isinstance(c, int)
                    )
                    if text:
                        GLib.idle_add(self._render_inbound_braille_idle_cb, text)
        elif msg_type == protocol.MSG_TONE:
            # The peer's screen reader beeped (progress bar updates,
            # browse-mode errors, NVDA's own cues). NVDA sends these
            # as tone frames; the legacy implementation played them
            # locally, but the modern dispatch had no tone branch and
            # dropped them as unhandled until 0.9.10. Same focus and
            # mute gate as cancel: a remote we are not listening to
            # must not beep at us either.
            if not self._inbound_speech_drop_reason():
                self._play_tone(message)
        else:
            self._log(f"unhandled message type: {msg_type}")

    def _inbound_speech_drop_reason(self) -> str:
        """Why an inbound `speak` frame would be dropped; "" means speak it."""

        if self._current_role() != ROLE_CLIENT:
            return "we are the host"
        if not self._focus_on_remote:
            return "focused on the local machine"
        if self._inbound_speech_muted:
            return "inbound muted"
        return ""

    def _log_speak_ignored(self, reason: str) -> None:
        """Log the first inbound `speak` dropped for each reason this session.

        A peer that never sends speech and a gate that drops all of it
        sound identical to the user; one line per reason in the debug
        log tells them apart without speaking anything.
        """

        if reason in self._speak_ignored_reasons:
            return
        self._speak_ignored_reasons.add(reason)
        self._log(f"inbound speech dropped ({reason})")

    def _play_tone(self, message: dict) -> None:
        """Parse a `tone` frame and play it on a worker thread.

        Payload (NVDA Remote v2): hz and length (milliseconds) are
        required and must be positive; left/right are per-channel
        volumes in 0..100, defaulting to 50 when absent. Malformed
        frames are dropped silently -- a bad beep is never worth an
        announcement or an exception on the read loop.
        """

        try:
            hz = int(message.get("hz") or 0)
            length = int(message.get("length") or 0)
            left = int(message.get("left", 50) or 0)
            right = int(message.get("right", 50) or 0)
        except (TypeError, ValueError):
            return
        if hz <= 0 or length <= 0:
            return
        left = min(100, max(0, left))
        right = min(100, max(0, right))
        threading.Thread(
            target=_play_tone_blocking,
            args=(hz, length, left, right, self._log),
            daemon=True,
        ).start()

    def toggle_speech_mirror(self) -> bool:
        """Flip the host-mode speech mirror on/off. Spoken feedback."""

        self._mirror_speech = not self._mirror_speech
        if self._mirror_speech:
            self._say("Orca Remote: speech mirroring enabled.")
        else:
            self._say("Orca Remote: speech mirroring muted.")
        # Reset the coalesce sentinel so re-enabling speaks the next
        # utterance even if it happens to match the last one we sent.
        self._last_outbound_speech = ""
        return True

    def toggle_braille_mirror(self) -> bool:
        """Flip the host-mode braille mirror on/off. Spoken feedback."""

        self._mirror_braille = not self._mirror_braille
        if self._mirror_braille:
            self._say("Orca Remote: braille mirroring enabled.")
        else:
            self._say("Orca Remote: braille mirroring disabled.")
        # Same sentinel reset as speech, and force a fresh
        # set_braille_info on the next paint so the master gets the
        # dimensions again in case it forgot.
        self._last_outbound_braille = ("", -2)
        self._sent_braille_info = False
        return True

    def push_clipboard(self) -> bool:
        """Send the local clipboard text to the peer as set_clipboard_text.

        Reads the local clipboard (GLib main thread), then schedules the
        wire send. No-op (with spoken feedback) if the transport is down
        or the clipboard is empty.
        """

        if self._transport is None:
            self._say("Orca Remote: not connected; clipboard not pushed.")
            return True
        text = self._read_clipboard_text()
        if not text:
            self._say("Orca Remote: clipboard is empty.")
            return True
        self._schedule_send(
            {"type": protocol.MSG_SET_CLIPBOARD_TEXT, "text": text},
            what="clipboard",
        )
        # Spoken confirmation that includes the length so the user
        # has some feedback that something happened. We avoid speaking
        # the text itself; could be sensitive (passwords, etc.).
        self._say(f"Orca Remote: pushed clipboard ({len(text)} characters).")
        return True

    def _read_clipboard_text(self) -> str:
        """Read the clipboard, preferring the controller's helper.

        The controller helper is the polite path (it goes through
        whatever clipboard manager Orca uses), but some builds ship a
        clipboard presenter without any way to read text, which makes
        controller.get_clipboard_text() return "" no matter what is on
        the clipboard. Falling back to Gtk keeps push working there.
        """

        try:
            text = str(self.controller.get_clipboard_text() or "")
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"get_clipboard_text failed: {error}")
            text = ""
        if text:
            return text
        clipboard = _gtk_clipboard()
        if clipboard is None:
            return text
        try:
            return str(clipboard.wait_for_text() or "")
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"Gtk clipboard read failed: {error}")
            return text

    def _render_inbound_braille_idle_cb(self, text: str) -> bool:
        """Apply an inbound braille frame to the local display.

        Runs on the GLib main thread (display_braille_text touches
        BrlAPI state). If the call fails for any reason -- no local
        braille display attached, BrlAPI session dead, older Orca
        without the controller hook -- it's logged once at debug
        level and we move on. No spoken feedback (would be too
        noisy on every frame).
        """

        try:
            self.controller.display_braille_text(text)
        except AttributeError:
            # Released Orca (51.1) has no display_braille_text, but
            # its display_message_internal is a braille-only message
            # display -- exactly the render an inbound frame needs.
            fallback = getattr(self.controller, "display_message_internal", None)
            if callable(fallback):
                try:
                    fallback(text, False)
                except Exception as error:  # pylint: disable=broad-except
                    self._log(f"display_message_internal failed: {error}")
                return False
            # Older Orca without either call. Surface once so the user
            # knows; subsequent failures just no-op via the flag.
            if not getattr(self, "_warned_no_braille_render", False):
                self._log(
                    "controller has no display_braille_text (older Orca?); "
                    "inbound braille rendering unavailable"
                )
                self._warned_no_braille_render = True
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"display_braille_text failed: {error}")
        return False  # one-shot

    def _set_clipboard_idle_cb(self, text: str) -> bool:
        """Apply an inbound clipboard text on the GLib main thread."""

        if not self._write_clipboard_text(text):
            return False
        # Brief spoken cue so the user knows the peer pushed something.
        # Length only -- the text itself could be a password.
        self._say(f"Orca Remote: peer pushed clipboard ({len(text)} characters).")
        return False  # one-shot

    async def _handle_inbound_key(self, message: dict) -> None:
        """Synthesize a remote keystroke on the slave side.

        NVDA Remote key frame:
            {"type":"key","vk_code":<int>,"extended":<bool>,
             "pressed":<bool>,"scan_code":<int>}

        Only honoured when we're the host (a master should never
        receive key frames; if it does, that's a misbehaving peer
        and we drop the frame).
        """

        if self._current_role() != ROLE_HOST:
            return
        try:
            vk_code = int(message.get("vk_code", 0))
            extended = bool(message.get("extended", False))
            pressed = bool(message.get("pressed", False))
        except (TypeError, ValueError):
            self._log(f"malformed key frame: {message!r}")
            return

        keysym = vk_to_keysym(vk_code, extended=extended)
        if keysym == 0:
            self._log(
                f"unmapped VK code 0x{vk_code:x} "
                f"(extended={extended}, pressed={pressed})"
            )
            return

        # Two-part interrupt on every PRESS:
        #
        # 1. MSG_CANCEL outbound to the master, scheduled (not awaited).
        #    The master's NVDA holds a speech queue of every speak
        #    message we've forwarded; pressing Ctrl on the master
        #    cancels NVDA's *local* speech but does nothing to that
        #    queue because the wire never told the master to drain.
        #    NVDA Remote v2.x's `cancel` message is exactly the signal
        #    to drain it. We schedule via run_coroutine_threadsafe (in
        #    _schedule_send) rather than await directly here so a
        #    backed-up writer can't stall the read loop and serialize
        #    every subsequent inbound key behind it -- the cause of
        #    "web browsing feels very sluggish" reports under load.
        #    Ordering vs any SPEAK we generate by reacting to this
        #    same key is still preserved because writer.write() pushes
        #    to the buffer in scheduling order and SPEAK only comes
        #    back through the loop after the synth has reached AT-SPI.
        # 2. Local SpeechManager.InterruptSpeech via GLib idle. Orca's
        #    natural interrupt-on-key path (_present) should also fire
        #    when XTest delivers the event we're about to synth, but
        #    under VM AT-SPI load it can lag noticeably. This makes
        #    the slave's own speech-dispatcher cancel deterministic.
        if pressed:
            self._schedule_send({"type": protocol.MSG_CANCEL}, what="cancel")
            GLib.idle_add(self._interrupt_speech_idle_cb)

        GLib.idle_add(self._synthesize_key_idle_cb, keysym, pressed)

    def _synth(self, keysym: int, pressed: bool) -> bool:
        """Inject one key into the local machine on the best backend.

        XTEST (controller.synthesize_key_event) covers Xorg and XWayland
        clients without external tools, so it is the default. Two cases
        need the external backends instead:

        - Wayland: XTEST cannot reach native Wayland clients, so prefer
          ydotool, then the RemoteDesktop portal, then xdotool.
        - Orca builds without synthesize_key_event (released 51.1):
          there is no XTEST path at all.

        If the external backends give up (injector.exhausted) the
        controller path is used again, and vice versa on failure.
        """

        injector = getattr(self, "_injector", None)
        prefer_injector = (
            injector is not None
            and not injector.exhausted
            and (
                is_wayland_session()
                or not hasattr(self.controller, "synthesize_key_event")
            )
        )
        if prefer_injector:
            name, evdev = resolve_keysym(keysym)
            injector.send(keysym, pressed, name=name, evdev=evdev)
            return True

        ok = False
        try:
            ok = bool(self.controller.synthesize_key_event(keysym, pressed))
        except AttributeError:
            self._log("controller has no synthesize_key_event (older Orca?)")
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"synthesize_key_event raised: {error}")
        if not ok and injector is not None and not injector.exhausted:
            name, evdev = resolve_keysym(keysym)
            injector.send(keysym, pressed, name=name, evdev=evdev)
            return True
        return ok

    def _synthesize_key_idle_cb(self, keysym: int, pressed: bool) -> bool:
        # Suppress duplicate PRESS events. NVDA Remote forwards OS-level
        # autorepeat as a stream of PRESS frames -- and a SINGLE physical
        # press can arrive as multiple frames on the wire. Without this,
        # every duplicate frame fires the bound Orca command again
        # ("Recognizing." looping on a single Insert+R tap). Dropping
        # any PRESS for a keysym already in _pressed_keysyms (PRESS
        # accepted, no matching wire RELEASE yet) collapses the burst to
        # one event. The cost: holding a key on master for typing-style
        # use does not autorepeat over the link -- the user taps for
        # each character. Worth it.
        if pressed and keysym in self._pressed_keysyms:
            return False

        # Refuse chords that would fire our own commands. Without this,
        # a remote master pressing e.g. Orca+Ctrl+R synthesizes through
        # XTest, which Orca's own input listener picks up, and our
        # open_settings command runs on the slave -- previously a
        # blocking modal Gtk dialog. The check uses _pressed_keysyms
        # (what we've synthesized PRESS for and not yet released), so
        # by the time the alphabetic key of the chord arrives, the
        # modifier keysyms are already in the set.
        if pressed and self._chord_matches_own_command(keysym):
            self._log(
                f"refusing own-command chord (keysym 0x{keysym:x}, "
                f"held={sorted(self._pressed_keysyms)})"
            )
            return False

        sticky = keysym in _STICKY_KEYSYMS

        # Non-sticky RELEASE arriving over the wire: the matching PRESS
        # branch already tapped (PRESS+RELEASE) this keysym, so the X
        # server has long since released it. Just clear the dedupe
        # bookkeeping; do not double-synth a release.
        if not pressed and not sticky:
            self._pressed_keysyms.discard(keysym)
            return False

        ok = self._synth(keysym, pressed)

        if not pressed:
            # Forget it even if the release failed; otherwise every
            # later PRESS of this modifier is dropped as a duplicate.
            self._pressed_keysyms.discard(keysym)
        if not ok:
            return False

        # Tap mode for non-modifier keys: immediately synth the matching
        # RELEASE. On X11 a PRESS without a RELEASE leaves the key
        # "held" in the server, and X dispatches its own auto-repeat
        # events at ~30 Hz -- one wire PRESS for 'H' becomes a flood of
        # real Pressed events that Orca processes as if the user were
        # jackhammering the key. The auto-release breaks that loop. We
        # still add the keysym to _pressed_keysyms below so the dedupe
        # at the top of this function collapses NVDA's per-physical-tap
        # PRESS frames down to one event; the eventual wire RELEASE
        # clears it (handled by the early-return above).
        if pressed and not sticky:
            self._synth(keysym, False)

        if pressed:
            self._pressed_keysyms.add(keysym)
        return False  # one-shot

    def _interrupt_speech_idle_cb(self) -> bool:
        """Cancel any in-progress Orca speech on the GLib main thread.

        The SpeechManager command name differs by Orca build: the perf
        branch registers InterruptSpeech, released 51.1 registers
        interrupt_speech. Try both, and log the failure once instead of
        on every inbound cancel or key press.
        """

        last_error: Exception | None = None
        for name in ("InterruptSpeech", "interrupt_speech"):
            try:
                self.controller.execute_command_internal(
                    "SpeechManager", name, notify_user=False,
                )
                return False  # one-shot
            except Exception as error:  # pylint: disable=broad-except
                last_error = error
        if not getattr(self, "_warned_no_interrupt", False):
            self._warned_no_interrupt = True
            self._log(f"speech interrupt unavailable on this Orca: {last_error}")
        return False  # one-shot

    def _chord_matches_own_command(self, keysym: int) -> bool:
        """True if synthesizing `keysym` PRESS would complete one of our chords.

        Relies on `_pressed_keysyms` already containing the modifier
        keysyms the master sent before the alphabetic key. NVDA Remote
        forwards modifiers first, so by the time the letter/Page key
        arrives the modifier set is populated; reversed orderings will
        not match here and fall through to a normal synth, which is
        acceptable: we'd rather miss a refusal than refuse a real key.
        """

        held = self._pressed_keysyms
        if not (held & _ORCA_MOD_KEYSYMS):
            return False
        if (
            keysym in _OWN_CTRL_SHIFT_CHORD_KEYSYMS
            and (held & _CTRL_KEYSYMS)
            and (held & _SHIFT_KEYSYMS)
        ):
            return True
        if keysym in _OWN_CTRL_CHORD_KEYSYMS and (held & _CTRL_KEYSYMS):
            return True
        if keysym in _OWN_ALT_CHORD_KEYSYMS and (held & _ALT_KEYSYMS):
            return True
        return False

    def _release_held_keys(self) -> None:
        """Synthesize RELEASE for any keysym we previously pressed.

        Called from the asyncio thread (via _stop_transport) and from
        disable(). Snapshots the set before iterating because each
        idle callback mutates it; runs on the GLib main thread for the
        same reason _synthesize_key_idle_cb does. Best-effort: if the
        AT-SPI device is gone we swallow the error.
        """

        # Snapshot on GLib after any already-queued presses have run.
        GLib.idle_add(self._release_held_keys_idle_cb)

    def _release_held_keys_idle_cb(self) -> bool:
        held = sorted(self._pressed_keysyms)
        if held:
            self._log(f"releasing {len(held)} held keysym(s) on shutdown")
        for keysym in held:
            self._synthesize_key_idle_cb(keysym, False)
        return False

    def _current_role(self) -> str:
        role = str(self._settings.get(SETTING_ROLE, ROLE_CLIENT) or ROLE_CLIENT)
        return role if role in (ROLE_CLIENT, ROLE_HOST) else ROLE_CLIENT

    def _on_status(self, status: str) -> None:
        self._last_transport_status = status
        self._log(f"transport: {status}")

    def _on_transport_disconnected(self) -> None:
        GLib.idle_add(self._transport_disconnected_idle_cb)

    def _transport_disconnected_idle_cb(self) -> bool:
        was_remote = self._focus_on_remote
        self._leave_remote_focus()
        self._release_held_keys_idle_cb()
        self._peer_not_connected_announced = False
        self._channel_peers = {}
        self._last_outbound_speech = ""
        self._last_outbound_braille = ("", -2)
        self._sent_braille_info = False
        if was_remote:
            self._say("Orca Remote connection lost. Controlling local machine.")
        return False

    def _on_fingerprint_mismatch(self, actual: str) -> None:
        """Transport saw a certificate we do not trust (asyncio thread).

        Ask the user, on the GLib thread, whether to trust it. A blank
        pin (first connection) and a changed pin get different dialogs.
        """

        GLib.idle_add(self._fingerprint_prompt_idle_cb, actual)

    def _fingerprint_prompt_idle_cb(self, actual: str) -> bool:
        actual = _normalize_fingerprint(actual)
        host = str(self._settings.get(SETTING_HOST, "") or "")
        port = int(self._settings.get(SETTING_PORT, 0) or 0)
        saved = _normalize_fingerprint(
            str(self._settings.get(SETTING_FINGERPRINT, "") or ""),
        )
        changed = bool(saved)
        # Keep a copy on disk for comparing with the server's owner.
        self._write_fingerprint_file(actual)
        if self._trust_dialog is not None:
            try:
                self._trust_dialog.destroy()
            except Exception:  # pylint: disable=broad-except
                pass
            self._trust_dialog = None

        def _on_result(trusted: bool) -> None:
            self._trust_dialog = None
            self._on_trust_result(trusted, actual, host, port)

        try:
            self._trust_dialog = build_trust_dialog(
                host, port, actual, changed, _on_result,
            )
        except Exception as error:  # pylint: disable=broad-except
            # No dialog (GTK trouble): fall back to the manual route.
            self._log(f"trust dialog failed: {error}")
            self._copy_fingerprint_idle_cb(actual)
            self._say(self._manual_fingerprint_instructions())
        return False  # one-shot

    def _on_trust_result(self, trusted: bool, actual: str, host: str, port: int) -> None:
        if not trusted:
            self._say(
                "Orca Remote: not connected. The server was not trusted."
            )
            return
        if (
            str(self._settings.get(SETTING_HOST, "") or "") != host
            or int(self._settings.get(SETTING_PORT, 0) or 0) != port
        ):
            # Settings moved to another server while the dialog was up:
            # this fingerprint belongs to the old one.
            self._say("Orca Remote: the server changed meanwhile; not saved.")
            return
        self._say("Orca Remote: server trusted. Connecting.")
        self._set_auto_connect(True)
        # Saves the pin and restarts the transport.
        self._set_setting(SETTING_FINGERPRINT, actual)

    def _manual_fingerprint_instructions(self) -> str:
        return (
            "Orca Remote: server fingerprint did not match. "
            f"The fingerprint was copied to the clipboard and saved to "
            f"{_fingerprint_file_path()}. "
            "Press Orca+Ctrl+R, focus the Server fingerprint field, "
            "press Control+A to select the old value, then paste with "
            "Control+V. If pasting does not work, copy the line from "
            "that file instead."
        )

    def _copy_fingerprint_idle_cb(self, actual: str) -> bool:
        self._write_clipboard_text(actual)
        self._write_fingerprint_file(actual)
        return False  # one-shot

    def _write_clipboard_text(self, text: str) -> bool:
        copied = False
        try:
            self.controller.set_clipboard_text(text)
            copied = True
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"set_clipboard_text failed: {error}")
        # That call is a silent no-op when the build's clipboard
        # presenter has no active manager (no GPaste/Klipper, presenter
        # not yet activated), so also set it through Gtk: the
        # announcement promises the value is on the clipboard.
        clipboard = _gtk_clipboard()
        if clipboard is not None:
            try:
                clipboard.set_text(text, -1)
                copied = True
            except Exception as error:  # pylint: disable=broad-except
                self._log(f"Gtk clipboard write failed: {error}")
        return copied

    def _write_fingerprint_file(self, actual: str) -> str:
        """Save the last-seen fingerprint for manual copying.

        Returns the path, or "" when it could not be written.
        """

        path = _fingerprint_file_path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(actual.strip() + "\n")
        except OSError as error:
            self._log(f"could not write {path}: {error}")
            return ""
        return path

    # ---- helpers ---------------------------------------------------

    def _say(self, text: str) -> None:
        """Speak from the main (GLib) thread."""

        try:
            self.controller.present_message_internal(text)
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"present_message_internal failed: {error}")

    def _say_async(self, text: str) -> None:
        """Speak from the asyncio thread by marshalling onto the GLib loop."""

        GLib.idle_add(self._say_idle_cb, text)

    def _say_idle_cb(self, text: str) -> bool:
        self._say(text)
        return False  # one-shot

    def _log(self, message: str) -> None:
        debug.print_message(debug.LEVEL_INFO, f"REMOTE: {message}", True)
