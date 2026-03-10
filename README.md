# Orca-Remote

Orca-Remote lets Orca on Linux and NVDA on Windows work together over the NVDA Remote protocol.

You can send speech, braille, clipboard, tones, and control input between both machines.

## What it does

- Forward speech between Orca and NVDA
- Control NVDA from Orca, or Orca from NVDA
- Share clipboard text
- Forward braille output
- Play remote tones and connection sounds
- Mute remote speech without disconnecting
- Send Ctrl+Alt+Del to the remote machine

It supports all NVDA Remote protocol v2 message types.

## Install

Optional packages:

```bash
sudo apt install xdotool sox xclip
````

Install the plugin:

```bash
./install
```

Restart Orca:

```bash
orca --replace
```

Done.

To auto-connect on startup:

```bash
./install <SERVER_ADDRESS> <PORT> <CHANNEL_KEY>
```

## Quick start

On the NVDA machine, open NVDA Remote and get the server address, port, and key.

On the Orca machine, press **Orca + Alt + C, or Page Up**. Enter the same details and connect.

To control NVDA from Orca, press **Orca + Alt + Tab**. Press it again to return to local control.

## Shortcuts

* **Orca + Alt + Tab**: Toggle remote control
* **Orca + Alt + Page Up or Orca + Alt + C  **: Open connect dialog
* **Orca + Alt + Page Down**: Disconnect
* **Ctrl + Shift + Orca + C**: Send clipboard
* **Orca + Alt + M**: Mute or unmute remote audio
* **Orca + Shift + Delete**: Send Ctrl+Alt+Del

## Connection modes

* **Control the remote machine**: Orca controls NVDA
* **Allow this machine to be controlled**: NVDA controls Orca

## Dependencies

* Python 3
* GTK 3
* `xdotool` for remote key input to Orca
* `sox` optional, for tones
* `xclip` optional, for clipboard fallback

## Uninstall

```bash
./uninstall
```

## Credits

Based on the NVDA Remote protocol.

## License

GNU GPL v2.

```
