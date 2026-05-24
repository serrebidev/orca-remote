# Orca Remote — troubleshooting

Symptoms we've actually hit, with their root cause and fix. If
something here doesn't match what you're seeing, check
`~/.local/share/orca/orca-debug.txt` and grep for `REMOTE:`.

## "Web browsing on the remote machine is very sluggish"

**Symptom (pre-0.5.0).** When connected and using the master's
NVDA to control the slave, holding an arrow key in Firefox or
Chrome on the slave felt laggy; each press paid noticeable
latency. Heavy pages could lock the VM enough to need a power
cycle.

**Root cause.** The inbound key handler `await`ed
`transport.send({"type":"cancel"})` inline. The transport's read
loop processes inbound frames sequentially, so each rapid arrow
press serialized behind the previous CANCEL's `writer.drain()`.
Under VM-network jitter that drain took milliseconds per key.

Compounding: autorepeat dedup means held-key repeats are dropped
(rapid tap is the workaround), so the user was already tapping
fast, making the per-key drain more visible. Speech-dispatcher
backlog from the resulting torrent of speak events could also
make speechd unresponsive.

**Fix (0.5.0).** CANCEL is now fire-and-forget via
`_schedule_send` (built on `asyncio.run_coroutine_threadsafe`).
Ordering vs the SPEAK reaction to the same key is preserved
because `writer.write()` buffers in scheduling order; the read
loop never waits.

Also landed: a bounded outbound buffer guard
(`get_write_buffer_size() > 256 KiB` → drop) so a congested link
can't produce unbounded backlog.

## "Caps Lock is stuck and Orca restart doesn't help"

**Symptom.** After a session, every alphabetic Orca chord stopped
matching. Caps Lock light was on. Restarting Orca didn't help.

**Root cause.** `Atspi.generate_keyboard_event` goes through XTEST.
XTest treats a press of any locking keysym
(Caps_Lock / Num_Lock / Scroll_Lock) as a TOGGLE of the X-server
lock state, which outlives Orca itself. NVDA's laptop-layout
modifier is Caps Lock; one press locked the slave's caps lock on,
and Orca restart couldn't undo it because the lock lives in the
X server.

**Fix (0.4.1 → 0.4.3 progression).**

- 0.4.1: refuse locking keysyms unconditionally. Worked but also
  killed legitimate Caps Lock taps.
- 0.4.2: tap-vs-modifier detection. Standalone tap → real toggle;
  press-with-chord → drop on release.
- 0.4.3: straight pass-through with strict autorepeat dedup. The
  slave's lock state follows the slave's keyboard regardless of
  master's layout. One extra tap clears any accidental lock.

**Recovery if it ever happens again.** Tap the offending lock key
once on the slave's local keyboard — that toggles the X-server
state. If you can't reach the slave (VM is wedged): hard restart
the X server (`Ctrl+Alt+Backspace` if enabled, or VM power cycle).

## "Stuck modifier after dropped connection"

**Symptom.** Master had Shift held; connection dropped before the
Shift release frame arrived. Slave's Shift was permanently down.

**Root cause.** Same as Caps Lock — XTEST PRESS without RELEASE
outlives the process.

**Fix (0.4.0).** `_pressed_keysyms` tracks every keysym we synth
a PRESS for. On `_stop_transport` and `disable`, every still-held
keysym gets a synthesized RELEASE. Best-effort: swallowed if the
AT-SPI device is gone.

## "Master hears 'connected in host mode' every 30 seconds"

**Symptom.** Repetitive announcement every ~30s when the relay
link was flaky.

**Root cause.** Reconnect-on-EOF was firing every backoff cap
(30s) and each `channel_joined` was announced.

**Fix (0.4.1).** `_announced_join` is set True on the first join
and reset only by explicit Connect / Disconnect / settings-save
/ disable. Silent auto-reconnects don't re-announce.

## "Pressing Insert+R on the master opens settings on the slave"

**Symptom.** Master fires an Orca command chord; the master's
NVDA + remote forwards the keys; the slave receives them, XTEST
delivers them, and Orca on the slave runs the bound command.

**Fix (0.4.1).** Own-chord refusal. The synth callback uses
`_pressed_keysyms` to detect when the master has modifier keysyms
held; if PRESS would complete one of our own chords (Orca+Ctrl+R,
Orca+Ctrl+Page Up/Down, Orca+Alt+Tab), the synth is dropped.

## "Master hears every utterance twice"

**Symptom.** Two NVDA reads of the same string back-to-back.

**Root cause.** Orca emits the same string twice in legitimate
flows (caret-moved followed by name-changed, focus-of-focus). On
the wire each duplicate becomes a separate `speak` frame; the
master's NVDA queues both.

**Fix (0.5.0).** `_last_outbound_speech` coalesces consecutive
identical strings host-side before they go on the wire. Reset on
disable/disconnect so a fresh reconnect doesn't accidentally
swallow the next-first utterance.

## "Settings file is 0o644 (anyone on the box can read the channel key)"

**Fix (0.5.0).** Settings are now written via `os.open(O_CREAT,
0o600)` and `os.chmod(0o600)` on every save so even a pre-fix
0o644 file gets tightened.

## "Fingerprint mismatch announcement and I can't paste 64 hex chars"

**Symptom.** Relay rotates cert (or first connect with empty
pin); Orca announces "server fingerprint did not match" and the
new fingerprint, which the user has to type into the settings
field.

**Fix (0.2.0).** The actual fingerprint is also copied to the X
clipboard via `controller.set_clipboard_text`. The announcement
ends with "paste with Control+V."

To pre-fetch from the shell:

```sh
openssl s_client -servername nvdaremote.com -connect nvdaremote.com:6837 \
    < /dev/null 2>/dev/null \
  | openssl x509 -fingerprint -sha256 -noout \
  | sed 's/SHA256 Fingerprint=//; s/://g' \
  | tr '[:upper:]' '[:lower:]'
```

## "I'm on Wayland and host mode doesn't inject keys"

**Symptom.** Host mode connects fine; inbound keys are received;
nothing happens on the slave's focused application.

**Root cause.** `Atspi.generate_keyboard_event` requires XTEST.
On a real Wayland session there's no XTEST — synth fails or is
silently no-op.

**Workaround.** Use X11 (or XWayland with a `XDG_SESSION_TYPE=x11`
override). The Linux master direction has no such constraint
because there's no synth involved on a master.

There's no Wayland synth path on the roadmap; Wayland's security
model deliberately excludes it. A future user-level
workaround could use libei (XDG remote desktop portal) but that's
a substantial rewrite.

## "I want to know how many speech items got dropped this session"

**Where to look.** On extension disable, the log line:

```
REMOTE: dropped N non-string sequence item(s) over this session
(LangChange / IndexCommand / etc.)
```

These are speech-command items in a NVDA `speak` frame that we
turn into "" because we don't render speech commands (yet). High
counts mean you're losing language-tag information or index
marks; the audible speech is still correct, but TTS
language-switching won't happen.

## "Outbound buffer congested" in the log

**Where it comes from.** `RemoteTransport.send` saw
`writer.transport.get_write_buffer_size() > 256 KiB` and dropped
the frame.

**What it means.** The relay or the TCP path is slow enough that
we can't keep up. Speech and braille drops have happened; key
synth itself doesn't go through this path (synth is local), so
control is unaffected. The first drop and every 50th in an
episode are logged.

**What to do.** If it's transient (network blip), nothing — the
drop is the right behavior. If it's persistent, check relay
reachability (`ping`, `mtr`) and consider self-hosting closer to
both peers.
