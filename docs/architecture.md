# Orca Remote — architecture

This file is a tour of how the extension is laid out and **why** it
is laid out that way. For the on-wire message vocabulary, see
[wire-protocol.md](wire-protocol.md). For symptom-to-fix
debugging, see [troubleshooting.md](troubleshooting.md).

## Module layout

| File | Purpose |
|------|---------|
| `remote.py` | `RemoteExtension` subclass: lifecycle, commands, GLib↔asyncio marshalling, state machines. |
| `transport.py` | Single-channel asyncio TLS client with fingerprint pin, reconnect backoff, write-buffer backpressure guard. |
| `protocol.py` | NVDA Remote v2.x wire (newline-JSON), message constants, encode/decode helpers. |
| `keymap.py` | Windows VK → X11 keysym table. Pure data + lookup. |
| `braille_table.py` | US computer braille ASCII → cell byte table + Unicode braille block passthrough. |
| `settings_dialog.py` | Non-blocking Gtk dialog for relay host / port / channel / fingerprint / role. |
| `__init__.py` | Thin re-export so Orca's loader can find `RemoteExtension`. |
| `vendor/orca_ext_utils/` | Vendored from <https://github.com/churst90/orca-ext-utils>; we only use `keyboard_grab.KeysetGrab`. See `vendor/UPDATE.md` for sync notes. |

## Threads

Three threads are in play:

1. **GLib main thread.** Orca lives here. Every `controller.*` call
   (speech, clipboard, modal mode), every `Atspi.*` call, every
   Gtk widget operation MUST run here. The extension's command
   handlers fire here.
2. **asyncio loop thread.** Started on extension enable, daemon.
   Owns the single TLS connection to the relay. Every wire send
   and every wire receive happens here.
3. **Speech path thread.** `speechdispatcherfactory.SpeechServer.speak`
   is invoked from various threads depending on the speech
   engine; assume it's GLib-thread but don't rely on it.
   `_on_speech_emitted` defensively marshals to asyncio.

The thread-crossing rules:

- **GLib → asyncio**: use `asyncio.run_coroutine_threadsafe(coro, loop)`.
  Always thread the resulting Future through
  `RemoteExtension._schedule_send` so a `add_done_callback` logs
  transport errors instead of swallowing them.
- **asyncio → GLib**: use `GLib.idle_add(callback, *args)`. Every
  inbound message handler that touches the controller, clipboard,
  or any Gtk widget does this. The callback signature must return
  `False` to be one-shot.
- **Speech path → asyncio**: same as GLib → asyncio. Don't block
  the speech path; speech is on the critical interactive path.

## Message flow

### Inbound from relay

```
relay ──TLS──► RemoteTransport._connect_and_read (asyncio)
            ── reader.readline ──► protocol.decode
            ── await self._on_message ──► RemoteExtension._on_message
                  ├── MSG_SPEAK / MSG_CANCEL / MSG_PAUSE_SPEECH
                  │     └── GLib.idle_add(InterruptSpeech) / _say_async
                  ├── MSG_KEY (host mode)
                  │     ├── _schedule_send(MSG_CANCEL)  [fire-and-forget]
                  │     ├── GLib.idle_add(InterruptSpeech)
                  │     └── GLib.idle_add(synth keysym, pressed)
                  ├── MSG_SET_CLIPBOARD_TEXT
                  │     └── GLib.idle_add(controller.set_clipboard_text)
                  └── MSG_CHANNEL_JOINED / MSG_CLIENT_LEFT / MSG_MOTD
                        └── _say_async / log
```

Critical property: `_on_message` itself is `async` but should
return quickly. The inline `await` on `_handle_inbound_key`
sending CANCEL was the cause of the "web browsing is sluggish"
bug pre-0.5.0 — every inbound key serialized behind a drain. Now
the CANCEL is `_schedule_send`'d (which is fire-and-forget) and
the read loop never waits on outbound writes.

### Outbound from extension

```
SpeechServer.speak ──► controller.emit_speech_emitted
                    ── (each subscriber) ──► RemoteExtension._on_speech_emitted (GLib)
                            ├── coalesce dup
                            └── _schedule_send(MSG_SPEAK)
                                  └── asyncio.run_coroutine_threadsafe
                                        └── transport.send (asyncio)
                                              ├── buffer-size guard (drop if congested)
                                              └── writer.write + drain

braille.refresh ──► controller.emit_braille_emitted
                 ── RemoteExtension._on_braille_emitted (GLib)
                       ├── frame dedup (text, cursor_cell)
                       ├── braille_table.text_to_cells
                       ├── (first frame) _schedule_send(MSG_SET_BRAILLE_INFO)
                       └── _schedule_send(MSG_DISPLAY)
```

Both paths assume the underlying perf-branch `emit_*` hooks exist
on the controller; `subscribe_*` calls are wrapped in
`try/except AttributeError` so the extension degrades silently on
an older Orca.

## State, and where it lives

