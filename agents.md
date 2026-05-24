# Agents Guide - Orca Remote

Update this file when necessary.

## What This Project Is

Orca Remote is an Orca screen reader plugin/extension that enables bidirectional communication between Orca on Linux and NVDA Remote-compatible peers using the NVDA Remote protocol v2.

The primary implementation is now a modern Orca user extension packaged as `remote.orca-ext`. The legacy `orca-customizations.py` monkey-patch plugin remains in the repository for older Orca installations and compatibility testing.

## Architecture Overview

Modern extension path:

```text
manifest.toml                  Orca extension metadata
remote.py                      RemoteExtension entry point and state machines
  - registers Orca commands
  - owns settings lifecycle
  - bridges GLib main thread and asyncio transport thread
  - mirrors speech/braille/clipboard
  - forwards or synthesizes remote keys

transport.py                   asyncio TLS transport, reconnect, fingerprint pinning
protocol.py                    NVDA Remote newline-JSON framing and helpers
keymap.py                      Windows VK <-> X11 keysym mapping
braille_table.py               text -> braille cell mapping
settings_dialog.py             GTK 3 non-blocking settings dialog
vendor/orca_ext_utils/         vendored KeysetGrab support for keyboard consume
```

Legacy compatibility path:

```text
orca-customizations.py          Loaded by Orca from ~/.local/share/orca/
orca-scripts/
  transport.py                  SSL/TCP networking and NVDA Remote handshake
  remote_controller.py          Legacy controller and message handlers
  local_machine.py              Local speech/key/clipboard/audio/braille actions
  connect_dialog.py             Legacy GTK connect dialog
  callback_manager.py           Event dispatcher
  serializer.py                 JSON newline framing
  local_server.py               Built-in legacy relay server
```

## Modern Extension Concepts

### Orca Extension API

`RemoteExtension` subclasses `orca.extension.Extension`. Commands are registered through `_get_commands()` and use Orca `KeyboardCommand` bindings.

The extension starts an asyncio event loop in a daemon thread. Orca, GTK, AT-SPI, and controller calls must stay on the GLib main thread. Cross-thread calls use:

- GLib to asyncio: `asyncio.run_coroutine_threadsafe(...)`, normally through `_schedule_send`.
- asyncio to GLib: `GLib.idle_add(...)`, returning `False` for one-shot callbacks.

### Settings

Settings live in:

```text
$XDG_DATA_HOME/orca/orca-remote-settings.json
```

Usually this is:

```text
~/.local/share/orca/orca-remote-settings.json
```

The settings file is written with `0o600` permissions because the channel key is a shared secret.

### TLS Fingerprint Pinning

The modern transport does not use CA trust or TOFU. It computes the relay certificate SHA-256 fingerprint after TLS handshake and refuses the connection unless the configured fingerprint matches. On mismatch, it reports the actual fingerprint so the UI can copy it to the clipboard.

### Roles

- `client` maps to NVDA Remote `master`: receive remote speech/braille and send keys.
- `host` maps to NVDA Remote `slave`: broadcast local speech/braille and accept keys.

### Key Forwarding

Modern key forwarding uses `keymap.keysym_to_vk()` for outbound keys and `keymap.vk_to_keysym()` for inbound keys.

When the client is focused on the remote:

- Forwardable keys are sent as NVDA Remote `key` messages.
- Orca Remote's own command chords bypass forwarding and dispatch locally.
- `KeysetGrab` tries to consume forwarded keys at the AT-SPI level so they do not also act on the focused local app.

Inbound host-side key synthesis keeps modifier keys sticky but taps ordinary keys with press+release in the same idle callback to avoid X11 auto-repeat floods.

### Braille

Outbound host braille uses `braille_table.text_to_cells()` and sends NVDA Remote `display` frames. Inbound client braille renders cells as Unicode braille block characters through `controller.display_braille_text` when that Orca hook exists.

### Clipboard

Modern extension clipboard operations use the Orca controller clipboard helpers. Legacy mode uses GTK clipboard first, with `xclip` fallback.

## Legacy Plugin Concepts

Orca executes `~/.local/share/orca/orca-customizations.py` on startup. The legacy plugin monkey-patches Orca internals:

