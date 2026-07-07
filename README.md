# Orca Remote

A screen-reader-friendly extension that lets Orca on Linux control, and be controlled by, NVDA Remote-compatible peers over the NVDA Remote v2 protocol — built for dependable speech, braille, and keyboard mirroring across machines.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)

**Have a question, hit a bug, or want early word on new releases?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects) — the community hub for Orca Remote and my other projects, and the fastest place to get help.

Orca Remote can act as a **client** that controls and listens to a remote machine, or as a **host** that broadcasts Orca speech and braille while accepting remote key input. The modern install path is an Orca user extension (`.orca-ext`); a legacy `orca-customizations.py` installer is still included for older Orca builds.

## Features

- Connects to any NVDA Remote v2-compatible TLS relay.
- Works in both client and host roles.
- Mirrors speech in both directions.
- Mirrors braille, including inbound rendering on modern Orca builds.
- Forwards master-side keystrokes with command-chord bypass.
- Synthesizes host-side keys with stuck-key and auto-repeat guards.
- Syncs the clipboard in both directions.
- Stores settings persistently with user-only file permissions.
- Pins the relay certificate by SHA-256 fingerprint.
- Recovers from drops with reconnect backoff and write-buffer protection.

File transfer is not implemented — use clipboard sync for text, or a separate file-transfer tool for files.

## Requirements

- Orca with the user-extension API. The extension manifest targets Orca `51.alpha`.
- Python 3.12 or newer.
- GTK 3 and GLib, normally present with Orca.
- A reachable NVDA Remote v2 relay, such as `nvdaremote.com:6837`.
- Optional, for legacy mode only: `xdotool`, `ydotool`, `sox`, `xclip`.

Some features depend on newer Orca controller hooks:

- `subscribe_speech_emitted` for host speech mirroring.
- `subscribe_braille_emitted` and `display_braille_text` for braille mirroring.
- `subscribe_keyboard_event` and `synthesize_key_event` for modern key forwarding and injection.

The extension degrades gracefully when a hook is missing, but the related feature will not be available. For the complete modern feature set, use an Orca build that provides all of the hooks listed above.

## Install

Build and install the extension:

```bash
./build-orca-ext.sh .
orca --install-extension remote.orca-ext
orca --replace
```

Open settings with **Orca + Ctrl + R**, enter the relay host, port, channel key, certificate fingerprint, and role, then save.

## First connect

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

## Legacy install

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

## Run and test from source

Run the pure-function tests:

```bash
python3 -m pytest tests/
```

For a live host-side key-injection smoke test, `tests/fake_master.py` can join the same relay/channel as an NVDA Remote-style master and send a scripted key sequence to a running host session.

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

## Contributing

Pull requests are welcome. If Orca Remote has been useful to you, open a PR with a fix or feature and I'll review it.

## Community and support

Report bugs and request features in [Issues](https://github.com/serrebidev/orca-remote/issues). For questions, feedback, and release news, join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects).

## Credits

A few functions originate from [orca-remote by churst90](https://github.com/churst90/orca-remote), which helped this package support both the modern Orca extension and the legacy plugin.

## License

LGPL-2.1-or-later. See [LICENSE](LICENSE).
