# Control Linux Orca from Windows NVDA Remote Access

This guide is for someone using **NVDA on Windows to control an Orca desktop on Linux**, including a braille display connected to the Windows computer. It covers the modern Orca Remote extension, not the legacy `orca-customizations.py` installation.

> **Braille compatibility warning:** A working connection, keyboard control, and speech do **not** guarantee braille forwarding. In particular, the upstream 0.9.10 extension documents a dependency on `controller.subscribe_braille_emitted`, which released Orca 51.x may not provide. A locally tested 0.9.11 patch using `Extension.on_braille_output` produced basic braille output on Orca 51.2, but that patch is **not part of upstream 0.9.10**. Do not present it as a released feature. See [Braille compatibility](#braille-compatibility) below.

## Which computer does what?

| Device | Software | Role |
| --- | --- | --- |
| Linux desktop (machine to be controlled) | Orca + Orca Remote | **Host** |
| Windows laptop (machine doing the controlling) | NVDA + NVDA Remote Access | **Controller / controlling computer** |
| Braille display plugged into Windows | NVDA braille driver | Shows braille received over Remote Access, *if* the Linux host can send it |

You do **not** configure Linux as the Orca Remote client for this scenario. You do **not** plug the display into Linux to read forwarded braille on Windows. If instead you want to control *Windows from Linux*, the roles are reversed.

## Before starting

1. Make sure Orca runs and speaks **locally on Linux**, and its extension API is supported. Consult [README requirements](../README.md#requirements) for supported Orca/Python versions and key injection backends.
2. Confirm NVDA works **locally on Windows**, including your connected braille display, before trying remote braille.
3. Ensure both computers can reach the same NVDA Remote v2-compatible relay.
4. Choose a private, unpredictable channel key. **Never publish it or the local settings JSON in an issue, log, screenshot, or PR.**
5. On Linux X11/Xorg, host keyboard injection may require `xdotool`. On Wayland, see [Wayland and Xorg](../README.md#wayland-and-xorg).
6. Use a supported Orca build and matching Orca Remote extension. This guide does not guarantee compatibility across every Orca release.

## 1. Install the extension on Linux

In a terminal on the Linux computer, in a checkout of this repository:

```bash
./install-extension
orca --replace
```

The script builds/unpacks the modern extension and approves it. Orca must approve the extension before it can load. An extension may require reapproval after its contents change. For manual installation instructions, see [Install](../README.md#install).

**Safety:** If you already have a working installation, back up `~/.local/share/orca/extensions/remote/` and `~/.local/share/orca/orca-remote-settings.json` before replacing files. Do not share the settings file because it contains the channel key.

## 2. Configure the Linux host

1. In Orca, press **Orca+Ctrl+R** to open Orca Remote settings.
2. Set the role to **Host**: this Linux machine is the one being controlled.
3. Set the relay host and port (for example, `nvdaremote.com`, port `6837`).
4. Enter the private **channel key** that you will also enter on Windows.
5. Configure the relay's **SHA-256 certificate fingerprint**. The first-connect procedure is described in [First connect](../README.md#first-connect). Verify the fingerprint through a trusted source; do not blindly accept an unexpected certificate change.
6. Save, connect, and verify that the host connects to the relay.

Do not confuse the **channel key** (shared between machines) with the **certificate fingerprint** (which identifies the relay).

## 3. Configure NVDA Remote Access on Windows

1. Start NVDA and confirm that Windows speech and your braille display work locally.
2. Open the **NVDA menu → Tools → Remote Access** (menu wording can vary by installed NVDA version).
3. Choose **Connect**, then **Control another computer**, using an **existing host / remote relay**.
4. Enter the **same relay address, port, and channel key** configured on the Linux host. Do not select a mode that makes Windows the controlled machine.
5. Complete the connection and confirm the Remote Access connection announcement.

For exact Windows menu labels and options, follow the documentation for your installed NVDA Remote Access version.

## 4. Control Linux from Windows

In the configuration used for this guide, **Insert+Alt+Tab** switches whether keyboard commands control the local Windows computer or the remote Linux computer. Verify the shortcut with your NVDA Remote Access version.

While remote control is active:

- Keyboard input is sent to Linux; a command you expect to affect **NVDA** may instead affect **Orca**.
- **Insert+S on the Linux side can toggle Orca speech**. If speech disappears after pressing it, toggle it back while controlling Linux. Do not disable Orca speech to solve duplicate audio: doing so can also stop speech forwarding.
- To adjust NVDA's local speech or Remote Access settings, first switch keyboard control back to Windows.
- Orca may speak through Linux's own speakers *and* forward speech for playback on Windows. If the two computers are nearby, you may hear both.

## 5. Test keyboard, speech, and braille separately

Do not mark a feature as working based only on a relay connection or automated tests.

1. **Keyboard:** Switch to remote control and open a terminal on Linux (often **Ctrl+Alt+T**). Type `echo abc 123` and press Enter. Confirm Linux received the command.
2. **Speech:** Navigate Linux controls and confirm that speech is heard through the intended output.
3. **Basic braille:** With a braille display connected to Windows and selected in NVDA, navigate Linux controls. Check whether *Linux* text appears on the physical display. If it does not, continue to [Braille compatibility](#braille-compatibility).
4. **Focus:** On Linux press **Alt+F2**, move focus, then Escape. Confirm braille follows the current Linux focus.
5. **Long line / panning:** In a Linux terminal run `seq -s ' ' 1 60`. Check whether the expected portion is shown on the display, and whether pan-forward / pan-back move correctly. Do not assume that output on an 80-cell display means all 80 cells and panning are correct.
6. **Reconnect:** Disconnect and reconnect NVDA Remote Access, re-enter remote control, and check speech, keyboard, and braille again.
7. **Routing keys:** Test separately. A routing-key failure is not the same as failed basic braille output; don't claim routing support without testing it.

Keep a short record: *speech*, *keyboard*, *basic braille*, *focus*, *panning*, *routing*, and *reconnect* should each be marked **passed**, **failed**, or **not tested**.

## Braille compatibility

The upstream README's [feature matrix](../README.md#which-orca-builds-do-what) states that released Orca 51.1 lacks some controller hooks needed for upstream braille mirroring. Do **not** infer that released Orca 51.2 supports upstream braille mirroring merely because the modern extension installs or the remote connection succeeds.

A DeafBlind tester using **Orca 51.2 on Linux Mint MATE/X11**, **NVDA on Windows**, and a **Eurobraille b.note 80-cell** confirmed **basic Linux braille reading** using a *local, not yet upstreamed* Orca Remote 0.9.11 patch. The patch captures `Extension.on_braille_output` where the controller's `subscribe_braille_emitted` hook is unavailable. That confirmation does **not** establish released-version support, 80-cell correctness, panning, routing, focus, or reconnect behavior.

If braille does not appear:

- First verify the braille display works in NVDA **on Windows without remote control**.
- Confirm the relay session and Linux keyboard/speech work; troubleshoot those separately.
- Check your exact **Orca version**, **Orca Remote version**, and whether that code can capture outgoing braille on that Orca build.
- Inspect Orca Remote debug logging for braille-hook unavailability, but **redact channel keys, fingerprints, addresses, and personal data** before sharing logs.
- Do not replace Orca, BRLTTY, NVDA, or an installed extension speculatively. Back up and test version-specific changes separately.

## If two voices speak

Orca can speak on Linux's speakers while forwarded speech is reproduced through NVDA Remote Access on Windows. This can be expected behavior, not a braille defect. The NVDA Remote **Mute Remote** option may not suppress every speech source in every setup. Before changing settings, identify which computer and audio session produces each voice. Keep local Orca speech enabled when remote forwarding depends on it.

## Additional help

- [Orca Remote README](../README.md)
- [Troubleshooting](troubleshooting.md)
- [SerrebiProjects Telegram group](https://t.me/SerrebiProjects)
- [Report an issue](https://github.com/serrebidev/orca-remote/issues)

When reporting a bug, include Orca and extension versions, X11/Wayland, NVDA version, whether speech and keyboard work, and **physical braille test results**. Never post the relay's private channel key.
