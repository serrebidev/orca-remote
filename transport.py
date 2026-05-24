"""asyncio TLS transport for the NVDA Remote wire protocol.

Owns one outbound TLS connection to the relay. Reads newline-framed
JSON messages, dispatches them to a callback, and exposes a send()
coroutine for outbound messages. Reconnects on failure with
exponential backoff. Verifies the peer cert by SHA-256 fingerprint
pin -- no CA trust, no TOFU. If the configured fingerprint is empty
or does not match, the connection is rejected and the actual
fingerprint is reported back to the caller so it can be surfaced to
the user (who then pastes it into settings).
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from typing import Awaitable, Callable

from . import protocol


# Backoff schedule (seconds) used between reconnect attempts. Tops
# out at 30s so a flapping relay doesn't burn CPU while still
# recovering quickly after a transient blip.
_BACKOFF_SCHEDULE: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)


# Max inbound line size accepted from the relay. NVDA Remote frames
# are normally well under 4 KiB; we cap at 1 MiB so a misbehaving or
# malicious peer can't OOM us by streaming an unbounded line, but a
# legitimate huge speak/braille frame still goes through.
_READ_LIMIT: int = 1 * 1024 * 1024

# Drop outbound sends when the writer's pending buffer exceeds this
# many bytes. Prevents the asyncio side from accumulating unbounded
# backlog when the relay or its TCP path is congested: a slow link
# with high speak volume would otherwise blow memory and stall
# producers via drain() backpressure. 256 KiB is enough for hundreds
# of typical speak frames in flight; if we're over that, dropping
# fresh speech is the right call (the user is hearing stale audio
# anyway).
_MAX_WRITE_BUFFER: int = 256 * 1024


class FingerprintMismatch(Exception):
    """Raised when the peer cert does not match the configured pin.

    The actual SHA-256 fingerprint of the cert we saw is attached as
    `.actual` so the extension can surface it for the user to copy.
    """

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"fingerprint mismatch: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


def _make_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that defers all trust to our fingerprint check.

    We disable CA verification and hostname checks because the relay
    is expected to use a self-signed cert. Trust is established
    purely by SHA-256 pin against the DER-encoded certificate, which
    the caller compares after the handshake completes.
    """

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Modern ciphers only; the relay is happy with these.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _normalize_fingerprint(value: str) -> str:
    """Strip separators and lowercase a fingerprint string for comparison."""

    return "".join(c for c in value.lower() if c in "0123456789abcdef")


def _peer_fingerprint(writer: asyncio.StreamWriter) -> str:
    """Return the lowercase hex SHA-256 of the peer's DER-encoded cert."""

    ssl_object = writer.get_extra_info("ssl_object")
    if ssl_object is None:
        return ""
    der = ssl_object.getpeercert(binary_form=True)
    if not der:
        return ""
    return hashlib.sha256(der).hexdigest()


