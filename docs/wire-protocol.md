# Orca Remote — wire protocol

The wire is **newline-delimited JSON over TLS**. Each message is a
single JSON object terminated by `\n`, framed by
`protocol.encode` / `protocol.decode`. Compatibility target is
NVDA Remote v2.x; the public relay (`nvdaremote.com:6837`) and
self-hosted v2 relays both work.

## Connection lifecycle

1. **TLS handshake.** Self-signed cert; trust is established by
   SHA-256 fingerprint pin against the DER-encoded peer cert.
   Hostname check is disabled (pin replaces it).
2. **`protocol_version` outbound.** Announces we speak v2.
3. **`join` outbound.** Subscribes us to the channel with a role
   (`master` to listen, `slave` to broadcast).
4. **Inbound `channel_joined`.** Relay confirms membership.
5. **Inbound `client_joined`.** Another peer arrived on the same
   channel.
6. **Steady state.** Speak / cancel / key / display / clipboard
   messages flow.
7. **Inbound `client_left`.** Peer dropped.
8. **TLS close / read EOF.** Triggers reconnect with exponential
   backoff (1s, 2s, 5s, 10s, 30s, repeat 30s).

## Message vocabulary

### Sent by us

| `type`              | Direction enabled | Payload | Purpose |
|---------------------|-------------------|---------|---------|
| `protocol_version`  | always            | `{version: 2}` | Handshake. |
| `join`              | always            | `{channel, connection_type}` | Subscribe to channel. |
| `speak`             | host only         | `{sequence: [str, ...]}` | Mirror our speech. |
| `cancel`            | host only         | `{}` | Drain master's NVDA speech queue when a key arrives. |
| `set_clipboard_text`| either            | `{text: str}` | Push our local clipboard. |
| `set_braille_info`  | host only         | `{name: "orca", numCells: int}` | Tell master our braille dimensions. Sent once per session on first braille frame. |
| `display`           | host only         | `{cells: [int, ...]}` | Mirror our braille buffer; each int is one cell byte (low 8 bits = dots 1..8). |

### Received by us

| `type`              | Handled in role | Action |
|---------------------|-----------------|--------|
| `channel_joined`    | both            | First per session: speak "connected" / "connected in host mode". Subsequent (auto-reconnect): silent. |
| `client_joined`     | both            | (Currently ignored; future: speak "peer joined".) |
| `client_left`       | both            | Speak "peer left." |
| `motd`              | both            | Log at debug level. |
| `speak`             | client only     | Coalesce non-string sequence items, extract text, speak via `controller.present_message_internal`. Gated on `focus_on_remote AND NOT inbound_speech_muted`. |
| `cancel`            | client only     | `controller.execute_command_internal("SpeechManager", "InterruptSpeech")`. |
| `pause_speech`      | client only     | Same as cancel. (Screen-reader users want "stop", not "pause/resume".) |
| `key`               | host only       | Synthesize via `controller.synthesize_key_event` after VK→keysym translation; dedup, refuse own chords, schedule outbound cancel to drain master's queue. |
| `set_clipboard_text`| both            | `controller.set_clipboard_text`; brief spoken "peer pushed clipboard (N chars)" cue (length only — could be a password). |
| `set_braille_info`  | both            | Track peer's `numCells` in `_peer_braille_cells` for informational use. |
| `display`           | client only     | Render incoming cells as Unicode braille block characters (U+2800 + cell_byte) via `controller.display_braille_text`. Same gating as `speak` (`focus_on_remote AND NOT inbound_speech_muted`). Shipped 0.6.1. |
| `nvda_not_connected`| both            | Constant defined, no current handler. |

Anything not in the table is logged as "unhandled message type: ..."
and dropped.

## Key message format (NVDA Remote v2)

```json
{
  "type": "key",
  "vk_code": 65,          // Windows virtual-key code
  "extended": false,      // Windows "enhanced keyboard" flag
  "pressed": true,
  "scan_code": 0          // ignored; we route by vk_code
}
```