- `SpeechServer._speak`, `speak_character`, and `stop` to forward speech.
- `KeyboardEvent.process` to forward keystrokes while remote control is active.

The sentinel values `"host"`, `6837`, and `"key"` in `orca-customizations.py` must remain exactly as-is. The legacy installer replaces them only when auto-connect arguments are provided.

## Shortcuts

Modern extension shortcuts:

| Gesture | Handler |
|---|---|
| Orca+Ctrl+R | `open_settings` |
| Orca+Ctrl+M | `mute_inbound_toggle` |
| Orca+Ctrl+PageUp | `connect` |
| Orca+Ctrl+PageDown | `disconnect_session` |
| Ctrl+Shift+Orca+C | `push_clipboard` |
| Orca+Alt+Tab | `switch_side` |

Legacy shortcuts:

| Gesture | Handler |
|---|---|
| Orca+Alt+Tab | `_toggle_remote_control` |
| Orca+Alt+PageUp / Orca+Alt+C | `_show_connect_dialog` |
| Orca+Alt+PageDown / Orca+Alt+D | `_disconnect` |
| Ctrl+Shift+Orca+C | `_push_clipboard` |
| Orca+Alt+M | `_toggle_mute` |
| Orca+Shift+Delete | `_send_ctrl_alt_del` |

## Build And Test

Build modern extension:

```bash
./build-orca-ext.sh .
```

The archive should include only:

- `manifest.toml`
- `__init__.py`
- `remote.py`
- `settings_dialog.py`
- `transport.py`
- `protocol.py`
- `keymap.py`
- `braille_table.py`
- `vendor/`
- `LICENSE`

Run tests:

```bash
python3 -m pytest tests/
```

There is no full Orca integration test suite in this repo. Prefer pure-function tests for protocol, keymap, and braille behavior, and verify live behavior in an Orca session when changing controller hooks or keyboard handling.

## Important Conventions

- Prefer the modern extension path for new features.
- Keep the legacy monkey-patch plugin working unless explicitly removing it.
- No runtime dependencies beyond Python stdlib plus Orca/GTK/GI for the extension. Vendored utilities must stay under `vendor/`.
- GTK, Orca controller, and AT-SPI calls must run on the GLib main thread.
- Transport code must not block the GLib thread.
- Be defensive with Orca internals; hooks vary by Orca version.
- Keep error handling non-fatal so Orca does not crash if a feature is unavailable.
- Use `apply_patch` for manual edits.

## Install / Uninstall Scripts

- `build-orca-ext.sh` builds the modern extension archive.
- `install` installs the legacy `orca-customizations.py` path.
- `uninstall` removes the legacy install and restores a legacy backup if present.

## Git / GitHub Workflow

- Default branch is `master`.
- Before GitHub-visible work, run `git status --short --branch` and `git ls-remote --symref origin HEAD refs/heads/master`.
- After pushing, verify the remote with `git ls-remote origin refs/heads/master`.
- Only push or update a non-`master` branch when the user explicitly asks for branch work.

## Common Tasks

### Adding A Modern Message Handler

1. Add or confirm a message constant in `protocol.py`.
2. Handle it in `RemoteExtension._on_message()`.
3. Marshal to GLib with `GLib.idle_add()` if it touches Orca, GTK, AT-SPI, or clipboard state.
4. Add focused tests where the logic is pure enough to isolate.

### Adding A Modern Shortcut

1. Add a `KeyboardCommand` in `RemoteExtension._get_commands()`.
2. If it must stay local while forwarding, update `_OWN_CTRL_CHORD_KEYSYMS` or `_OWN_ALT_CHORD_KEYSYMS`.
3. Update `tests/test_bypass_chords.py`.
4. Update README.md.

### Adding A Legacy Message Handler

1. Add a callback registration in `RemoteController.__init__()`: `cb.register_callback('msg_<type>', self._on_<type>)`.
2. Add `def _on_<type>(self, **kwargs)`.
3. If it needs local execution, add a method to `LocalMachine`.

## External References

- NVDA Remote protocol: https://github.com/NVDARemote/NVDARemote
- GNOME Orca source: https://gitlab.gnome.org/GNOME/orca
- Orca keybindings reference: https://help.gnome.org/users/orca/stable/commands.html.en
- NVDA Remote default relay port: `6837`
- NVDA Remote protocol version: `2`
