# Agents Guide - Orca Remote

Update this file when necessary.

## What This Project Is

Orca Remote is an Orca screen reader plugin/extension that enables bidirectional communication between Orca on Linux and NVDA Remote-compatible peers using the NVDA Remote protocol v2.

The primary implementation is now a modern Orca user extension packaged as `remote.orca-ext`. The legacy `orca-customizations.py` monkey-patch plugin remains in the repository for older Orca installations and compatibility testing.

## Architecture Overview

Modern extension path:

```text
manifest.toml                  Orca extension metadata
__init__.py                    Package entry point; carries the literal
                               metadata released Orca reads by parsing it
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
inject.py                      host key injection: portal, ydotool, xdotool
                               (FIFO worker thread; never blocks GLib)
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

### Orca Build Compatibility

Two Orca APIs report outbound speech, and a build has only one of them:

- Perf-branch builds: `controller.subscribe_speech_emitted`.
- Released 51.1: `Extension.on_speech_output`.

`_HAVE_ON_SPEECH_OUTPUT` (module level in `remote.py`) selects which is
used; both funnel into `_on_speech_emitted`, exactly once per utterance.

Released 51.1 also has no `subscribe_braille_emitted`,
`subscribe_keyboard_event`, `synthesize_key_event`,
`display_braille_text`, or `get_clipboard_text`. Those features are
unavailable there and their absence is logged; do not make something
depend on them without a fallback.

Released Orca has no `--install-extension` flag either. Installing means
unpacking the archive into `$XDG_DATA_HOME/orca/extensions/remote/` and
approving it (`orca --approve-extension remote`); `install-extension`
does both. `__init__.py` must keep a ClassDef whose base is named
`Extension` with literal metadata: released Orca reads package metadata
by parsing that file and marks packages without it "Invalid".

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

The modern transport does not use CA trust. It computes the relay certificate SHA-256 fingerprint after the TLS handshake and refuses the connection, before sending anything, unless the configured fingerprint matches. On a blank or mismatched pin it reports the actual fingerprint, and `RemoteExtension._fingerprint_prompt_idle_cb` shows `settings_dialog.build_trust_dialog`: a first-use "Trust this server?" dialog, or a "certificate changed" warning whose default button is Stay disconnected. Trusting saves the pin and reconnects. The spoken copy-and-paste instructions are the fallback when no dialog can be shown.

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
| Orca+Ctrl+Shift+M | `mute_inbound_toggle` |
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
- `inject.py`
- `vendor/`
- `LICENSE`

Run tests:

```bash
python3 -m pytest tests/
```

There is no full Orca integration test suite in this repo. Prefer pure-function tests for protocol, keymap, and braille behavior, and verify live behavior in an Orca session when changing controller hooks or keyboard handling.

### Live testing on WSLg / Xvfb

Orca's AT-SPI keyboard device never sees key events in WSLg (synthetic
XTEST keys included), so **no Orca+ chord dispatches there** and
`_on_keyboard_event` never fires. Verified on Orca 51.alpha in WSLg:
a plain letter and Orca+ chords all produce zero Orca activity. Chord
registration and the bypass list therefore stay unit-tested; do not
"fix" a chord that looks dead in WSLg without checking a real session.

The mechanism, for whoever hits this next (measured on WSLg, Orca
51.alpha, 2026-10-17):

- GDK defaults to the **Wayland** display (`wayland-0`) there; the
  X11 display (`:0`) is Xwayland under Weston. Neither presents a
  keyboard: `Gdk.Display.list_devices()` on the Wayland side lists
  pointers only, and on `GDK_BACKEND=x11` the XI2 device list is five
  pointer devices with no keyboard at all (WSLg's seat only gains a
  keyboard while a WSLg desktop/RDP client is attached).
- `at-spi2-registryd`, which owns Orca's key grabs, is started by the
  **session bus**, not by Orca, and here it was started with `DISPLAY`
  unset -- its X11 backend has no connection at all. Restarting
  `at-spi-bus-launcher` with `DISPLAY=:0` fixes that (`/proc/<pid>/environ`
  then shows it), but it does not restore key delivery.
- at-spi grabs are XInput2 grabs. With no keyboard device for XI2,
  XTEST-injected keys still reach a **focused X11 window through core
  delivery** (verified: a GTK X11 entry received `hello` from
  `xdotool key`), but they never reach XI2 grabs -- so Orca stays blind
  even as an X11 client with a DISPLAY-aware registryd.

Net: key delivery to Orca needs a real session (Xorg or Wayland with a
compositor-provided keyboard). Everything below is what WSLg *can*
verify.

What a WSLg session *can* verify:

- Command handlers, called directly (write the handler name to a file
  that a throwaway test extension polls, as the 0.9.6 test rig did).
- Announcements and speech gating: the debug log prints every spoken
  string as `SPEECH: Speak '...'`, and inbound drops as
  `REMOTE: inbound speech dropped (<reason>)`.
- Transport behaviour against `orca-scripts/local_server.py` (relay)
  with `tests/fake_master.py` or a small peer script as the observer;
  the relay logs every inbound frame to
  `~/.local/share/orca/orca-remote-debug.log`.
- Grabs (`REMOTE: master grab active (orca device): N held, M refused`).

What it cannot: chord → handler dispatch, and X clipboard transfers
between processes — WSLg has no clipboard manager, so a selection set
by one process is not readable by another (reads inside the owning
process still work, which is enough to verify a Gtk fallback path).

## Important Conventions

- Prefer the modern extension path for new features.
- Keep the legacy monkey-patch plugin working unless explicitly removing it.
- No runtime dependencies beyond Python stdlib plus Orca/GTK/GI for the extension. Vendored utilities must stay under `vendor/`.
- GTK, Orca controller, and AT-SPI calls must run on the GLib main thread.
- Transport code must not block the GLib thread.
- Be defensive with Orca internals; hooks vary by Orca version.
- Keep error handling non-fatal so Orca does not crash if a feature is unavailable.
- Key injection runs on the `inject.py` worker thread. Never call the portal or the subprocess backends from the GLib thread; the consent prompt and fork/exec would freeze Orca.
- Use `apply_patch` for manual edits.

## Install / Uninstall Scripts

- `build-orca-ext.sh` builds the modern extension archive.
- `install-extension` unpacks the archive into the user extensions
  directory and approves it (the path that works on released Orca,
  which has no `--install-extension` flag).
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

## Agent skills

### Issue tracker

Issues are tracked in this repo's GitHub Issues, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
