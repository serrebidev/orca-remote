# Orca Remote

A screen-reader-friendly extension that lets Orca on Linux control, and be controlled by, NVDA Remote-compatible peers over the NVDA Remote v2 protocol — built for dependable speech, braille, and keyboard mirroring across machines.

[![Join SerrebiProjects on Telegram](https://img.shields.io/badge/Telegram-SerrebiProjects-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/SerrebiProjects)

**Questions, bugs, or release news?** Join the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects), the fastest place to get help.

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
- Optional, host-mode key injection: `xdotool` (Xorg and XWayland) or
  `ydotool` (any session, needs `ydotoold` running). On GNOME/KDE
  Wayland the extension uses the XDG RemoteDesktop portal instead, with
  a one-time keyboard-control prompt.
- Optional: `sox` (client-mode tone playback; a generated-WAV + `paplay`
  fallback is built in), `xclip` (legacy mode only).

Some features depend on newer Orca controller hooks:

- `subscribe_speech_emitted` for host speech mirroring on perf-branch
  builds (released Orca uses the `on_speech_output` hook instead).
- `subscribe_braille_emitted` and `display_braille_text` for braille mirroring.
- `subscribe_keyboard_event` and `synthesize_key_event` for modern key forwarding and injection.

The extension degrades gracefully when a hook is missing, but the related feature will not be available. For the complete modern feature set, use an Orca build that provides all of the hooks listed above.

## Install

Build the extension archive and install it:

```bash
./install-extension
orca --replace
```

`install-extension` builds `remote.orca-ext` when needed, unpacks it into
`$XDG_DATA_HOME/orca/extensions/remote/`, and approves it with
`orca --approve-extension remote`.

Two things worth knowing about that path:

- **Released Orca has no `--install-extension` flag.** Orca 51.1 ships
  `--approve-extension` and an Approve button in the Preferences
  extension list, but nothing that unpacks an archive for you, so this
  repository does the unpacking. (Newer Orca builds used for
  development do have a manifest-based installer; it produces the same
  directory.)
- An unapproved extension is listed in Preferences but never loads.
  `install-extension` approves it; in the GUI, use Orca Preferences,
  Extensions, and press Approve on the Orca Remote row.

By hand, the same thing:

```bash
mkdir -p ~/.local/share/orca/extensions/remote
python3 -m zipfile -e remote.orca-ext ~/.local/share/orca/extensions/remote
orca --approve-extension remote
orca --replace
```

Open settings with **Orca + Ctrl + R**, enter the relay host, port, channel key, certificate fingerprint, and role, then save.

## Which Orca builds do what

| Orca build | Speech mirroring | Being controlled (keys) | Controlling (keys) | Braille mirroring |
|---|---|---|---|---|
| Released 51.1 | yes, both directions | yes, via xdotool / portal / ydotool | no | no |
| Perf branch or newer main | yes | yes, XTEST first, external backends as fallback | yes | yes |

Released Orca's controller has no `subscribe_speech_emitted`,
`subscribe_braille_emitted`, `subscribe_keyboard_event`,
`synthesize_key_event` or `display_braille_text`. The extension still
loads there and mirrors speech through `Extension.on_speech_output`;
the missing features are logged and disabled rather than crashing.
Key forwarding needs a build with those controller hooks; key
*injection* (being controlled) uses the external backends instead.

## Wayland and Xorg

Host-side key injection picks a backend per session:

- **Xorg**: `controller.synthesize_key_event` (XTEST) first, then
  `xdotool`, `ydotool`, and the RemoteDesktop portal.
- **Wayland**: `ydotool` first (it reaches native Wayland clients),
  then the XDG RemoteDesktop portal (GNOME/KDE ask once for keyboard
  access), then `xdotool` (XWayland clients only).

The backend in use is written to the debug log as
`REMOTE: key injection backend: <name>`. If none is usable you get one
`REMOTE: key injection: no external backend available ...` line and the
extension falls back to XTEST.

Master-side key forwarding works on both session types. System-level
consume of forwarded keys (so they do not also reach the focused local
app) is complete on X11, partial on Mutter/KWin, and unavailable on
wlroots -- see [docs/architecture.md](docs/architecture.md).

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

Paste that value into **Server fingerprint (SHA-256)** in Orca Remote settings. If the relay's certificate ever changes, Orca announces the mismatch, copies the new fingerprint to the clipboard, and saves it to `~/orca-remote-fingerprint.txt` -- open that file with `nano ~/orca-remote-fingerprint.txt` if the clipboard paste does not work. Then press Orca+Ctrl+R, focus the Server fingerprint field, press **Control+A** to select the old value, then paste with Control+V. A value that is not 64 hex characters is refused with a spoken reason, so a paste that lands next to the old value cannot silently break the connection.

## Roles

**Client** means this Orca machine controls or listens to the remote machine. In this role, Orca receives remote speech and braille, forwards local keystrokes while focused on the remote, and can push clipboard text.

**Host** means this Orca machine is controlled by a remote peer. In this role, Orca broadcasts local speech and braille, accepts remote key input, and receives clipboard text.

## Windows NVDA controlling Linux with braille

For a step-by-step, keyboard-friendly walkthrough covering **NVDA Remote Access on Windows as controller**, **Orca Remote on Linux as host**, and a **braille display connected to Windows**, see [Control Linux Orca from Windows NVDA Remote Access](docs/windows-nvda-linux-orca.md).

**Braille compatibility varies by Orca and Orca Remote version.** A successful speech or keyboard connection does not prove remote braille works. The guide distinguishes the upstream release from a locally tested, not-yet-upstreamed Orca 51.2 braille patch and includes a physical test checklist.

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

The legacy transport encrypts traffic but does not verify the relay certificate.
Use the modern extension's fingerprint pinning when relay authentication is required.

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
