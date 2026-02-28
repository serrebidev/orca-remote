# Orca-Remote

Orca-Remote is a plugin that enables bidirectional communication between the
Orca (Linux) and NVDA (Windows) screen readers using the NVDA Remote protocol.
It implements all NVDA Remote protocol v2 message types.

## Features

- **Speech forwarding**: Orca speech is sent to NVDA for output (and vice versa)
- **Bidirectional control**: Control NVDA from Orca, or Orca from NVDA
- **Connect dialog**: Accessible GTK dialog to connect to any NVDA Remote server
- **Clipboard sharing**: Push clipboard between machines
- **Braille forwarding**: Forward braille display output between machines
- **Audio cues**: Beep sequences for connection events (matches NVDA Remote)
- **Tone/wave playback**: Remote tones and wave files played locally
- **Mute remote**: Silence remote speech/audio without disconnecting
- **Send Ctrl+Alt+Del**: Send Secure Attention Sequence to remote
- **Session management**: Client join/leave notifications, MOTD, error handling
- **Orca key gestures**: Uses the Orca modifier key (Insert or CapsLock)

## Supported NVDA Remote Protocol Messages

| Message Type | Direction | Status |
|---|---|---|
| `protocol_version` | Outbound | Supported |
| `join` | Outbound | Supported |
| `channel_joined` | Inbound | Supported |
| `client_joined` | Inbound | Supported |
| `client_left` | Inbound | Supported |
| `generate_key` | Outbound | Supported |
| `key` | Both | Supported |
| `speak` | Both | Supported |
| `cancel` | Both | Supported |
| `pause_speech` | Inbound | Supported |
| `tone` | Inbound | Supported |
| `wave` | Inbound | Supported |
| `send_SAS` | Both | Supported |
| `index` | Inbound | Logged |
| `display` | Inbound | Supported |
| `braille_input` | Inbound | Logged |
| `set_braille_info` | Both | Supported |
| `set_display_size` | Inbound | Logged |
| `set_clipboard_text` | Both | Supported |
| `motd` | Inbound | Logged |
| `version_mismatch` | Inbound | Supported |
| `ping` | Inbound | Supported |
| `error` | Inbound | Supported |
| `nvda_not_connected` | Inbound | Supported |

## Gestures

All gestures use the Orca modifier key (Insert or CapsLock, depending on
your Orca layout setting).

| Gesture | Action | NVDA Remote Equivalent |
|---|---|---|
| Orca + Alt + Tab | Toggle local/remote control | F11 |
| Orca + Alt + Page Up | Open connect dialog | Alt + NVDA + Page Up |
| Orca + Alt + Page Down | Disconnect | Alt + NVDA + Page Down |
| Ctrl + Shift + Orca + C | Push clipboard to remote | Ctrl + Shift + NVDA + C |
| Orca + Alt + M | Toggle mute remote speech | (menu item in NVDA) |
| Orca + Shift + Delete | Send Ctrl+Alt+Del to remote | (menu item in NVDA) |

When controlling the remote machine, all keystrokes are forwarded to NVDA
except the toggle gesture (Orca + Alt + Tab), which always stays local.

## Dependencies

- Python 3
- GTK 3 (for the connect dialog)
- `xdotool` (for receiving remote key events from NVDA):
  ```
  sudo apt install xdotool
  ```
- `sox` (optional, for tone playback):
  ```
  sudo apt install sox
  ```
- `xclip` (optional fallback for clipboard, GTK clipboard is preferred):
  ```
  sudo apt install xclip
  ```

## Installation

1. Set up an NVDA Remote server on your network, or use a public relay server.
   On the NVDA side, use the "Control another machine" option.

2. Run the install script with your NVDA Remote server details:
   ```
   ./install <SERVER_ADDRESS> <PORT> <CHANNEL_KEY>
   ```

3. Restart Orca:
   ```
   orca --replace
   ```

## Usage

### Automatic connection

If you provided connection details during installation, Orca Remote will
automatically connect to the NVDA Remote server when Orca starts.

### Connect dialog

Press **Orca + Alt + Page Up** to open the connect dialog. Choose:
- **Allow this machine to be controlled** (slave mode) - NVDA controls Orca
- **Control the remote machine** (master mode) - Orca controls NVDA

Enter server address, port, and channel key. Use "Generate key" for a random key.

### Controlling NVDA from Orca

1. Connect to the NVDA Remote server (master mode)
2. Press **Orca + Alt + Tab** to switch to remote control mode
3. All keystrokes are now forwarded to NVDA
4. Press **Orca + Alt + Tab** again to return to local control

### Controlling Orca from NVDA

1. On the NVDA side, connect using "Control another machine"
2. On the Orca side, connect using "Allow this machine to be controlled"
3. NVDA can then send keystrokes to Orca (requires `xdotool`)

### Clipboard sharing

Press **Ctrl + Shift + Orca + C** to push your clipboard to the remote machine.
When the remote machine pushes clipboard, it is automatically received.

### Muting remote output

Press **Orca + Alt + M** to mute/unmute remote speech and sounds.

## Audio Cues

| Event | Sound |
|---|---|
| Connected to server | 440Hz + 660Hz |
| Disconnected | 660Hz + 440Hz |
| Client joined channel | 1000Hz |
| Client left channel | 108Hz |
| Clipboard sent | 500Hz + 600Hz |
| Clipboard received | 600Hz + 500Hz |

## Uninstallation

```
./uninstall
```

## Credits

* Based on the [NVDA Remote](https://github.com/nvdaremote/nvdaremote) protocol

## License

Licensed under the GNU GPL Version 2 (due to NVDA Remote code being used).
