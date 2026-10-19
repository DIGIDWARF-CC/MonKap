"""A minimal WebSocket client (RFC 6455) on plain sockets, for listening to a ComfyUI that reports its progress.

Only what that needs: `ws://`, text and binary messages (also in fragments), ping answered with pong, close. There is no
`wss://` and no extensions. The standard library has no WebSocket client, and one more dependency would make the
installer heavier for a hundred lines of protocol.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import urllib.parse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TEXT, BINARY = 1, 2


class WebSocketError(OSError):
    """The connection could not be made, or it is closed."""


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "ws" or not u.hostname:
            raise WebSocketError("only ws:// addresses are supported")
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        try:
            self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        except OSError as e:
            raise WebSocketError(f"can't connect: {e}") from e
        self.closed = False
        self._buf = bytearray()
        self._frag = None                       # [opcode, bytearray] of a message still arriving in pieces
        try:
            self._handshake(u.netloc, path)
        except Exception:
            self.sock.close()
            raise

    def _handshake(self, host: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        raw = b""
        try:
            while b"\r\n\r\n" not in raw:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise WebSocketError("the server closed the connection during the handshake")
                raw += chunk
                if len(raw) > 65536:
                    raise WebSocketError("the handshake answer is too long")
        except socket.timeout as e:
            raise WebSocketError("no answer to the handshake") from e
        head, _, rest = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        if len(parts) < 2 or parts[1] != "101":
            raise WebSocketError(f"the server did not switch to WebSocket ({lines[0]})")
        headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
        want = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != want:
            raise WebSocketError("the server's handshake answer is wrong")
        self._buf += rest

    # -- receiving --------------------------------------------------------------------------------------------------------

    def _parse(self):
        """One whole frame from the buffer, or None when it has not all arrived. (fin, opcode, payload)."""
        b = self._buf
        if len(b) < 2:
            return None
        fin, op = bool(b[0] & 0x80), b[0] & 0x0F
        masked, n, i = bool(b[1] & 0x80), b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, i = struct.unpack(">H", bytes(b[2:4]))[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, i = struct.unpack(">Q", bytes(b[2:10]))[0], 10
        if len(b) < i + (4 if masked else 0) + n:
            return None
        key = bytes(b[i:i + 4]) if masked else b""
        i += 4 if masked else 0
        payload = bytes(b[i:i + n])
        if masked:
            payload = bytes(c ^ key[k % 4] for k, c in enumerate(payload))
        del b[:i + n]
        return fin, op, payload

    def recv(self, timeout: float | None = None):
        """The next message as (TEXT, str) or (BINARY, bytes); None when nothing whole arrived within `timeout` seconds
        (what did arrive is kept). Raises WebSocketError when the connection is closed."""
        if self.closed:
            raise WebSocketError("the connection is closed")
        self.sock.settimeout(timeout)
        while True:
            frame = self._parse()
            if frame is None:
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    return None
                except OSError as e:
                    self.closed = True
                    raise WebSocketError(str(e)) from e
                if not chunk:
                    self.closed = True
                    raise WebSocketError("the connection was closed")
                self._buf += chunk
                continue
            fin, op, payload = frame
            if op == 0x9:                       # ping: answer with the same bytes
                self._send(0xA, payload)
                continue
            if op == 0xA:
                continue
            if op == 0x8:
                self.closed = True
                try:
                    self._send(0x8, payload[:2])
                except OSError:
                    pass
                raise WebSocketError("the server closed the connection")
            if op in (TEXT, BINARY):
                self._frag = [op, bytearray(payload)]
            elif op == 0 and self._frag is not None:
                self._frag[1] += payload
            else:
                raise WebSocketError("a broken frame sequence")
            if fin:
                kind, data = self._frag
                self._frag = None
                return (kind, bytes(data).decode("utf-8", "replace")) if kind == TEXT else (kind, bytes(data))

    # -- sending and closing ----------------------------------------------------------------------------------------------

    def _send(self, op: int, payload: bytes = b"") -> None:
        n = len(payload)
        head = bytes([0x80 | op])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                                    else bytes([0x80 | 127]) + struct.pack(">Q", n))
        key = os.urandom(4)
        self.sock.sendall(head + key + bytes(c ^ key[k % 4] for k, c in enumerate(payload)))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._send(0x8, b"")
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