The slave-side handler:

1. `keymap.vk_to_keysym(vk_code, extended)` → X11 keysym (or 0
   to drop unmapped).
2. Dedup: drop a PRESS for a keysym already in `_pressed_keysyms`.
3. Own-chord refusal: if PRESS would complete one of our own
   bindings (Orca+Ctrl+R, Orca+Ctrl+Page Up/Down, Orca+Alt+Tab),
   drop the synth.
4. `_schedule_send({"type": "cancel"})` outbound (fire-and-forget).
5. `GLib.idle_add(InterruptSpeech)` for the slave's local
   speech-dispatcher.
6. `GLib.idle_add(synth keysym, pressed)` to actually call
   `controller.synthesize_key_event`.
7. Track PRESS in `_pressed_keysyms`; clear on RELEASE.

## Speak message format

```json
{
  "type": "speak",
  "sequence": [
    {"type": "LangChangeCommand", "language": "en_US"},
    "hello ",
    {"type": "IndexCommand", "index": 1},
    "world"
  ]
}
```

`extract_speech_text` returns `("hello world", 2)` — the two
non-string items are counted (surfaced as
`_dropped_nonstring_items` on disable so the user can see if
they're losing structure).

## Display message format

NVDA Remote v2.x:

```json
{
  "type": "display",
  "cells": [0x13, 0x0a, 0x00, ...]
}
```

Each integer is one braille cell, low byte = dot pattern with
bits 0..7 mapping to dots 1..8 (same encoding as the Unicode
braille block).

We populate `cells` via `braille_table.text_to_cells(text)`:

- Unicode braille block chars (U+2800..U+28FF) pass through as
  their low byte.
- Printable ASCII looks up the US computer braille table.
- Everything else maps to an empty cell (0x00).

Lossy for non-Latin scripts. See
[architecture.md](architecture.md) → "Liblouis-backed braille
translation" for the future plan.

## Fingerprint pin

Implemented in `transport._peer_fingerprint(writer)`. After TLS
handshake we:

1. `writer.get_extra_info("ssl_object").getpeercert(binary_form=True)` → DER bytes.
2. `hashlib.sha256(der).hexdigest()` → 64 hex chars.
3. Compare to `_normalize_fingerprint(self._expected_fp)` (strips
   colons/spaces, lowercases).
4. Mismatch → `FingerprintMismatch(expected, actual)`. The reconnect
   loop catches this, surfaces `actual` via the status callback,
   and DOES NOT retry — the user has to update the setting first.

Bootstrap UX: with an empty pin, every connect raises
`FingerprintMismatch(expected="(unset)", actual=<actual>)`. The
extension catches the actual value, copies it to the X clipboard
via `controller.set_clipboard_text`, and announces "fingerprint
copied; paste with Control+V."

## Framing limits

- Inbound reader: `asyncio.open_connection(limit=1MiB)`. Enough
  for any legitimate speak/braille frame; rejects unbounded
  abuse.
- Outbound buffer guard: `RemoteTransport.send` drops the frame
  if `writer.transport.get_write_buffer_size() > 256 KiB`. First
  drop and every 50th drop in an episode get surfaced via the
  status callback.

## Reconnect backoff

`_BACKOFF_SCHEDULE = (1, 2, 5, 10, 30)` seconds. After a clean
close (`reader.readline()` returned empty), `attempt` resets to
0. After an error, `attempt` increments. The wait is itself
interruptible by `stop_event` so explicit Disconnect / disable
exits cleanly.

The "announce-once-per-session-intent" gate (`_announced_join`)
exists exactly because a flaky link with the 30s backoff cap was
producing a "connected in host mode" announcement every 30
seconds, which the master heard as repetitive noise. Explicit
connect / disconnect / settings-save reset the gate; silent
auto-reconnects don't.
