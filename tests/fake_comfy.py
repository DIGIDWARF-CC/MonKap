"""A made-up ComfyUI for tests: the HTTP routes MonKap uses and a WebSocket that reports progress. Nothing real is touched."""
import base64
import hashlib
import io
import json
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


RESULT = _png()
PREVIEW = b"\xff\xd8\xff-a-preview-frame-"


def frame(op: int, payload: bytes, fin: bool = True) -> bytes:
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | op])
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    return head + payload


class _Quiet(ThreadingHTTPServer):
    def handle_error(self, request, client_address):      # a client that hangs up is not news in a test
        pass


class FakeComfy:
    def __init__(self, steps=4, ws=True, fail=False, reject=None, classes=None, choices=None, step_delay=0.02, hang=False):
        self.steps, self.ws, self.fail, self.reject, self.step_delay, self.hang = steps, ws, fail, reject, step_delay, hang
        # node class -> its /object_info entry's required inputs (a list means a combo's choices)
        self.classes = dict(classes) if classes is not None else {}
        self.choices = choices or {}
        self.uploads, self.prompts, self.deleted, self.queue_deleted = [], [], [], []
        self.interrupted = threading.Event()
        self.freed = 0
        self.history, self.files, self.clients = {}, {}, []
        fc = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/ws":
                    if not fc.ws:
                        return self._send(404, {"error": "no websocket"})
                    key = self.headers["Sec-WebSocket-Key"]
                    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.send_header("Sec-WebSocket-Accept", accept)
                    self.end_headers()
                    fc.clients.append(self.connection)
                    self.close_connection = True
                    try:
                        while self.connection.recv(4096):
                            pass
                    except OSError:
                        pass
                    return
                if path.startswith("/object_info/"):
                    cls = path.rsplit("/", 1)[1]
                    if cls not in fc.classes:
                        return self._send(200, {})
                    required = {k: ([fc.choices[(cls, k)]] if (cls, k) in fc.choices else ["STRING"]) for k in fc.classes[cls]}
                    return self._send(200, {cls: {"input": {"required": required}}})
                if path.startswith("/history/"):
                    pid = path.rsplit("/", 1)[1]
                    return self._send(200, {pid: fc.history[pid]} if pid in fc.history else {})
                if path == "/view":
                    name = self.path.split("filename=")[1].split("&")[0]
                    return self._send(200, fc.files.get(name, b""), "image/png")
                self._send(404, {})

            def do_POST(self):
                path = self.path.split("?")[0]
                body = self._body()
                if path == "/upload/image":
                    head, _, rest = body.partition(b'name="image"; filename="')
                    name = rest.split(b'"', 1)[0].decode()
                    data = rest.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                    fc.uploads.append((name, data, b'name="type"\r\n\r\ntemp' in head))
                    return self._send(200, {"name": name, "subfolder": "", "type": "temp"})
                if path == "/prompt":
                    req = json.loads(body)
                    if fc.reject:
                        return self._send(400, fc.reject)
                    pid = uuid.uuid4().hex
                    fc.prompts.append(req["prompt"])
                    threading.Thread(target=fc._simulate, args=(pid, req["prompt"]), daemon=True).start()
                    return self._send(200, {"prompt_id": pid, "number": 1, "node_errors": {}})
                if path == "/interrupt":
                    fc.interrupted.set()
                    return self._send(200, {})
                if path == "/free":
                    fc.freed += 1
                    return self._send(200, {})
                if path == "/history":
                    fc.deleted += json.loads(body).get("delete", [])
                    for pid in json.loads(body).get("delete", []):
                        fc.history.pop(pid, None)
                    return self._send(200, {})
                if path == "/queue":
                    fc.queue_deleted += json.loads(body).get("delete", [])
                    return self._send(200, {})
                self._send(404, {})

        self.server = _Quiet(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # -- what the made-up server does with a job ----------------------------------------------------------------------

    def say(self, kind, **data):
        raw = frame(1, json.dumps({"type": kind, "data": data}).encode())
        for c in list(self.clients):
            try:
                c.sendall(raw)
            except OSError:
                pass

    def preview(self):
        raw = frame(2, struct.pack(">II", 1, 1) + PREVIEW)
        for c in list(self.clients):
            try:
                c.sendall(raw)
            except OSError:
                pass

    def _simulate(self, pid, graph):
        sampler = next(i for i, n in graph.items() if n["class_type"] == "KSampler")
        result = next(i for i, n in graph.items() if n["class_type"] == "PreviewImage")
        if self.ws:
            time.sleep(0.05)
            self.say("status", status={"exec_info": {"queue_remaining": 1}})
            self.say("execution_start", prompt_id=pid)
            for nid in graph:
                if nid != sampler:
                    self.say("executing", node=nid, prompt_id=pid)
                    break
            if self.hang:
                self.interrupted.wait(10)
                self.say("execution_interrupted", prompt_id=pid, node_id=sampler)
                return
            self.say("executing", node=sampler, prompt_id=pid)
            for s in range(1, self.steps + 1):
                if self.interrupted.is_set():
                    self.say("execution_interrupted", prompt_id=pid, node_id=sampler)
                    return
                if self.fail and s == 2:
                    self.say("execution_error", prompt_id=pid, node_id=sampler, exception_message="out of memory",
                             exception_type="OOM")
                    return
                self.say("progress", value=s, max=self.steps, prompt_id=pid, node=sampler)
                self.preview()
                time.sleep(self.step_delay)
        else:
            time.sleep(0.3)
        self.files["out.png"] = RESULT
        self.history[pid] = {"outputs": {result: {"images": [{"filename": "out.png", "subfolder": "", "type": "temp"}]}},
                             "status": {"status_str": "error" if (self.fail and not self.ws) else "success"}}
        if self.ws:
            self.say("executing", node=None, prompt_id=pid)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        for c in self.clients:
            try:
                c.close()
            except OSError:
                pass