class RemoteTransport:
    """Single-channel TLS client for the NVDA Remote wire.

    Construction does not open the connection. Call `start()` to
    kick off the reconnect loop; call `stop()` to tear it down.
    `on_message` is invoked from the asyncio loop for every decoded
    inbound frame.
    """

    def __init__(
        self,
        host: str,
        port: int,
        channel: str,
        fingerprint: str,
        connection_type: str,
        on_message: Callable[[dict], Awaitable[None]],
        on_status: Callable[[str], None] | None = None,
        on_fingerprint_mismatch: Callable[[str], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._channel = channel
        self._expected_fp = _normalize_fingerprint(fingerprint)
        self._connection_type = connection_type
        self._on_message = on_message
        self._on_status = on_status or (lambda _msg: None)
        self._on_fp_mismatch = on_fingerprint_mismatch or (lambda _actual: None)

        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None

        # Cumulative count of outbound frames dropped because the
        # writer's pending buffer was over _MAX_WRITE_BUFFER. Surfaced
        # via status callback when nonzero so the user can see when
        # the wire can't keep up.
        self._dropped_outbound: int = 0

    def start(self) -> None:
        """Spawn the reconnect/read loop as an asyncio task."""

        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="orca-remote-transport")

    async def stop(self) -> None:
        """Signal the loop to exit and wait for it to finish."""

        if self._stop_event is not None:
            self._stop_event.set()
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # pylint: disable=broad-except
                pass
            self._writer = None
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def send(self, message: dict) -> None:
        """Send a message dict; silently dropped if not connected.

        Frames are also dropped (and counted) if the writer's pending
        buffer is over `_MAX_WRITE_BUFFER`. This protects us from
        unbounded backlog when the relay or its TCP path is congested
        -- letting drain() block here would otherwise cascade into
        backpressure on every producer (every speech_emitted, every
        inbound key reaction). Dropping speech-and-cancel frames is
        the right trade-off in that regime: by the time the buffer
        clears the audio would be stale anyway.
        """

        writer = self._writer
        if writer is None:
            return
        transport_obj = writer.transport
        try:
            buffered = (
                transport_obj.get_write_buffer_size()
                if transport_obj is not None else 0
            )
        except Exception:  # pylint: disable=broad-except
            buffered = 0
        if buffered > _MAX_WRITE_BUFFER:
            self._dropped_outbound += 1
            # Log on the first drop in each "episode" of congestion
            # so the user gets one notice rather than a flood; the
            # status callback is wired to debug.print_message in the
            # extension, not to speech.
            if self._dropped_outbound == 1 or self._dropped_outbound % 50 == 0:
                self._on_status(
                    f"outbound buffer congested "
                    f"({buffered} bytes >{_MAX_WRITE_BUFFER}); "
                    f"dropped {self._dropped_outbound} frame(s) so far"
                )
            return
        try:
            writer.write(protocol.encode(message))
            await writer.drain()
        except Exception:  # pylint: disable=broad-except
            # Reader loop will catch the EOF/exception and trigger a
            # reconnect; we don't need to handle it twice here.
            pass

    async def _run(self) -> None:
        """Main reconnect loop -- runs until stop_event is set."""

        attempt = 0
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._connect_and_read()
                attempt = 0  # clean close -> reset backoff
            except FingerprintMismatch as error:
                # Don't retry on a fingerprint mismatch -- the user
                # has to update the setting first. Surface the actual
                # value and stay idle until stop() is called.
                self._on_status(f"fingerprint mismatch (got {error.actual})")
                self._on_fp_mismatch(error.actual)
                await self._stop_event.wait()
                return
            except Exception as error:  # pylint: disable=broad-except
                self._on_status(f"connection error: {error}")

            if self._stop_event.is_set():
                return
            delay = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
            attempt += 1
            self._on_status(f"reconnecting in {delay:.0f}s")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return  # stop_event fired during backoff -> exit
            except asyncio.TimeoutError:
                pass  # backoff elapsed -> next attempt

    async def _connect_and_read(self) -> None:
        """One connection lifecycle: handshake, join, read until EOF."""

        self._on_status(f"connecting to {self._host}:{self._port}")
        reader, writer = await asyncio.open_connection(
            self._host, self._port,
            ssl=_make_ssl_context(),
            # Bigger-than-default StreamReader buffer so a legitimately
            # large speak/braille frame doesn't trip
            # LimitOverrunError. See _READ_LIMIT comment for sizing.
            limit=_READ_LIMIT,
        )

        # Fingerprint pin: refuse blank pins outright; compute the
        # actual fingerprint and either match or bail with the value
        # for the user.
        actual_fp = _peer_fingerprint(writer)
        if not self._expected_fp:
            writer.close()
            await writer.wait_closed()
            raise FingerprintMismatch(expected="(unset)", actual=actual_fp)
        if actual_fp != self._expected_fp:
            writer.close()
            await writer.wait_closed()
            raise FingerprintMismatch(expected=self._expected_fp, actual=actual_fp)

        self._writer = writer
        self._on_status("connected; joining channel")

        # Handshake: announce protocol version, then join the channel
        # in whichever role the caller configured (master = receive
        # speech from the remote slave; slave = broadcast our speech).
        writer.write(protocol.encode(protocol.build_protocol_version()))
        writer.write(protocol.encode(
            protocol.build_join(self._channel, self._connection_type)
        ))
        await writer.drain()

        # Read loop. Each iteration consumes exactly one
        # newline-terminated JSON object.
        while not self._stop_event.is_set():  # type: ignore[union-attr]
            line = await reader.readline()
            if not line:
                self._on_status("connection closed by peer")
                break
            try:
                message = protocol.decode(line)
            except protocol.ProtocolError as error:
                self._on_status(f"dropped malformed frame: {error}")
                continue
            try:
                await self._on_message(message)
            except Exception as error:  # pylint: disable=broad-except
                self._on_status(f"handler raised: {error}")

        self._writer = None
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # pylint: disable=broad-except
            pass
