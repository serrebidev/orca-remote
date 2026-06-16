# Changelog

## Unreleased

### Fixed

- Forward Shift+Tab from the client side by mapping `XK_ISO_Left_Tab`
  back to `VK_TAB` and including it in the forwardable keysym grab set.
  Plain Tab remains mapped to `VK_TAB`; Shift travels as its own key
  frame, so the host reconstructs Shift+Tab correctly.
- Prefer Orca's own AT-SPI device for master-mode key grabs, with the
  existing vendored grab as fallback, so forwarded keys are less likely
  to leak into the focused local app and trigger local speech while the
  user is focused on the remote side.
- Announce `client_joined` and `nvda_not_connected` protocol messages
  instead of logging them as unhandled, while suppressing repeated
  "peer not connected" announcements until the peer state changes.
- Keep build/test artifacts and editor leftovers out of Git, and mark
  helper scripts executable where the documentation uses direct command
  execution.

## 0.9.0 -- 2026-05-24

Modern extension packaging landed in this repository alongside the
existing `orca-customizations.py` compatibility path.

### Added

- `.orca-ext` packaging via `manifest.toml` and `build-orca-ext.sh`.
- `RemoteExtension` implementation with persistent settings,
  certificate fingerprint pinning, asyncio TLS transport, master/host
  roles, speech and braille mirroring, clipboard sync, master-side key
  forwarding, command-chord bypass, and host-side key synthesis guards.
- Pure-function unit tests for protocol framing, key mapping, braille
  cell mapping, bypass chords, and key synthesis.

### Changed

- Extension metadata now points at this project and uses the current
  Orca Remote command-chord model instead of the older F11 escape text.

## 0.8.1 -- 2026-05-22

Bug fix: stop X11 from auto-repeating synthesized inbound keys.

### Fixed

- **Host-side freeze on first inbound non-modifier key.**
  `_synthesize_key_idle_cb` used to call
  `controller.synthesize_key_event(keysym, True)` for an inbound
  PRESS frame and wait for the master's wire RELEASE to call the
  matching `(keysym, False)`. On X11 that leaves the key in the
  server's "held" state, at which point the X server itself starts
  dispatching real auto-repeat Pressed events at ~30 Hz -- one
  inbound `H` frame from an NVDA master became hundreds of host-side
  H presses, Orca's main loop saturated processing them as discrete
  `previous_heading` commands, and the session looked frozen.
  Reproduced with 772 PRESS / 0 RELEASE events for `H` in 10 seconds,
  Orca CPU pegged at ~55%.

  Fix: tap mode for non-modifier keysyms. On an accepted inbound
  PRESS we synthesize the PRESS *and* immediately synthesize the
  matching RELEASE in the same idle callback, then bookkeep the
  keysym in `_pressed_keysyms` so the dedupe path keeps collapsing
  NVDA's per-physical-tap PRESS bursts to one event. The wire
  RELEASE arriving later is a no-op (X already released). Modifier
  keysyms (`_STICKY_KEYSYMS`: Shift / Ctrl / Alt / Meta / Super /
  Hyper / ISO_Level3_Shift / ISO_Level5_Shift / Insert / KP_Insert)
  stay sticky -- they hold across multiple inbound frames so chords
  like Insert+H or Ctrl+Tab still work.

### Added

- `tests/test_synth_keys.py` -- 9 tests covering tap mode for
  letters, sticky mode for modifiers, chord composition, dedupe
  across duplicate PRESS frames, own-command refusal, and synth
  failure handling.

## 0.8.0 -- 2026-05-22

UX rework: replace the menu/dialog with direct shortcuts; replace
the F11 escape with an explicit bypass-chord list so the user can
still send arbitrary NVDA commands (Insert+Down, etc.) to the remote
while only orca-remote's own command chords stay local.

### Removed

