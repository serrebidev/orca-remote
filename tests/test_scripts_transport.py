"""The threaded relay transport in orca-scripts releases what it holds."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orca-scripts"))

import transport  # noqa: E402


class FakeSocket:
    def __init__(self):
        self.closed = False
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


def test_disconnect_after_connection_drop_closes_socket_and_sender():
    tcp = transport.TCPTransport(serializer=None, address=("example.invalid", 6837))
    sock = FakeSocket()
    tcp.server_sock = sock
    sender = threading.Thread(target=tcp.send_queue, daemon=True)
    tcp.queue_thread = sender
    sender.start()
    # run() clears the flag before it disconnects.
    tcp.connected = False

    tcp._disconnect()

    sender.join(timeout=2)
    assert not sender.is_alive()
    assert sock.closed is True
    assert tcp.server_sock is None
    assert tcp.queue_thread is None
