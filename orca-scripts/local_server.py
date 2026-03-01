"""Local relay server for Orca Remote.

Implements a minimal NVDA Remote-compatible relay server so remote clients
(NVDA or another Orca Remote) can connect directly to this machine without
needing a public relay server.

The server speaks the same JSON-over-SSL protocol as the official NVDA Remote
relay, supporting protocol_version, join, generate_key, and arbitrary message
relay within a channel.
"""

import json
import os
import select
import socket
import ssl
import subprocess
import threading
import time
import random
from logging import getLogger

log = getLogger('local_server')

_DBG_LOG = os.path.expanduser("~/.local/share/orca/orca-remote-debug.log")

def _dbg(msg):
    try:
        with open(_DBG_LOG, "a") as f:
            f.write("[%.3f] SRV: %s\n" % (time.time(), msg))
    except Exception:
        pass


def _ensure_ssl_cert():
    """Generate a persistent self-signed cert/key pair if not already present.

    Uses the Python 'cryptography' package (always available with Orca's deps).
    Falls back to the 'openssl' binary if the package is unavailable.
    Returns (cert_path, key_path).
    """
    cert_dir = os.path.expanduser("~/.local/share/orca")
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "orca-remote-cert.pem")
    key_path = os.path.join(cert_dir, "orca-remote-key.pem")
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        try:
            _generate_cert_python(cert_path, key_path)
            log.info("Generated SSL certificate (Python) at %s" % cert_path)
        except Exception as py_exc:
            log.warning("Python cert gen failed (%s), trying openssl binary" % py_exc)
            try:
                subprocess.run(
                    [
                        "openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-keyout", key_path, "-out", cert_path,
                        "-days", "3650", "-nodes", "-subj", "/CN=orca-remote",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("Generated SSL certificate (openssl) at %s" % cert_path)
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                raise RuntimeError(
                    "Cannot generate SSL certificate. Install the Python "
                    "'cryptography' package or the 'openssl' binary.\n%s" % exc
                )
    return cert_path, key_path


def _generate_cert_python(cert_path, key_path):
    """Generate a self-signed RSA certificate using the cryptography package."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "orca-remote"),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


class _Client:
    """Represents one connected remote client."""

    def __init__(self, sock, addr, client_id, server):
        self.sock = sock
        self.addr = addr
        self.client_id = client_id
        self.server = server
        self.channel = None
        self.connection_type = None
        self._buf = b""
        self._lock = threading.Lock()

    def send(self, obj):
        """Send a JSON object to this client (thread-safe)."""
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self._lock:
            try:
                self.sock.sendall(data)
            except Exception:
                pass

    def handle(self):
        """Read loop — runs in its own daemon thread."""
        try:
            while True:
                try:
                    readable, _, errored = select.select(
                        [self.sock], [], [self.sock], 10.0
                    )
                except Exception:
                    break
                if errored:
                    break
                if not readable:
                    continue
                try:
                    chunk = self.sock.recv(16384)
                except Exception:
                    break
                if not chunk:
                    break
                self._buf += chunk
                while b"\n" in self._buf:
                    line, _, self._buf = self._buf.partition(b"\n")
                    self._dispatch(line.strip())
        finally:
            self.server._on_client_gone(self)
            try:
                self.sock.close()
            except Exception:
                pass

    def _dispatch(self, line):
        if not line:
            return
        try:
            obj = json.loads(line)
        except Exception:
            _dbg("client %s: bad JSON: %r" % (self.client_id, line[:80]))
            return
        msg_type = obj.pop("type", None)
        _dbg("client %s (addr=%s): msg type=%r chan=%r" % (
            self.client_id, self.addr, msg_type, obj.get("channel", self.channel)))
        if msg_type == "protocol_version":
            pass  # Accept any version
        elif msg_type == "join":
            self._do_join(obj.get("channel"), obj.get("connection_type", "slave"))
        elif msg_type == "generate_key":
            key = str(random.randint(1000000, 9999999))
            self.send({"type": "generate_key", "key": key})
        elif msg_type is not None and self.channel is not None:
            obj["type"] = msg_type
            self.server._relay(self, obj)

    def _do_join(self, channel, connection_type):
        if not channel:
            self.send({"type": "error", "message": "no_channel"})
            return
        self.channel = channel
        self.connection_type = connection_type
        existing = self.server._add_to_channel(self, channel)
        # Tell this client about existing channel members
        self.send({
            "type": "channel_joined",
            "channel": channel,
            "user_ids": [c.client_id for c in existing],
            "clients": [
                {"id": c.client_id, "connection_type": c.connection_type}
                for c in existing
            ],
        })
        # Tell existing members that this client joined
        for peer in existing:
            peer.send({
                "type": "client_joined",
                "client": {
                    "id": self.client_id,
                    "connection_type": self.connection_type,
                },
            })


class LocalRelayServer:
    """A minimal NVDA Remote-compatible relay server.

    Starts an SSL TCP listener on *port*. Remote clients (NVDA or another
    Orca Remote instance) connect directly to this machine and communicate
    through channels identified by a shared connection code (channel key).

    Usage::

        server = LocalRelayServer(port=6837)
        server.start()
        # ... later ...
        server.stop()
    """

    def __init__(self, port):
        self.port = port
        self._lock = threading.Lock()
        self._channels = {}   # channel -> [_Client, ...]
        self._clients = {}    # client_id -> _Client
        self._next_id = 1
        self._server_sock = None
        self._running = False
        self._thread = None

    def start(self):
        """Start the relay server in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._serve,
            name="orca_local_relay",
            daemon=True,
        )
        self._thread.start()
        log.info("LocalRelayServer starting on port %d" % self.port)

    def stop(self):
        """Stop the relay server and close all connections."""
        self._running = False
        sock = self._server_sock
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        log.info("LocalRelayServer stopped")

    # ---- internal ----

    def _serve(self):
        try:
            cert, key = _ensure_ssl_cert()
        except RuntimeError as exc:
            log.error(str(exc))
            return

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.load_cert_chain(cert, key)
        except Exception as exc:
            log.error("SSL cert load failed: %s" % exc)
            return

        # Prefer a dual-stack IPv6 socket (IPV6_V6ONLY=0) so the server
        # accepts connections on all interfaces over both IPv4 and IPv6.
        # Fall back to IPv4-only if IPv6 is unavailable on this system.
        try:
            raw = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            raw.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            raw.bind(("::", self.port))
        except OSError:
            try:
                raw.close()
            except Exception:
                pass
            raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            raw.bind(("", self.port))
        try:
            raw.listen(10)
        except OSError as exc:
            log.error("Cannot bind to port %d: %s" % (self.port, exc))
            raw.close()
            return

        self._server_sock = raw
        log.info("LocalRelayServer listening on port %d" % self.port)

        while self._running:
            try:
                readable, _, _ = select.select([raw], [], [], 1.0)
            except Exception:
                break
            if not readable:
                continue
            try:
                conn, addr = raw.accept()
            except Exception:
                if self._running:
                    log.exception("accept() error")
                break
            try:
                ssl_conn = ctx.wrap_socket(conn, server_side=True)
            except Exception as exc:
                log.warning("SSL handshake failed from %s: %s" % (addr, exc))
                conn.close()
                continue

            with self._lock:
                client_id = self._next_id
                self._next_id += 1
                client = _Client(ssl_conn, addr, client_id, self)
                self._clients[client_id] = client
            _dbg("new client %s from %s" % (client_id, addr))

            t = threading.Thread(
                target=client.handle,
                name="orca_client_%d" % client_id,
                daemon=True,
            )
            t.start()

        raw.close()

    def _add_to_channel(self, client, channel):
        """Add *client* to *channel*, return list of previously existing clients."""
        with self._lock:
            peers = self._channels.setdefault(channel, [])
            existing = list(peers)
            peers.append(client)
        return existing

    def _on_client_gone(self, client):
        """Called when a client disconnects; notifies remaining channel members."""
        peers_to_notify = []
        with self._lock:
            self._clients.pop(client.client_id, None)
            ch = self._channels.get(client.channel)
            if ch:
                try:
                    ch.remove(client)
                except ValueError:
                    pass
                peers_to_notify = list(ch)
                if not ch:
                    del self._channels[client.channel]
        for peer in peers_to_notify:
            peer.send({"type": "client_left", "client": {"id": client.client_id}})

    def _relay(self, sender, obj):
        """Relay *obj* to all other clients in the sender's channel."""
        with self._lock:
            peers = [
                c for c in self._channels.get(sender.channel, [])
                if c is not sender
            ]
        for peer in peers:
            peer.send(obj)
