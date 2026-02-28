# Agents Guide — Orca Remote

## What This Project Is

Orca Remote is an Orca screen reader plugin that enables bidirectional communication between **Orca** (GNOME/Linux screen reader) and **NVDA** (Windows screen reader) using the **NVDA Remote protocol v2**. It allows either screen reader to control the other over an SSL/TLS network connection through a relay server.

This is **not** a standalone application. It is a monkey-patch plugin loaded by Orca at startup via `~/.local/share/orca/orca-customizations.py`.

## Architecture Overview

```
orca-customizations.py          Entry point — loaded by Orca on startup
  ├── Patches SpeechServer       (forward local speech to remote)
  ├── Patches KeyboardEvent      (intercept keys when controlling remote)
  ├── Registers 6 Orca gestures  (keybindings for all remote features)
  └── Creates transport + controller instances

orca-scripts/
  ├── transport.py               SSL/TCP networking, NVDA Remote protocol handshake
  ├── remote_controller.py       State machine, all 24 message handlers, audio cues
  ├── local_machine.py           Executes remote commands locally (speech, keys, clipboard, tones, braille)
  ├── connect_dialog.py          GTK 3 accessible dialog for connection parameters
  ├── callback_manager.py        Simple event dispatcher (event → list of callbacks)
  └── serializer.py              JSON serialization with newline delimiters
```

## Key Concepts

### Plugin Mechanism
Orca executes `~/.local/share/orca/orca-customizations.py` on startup. The plugin works by **monkey-patching** Orca's internal classes:
- `SpeechServer._speak`, `.speak_character`, `.stop` — intercepted to forward speech to the remote NVDA
- `KeyboardEvent.process` — intercepted to forward keystrokes when in remote control mode

### NVDA Remote Protocol v2
- **Transport**: SSL/TLS over TCP, default port 6837
- **Serialization**: JSON objects delimited by `\n` (newline), UTF-8 encoded
- **Handshake**: Client sends `protocol_version` then `join` with channel key and connection type
- **Connection types**: `"master"` (controller) and `"slave"` (controlled)
- **24 message types** handled: see README.md for the full table

### Control State Machine
`RemoteController` has two states:
- `LOCAL` (default) — keystrokes processed by Orca normally
- `REMOTE` — keystrokes forwarded to NVDA via `{"type": "key", ...}` messages

The toggle gesture (Orca+Alt+Tab) is **always** processed locally even in REMOTE mode — it's the escape hatch.

### Message Flow (outbound, slave mode)
```
Orca speaks → my_speak() → transport.send(type="speak") → JSON → SSL → relay server → NVDA
```

### Message Flow (inbound)
```
NVDA → relay server → SSL → transport.handle_server_data() → parse()
  → callback_manager.call_callbacks("msg_<type>")
  → RemoteController._on_remote_<type>()
  → LocalMachine.<action>()
```

### Key Injection (NVDA → Orca)
Remote key events are injected into the local Linux system using `xdotool keydown/keyup`. Key names are mapped from NVDA conventions to X11 keysym names via `LocalMachine._map_key_name()`.

### Audio
Tones use `sox` (`play` command) with a pure-Python fallback that generates temporary WAV files and plays them via `paplay`. Wave files use `paplay` or `aplay`.

### Clipboard
Uses GTK clipboard (`Gtk.Clipboard`) as primary, with `xclip` as fallback.

## File Details

### `orca-customizations.py` (entry point, ~297 lines)
- **Config**: Lines 7-10 — `YOUR_NVDAREMOTE_SERVER_ADDRESS`, `_PORT`, `_KEY` (replaced by install script)
- **Speech patches**: Lines 60-79 — monkey-patches `SpeechServer` to forward speech
- **Key interception**: Lines 83-144 — patches `KeyboardEvent.process` for remote control mode
- **Gesture handlers**: Lines 148-208 — 6 handler functions for keybindings
- **Gesture registration**: Lines 212-296 — `_register_gestures()` runs on GLib idle after 2-second delay
- Uses `getattr()` with fallbacks for Orca keybinding constants to support different Orca versions

### `orca-scripts/transport.py` (~200 lines)
- `Transport` — base class with `callback_manager`, `connected` flag
- `TCPTransport` — SSL socket, `select.select()` read loop, queue-based send thread, auto-reconnect
- `RelayTransport` — NVDA Remote handshake (`protocol_version` + `join`), `reconnect()` method
- `ConnectorThread` — daemon thread, retries connection every 5 seconds on failure
- **SSL**: Uses `ssl.SSLContext(PROTOCOL_VERSION)` with `check_hostname=False`

### `orca-scripts/remote_controller.py` (~308 lines)
- Registers 24 `msg_*` callbacks on the transport's callback_manager
- Tracks `connected_clients` dict (client_id → {connection_type})
- `_play_cue()` plays notification beep sequences in background threads
- On disconnect, auto-reverts to LOCAL control state