`RemoteExtension` is the only stateful actor. Categories of state:

**Settings (persisted):**

- `_settings` — dict mirrored to `~/.local/share/orca/orca-remote-settings.json`
  (mode 0o600). Host, port, channel key, fingerprint, role,
  `auto_connect`.

**Transport (asyncio-thread):**

- `_loop`, `_loop_thread`, `_transport`.
- `RemoteTransport._writer`, `_stop_event`, `_task`,
  `_dropped_outbound`.

**Wire-state (GLib-thread, read by asyncio):**

- `_pressed_keysyms` — keysyms synth'd PRESS for but not yet
  RELEASE. Drained on disconnect/disable so a force-killed VM is
  never the only way out of a stuck modifier.
- `_announced_join` — true after the first `channel_joined` for
  the current session intent; reset by explicit
  connect/disconnect/disable. Prevents reconnect spam.
- `_last_outbound_speech`, `_last_outbound_braille`,
  `_sent_braille_info` — coalesce / dedup sentinels.
- `_focus_on_remote` — master-side: hearing the slave?
- `_mirror_speech`, `_mirror_braille` — host-side: emitting to
  the master?

**Counters (debug visibility):**

- `_dropped_nonstring_items` — count of inbound speak sequence
  items that were not plain strings (LangChange / IndexCommand
  etc.). Logged on disable.

## Why the choices we made

### Fingerprint pin instead of CA trust

The public relay (`nvdaremote.com`) uses a self-signed cert; even
self-hosters often do. CA trust would either be useless (everyone
self-signs) or require manual CA install on every client. SHA-256
pin gives the same guarantee as TOFU after first connect, but
makes the first-connect bootstrap explicit (you have to paste the
fingerprint), which avoids the silent-on-bootstrap surprise of
TOFU.

### Non-blocking settings dialog

A remote master in host mode can synthesize Orca+Ctrl+R, which
opens our settings. If that dialog used `Gtk.Dialog.run()` (a
nested GLib main loop), the GLib thread would block until a local
user clicked something. Real lock-up. The dialog is built around
the `response` signal callback so the main loop keeps running.

### Stuck-key drain on disconnect

`Atspi.generate_keyboard_event` goes through XTEST. A PRESS
without a RELEASE outlives the process — the X server believes
the key is held. We track every synth'd PRESS in `_pressed_keysyms`
and synthesize matching RELEASEs on `_stop_transport` and
`disable`. Before this safety net a dropped connection mid-pair
left the slave's modifier stuck.

### Locking keys pass through (don't drop)

0.4.1 dropped Caps_Lock / Num_Lock / Scroll_Lock unconditionally
to stop the XTest "press = toggle" foot-gun, which also killed
legitimate taps. 0.4.3 reverted to pass-through with strict
autorepeat dedupe: a tap toggles, NVDA-laptop-modifier chord
usage produces extra toggles the user can untoggle with another
tap. Slave's lock state follows the slave's keyboard, not the
master's NVDA layout.

### Own-chord refusal in host mode

If the master sends Orca+Ctrl+R, XTest delivers it on the slave,
Orca's input listener picks it up, and we open the settings
dialog on the slave (not the master). Same for Orca+Ctrl+Page
Up/Down (transport bounce) and Orca+Alt+Tab (focus toggle). The
extension drops these chords on the synth side using
`_pressed_keysyms` to detect when modifiers are held.

### Fire-and-forget outbound CANCEL

Pre-0.5.0 the inbound key handler `await`ed
`transport.send({"type":"cancel"})`. Each web-browsing arrow key
paid a per-key writer-drain round-trip; under VM-network jitter
this was the "very sluggish" symptom. 0.5.0 schedules CANCEL via
`_schedule_send` (which fire-and-forgets via
`run_coroutine_threadsafe`); ordering vs SPEAK reaction to the
same key is still preserved because `writer.write()` buffers in
call order.

### Bounded outbound buffer

`RemoteTransport.send` checks `writer.transport.get_write_buffer_size()`
and drops if over 256 KiB. Stops unbounded backlog when the relay
or its TCP path is congested. Letting drain() block here would
cascade backpressure into every producer (speech_emitted, inbound
key reaction).

### US computer braille for outbound mirroring

We need NVDA's braille viewer to show legible content. NVDA Remote
v2.x's `display` carries raw cell bytes; we have a TEXT string
(from the perf-branch `braille_emitted` hook). The simplest robust
translation is a static ASCII → cell table. English text renders
correctly; non-Latin scripts come through as blank cells. A
liblouis-backed translation can drop in behind `text_to_cells`
without changing the wire layer.

## Master-side key forwarding (Orca master → slave)

**Implemented across three releases.** End-to-end tested over a
real relay with an Orca master controlling an NVDA host (and the
reverse). 0.6.0 added the forwarding hook, 0.7.0 added full
system-level consume, 0.8.0 added the local-command bypass.

