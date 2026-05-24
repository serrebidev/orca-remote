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
import os
import sys
import threading
import types
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
)
from .keymap import forwardable_keysyms, keysym_to_vk, vk_to_keysym
from .protocol import CONNECTION_TYPE_MASTER, CONNECTION_TYPE_SLAVE
from .transport import RemoteTransport

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


# Modifier keysyms whose press/release we use to recognize when an
# inbound key sequence is about to complete one of our own command
# chords. Tracked against `_pressed_keysyms`, which records what we've
# synthesized PRESS for and not yet RELEASED.
_ORCA_MOD_KEYSYMS: frozenset[int] = frozenset({
    0xff63,  # XK_Insert       (desktop-layout Orca modifier)
    0xff9e,  # XK_KP_Insert    (numpad variant)
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
    0xff9e,          # KP_Insert    (Orca mod, laptop layout)
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
    0x6d,    # XK_m          -> mute_inbound_toggle
    0xff55,  # XK_Page_Up    -> connect
    0xff56,  # XK_Page_Down  -> disconnect_session
    0xff9a,  # XK_KP_Page_Up
    0xff9b,  # XK_KP_Page_Down
})
_OWN_CTRL_SHIFT_CHORD_KEYSYMS: frozenset[int] = frozenset({
    0x63,    # XK_c          -> push_clipboard
    0x43,    # XK_C          -> push_clipboard on layouts reporting shifted keysyms
})
_OWN_ALT_CHORD_KEYSYMS: frozenset[int] = frozenset({
    0xff09,  # XK_Tab        -> switch_side
})

# AT-SPI key-event modifier bit positions (standard X11 layout).
# Used by the master-side bypass check to distinguish our own command
# chords from regular typing that should be forwarded to the remote.
_AT_SPI_MOD_CTRL: int = 0x04
_AT_SPI_MOD_SHIFT: int = 0x01
_AT_SPI_MOD_ALT: int = 0x08


