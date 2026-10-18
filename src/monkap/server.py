"""The local web server: the page (static/) and the routes (api.py), for one user on this machine.

It listens on 127.0.0.1 only and wants the session token on every request: the address printed at start carries it
once, the browser keeps it in a cookie, and a page on another site can't use it (cookie is SameSite=Strict, the Host
and Origin headers are checked, so a DNS-rebinding page is refused too).
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import api

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "monkap_session"
MAX_BODY = 4 << 20                     # a request body is a few lines of JSON


class Handler(BaseHTTPRequestHandler):
    server_version = "MonKap"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):                 # requests are not logged: they name the user's files
        pass

    # -- replies
    def _send(self, code, data: bytes, ctype, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, obj, code=200, extra=()):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", extra)

    # -- who may ask
    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "").lower() in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urlparse(origin).netloc.lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _token_ok(self, qs) -> bool:
        tok = self.server.token
        jar = SimpleCookie(self.headers.get("Cookie") or "")
        got = jar[COOKIE].value if COOKIE in jar else (self.headers.get("X-MonKap-Token") or "")
        return secrets.compare_digest(got.encode(), tok.encode())

    # -- verbs
    def do_GET(self):
        self._handle("GET")

    do_HEAD = do_GET

    def do_POST(self):
        self._handle("POST")

    def _handle(self, verb):
        if not self._host_ok() or not self._origin_ok():
            return self.json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        path = u.path
        if verb == "GET" and path == "/" and (qs.get("t") or [""])[0]:      # the first visit: the token becomes the cookie
            if not secrets.compare_digest(qs["t"][0].encode(), self.server.token.encode()):
                return self.json({"error": "forbidden"}, 403)
            return self._send(302, b"", "text/plain", [
                ("Location", "/"), ("Set-Cookie", f"{COOKIE}={self.server.token}; Path=/; HttpOnly; SameSite=Strict")])
        if not self._token_ok(qs):
            return self._send(401, b"Open MonKap with the address it printed when it started (it carries the session key).\n",
                              "text/plain; charset=utf-8")
        try:
            if path.startswith("/api/"):
                if verb == "GET":
                    return api.get(self, path, qs)
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    return self.json({"error": "too big"}, 413)
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                except ValueError:
                    return self.json({"error": "not JSON"}, 400)
                return api.post(self, path, body if isinstance(body, dict) else {})
            return self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:                          # one bad request must not take the server down
            try:
                self.json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except OSError:
                pass

    def _static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, name))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, token):
        self.token = token
        super().__init__(addr, Handler)


def _free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError("No free port near %d." % start)


def start(port=8765, token=None) -> tuple[Server, str]:
    """A server on 127.0.0.1 (the next free port from `port`), running in a thread; (server, address with the key)."""
    token = token or secrets.token_urlsafe(24)
    srv = Server(("127.0.0.1", _free_port(port)), token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/?t={token}"