- `remote_menu.py` and the Gtk popup/dialog landing page. The
  0.7.1 Gtk.Window dialog worked but felt inconsistent (focus
  shifts left it stranded; subsequent Insert+Ctrl+R no-op'd).
- F11 escape (`_FORWARD_ESCAPE_KEYSYM`). Replaced by the
  bypass-chord mechanism described below.
- Host-mode mirror toggles (`toggle_speech_mirror` /
  `toggle_braille_mirror`). Were only reachable through the
  menu; the underlying methods remain available for future
  shortcut bindings if requested.

### Added

- **Insert+Ctrl+M -- master-side inbound mute.** New
  `_inbound_speech_muted` flag (default False), independent of
  `_focus_on_remote`. When muted, inbound speech AND braille are
  dropped regardless of focus state. Lets the user keep the
  connection up and forwarding active while silencing the remote
  ("stop hearing them move around while I work"). Spoken feedback
  on toggle: "Orca Remote: remote muted." / "remote unmuted."
- **Bypass-chord list in `_on_keyboard_event`.** Tracks the
  Orca-modifier (Insert / Caps_Lock) press/release count
  separately. When the user holds Orca-mod and presses one of
  the five orca-remote chords (Ctrl+R / Ctrl+M / Ctrl+PgUp /
  Ctrl+PgDn / Alt+Tab), the trigger keysym is NOT forwarded;
  Orca dispatches the local binding instead. Every other Orca
  or NVDA chord (Insert+Down for sayAll, review keys, etc.)
  continues to forward so the remote screen reader can act on it.
- **switch_side announces mute state on re-entry.** "Orca Remote:
  focused on remote machine. Muted." when returning to remote
  with the mute flag still set; plain "focused on remote machine."
  when unmuted. Mute persists across switch_side toggles.
- **Grab-fail count logged.** `_enable_master_grab` logs the
  held/refused split at the debug level so users on partial-
  coverage compositors can see what happened. Not spoken: doing
  so on every switch_side proved too chatty, and the count varies
  between activations as the X grab table changes.

### Changed

- **`_focus_on_remote` default flipped to False.** Extension now
  loads in a known-quiet state; the first Insert+Alt+Tab enables
  both grab + hearing together. Symmetric with the inverse toggle.
- **Insert+Ctrl+R now opens the settings dialog directly.** No
  more intermediate menu.
- **Settings dialog singleton: destroy + rebuild instead of
  present.** `present()` doesn't reliably re-raise an alive-but-
  unfocused window on marco / Wayland-flagged sessions; tearing
  down first guarantees a freshly-mapped focused window every
  time.
- **`_OWN_CTRL_CHORD_KEYSYMS` now includes 0x6d (XK_m).** Used by
  both the host-side refusal check (existing behavior) and the
  new master-side bypass check.

### Known limitation

- The bypass list captures only the five orca-remote command
  chords. Any custom Orca command the user binds to a chord that
  uses Orca-mod + Ctrl/Alt + one of {r, m, PgUp, PgDn, Tab} will
  also be intercepted and not forwarded -- there's no way for the
  bypass logic to distinguish "orca-remote owns this" from
  "another extension owns this." If a user hits this, a future
  release can either query the controller's command registry at
  grab time or expose the bypass list as a setting.

## 0.7.1 -- 2026-05-22

Fix: Orca+Ctrl+R menu was silently failing to appear on sessions
where `XDG_SESSION_TYPE=wayland` is set even though the actual
display server is X11 (Fedora MATE's default). `Gtk.Menu.popup_at_
pointer` returned without raising but the menu never became
visible, and the screen-reader user heard nothing because there
was nothing on screen to read.

- Replaced the Gtk.Menu popup with a Gtk.Window-based dialog
  (same shape as `build_settings_dialog`, which already worked
  on the affected session). The window doesn't depend on pointer
  position or on Orca owning an active GTK toplevel.
- Same actions, same state-aware item set. Focus traversal is
  standard Tab/Shift+Tab; Escape and the Close button dismiss.
- `open_menu` now hooks `destroy` instead of `selection-done`
  for singleton cleanup (Gtk.Window doesn't have selection-done).

## 0.7.0 -- 2026-05-21

Master-side full system-level key consume via vendored
`orca_ext_utils.keyboard_grab.KeysetGrab`.

Pre-0.7.0 master-side key forwarding consumed events from Orca's
dispatch chain but NOT from the focused local application -- so
a forwarded letter typed in the remote machine AND landed in
whatever local app the master had focus on. F11 was the escape
hatch. 0.7.0 adds the AT-SPI key-grab layer so forwarded keys
only act on the remote side.

- **Vendored `orca_ext_utils`**: copied `v0.2.0` (commit `0139105`)
  to `vendor/orca_ext_utils/`. See `vendor/UPDATE.md` for the
  sync procedure and which modules we use.
- **`keymap.forwardable_keysyms()`**: returns the keysym set
  master-side forwarding can send (every key with a non-zero
  `keysym_to_vk` mapping). The KeysetGrab takes this set on
  forwarding-mode entry. F11 is intentionally NOT in the set --
  it stays un-grabbed so the escape path always works even if
  the grab partially fails on a given compositor.
- **`_enable_master_grab` / `_disable_master_grab`**: lifecycle
  hooked into `switch_side`. Entering focused-on-remote: take
  the grab, log held/refused counts. Leaving (or `disable()`):
  release. The grab callback is a no-op consume; forwarding
  still happens through `_on_keyboard_event`.
- **Compositor coverage matrix** documented in
  `docs/architecture.md`: full consume on X11/XWayland; partial
  on Mutter/KWin; degrades to Orca-dispatch-only on wlroots.
  `failed_keysyms` count logged at grab time so partial coverage
  is observable.
- **Graceful degradation**: if the vendored ext-utils is missing
  (a dev running from a partial checkout), forwarding still works
  at the pre-0.7.0 level rather than crashing on import.

## 0.6.1 -- 2026-05-21

Inbound braille rendering (master side).

Requires perf-branch commit `ad003eeae` (display_braille_text +
cursor in display_message). On older Orca the AttributeError is
caught once, logged, and inbound braille silently no-ops.

- **MSG_DISPLAY handler**: decodes the peer's cell bytes as
  Unicode braille block characters (U+2800 + cell_byte) and
  pushes via `controller.display_braille_text`. Unicode braille
  passthrough is the standard interpretation; the local BrlAPI
  driver renders exactly the dot pattern the peer intended,
  regardless of whether the peer ran orca-remote or NVDA Remote.
- **MSG_SET_BRAILLE_INFO handler**: tracks peer's `numCells` in
  `_peer_braille_cells` for informational use.
- **Focus-aware gating**: inbound braille only renders when
  role=client AND `_focus_on_remote=True`. Same toggle that mutes
  inbound speech (Orca+Alt+Tab) now mutes inbound braille for
  consistency.
- **No spoken feedback per frame**: would be too noisy. Failures
  are logged once at debug; subsequent frames silent.

## 0.6.0 -- 2026-05-21

Master-side key forwarding (Linux Orca master -> NVDA / Orca slave).

Requires perf-branch commit `5fe13e743` (subscribe_keyboard_event
hook). On older Orca the AttributeError on subscribe is silently
swallowed and forwarding is unavailable.

- **`keymap.keysym_to_vk(keysym) -> (vk_code, extended)`**: reverse
  lookup built once at module init from the existing forward
  tables. Extended-flag bias matches NVDA Remote's wire (main-row
  Page_Up = extended=True; numpad KP_Page_Up = extended=False).
- **`_on_keyboard_event` handler**: subscribes to perf-branch
  keyboard_event. Active only when role=client AND
  `_focus_on_remote=True` AND a live transport. Translates keysym
  -> VK and emits `key` frames for press AND release.
- **F11 escape**: while forwarding is active, plain F11 fires
  `switch_side()` (flips `_focus_on_remote` back off) and is
  itself consumed. Matches NVDA Remote's "send keys" convention.
  Without this escape the user has no way to fire Orca+Alt+Tab
  -- its component keys all go on the wire.
- **Unmapped keysyms pass through**: keysym_to_vk -> (0, False)
  returns False from the handler so an exotic key reaches Orca
  normally instead of being silently dropped.

**LIMITATION** (documented in docs/architecture.md): this is
Orca-dispatch consume only. The key still reaches the focused
application via the X server -- AT-SPI's true consume needs
`Atspi.Device.add_key_grab` per-keysym. Practical effect: while
forwarding, your local Orca won't fire commands on the keys, but
they ALSO type into whatever local app has focus. The honest
answer for daily use right now: minimize a benign window to
absorb the local keystrokes, or wait for the planned grab_keyset
follow-up that adds full system-level consume.

## 0.5.7 -- 2026-05-21

Real popup context menu (NVDA NVDA+N style).

0.5.4 shipped the menu as a `Gtk.Dialog` of buttons -- functional
but not what the user wanted. Replaced with `Gtk.Menu` so it's
the screen-reader-familiar popup: arrow keys navigate, Enter
activates, Escape (or click-outside) dismisses, no window frame.

- `remote_menu.py` rewritten around `Gtk.Menu` + `Gtk.MenuItem`.
  Header row (non-sensitive) gives the screen reader a "Orca
  Remote: connected as host" announcement on open. Items are
  the same set as 0.5.4 (Settings, Disconnect, Push clipboard,
  mute/unmute speech / braille / inbound).
- Popup positioning: prefer `popup_at_widget` against the active
  toplevel (where a screen-reader user actually is on their
  desktop) over `popup_at_pointer` (which is unreliable when the
  pointer is parked off-screen on a multi-monitor setup); falls
  back through pointer -> legacy `popup()`.
- Singleton guard updated: while the menu is visible, a repeat
  Orca+Ctrl+R is a no-op (prevents grab thrashing). `selection-
  done` signal clears the ref so the next press opens fresh.
- Chosen action runs on the next main-loop tick (`GLib.idle_add`)
  so the menu finishes tearing down before any follow-up dialog
  (e.g. Settings) opens -- avoids grab fighting and "dialog
  appeared behind menu" symptoms.

## 0.5.6 -- 2026-05-21

Singleton-dialog guard for settings and the remote menu.

- **Bug**: pre-0.5.6, rapid Orca+Ctrl+R presses stacked a fresh
  Settings (or, in 0.5.4+, Menu) dialog each time — the user had
  to dismiss N windows one by one. Pre-0.4.1 the dialog was
  modal-blocking and self-prevented this, but the non-blocking
  switch in 0.4.1 (so a remote master couldn't lock the GLib
  loop) lost the guard.
- **Fix**: `_settings_dialog` and `_menu_dialog` refs on the
  extension. `open_settings` / `open_menu` check the existing
  ref and call `.present()` to refocus the live window instead
  of building a duplicate. Refs are cleared by the dialog's
  response (settings) or destroy (menu) signal so the next press
  opens a fresh window.

## 0.5.5 -- 2026-05-21

Documentation pass. No behavior changes.

- README rewritten: feature matrix (host/master × speech/braille/
  keys/clipboard), pairing scenarios (NVDA-master, Orca-master,
  two-Orca), menu reference, install + first-connect bootstrap.
- `docs/architecture.md`: module layout, thread model, message
  flow, state inventory, why-each-decision rationale, deferred
  work (master-side keys, inbound braille render, liblouis).
- `docs/wire-protocol.md`: every message type we send/receive with
  payload shapes, NVDA Remote v2.x compatibility matrix, key
  message handling pipeline, fingerprint pin mechanics, framing
  limits, reconnect backoff.
- `docs/troubleshooting.md`: every symptom we've hit (web
  sluggishness, stuck Caps Lock, stuck modifiers, reconnect
  announce spam, master-chord misfire on slave, double-speak,
  insecure settings file, fingerprint mismatch, Wayland synth
  failure, drop counters, outbound congestion) with root cause
  and the commit that fixed it.

## 0.5.4 -- 2026-05-21

Remote menu UI. Orca+Ctrl+R now opens a state-aware Gtk dialog
instead of going straight to Settings. Items shown depend on
connection state, role, and mirror toggles:

- **Settings…** — always shown; opens the existing settings dialog.
- **Disconnect** — shown when connected.
- **Push clipboard to remote** — shown when connected.
- **Mute / Unmute outbound speech mirror** — host mode, when
  connected. Wires `toggle_speech_mirror()`.
- **Stop / Resume outbound braille mirror** — host mode, when
  connected. Wires `toggle_braille_mirror()`.
- **Mute / Unmute inbound remote speech** — client mode, when
  connected. Wires the existing `switch_side()` (Orca+Alt+Tab
  remains bound to the same action for muscle memory).
- **Connect (opens settings)** — shown when disconnected. The
  user's chosen convention is "connect = settings dialog" since
  configuring the relay is how you make a first connection
  succeed.

Implementation in `remote_menu.py`: a non-blocking Gtk.Dialog with
mnemonic-labeled buttons in a vertical Box. Heading line states
current status (connected/disconnected, role) so a screen-reader
user gets the picture on dialog activation. Each button records
the chosen callback; on response the dialog destroys itself
before invoking the callback (so a follow-up like Settings opens
in a clean z-order).

## 0.5.3 -- 2026-05-21

Host-mode braille mirroring (outbound).

Requires perf-branch commits `d846b2a70` (synthesize_key_event
promoted to a real commit) and `9c98efe6a` (braille_emitted hook).
On older Orca builds the extension catches the AttributeError on
subscribe and silently disables braille mirroring.

- **`braille_table.py`**: US computer braille ASCII→cell table
  plus a Unicode-braille-block passthrough. Lossy for non-Latin
  scripts but legible for English (the practical Orca-host →
  NVDA-master case). Liblouis-backed translation is a future
  swap-out behind the same `text_to_cells` interface.
- **`_on_braille_emitted`**: subscribes to the perf-branch hook;
  translates the rendered braille text to cells; sends NVDA Remote
  v2 `display` frames. `set_braille_info` is sent once per session
  on the first frame with the cell count so the master's braille
  viewer knows the dimensions.
- **Frame dedup**: braille_emitted fires on every paint including
  no-op refreshes. Identical (text, cursor_cell) frames are
  dropped before the wire.
- **Inbound `display` / `set_braille_info`**: logged only. Rendering
  arbitrary braille onto a local BrlAPI display from a master
  needs another perf-branch hook (push-from-extension) that hasn't
  landed yet; for now the Orca-master direction is listen-only on
  braille.
- **Menu toggle methods**: `toggle_speech_mirror()` and
  `toggle_braille_mirror()` are public on the extension instance
  so the remote menu (Phase 6) can wire them up. Both speak status
  on toggle and reset their dedup sentinels so a re-enable doesn't
  swallow a fresh first frame.

## 0.5.2 -- 2026-05-21

Bidirectional clipboard sync.

- **Inbound `set_clipboard_text`**: a peer's clipboard push lands in
  the local X clipboard via `controller.set_clipboard_text`. A brief
  spoken cue announces "peer pushed clipboard (N characters)" -- we
  speak length only, never the content (could be a password).
- **Outbound `push_clipboard()` method**: reads the local clipboard
  via `controller.get_clipboard_text`, sends as `set_clipboard_text`.
  Spoken confirmation with length. Wired to the remote menu in
  Phase 6; safe to call directly meanwhile.
- New protocol constants: `MSG_SET_CLIPBOARD_TEXT`,
  `MSG_SET_BRAILLE_INFO`, `MSG_DISPLAY` (latter two land in 0.5.3
  when braille mirroring arrives).

## 0.5.1 -- 2026-05-21

VK coverage expansion in `keymap.py`. 146 VK codes mapped (up from
the 0.4.x letter+digit+nav+F-key set):

- **Browser keys** (VK 0xA6..0xAC): Back / Forward / Refresh / Stop
  / Search / Favorites / Home → matching XF86 keysyms.
- **Media / volume keys** (VK 0xAD..0xB7): Mute / VolUp / VolDown
  / Next / Prev / Stop / Play-Pause / Mail / MediaSelect / App1
  / App2 → XF86Audio*, XF86Mail, XF86MyComputer, XF86Calculator.
- **IME keys** (VK 0x15, 0x17..0x19, 0x1C..0x1F): Hangul/Kana,
  Junja, Final, Hanja/Kanji, Convert, NonConvert, Accept,
  ModeChange → XK_Hangul, XK_Kanji, XK_Henkan_Mode, XK_Muhenkan
  etc. CJK-input users on an NVDA master can now drive the
  matching ibus / fcitx IME on a Linux slave.

## 0.5.0 -- 2026-05-21

Flow-control and silent-drop fixes. First wave of a larger Stage-3
push (bidirectional menu UI, clipboard, braille, master-side key
forwarding). 0.5.0 is the foundation; user-visible features land in
0.5.x and 0.6.x.

- **Fire-and-forget outbound CANCEL.** The 0.4.4 inline
  `await transport.send({"type":"cancel"})` inside the inbound key
  handler was serializing every subsequent inbound key behind the
  CANCEL's `writer.drain()`, which under VM-network jitter caused
  the "web browsing is very sluggish" symptom: each arrow press
  paid a per-key round-trip. CANCEL now schedules via
  `run_coroutine_threadsafe`; ordering vs any SPEAK reaction to the
  same key is still preserved because `writer.write()` buffers in
  scheduling order.
- **Bounded outbound buffer with drop counter.** `RemoteTransport.send`
  now checks `transport.get_write_buffer_size()` and drops the frame
  (incrementing a counter) when over 256 KiB. Stops unbounded
  backlog on congested links, which previously cascaded into
  drain-backpressure on every producer. The first drop and every
  50th drop are surfaced via the status callback.
- **Done-callback on every scheduled send.** New `_schedule_send`
  helper in the extension wraps `run_coroutine_threadsafe` and adds
  a done-callback so transport-side exceptions are logged with the
  kind of message that failed (`speech` / `cancel` / `key`), not
  silently swallowed.
- **Coalesce identical back-to-back outbound speech.** Orca emits
  the same string twice in a few legitimate flows (caret-moved +
  name-changed, focus-of-focus); the master's NVDA queues and speaks
  both. Host mode now compares last-sent text and skips a repeat.
  Reset on disable/disconnect so a reconnect doesn't accidentally
  swallow a fresh first utterance.
- **`pause_speech` inbound is now handled.** Treated the same as
  `cancel` (screen-reader use wants "stop now," not pause/resume).
  Previously we recognized the constant but never acted on it.
- **Bigger reader limit.** `asyncio.open_connection(limit=1MiB)` so
  a legitimate huge speak/braille frame doesn't trip
  `LimitOverrunError`. Default was 64 KiB.
- **Settings file written with 0o600.** Channel key (a shared
  passphrase) used to land at default umask -- usually 0o644.
  Created via `os.open(..., 0o600)`; an existing 0o644 file is
  tightened on next save.
- **Non-string sequence-item counter.** `extract_speech_text` now
  returns `(text, dropped)`. The extension accumulates the dropped
  count for the session and logs the total on disable, so a user
  with NVDA-side speech commands (LangChange, IndexCommand) can see
  what's being lost.

## 0.4.4 -- 2026-05-21

Master-queue cancel: send MSG_CANCEL to the master on every inbound
PRESS.

Root cause this addresses: 0.4.3 added a proactive local interrupt
on the slave, but the user is hearing speech through NVDA on the
master, and NVDA holds its own speech queue of every `speak`
message it has received. Cancelling the slave's speechd has no
effect on that queue, so pressing Ctrl on the master and arrowing
fast both left the queue draining at its own pace.

NVDA Remote v2.x's `cancel` wire message is the signal to flush
that queue. The slave now sends `{"type":"cancel"}` outbound
*before* synthesizing the key, on every inbound PRESS. The
existing local SpeechManager.InterruptSpeech idle callback stays
in place so the slave's own speechd is also cancelled
deterministically.

Send is inline-`await`ed inside `_handle_inbound_key` (now async)
so the CANCEL is strictly ordered ahead of any SPEAK we generate
by reacting to the same key.

## 0.4.3 -- 2026-05-21

Third round of host-mode fixes. Replaces the 0.4.2 tap-detection
with straight pass-through (per user request: slave behavior
should follow the slave's own layout, not the master's NVDA
layout) and adds two new defenses.

- **Locking keys pass through.** Removed the tap-vs-modifier
  detection added in 0.4.2. Caps Lock / Num Lock / Scroll Lock
  are now synthesized straight to the X server; a tap toggles
  the lock state as on any normal keyboard. The slave's caps
  lock is whatever the user has toggled it to, regardless of
  which layout NVDA is using on the master. If an NVDA-laptop
  modifier chord accidentally leaves the slave's caps lock on,
  one more tap clears it.
- **Strict autorepeat dedupe.** 0.4.2 dropped duplicate PRESS
  events only while an Orca modifier was held. User reports
  that even a single physical Insert+R press still loops the
  OCR "Recognizing." command, which means NVDA Remote can send
  duplicate PRESS frames for a single keystroke even without
  autorepeat. The dedupe now drops ANY PRESS for a keysym
  already in `_pressed_keysyms`. Cost: held-key autorepeat for
  typing (e.g. holding 'a' to fill a text field) no longer
  works over the link; tap each key instead.
- **Proactive speech interrupt on every inbound PRESS.** Orca's
  natural interrupt-on-key path (`KeyboardEvent._present`)
  should also fire on XTest-synthesized events, but under VM
  AT-SPI load with a backed-up speech-dispatcher queue the
  interrupt lags noticeably -- Control no longer silences
  speech, quick-arrow no longer cuts off the previous
  utterance. We now call `SpeechManager.InterruptSpeech` from
  the asyncio thread the moment a PRESS arrives, which mirrors
  the local feel.

## 0.4.2 -- 2026-05-21

Second round of host-mode safety fixes after the 0.4.1 VM test.

- **Autorepeat suppression for Orca-modifier chords.** NVDA Remote
  forwards OS-level key autorepeat as a stream of PRESS frames,
  one per repeat. Without dedupe, holding Insert+R rapid-fired
  Orca's OCR "Recognizing." command and the slave looped until
  release. The synth callback now drops a PRESS whose keysym is
  already in `_pressed_keysyms` while any Orca modifier is held.
  Plain-key autorepeat (typing, terminal scroll) is unaffected
  because no Orca modifier is in the held set in that context.
- **Tap-vs-modifier detection for Caps Lock / Num Lock /
  Scroll Lock.** 0.4.1 dropped these unconditionally to stop the
  XTest "press = toggle" foot-gun, which also killed legitimate
  taps. Replaced with the behaviour NVDA itself uses internally:
  a standalone tap (PRESS then RELEASE with no other key in
  between) synthesizes a real PRESS+RELEASE toggle; a press-with-
  chord (NVDA-laptop-modifier usage) is dropped on the RELEASE.
  Pending-lock state lives in `_lock_press_pending` on the
  asyncio thread and is cleared on transport teardown so a
  dropped connection mid-press leaves nothing stale.

## 0.4.1 -- 2026-05-21

Host-mode safety fixes from a VM session that locked Orca into
a stuck state requiring a forced power-off.

- **Refuse locking keysyms.** `Caps_Lock`, `Num_Lock`, and
  `Scroll_Lock` are no longer synthesized through XTest. XTest
  treats a press of any locking keysym as a TOGGLE of the X
  server's lock state, and the toggle outlives Orca itself
  because it lives in the X server, not in Orca. NVDA Remote
  forwards Caps Lock as the laptop-layout NVDA modifier; one
  press locked the slave's caps lock on, after which every
  alphabetic Orca chord stopped matching and an Orca restart
  could not undo it (the lock was in X). Slave-side users still
  use their own modifier locally.
- **Refuse own-command chords in host mode.** When the master
  sends an inbound key whose press would complete one of our own
  command bindings (Orca+Ctrl+R, Orca+Ctrl+Page Up/Down,
  Orca+Alt+Tab), the synth is dropped so the chord doesn't fire
  the local settings dialog / connect / disconnect / switch-side.
  The check uses `_pressed_keysyms` (what we've synthesized
  PRESS for and not yet RELEASED), which works because NVDA
  Remote forwards modifiers before the letter key.
- **Non-blocking settings dialog.** Replaced the blocking
  `Gtk.Dialog.run()` with a `response`-signal callback. A
  remote-master-triggered open of the settings dialog can no
  longer suspend the GLib main loop until a local user clicks
  something. `build_settings_dialog` now takes an `on_result`
  callback and returns the dialog immediately.
- **Suppress reconnect re-announces.** The "Orca Remote
  connected" / "...connected in host mode" announcement now
  fires only on the first `channel_joined` per session intent.
  Subsequent auto-reconnects (network blip, relay restart) are
  silent; explicit Connect / Disconnect chords and settings
  saves reset the gate so the next user-driven join is
  announced. Previously a flaky link to nvdaremote.com produced
  an announcement every ~30s (the backoff cap) which the master
  heard as "repeating things over and over."

## 0.4.0 -- 2026-05-21

Robustness fixes from first host-mode VM test.

- **Stuck-key safety net.** Host mode now tracks every keysym it
  synthesizes a `PRESS` for, and on transport teardown or extension
  disable it synthesizes the matching `RELEASE` for anything still
  held. `Atspi.generate_keyboard_event` goes through XTEST, so a
  press without a release outlives Orca itself; previously, a
  dropped connection mid-pair could leave a key held until the
  user force-killed the session. Releases are best-effort and
  swallowed if the AT-SPI device is already gone.
- **Auto-connect persistence.** Settings now carry an
  `auto_connect` flag (default True). Orca+Ctrl+Page Up flips it
  on; Orca+Ctrl+Page Down flips it off. On extension startup we
  only dial the relay if the flag is True, so an explicit
  disconnect followed by Orca restart stays offline until the user
  asks to reconnect. Settings file at
  `$XDG_DATA_HOME/orca/orca-remote-settings.json` will gain the
  new key on first save.

## 0.3.0 -- 2026-05-20

Stage 2 (Phase 1): host mode lands.

- New **Role** setting: "Receive speech (control a remote machine)"
  or "Broadcast speech (let a remote machine control us)".
- In host mode, the extension subscribes to the
  `speech_emitted` signal on the controller (perf-branch addition)
  and forwards every utterance to the relay as an NVDA-Remote
  `speak` message. No monkey-patching of the speech server.
- Inbound `speak` messages are now ignored when we're in host mode
  (prevents feedback if both peers somehow broadcast).
- **Orca + Alt + Tab** is no longer a placeholder: in client mode
  it toggles master focus between the remote session and the local
  machine. Focused-on-local mutes the inbound speech stream without
  dropping the connection; useful when a helper wants to use their
  own machine briefly. No-op in host mode (the slave has no remote
  session to focus away from); role changes happen in the settings
  dialog.
- Channel-joined announcement is role-aware
  ("connected" vs "connected in host mode").
- README rewritten for bidirectional scope.

## 0.2.0 -- 2026-05-20

- Rebind settings dialog from Orca+Shift+M to **Orca+Ctrl+R**.
- Split connect/disconnect into explicit chords:
  **Orca+Ctrl+Page Up** connects; **Orca+Ctrl+Page Down** disconnects.
- Reserve **Orca+Alt+Tab** for switching between host and remote
  machine (placeholder; lands with Stage 2 host mode).
- Auto-copy the server fingerprint to the clipboard on a pin mismatch
  so a screen-reader user can paste it directly into the settings
  field instead of memorising 64 hex characters.
- Workaround for the extension loader not registering the synthetic
  top-level parent (`orca_user_extension`) in sys.modules, which
  blocked relative imports.

## 0.1.0 -- 2026-05-20

Initial release. Stage 1 MVP.

- Client-only receive-speech mirror.
- NVDA Remote v2.x wire-protocol compatibility (newline-JSON over
  TLS, `protocol_version` + `join` handshake, `speak` / `cancel`
  / `motd` / `channel_joined` / `client_joined` / `client_left`
  inbound handling).
- Custom Gtk settings dialog bound to **Orca + Shift + M** (host,
  port, channel key, server fingerprint).
- Server cert pinned by SHA-256 fingerprint; first-connect
  bootstrap surfaces the actual fingerprint to the user.
- Settings persist to `$XDG_DATA_HOME/orca/orca-remote-settings.json`.
- Inbound speech routed through `controller.present_message_internal`,
  so it speaks through whatever TTS Orca is configured to use
  (espeak-ng / Voxin / sd-piper / etc.).