def _settings_path() -> str:
    """Absolute path to the JSON settings file."""

    return os.path.join(
        GLib.get_user_data_dir(),  # pylint: disable=no-value-for-parameter
        "orca",
        _SETTINGS_FILENAME,
    )


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
        # Insert+Ctrl+M.
        self._inbound_speech_muted: bool = False
        # Master-only: how many Orca-modifier keys (Insert / Caps_Lock)
        # are currently held. Counter (not bool) so two Orca-mod keys
        # held together don't double-decrement. Read by the bypass
        # check in _on_keyboard_event to decide whether the incoming
        # chord is one of our local commands (which dispatch locally
        # via Orca) versus regular typing (which gets forwarded so the
        # remote NVDA/Orca can act on it).
        self._master_orca_mod_held: int = 0
        # Host-side: keysyms we've synthesized PRESS for but not yet
        # RELEASE. If the transport drops between a press/release pair
        # the X server would otherwise believe the key is still held,
        # which survives Orca restart (Atspi.generate_keyboard_event
        # uses XTEST). On disconnect / disable we drain this set with
        # synthetic releases so a force-killed VM is never the only
        # way out of a stuck modifier.
        self._pressed_keysyms: set[int] = set()
        # Set True the first time we see channel_joined for the current
        # session intent; reset by explicit connect / disconnect /
        # disable so user-initiated transitions re-announce but silent
        # auto-reconnects do not. Without this, a flaky link to the
        # relay produced a "connected in host mode" announcement every
        # 30s (the backoff cap), which is what the VM crash session
        # heard as "repeating things over and over."
        self._announced_join: bool = False
        # Host-side de-duplication of consecutive identical speech.
        # Orca emits the same string twice in rapid succession in a
        # few legitimate code paths (caret-moved + name-changed,
        # focus-of-focus, etc.). On the wire each duplicate becomes a
        # separate NVDA speak frame that the master's NVDA queues and
        # speaks. The user reported this as the master hearing
        # everything twice. Compare last-sent text and skip a repeat.
        self._last_outbound_speech: str = ""
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
        # KeysetGrab active while master-mode forwarding is on. When
        # set, the forwardable-keysym set is grabbed at the AT-SPI
        # level so the focused local app stops receiving keys we're
        # already sending on the wire. None when not forwarding or
        # when the grab is unavailable (vendored ext-utils missing,
        # AT-SPI couldn't construct a Device, compositor refused).
        # See vendor/orca_ext_utils/keyboard_grab.py for the grab
        # semantics. Decision rationale in docs/architecture.md
        # "Master-side full consume."
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
        # Subscribe to outbound Orca speech so host mode can mirror
        # local utterances onto the relay. No-op on older Orca builds
        # that predate the speech_emitted signal.
        try:
            self.controller.subscribe_speech_emitted(self._on_speech_emitted)
        except AttributeError:
            self._log("controller has no subscribe_speech_emitted (older Orca?)")
        # Same for braille. Hook landed in perf branch after speech;
        # AttributeError just means we're on a pre-braille-hook Orca,
        # in which case braille mirroring is silently unavailable.
        try:
            self.controller.subscribe_braille_emitted(self._on_braille_emitted)
        except AttributeError:
            self._log("controller has no subscribe_braille_emitted (older Orca?)")
        # And keyboard events for master-side key forwarding. Hook
        # landed even later; pre-hook Orca silently has no forwarding.
        try:
            self.controller.subscribe_keyboard_event(self._on_keyboard_event)
        except AttributeError:
            self._log(
                "controller has no subscribe_keyboard_event "
                "(older Orca?); master-side keys unavailable"
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
        self._last_outbound_speech = ""
        self._last_outbound_braille = ("", -2)
        self._sent_braille_info = False
        try:
            self.controller.unsubscribe_speech_emitted(self._on_speech_emitted)
        except AttributeError:
            pass
        try:
            self.controller.unsubscribe_braille_emitted(self._on_braille_emitted)
        except AttributeError:
            pass
        try:
            self.controller.unsubscribe_keyboard_event(self._on_keyboard_event)
        except AttributeError:
            pass
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
        if text == self._last_outbound_speech:
            return
        self._last_outbound_speech = text

        # NVDA Remote's speak message: {"type":"speak","sequence":[...]}
        # where sequence entries are either strings (text) or dicts
        # (speech commands). We forward a single text fragment; richer
        # ACSS / index marks can come later.
        message = {"type": protocol.MSG_SPEAK, "sequence": [text]}
        self._schedule_send(message, what="speech")

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
          one of our own command chords (Insert+Ctrl+R / Ctrl+M /
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
        if keysym in _ORCA_MOD_KEYSYMS:
            if pressed:
                self._master_orca_mod_held += 1
            else:
                self._master_orca_mod_held = max(
                    0, self._master_orca_mod_held - 1,
                )

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
        the user holds Insert and presses Ctrl+R / Ctrl+M / Ctrl+PgUp
        / Ctrl+PgDn / Alt+Tab, we don't want to forward those to the
        remote -- they're local control commands and should fire
        Orca's binding for the registered handler instead.

        Modifier bits come from the AT-SPI event: Ctrl = 0x04,
        Alt = 0x08. The Orca-modifier (Insert / Caps_Lock) isn't a
        true X11 modifier bit; we track its press/release ourselves
        in _master_orca_mod_held.
        """

        if self._master_orca_mod_held == 0:
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
    # Orca+Ctrl+M       toggle inbound speech mute (master-side)
    # Orca+Ctrl+PageUp  connect (no-op if already connected)
    # Orca+Ctrl+PageDn  disconnect (no-op if already disconnected)
    # Orca+Ctrl+Shift+C push local clipboard to the peer
    # Orca+Alt+Tab      switch_side -- toggle focus_on_remote (grab +
    #                   hearing together)
    #
    # The menu-based action list was removed in 0.8.0; the bypass-chord
    # mechanism in _on_keyboard_event ensures these five chords still
    # reach Orca's dispatcher even while master-mode forwarding is on.

    def _get_commands(self) -> list[Command]:
        ctrl = keybindings.ORCA_CTRL_MODIFIER_MASK
        alt = keybindings.ORCA_ALT_MODIFIER_MASK
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
                desktop_keybinding=keybindings.KeyBinding("m", ctrl),
                laptop_keybinding=keybindings.KeyBinding("m", ctrl),
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
        if self._transport is not None:
            self._say("Orca Remote already connected.")
            return True
        # User asked to connect -- they should hear the next join.
        self._announced_join = False
        self._say("Orca Remote: connecting.")
        self._restart_transport()
        return True

    def disconnect_session(self) -> bool:
        # Explicit disconnect: clear the intent so the next Orca
        # restart stays offline until the user reconnects.
        self._set_auto_connect(False)
        # Whatever happens next, the next join should announce again.
        self._announced_join = False
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
            self._disable_master_grab()
            self._say("Orca Remote: focused on local machine.")
        return True

    def mute_inbound_toggle(self) -> bool:
        """Toggle the master-side inbound-speech mute. Bound to Insert+Ctrl+M.

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
        """Take ownership of forwardable keys at the AT-SPI level.

        No-op when KeysetGrab isn't available (vendored ext-utils
        missing) or when a grab is already in place. Logs how many
        of the requested keysym/modifier pairs the AT-SPI device
        accepted vs refused; partial coverage is normal under
        compositors that don't honor every grab.
        """

        if self._master_grab is not None:
            return
        if not _HAVE_KEYSET_GRAB or KeysetGrab is None:
            self._log(
                "KeysetGrab unavailable (vendored ext-utils missing); "
                "forwarded keys will also reach the focused local app"
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
        self._log(f"master grab active: {held} grabs held, {refused} refused")
        # Partial coverage is common on Wayland compositors that don't
        # fully honor AT-SPI key grabs. The refused count goes to the
        # log but NOT to speech -- speaking it on every switch_side
        # was too chatty (and the count varies between activations
        # depending on transient grab-table state, which is confusing).
        # Users who want to know their coverage can grep the log.

    def _disable_master_grab(self) -> None:
        """Release the master-mode KeysetGrab if one is held."""

        if self._master_grab is None:
            return
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
        changed = False
        for key, value in result.items():
            if self._settings.get(key) != value:
                self._settings[key] = value
                changed = True
        if changed:
            self._save_settings()
            self._say("Orca Remote settings saved.")
            if not self._disabled:
                # Settings change -> connect-affecting restart; the
                # user deserves to hear the new join announce.
                self._announced_join = False
                self._restart_transport()

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
                self._announced_join = False
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
        fingerprint = str(self._settings.get(SETTING_FINGERPRINT, "") or "")
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

        async def _setup() -> None:
            transport = RemoteTransport(
                host=host,
                port=port,
                channel=channel,
                fingerprint=fingerprint,
                connection_type=connection_type,
                on_message=self._on_message,
                on_status=self._on_status,
                on_fingerprint_mismatch=self._on_fingerprint_mismatch,
            )
            transport.start()
            self._transport = transport

        asyncio.run_coroutine_threadsafe(_setup(), self._loop)

    def _stop_transport(self) -> None:
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
            # hasn't muted the remote (Insert+Ctrl+M). Mute is
            # independent of focus so a muted state persists across
            # switch_side toggles -- coming back to remote stays quiet
            # until the user explicitly unmutes.
            if (
                self._current_role() == ROLE_CLIENT
                and self._focus_on_remote
                and not self._inbound_speech_muted
            ):
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
            if self._current_role() == ROLE_CLIENT:
                GLib.idle_add(self._interrupt_speech_idle_cb)
        elif msg_type == protocol.MSG_CHANNEL_JOINED:
            if self._announced_join:
                return
            self._announced_join = True
            role = self._current_role()
            if role == ROLE_HOST:
                self._say_async("Orca Remote connected in host mode.")
            else:
                self._say_async("Orca Remote connected.")
        elif msg_type == protocol.MSG_CLIENT_LEFT:
            self._say_async("Orca Remote: peer left.")
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
        else:
            self._log(f"unhandled message type: {msg_type}")

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

        Reads the local clipboard via the controller (GLib main
        thread), then schedules the wire send. No-op (with spoken
        feedback) if the transport is down or the clipboard is empty.
        Wired to the remote menu in Phase 6; safe to call directly
        too.
        """

        if self._transport is None:
            self._say("Orca Remote: not connected; clipboard not pushed.")
            return True
        try:
            text = str(self.controller.get_clipboard_text() or "")
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"get_clipboard_text failed: {error}")
            self._say("Orca Remote: could not read clipboard.")
            return True
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
            # Older Orca without the hook. Surface once so the user
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

        try:
            self.controller.set_clipboard_text(text)
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"set_clipboard_text failed: {error}")
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

        ok = False
        try:
            ok = bool(self.controller.synthesize_key_event(keysym, pressed))
        except AttributeError:
            self._log("controller has no synthesize_key_event (older Orca?)")
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"synthesize_key_event raised: {error}")

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
            try:
                self.controller.synthesize_key_event(keysym, False)
            except Exception as error:  # pylint: disable=broad-except
                self._log(
                    f"synthesize_key_event (auto-release) raised: {error}"
                )

        if pressed:
            self._pressed_keysyms.add(keysym)
        else:
            self._pressed_keysyms.discard(keysym)
        return False  # one-shot

    def _interrupt_speech_idle_cb(self) -> bool:
        """Cancel any in-progress Orca speech on the GLib main thread."""

        try:
            self.controller.execute_command_internal(
                "SpeechManager", "InterruptSpeech", notify_user=False,
            )
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"InterruptSpeech failed: {error}")
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

        if not self._pressed_keysyms:
            return
        held = sorted(self._pressed_keysyms)
        self._log(f"releasing {len(held)} held keysym(s) on shutdown")
        for keysym in held:
            GLib.idle_add(self._synthesize_key_idle_cb, keysym, False)

    def _current_role(self) -> str:
        role = str(self._settings.get(SETTING_ROLE, ROLE_CLIENT) or ROLE_CLIENT)
        return role if role in (ROLE_CLIENT, ROLE_HOST) else ROLE_CLIENT

    def _on_status(self, status: str) -> None:
        self._log(f"transport: {status}")

    def _on_fingerprint_mismatch(self, actual: str) -> None:
        # Surface the fingerprint we actually saw so the user can
        # paste it into the setting if they trust it. Also copy it
        # to the clipboard from the GLib main thread so a blind user
        # doesn't have to memorise a 64-char hex string.
        GLib.idle_add(self._copy_fingerprint_idle_cb, actual)
        self._say_async(
            "Orca Remote: server fingerprint did not match. "
            "The fingerprint has been copied to the clipboard. "
            "Press Orca+Ctrl+R, focus the Server fingerprint field, "
            "and paste with Control+V."
        )

    def _copy_fingerprint_idle_cb(self, actual: str) -> bool:
        try:
            self.controller.set_clipboard_text(actual)
        except Exception as error:  # pylint: disable=broad-except
            self._log(f"set_clipboard_text failed: {error}")
        return False  # one-shot

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