### `orca-scripts/local_machine.py` (~275 lines)
- `is_muted` flag — when True, all incoming speech/audio/tones are silently dropped
- `speak()` — handles both string and list sequences from NVDA
- `beep()` — sox primary, python WAV generation fallback
- `send_key()` — xdotool subprocess for key injection
- `set_clipboard_text()` / `get_clipboard_text()` — GTK primary, xclip fallback

### `orca-scripts/connect_dialog.py` (~152 lines)
- GTK 3 dialog, must run on the GTK main thread via `GLib.idle_add()`
- `run_threadsafe(callback)` — safe to call from any thread
- All widgets have AT-SPI accessible names and mnemonic underlines
- Radio buttons for master/slave mode, generate key button (random 7-digit)

### `orca-scripts/callback_manager.py` (~31 lines)
- `defaultdict(list)` mapping event_type → [callbacks]
- Wildcard `'*'` callbacks receive all events with `(type, *args, **kwargs)`
- Exceptions in callbacks are logged but don't break execution

### `orca-scripts/serializer.py` (~16 lines)
- `serialize(type=None, **obj)` → `b'{"type": "...", ...}\n'`
- `deserialize(data)` → dict

## Registered Gestures

| Gesture | Handler | Modifier Mask Used |
|---|---|---|
| Orca+Alt+Tab | `_toggle_remote_control` | `orca_alt` |
| Orca+Alt+PageUp | `_show_connect_dialog` | `orca_alt` |
| Orca+Alt+PageDown | `_disconnect` | `orca_alt` |
| Ctrl+Shift+Orca+C | `_push_clipboard` | `ctrl_shift_orca` |
| Orca+Alt+M | `_toggle_mute` | `orca_alt` |
| Orca+Shift+Delete | `_send_ctrl_alt_del` | `orca_shift` |

**Conflict avoidance**: These were verified against all official GNOME Orca keybindings in both desktop (Insert) and laptop (CapsLock) layouts. `Orca+Alt+M` was specifically chosen over `Orca+Shift+M` because `CapsLock+M` is "previous character" in laptop flat review mode.

## Important Conventions

- **No tests**: There is no test suite. This is a plugin that runs inside Orca's process.
- **No dependencies beyond stdlib + GTK**: The plugin must work with just Python 3 standard library plus GTK 3 (which Orca already requires). External tools (`xdotool`, `sox`, `xclip`) are optional with graceful fallbacks.
- **Tabs in transport.py / callback_manager.py**: These files use tabs for indentation (inherited from original NVDA Remote code). All other files use 4-space indentation.
- **Monkey-patching pattern**: Always save the original function reference before patching, call it inside the wrapper, and return its result.
- **Thread safety**: GTK operations must go through `GLib.idle_add()`. Transport runs in daemon threads. Gesture registration is delayed 2 seconds to wait for Orca initialization.
- **Defensive attribute access**: Use `hasattr()` and `getattr()` when accessing Orca internals since the API varies between versions.
- **Error handling**: All patches and registrations are wrapped in try/except to avoid breaking Orca if something fails. Failures are printed to stdout/logged but never raise.

## Install / Uninstall Scripts

- `install` — Takes 3 args (host, port, key), does string replacement in `orca-customizations.py` to inject config, copies everything to `~/.local/share/orca/`
- `uninstall` — Removes installed files, restores backup of original `orca-customizations.py`
- **Install without args**: `./install` works with no arguments. The plugin loads but does not auto-connect — user connects via the dialog (Orca+Alt+PageUp). If args are passed (`./install host port key`), the sentinels are replaced and auto-connect is enabled.
- The sentinel values `"host"`, `6837`, and `"key"` in orca-customizations.py must remain exactly as-is in the repo. The `_has_config` check at startup compares against these to decide whether to auto-connect.

## Common Tasks for Agents

### Adding a new message type handler
1. Add a callback registration in `RemoteController.__init__()`: `cb.register_callback('msg_<type>', self._on_<type>)`
2. Add the handler method: `def _on_<type>(self, **kwargs)`
3. If it needs local execution, add a method to `LocalMachine`

### Adding a new gesture
1. Add a handler function in `orca-customizations.py` with signature `(script=None, inputEvent=None)`
2. Add a tuple to the `gesture_bindings` list in `_register_gestures()`
3. Verify the keybinding doesn't conflict with Orca's official shortcuts (check both desktop and laptop layouts)
4. Update README.md gesture table and install script output

### Changing connection parameters at runtime
Use `transport.reconnect(address=(host, port), channel=key, connection_type="master"|"slave")`. This closes the existing connection and starts a new `ConnectorThread`.

## External References

- [NVDA Remote protocol](https://github.com/NVDARemote/NVDARemote) — the protocol this plugin implements
- [GNOME Orca source](https://gitlab.gnome.org/GNOME/orca) — the screen reader this plugin extends
- [Orca keybindings reference](https://help.gnome.org/users/orca/stable/commands.html.en) — official shortcut list
- NVDA Remote default relay port: **6837**
- NVDA Remote protocol version: **2**
