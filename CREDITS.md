# Credits

Orca Remote exists for dependable speech, braille, and keyboard mirroring across machines. Orca on Linux can control, and be controlled by, NVDA Remote-compatible peers over the NVDA Remote v2 protocol. It runs as a client that controls and listens to a remote machine, or as a host that broadcasts Orca speech and braille while accepting remote key input.

Third-party projects it stands on:

- Orca, the GNOME screen reader (its controller has no `subscribe_speech_emitted` / `subscribe_braille_emitted` / `subscribe_keyboard_event`; the braille guide distinguishes the upstream release from the locally tested, not-yet-upstreamed Orca 51.2 braille patch).
- The NVDA Remote v2 protocol and any NVDA Remote-compatible TLS relay.
- orca-ext-utils from [churst90/orca-ext-utils](https://github.com/churst90/orca-ext-utils) (v0.2.0), vendored in `vendor/`; see `vendor/UPDATE.md`. Its `keyboard_grab.KeysetGrab` gives master-side full system-level key consume while forwarding.

Questions and bug reports are welcome in the [SerrebiProjects Telegram group](https://t.me/SerrebiProjects). More serrebi projects: [projects.serrebiradio.com](https://projects.serrebiradio.com).


## About this project

- GitHub profile: [serrebidev](https://github.com/serrebidev)
- Repository (GitHub): [https://github.com/serrebidev/orca-remote](https://github.com/serrebidev/orca-remote)
- Repository (Forgejo): [https://git.serrebiradio.com/serrebi/orca-remote](https://git.serrebiradio.com/serrebi/orca-remote)
- Projects site: [projects.serrebiradio.com](https://projects.serrebiradio.com)
- Questions and bug reports: [SerrebiProjects Telegram group](https://t.me/SerrebiProjects)