Both designs originally considered turned out to be necessary --
the right architecture is to combine them:

1. **Orca-dispatch consume (0.6.0).** The perf-branch
   `subscribe_keyboard_event` controller API delivers
   `(pressed, keycode, keysym, modifiers, text)` to
   `_on_keyboard_event` BEFORE `event.process()`; returning True
   consumes from Orca's perspective. This is what stops the
   local Orca from acting on forwarded chords (no double-fire
   of Orca+Ctrl+R producing two "Recognizing..." voices).

2. **Focused-app consume (0.7.0).** `_enable_master_grab` builds
   a `KeysetGrab` (vendored from `orca-ext-utils`) over
   `keymap.forwardable_keysyms()` when focused-on-remote
   activates. The AT-SPI grab takes the keys off the focused
   application's delivery, so a forwarded letter only types on
   the remote machine, not also into whatever local app the
   master has focus on. Released on the inverse switch_side or
   on `disable()`.

The grab's callback is a no-op consume; the actual forwarding
still happens through `_on_keyboard_event` (which fires from
input_event_manager regardless of whether the event was
AT-SPI-grabbed). The grab's only job is the focused-app block.

### Local-command bypass (0.8.0)

The user still needs to fire orca-remote's own commands locally
while master-mode forwarding is active. 0.6.x / 0.7.x used F11 as
a hard escape: press F11 to exit forwarding mode, then the user
could use any Orca chord normally. That worked but was
single-purpose -- you could only escape, not invoke a specific
command in place.

0.8.0 replaces the F11 escape with a per-chord bypass:

1. `_on_keyboard_event` tracks Orca-mod press/release into
   `_master_orca_mod_held` (counter, not bool, so Insert +
   KP_Insert held together balance correctly). Orca-mod keysyms:
   Insert, KP_Insert, Caps_Lock, Shift_Lock.

2. The Orca-mod keysyms themselves are STILL forwarded to the
   remote so its screen reader sees Insert/Caps_Lock state come
   through (NVDA needs Insert held to interpret Insert+Down as
   sayAll, etc.).

3. `_is_local_bypass_chord(keysym, modifiers)` checks whether the
   trigger keysym matches one of orca-remote's own command chords:
   - `_OWN_CTRL_CHORD_KEYSYMS` × Ctrl bit: r / m / Page_Up /
     Page_Down / KP_Page_Up / KP_Page_Down
   - `_OWN_ALT_CHORD_KEYSYMS` × Alt bit: Tab
   AND the user is currently holding Orca-mod.

4. If the bypass matches, `_on_keyboard_event` returns False --
   the trigger keysym is NOT forwarded, Orca's binding dispatcher
   fires the registered handler locally.

The component modifier keys (Insert, Ctrl, Alt) ARE forwarded.
The remote screen reader sees Insert+Ctrl held but no trigger
key arrives -- it treats the chord as Insert+Ctrl pressed-and-
released-with-nothing-between, which is a no-op for both NVDA
and Orca. Slight oddity but harmless.

Every other chord, including all standard NVDA/Orca commands
(Insert+Down for sayAll, Insert+T for window title, Insert+End
for status bar, etc.), is forwarded unchanged so the remote
screen reader can act on it.

**Known limitation**: a user binding an unrelated Orca extension
to one of {Ctrl+r, Ctrl+m, Ctrl+PgUp, Ctrl+PgDn, Alt+Tab} with
Orca-mod would have their chord intercepted by our bypass and
not forwarded -- the bypass logic can't distinguish "orca-remote
owns this" from "someone else owns this." Resolvable later by
querying the controller's command registry or surfacing the
bypass list as a setting.

### Compositor coverage

| Display server | Behavior |
|---|---|
| X11 (Xorg) | KeysetGrab.failed_keysyms is typically empty; full consume. |
| XWayland | Same as X11 for XWayland apps; native Wayland apps may still see the keys depending on the compositor. |
| Wayland (Mutter/KWin) | Partial: some grabs accepted, some refused. Refused list logged via `_log` at grab time. |
| Wayland (wlroots) | Usually no grabs accepted; degrades to Orca-dispatch consume only (pre-0.7.0 behavior). |

The user gets a one-line log message at grab time showing the
held/refused split, so partial coverage is observable rather
than silent.

## Deferred work

Items the user has explicitly asked for that aren't in current
releases, with the design constraint.

### File transfer

NVDA Remote v2.x has no file-transfer message. The user's
strategic decision was "skip — keep v2 wire" (file transfer would
either be incompatible with NVDA on Windows or require upgrading
the whole stack to NVDA Remote v3 protocol). Use `scp` / `rsync`.

### Liblouis-backed braille translation

The static ASCII→cell table is English-only. A liblouis
translator would let us send UEB Grade 2, language-tagged
contractions, and non-Latin scripts correctly. Liblouis is a
heavy dep (C library + Python bindings); not in scope for a
zero-dep extension. Future opt-in via a setting.
