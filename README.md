# Orca Remote

Orca Remote lets Orca on Linux work with NVDA Remote-compatible peers over the NVDA Remote v2 protocol. It can act as a client that controls and listens to a remote machine, or as a host that broadcasts Orca speech and braille while accepting remote key input.

The modern install path is an Orca user extension (`.orca-ext`). The older `orca-customizations.py` installer is still included for users who need the legacy monkey-patch plugin.

## Features

- NVDA Remote v2-compatible TLS relay connection.
- Client and host roles.
- Bidirectional speech mirroring.
- Braille mirroring, including inbound rendering on modern Orca builds.
- Master-side key forwarding with command-chord bypass.
- Host-side key synthesis with stuck-key and auto-repeat guards.
- Bidirectional clipboard sync.
- Persistent settings stored with user-only file permissions.
- SHA-256 relay certificate fingerprint pinning.
- Reconnect backoff and write-buffer protection.

File transfer is not implemented. Use clipboard sync for text, or a
separate file-transfer tool for files.

## Requirements

- Orca with the user-extension API. The extension manifest targets Orca `51.alpha`.
- Python 3.12 or newer.
- GTK 3 and GLib, normally present with Orca.
- A reachable NVDA Remote v2 relay, such as `nvdaremote.com:6837`.
- Optional for legacy mode only: `xdotool`, `ydotool`, `sox`, `xclip`.

Some features depend on newer Orca controller hooks:

- `subscribe_speech_emitted` for host speech mirroring.
- `subscribe_braille_emitted` and `display_braille_text` for braille mirroring.
- `subscribe_keyboard_event` and `synthesize_key_event` for modern key forwarding and injection.

The extension degrades gracefully when a hook is missing, but the related feature will not be available.
For the complete modern feature set, use an Orca build that provides all
of the hooks listed above.

## Install

Build and install the extension:

```bash
./build-orca-ext.sh .
orca --install-extension remote.orca-ext
orca --replace
```

Open settings with **Orca + Ctrl + R**, enter the relay host, port, channel key, certificate fingerprint, and role, then save.

## First Connect

The extension pins the relay TLS certificate by SHA-256 fingerprint. If the fingerprint field is empty or incorrect, the connection is refused, the actual fingerprint is copied to your clipboard, and Orca announces what happened.

To pre-fetch the fingerprint:

```bash
openssl s_client -servername nvdaremote.com -connect nvdaremote.com:6837 \
    < /dev/null 2>/dev/null \
  | openssl x509 -fingerprint -sha256 -noout \
  | sed 's/SHA256 Fingerprint=//; s/://g' \
  | tr '[:upper:]' '[:lower:]'
```

Paste that value into **Server fingerprint (SHA-256)** in Orca Remote settings.

## Roles

**Client** means this Orca machine controls or listens to the remote machine. In this role, Orca receives remote speech and braille, forwards local keystrokes while focused on the remote, and can push clipboard text.

**Host** means this Orca machine is controlled by a remote peer. In this role, Orca broadcasts local speech and braille, accepts remote key input, and receives clipboard text.

## Pairing

Common pairings are:

- Orca client to Orca host for Linux-to-Linux remote control.
- Orca client to an NVDA Remote host for controlling a Windows machine.
- NVDA Remote client to Orca host for letting a Windows user control this machine.

Both peers must use the same relay, channel key, and NVDA Remote v2-compatible protocol.

## Shortcuts

Modern extension shortcuts:

| Shortcut | Action |
|---|---|
| Orca + Ctrl + R | Open settings |
| Orca + Ctrl + M | Client role: mute or unmute inbound speech and braille |
| Orca + Ctrl + Page Up | Connect |
| Orca + Ctrl + Page Down | Disconnect |
| Ctrl + Shift + Orca + C | Push clipboard |
| Orca + Alt + Tab | Client role: focus remote or return to local |

While remote focus is active, Orca Remote keeps its own command chords local and forwards normal screen-reader commands such as Insert+Down to the remote peer.

## Legacy Install

The legacy installer copies `orca-customizations.py` and `orca-scripts/` into `~/.local/share/orca/`:

```bash
./install
orca --replace
```

To auto-connect with the legacy plugin:

```bash
./install <SERVER_ADDRESS> <PORT> <CHANNEL_KEY>
orca --replace
```

Legacy shortcuts:

| Shortcut | Action |
|---|---|
| Orca + Alt + Tab | Toggle remote control |
| Orca + Alt + Page Up or Orca + Alt + C | Open connect dialog |
| Orca + Alt + Page Down or Orca + Alt + D | Disconnect |
| Ctrl + Shift + Orca + C | Push clipboard |
| Orca + Alt + M | Mute or unmute remote output |
| Orca + Shift + Delete | Send Ctrl+Alt+Del |

Use the modern extension when possible. The legacy path exists for older Orca installations and compatibility testing.

## Development

Run pure-function tests:

```bash
python3 -m pytest tests/
```

For a live host-side key-injection smoke test, `tests/fake_master.py`
can join the same relay/channel as an NVDA Remote-style master and send
a scripted key sequence to a running host session.

Build the extension archive:

```bash
./build-orca-ext.sh .
```

The archive intentionally includes only the extension runtime files, not tests, docs, or the legacy installer.

## Documentation

- [Architecture](docs/architecture.md)
- [Wire protocol](docs/wire-protocol.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## License

LGPL-2.1-or-later. See [LICENSE](LICENSE).
