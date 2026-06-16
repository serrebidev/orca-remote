#!/usr/bin/env python3
"""Fake NVDA Remote master for testing slave-side key injection.

Connects to a relay as `connection_type=master`, joins a channel,
and sends a script of key press/release pairs. The slave (an Orca
running orca-remote in host mode on the same channel) should
synthesize each keystroke into its focused window.

Usage:
    python3 fake_master.py <channel> <fingerprint> [host] [port]
    python3 fake_master.py <channel> <fingerprint> --type "hello"

With --type the script sends each character of the supplied string
as a press/release pair so a human can watch text appear in the
focused editor on the slave.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import ssl
import sys


VK_LETTER_A = 0x41
VK_RETURN = 0x0D
VK_SPACE = 0x20

_CHAR_TO_VK: dict[str, tuple[int, bool]] = {}
# Lowercase letters: VK_A..VK_Z, no shift.
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _CHAR_TO_VK[ch] = (VK_LETTER_A + i, False)
# Digits 0..9.
for i, ch in enumerate("0123456789"):
    _CHAR_TO_VK[ch] = (0x30 + i, False)
_CHAR_TO_VK[" "] = (VK_SPACE, False)
_CHAR_TO_VK["\n"] = (VK_RETURN, False)


def make_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def normalize_fp(fp: str) -> str:
    return "".join(c for c in fp.lower() if c in "0123456789abcdef")


async def run(args: argparse.Namespace) -> int:
    expected_fp = normalize_fp(args.fingerprint)
    print(f"connecting to {args.host}:{args.port} ...", file=sys.stderr)
    reader, writer = await asyncio.open_connection(
        args.host, args.port, ssl=make_ctx(),
    )
    ssl_obj = writer.get_extra_info("ssl_object")
    der = ssl_obj.getpeercert(binary_form=True)
    actual_fp = hashlib.sha256(der).hexdigest()
    if expected_fp and actual_fp != expected_fp:
        print(
            f"fingerprint mismatch (expected {expected_fp}, got {actual_fp})",
            file=sys.stderr,
        )
        writer.close()
        await writer.wait_closed()
        return 2
    print(f"connected; peer fingerprint = {actual_fp}", file=sys.stderr)

    def send(msg: dict) -> None:
        writer.write(json.dumps(msg).encode("utf-8") + b"\n")

    send({"type": "protocol_version", "version": 2})
    send({"type": "join", "channel": args.channel, "connection_type": "master"})
    await writer.drain()

    # Spawn a background reader that logs every inbound frame to stderr
    # for the lifetime of the connection. The slave's client_joined
    # should land here so we can confirm the relay actually believes
    # we're paired with the slave.
    async def _reader_loop() -> None:
        while True:
            line = await reader.readline()
            if not line:
                print("<- (EOF)", file=sys.stderr)
                return
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception as error:  # pylint: disable=broad-except
                print(f"<- (decode error) {error}", file=sys.stderr)
                continue
            print(f"<- {msg.get('type')}: {msg}", file=sys.stderr)

    reader_task = asyncio.create_task(_reader_loop())
    # Give the relay a beat to send channel_joined + any client_joined
    # before we start blasting key frames at it.
    await asyncio.sleep(1.0)

    # Optional pre-send delay so the human can Alt-Tab into the
    # target window before the typing starts.
    if args.delay > 0:
        print(
            f"waiting {args.delay}s -- switch focus to the target window now...",
            file=sys.stderr,
        )
        await asyncio.sleep(args.delay)

    # Build the key script.
    if args.type:
        text = args.type
    else:
        text = "abc"
    print(f"sending text: {text!r}", file=sys.stderr)
    for ch in text:
        vk = _CHAR_TO_VK.get(ch)
        if vk is None:
            print(f"  skip {ch!r} (no VK mapping in fake-master)", file=sys.stderr)
            continue
        vk_code, extended = vk
        for pressed in (True, False):
            frame = {
                "type": "key",
                "vk_code": vk_code,
                "extended": extended,
                "pressed": pressed,
                "scan_code": 0,
            }
            send(frame)
        await writer.drain()
        await asyncio.sleep(0.05)

    # Hold the connection open for a couple of seconds so any
    # relay-side echoes of our keys land in the reader log before we
    # close.
    print("sent; waiting 2s for relay echoes...", file=sys.stderr)
    await asyncio.sleep(2.0)
    print("done; closing.", file=sys.stderr)
    reader_task.cancel()
    writer.close()
    try:
        await writer.wait_closed()
    except (ssl.SSLError, OSError):
        # Python 3.14's asyncio.sslproto raises if the peer sends
        # anything between our close_notify and theirs. The relay
        # often does (a final motd or a client_left echo). All our
        # key frames are already on the wire, so this is cosmetic.
        pass
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("channel")
    p.add_argument("fingerprint")
    p.add_argument("--host", default="nvdaremote.com")
    p.add_argument("--port", type=int, default=6837)
    p.add_argument(
        "--type", default=None,
        help="String to send as key press/release pairs.",
    )
    p.add_argument(
        "--delay", type=float, default=0.0,
        help="Seconds to wait after channel-join before sending keys, "
             "so you can Alt-Tab into the target window.",
    )
    args = p.parse_args()
    rc = asyncio.run(run(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
