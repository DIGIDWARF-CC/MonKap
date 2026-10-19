"""Enhancing a picture with a ComfyUI that is already running (its HTTP and WebSocket API), the way captions use LM Studio.

MonKap knows nothing about what happens between the marked nodes of a template: it needs four things, found by the
nodes' titles (`MonKap Image`, `MonKap Prompt`, `MonKap Seed`, `MonKap Result`, each may end in `v1`). The picture goes in
as a temporary upload with a random name, the result comes back from the temporary folder (the template ends in a
preview node, not a save node), and the run is removed from ComfyUI's history afterwards.

The template is a workflow saved with "Save (API Format)". `ai/templates/enhance-qwen21-reference.json` is the reference.
"""
from __future__ import annotations

import copy
import http.client
import json
import re
import struct
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass

from .wsclient import BINARY, WebSocket, WebSocketError

DEFAULT_URL = "http://127.0.0.1:8188"
SUPPORTED_VERSION = 1                       # the template contract this MonKap speaks (the `v1` in the titles)
MARK = re.compile(r"^\s*MonKap\s+(Image|Prompt|Seed|Result)\b(?:\s+v(\d+))?", re.I)
# the input of a marked node that MonKap writes (or, for the result, reads the node's images from)
FIELDS = {"image": ("image",), "prompt": ("value", "text", "prompt"), "seed": ("value", "seed"), "result": ("images",)}
REQUIRED = ("image", "result")


class ComfyError(Exception):
    """ComfyUI could not be reached, or it refused or failed the run: the text says what to do."""


class TemplateError(ValueError):
    """The template can't be used: the text says why."""


# ------------------------------------------------------------------------------------------------ the template

@dataclass
class Template:
    graph: dict
    nodes: dict                             # role -> node id (str)
    fields: dict                            # role -> the input's name on that node
    version: int = 1


def _title(node: dict) -> str:
    meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
    return str(meta.get("title") or "")


def _ancestors(graph: dict, start: str) -> set:
    seen, todo = set(), [start]
    while todo:
        for v in (graph.get(todo.pop(), {}).get("inputs") or {}).values():
            if isinstance(v, list) and v and str(v[0]) in graph and str(v[0]) not in seen:
                seen.add(str(v[0]))
                todo.append(str(v[0]))
    return seen


def parse_template(data) -> Template:
    """A workflow in API format with the marked nodes in place; TemplateError says what is wrong when it isn't."""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError as e:
            raise TemplateError(f"This is not a JSON file ({e}).") from e
    if not isinstance(data, dict) or not data:
        raise TemplateError("This is not a ComfyUI workflow.")
    if "nodes" in data and "links" in data:
        raise TemplateError("This is a workflow saved from the editor. In ComfyUI use Save (API Format) (Export (API) in the "
                            "older menu) and use that file.")
    graph = {}
    for nid, n in data.items():
        if not isinstance(n, dict) or "class_type" not in n or not isinstance(n.get("inputs"), dict):
            raise TemplateError(f"Node {nid} is not in API format (no class_type or inputs). Use Save (API Format).")
        graph[str(nid)] = n
    found: dict = {}
    versions = []
    for nid, n in graph.items():
        m = MARK.match(_title(n))
        if m:
            role = m.group(1).lower()
            found.setdefault(role, []).append(nid)
            versions.append(int(m.group(2) or 1))
    for role in REQUIRED:
        if role not in found:
            raise TemplateError(f"No node is titled \"MonKap {role.capitalize()}\". Start from the reference template.")
    nodes, fields = {}, {}
    for role, ids in found.items():
        if len(ids) > 1:
            raise TemplateError(f"{len(ids)} nodes are titled \"MonKap {role.capitalize()}\"; there must be one.")
        nid = ids[0]
        names = FIELDS[role]
        have = next((f for f in names if f in graph[nid]["inputs"]), None)
        if have is None:
            raise TemplateError(f"The \"MonKap {role.capitalize()}\" node ({graph[nid]['class_type']}) has no "
                                f"{' or '.join(repr(f) for f in names)} input.")
        nodes[role], fields[role] = nid, have
    if max(versions) > SUPPORTED_VERSION:
        raise TemplateError(f"The template is version {max(versions)}, newer than this MonKap understands "
                            f"({SUPPORTED_VERSION}). Update MonKap.")
    anc = _ancestors(graph, nodes["result"])
    for role in ("image", "prompt", "seed"):
        if role in nodes and nodes[role] not in anc:
            raise TemplateError(f"The result does not depend on the \"MonKap {role.capitalize()}\" node: a link is missing.")
    return Template(graph=graph, nodes=nodes, fields=fields, version=min(versions))


def prepare(t: Template, image_ref: str, prompt: str | None = None, seed: int | None = None) -> dict:
    """A copy of the graph with the uploaded picture, the prompt and the seed written into the marked nodes."""
    g = copy.deepcopy(t.graph)
    g[t.nodes["image"]]["inputs"][t.fields["image"]] = image_ref
    if prompt is not None and "prompt" in t.nodes:
        g[t.nodes["prompt"]]["inputs"][t.fields["prompt"]] = prompt
    if seed is not None and "seed" in t.nodes:
        g[t.nodes["seed"]]["inputs"][t.fields["seed"]] = int(seed)
    return g


def _options(spec):
    """The choices of a combo input in ComfyUI's /object_info (old form: a list; new form: ["COMBO", {"options": [...]}])."""
    if isinstance(spec, list) and spec:
        if isinstance(spec[0], list):
            return spec[0]
        if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1].get("options")
    return None


def problems(t: Template, node_info) -> list[str]:
    """What the server can't do with this template, before anything is sent: nodes it doesn't have, and models it doesn't
    list. `node_info(class_type)` returns that node's /object_info entry, or {} when the server doesn't know it."""
    out, marked = [], set(t.nodes.values())
    for nid, n in t.graph.items():
        cls = n["class_type"]
        info = node_info(cls) or {}
        if cls not in info:
            out.append(f"The ComfyUI at this address has no node \"{cls}\" (install the custom node that provides it).")
            continue
        if nid in marked:
            continue
        required = (info[cls].get("input") or {}).get("required") or {}
        for name, v in n["inputs"].items():
            if isinstance(v, list):
                continue
            opts = _options(required.get(name))
            if opts and v not in opts:
                out.append(f"{_title(n) or cls}: \"{v}\" is not among the server's choices for {name}; pick one in ComfyUI "
                           "and save the template again.")
    return out


# ------------------------------------------------------------------------------------------------ the server

def normalize_url(url: str | None) -> str:
    u = str(url or "").strip().rstrip("/")
    if not u:
        return DEFAULT_URL
    return u if "://" in u else "http://" + u


class Client:
    def __init__(self, url: str | None = None, timeout: float = 15.0):
        self.url = normalize_url(url)
        self.timeout = timeout

    def _call(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None, timeout: float | None = None):
        """One request. `self.timeout` limits the connecting (a ComfyUI that isn't there must not keep anyone waiting), the
        `timeout` argument limits the waiting for the answer (an upload or a download can be long)."""
        u = urllib.parse.urlsplit(self.url)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=self.timeout)
        try:
            conn.connect()
            conn.sock.settimeout(timeout or self.timeout)
            conn.request(method, (u.path or "") + path, body=body, headers=headers or {})
            r = conn.getresponse()
            data = r.read()
        except (OSError, http.client.HTTPException) as e:
            why = ("nothing is listening at that address" if isinstance(e, ConnectionRefusedError)
                   else "it did not answer in time" if isinstance(e, TimeoutError) else str(e))
            raise ComfyError(f"Can't reach ComfyUI at {self.url}: {why}. Is it running, and is the address right?") from e
        finally:
            conn.close()
        if r.status >= 400:
            raise ComfyError(_http_message(r.status, r.reason, data))
        return data

    def _json(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        raw = self._call(method, path, body, {"Content-Type": "application/json"} if body is not None else None)
        try:
            return json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except ValueError as e:
            raise ComfyError("ComfyUI answered something that is not JSON. Is this its address?") from e

    def node_info(self, cls: str) -> dict:
        return self._json("GET", "/object_info/" + urllib.parse.quote(cls, safe=""))

    def upload(self, data: bytes, filename: str) -> str:
        """Puts the picture in ComfyUI's temporary folder; returns how a LoadImage names it."""
        boundary = "----monkap" + uuid.uuid4().hex
        parts = []
        for k, v in (("type", "temp"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
                     "Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        raw = self._call("POST", "/upload/image", b"".join(parts), {"Content-Type": "multipart/form-data; boundary=" + boundary},
                         timeout=120)
        try:
            info = json.loads(raw.decode("utf-8"))
            name, sub = str(info["name"]), str(info.get("subfolder") or "")
        except (ValueError, KeyError) as e:
            raise ComfyError("ComfyUI's answer to the upload was not understood.") from e
        return f"{sub + '/' if sub else ''}{name} [temp]"

    def queue(self, graph: dict, client_id: str) -> str:
        r = self._json("POST", "/prompt", {"prompt": graph, "client_id": client_id})
        pid = r.get("prompt_id")
        if not pid:
            raise ComfyError("ComfyUI did not accept the job.")
        return str(pid)

    def history(self, pid: str) -> dict:
        return (self._json("GET", "/history/" + urllib.parse.quote(pid, safe="")) or {}).get(pid) or {}

    def view(self, filename: str, subfolder: str, kind: str) -> bytes:
        q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": kind})
        return self._call("GET", "/view?" + q, timeout=120)

    def interrupt(self) -> None:
        self._call("POST", "/interrupt", b"{}", {"Content-Type": "application/json"})

    def drop_from_queue(self, pid: str) -> None:
        self._json("POST", "/queue", {"delete": [pid]})

    def forget(self, pid: str) -> None:
        self._json("POST", "/history", {"delete": [pid]})

    def free(self) -> None:
        """Unloads the models and frees the memory, so a trainer has the video memory."""
        self._json("POST", "/free", {"unload_models": True, "free_memory": True})

    def listen(self, client_id: str) -> WebSocket:
        u = urllib.parse.urlsplit(self.url)
        return WebSocket(f"ws://{u.netloc}/ws?clientId={client_id}", timeout=self.timeout)


def _http_message(code: int, reason: str, raw: bytes) -> str:
    """ComfyUI's refusal in words: its error, then the first problem of each node it names."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except ValueError:
        return f"ComfyUI answered {code} {reason}."
    if not isinstance(body, dict):
        return f"ComfyUI answered {code} {reason}."
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    msg = str(err.get("message") or f"ComfyUI answered {code}")
    notes = []
    for nid, ne in (body.get("node_errors") or {}).items():
        for item in (ne.get("errors") or [])[:1]:
            notes.append(f"{ne.get('class_type', 'node')} {nid}: {item.get('message', '')} {item.get('details', '')}".strip())
    return msg + (" " + "; ".join(notes[:3]) if notes else "")


# ------------------------------------------------------------------------------------------------ one run

class Job:
    """One enhance run, in a thread: `snapshot()` is what the page shows, `preview` the latest picture of the process,
    `result` the finished picture's bytes."""

    def __init__(self, client: Client, template: Template, image: bytes, ext: str = ".png", prompt: str | None = None,
                 seed: int | None = None):
        self.client, self.template, self.image = client, template, image
        self.ext = ext if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext or "") else ".png"
        self.prompt, self.seed = prompt, seed
        self.status = "queued"              # queued -> running -> done | error | cancelled
        self.stage, self.error = "Starting", ""
        self.step = self.steps = 0
        self.preview: bytes | None = None
        self.preview_seq = 0
        self.result: bytes | None = None
        self.live = True                    # False: no progress channel, only "working / done"
        self._t0 = self._last = time.monotonic()
        self._end: float | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._pid = ""
        self._thread = threading.Thread(target=self._run, daemon=True)

    # -- what the page asks for -------------------------------------------------------------------------------------------

    def start(self) -> "Job":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()
        try:
            self.client.interrupt()
            if self._pid:
                self.client.drop_from_queue(self._pid)
        except ComfyError:
            pass

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def snapshot(self) -> dict:
        now = time.monotonic()
        end = self._end or now
        with self._lock:
            pct = round(100 * self.step / self.steps) if self.steps else None
            return {"status": self.status, "stage": self.stage, "step": self.step, "steps": self.steps, "percent": pct,
                    "elapsed": round(end - self._t0, 1), "idle": 0 if self._end else round(now - self._last, 1),
                    "error": self.error, "preview": self.preview_seq if self.preview else 0, "live": self.live,
                    "result": self.result is not None}

    # -- the run ----------------------------------------------------------------------------------------------------------

    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self) -> None:
        ws = None
        try:
            cid = uuid.uuid4().hex
            try:
                ws = self.client.listen(cid)         # before the job is queued, so no early message is missed
            except WebSocketError:
                self._set(live=False)
            self._set(status="running", stage="Sending the picture")
            ref = self.client.upload(self.image, f"monkap_{uuid.uuid4().hex[:10]}{self.ext}")
            self._pid = self.client.queue(prepare(self.template, ref, self.prompt, self.seed), cid)
            self._set(stage="Waiting for ComfyUI")
            outcome = self._watch(ws) if ws else self._poll()
            if self._cancel.is_set() or outcome == "cancelled":
                self._set(status="cancelled", stage="Cancelled")
            elif outcome == "error":
                self._set(status="error", stage="Failed")
            else:
                self._set(stage="Fetching the result")
                self._fetch()
                self._set(status="done", stage="Done")
        except ComfyError as e:
            self._set(status="error", stage="Failed", error=str(e))
        except Exception as e:                       # a bug here must reach the page as a message, not as a silent thread
            self._set(status="error", stage="Failed", error=f"{type(e).__name__}: {e}")
        finally:
            self._end = time.monotonic()
            if ws:
                ws.close()
            if self._pid:
                try:
                    self.client.forget(self._pid)    # leave nothing in ComfyUI's history
                except ComfyError:
                    pass

    def _title(self, node) -> str:
        n = self.template.graph.get(str(node)) or {}
        return _title(n) or str(n.get("class_type") or node)

    def _watch(self, ws: WebSocket):
        while not self._cancel.is_set():
            try:
                m = ws.recv(timeout=0.5)
            except WebSocketError:
                self._set(live=False)
                return self._poll()
            if m is None:
                continue
            self._last = time.monotonic()
            kind, data = m
            if kind == BINARY:
                self._preview(data)
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            outcome = self._handle(msg)
            if outcome:
                return outcome
        return "cancelled"

    def _handle(self, msg: dict):
        t, d = msg.get("type"), msg.get("data") or {}
        if not isinstance(d, dict) or d.get("prompt_id") not in (None, self._pid):
            return None
        if t == "status" and self.stage == "Waiting for ComfyUI":
            ahead = ((d.get("status") or {}).get("exec_info") or {}).get("queue_remaining")
            if isinstance(ahead, int) and ahead > 1:
                self._set(stage=f"In ComfyUI's queue ({ahead - 1} ahead)")
        elif t == "execution_start":
            self._set(stage="Starting")
        elif t == "executing":
            if d.get("node") is None:
                return "done"
            self._set(stage=self._title(d["node"]), step=0, steps=0)
        elif t == "progress":
            try:
                self._set(step=int(d.get("value", 0)), steps=int(d.get("max", 0)))
            except (TypeError, ValueError):
                pass
            if d.get("node") is not None:
                self._set(stage=self._title(d["node"]))
        elif t == "execution_success":
            return "done"
        elif t == "execution_error":
            node = d.get("node_id")
            where = f" in {self._title(node)}" if node is not None else ""
            self._set(error=f"ComfyUI failed{where}: {d.get('exception_message') or d.get('exception_type') or 'unknown error'}")
            return "error"
        elif t == "execution_interrupted":
            return "cancelled"
        return None

    def _preview(self, data: bytes) -> None:
        """ComfyUI's binary frames: 4 bytes type, then (type 1) 4 bytes image format and the image, or (type 4) 4 bytes of
        metadata length, the metadata and the image."""
        if len(data) < 8:
            return
        kind, second = struct.unpack(">II", data[:8])
        img = data[8:] if kind == 1 else data[8 + second:] if kind == 4 else b""
        if img:
            with self._lock:
                self.preview, self.preview_seq = img, self.preview_seq + 1

    def _poll(self):
        """No progress channel: ask for the finished run once a second."""
        self._set(live=False, stage="Working (no progress information from ComfyUI)")
        while not self._cancel.is_set():
            h = self.client.history(self._pid)
            if h:
                ok = (h.get("status") or {}).get("status_str", "success") == "success"
                if not ok:
                    self._set(error="ComfyUI reported that the run failed (see its console).")
                return "done" if ok else "error"
            self._last = time.monotonic()
            self._cancel.wait(1.0)
        return "cancelled"

    def _fetch(self) -> None:
        node = self.template.nodes["result"]
        for _ in range(40):                          # the history entry can lag the last message by a moment
            h = self.client.history(self._pid)
            images = (((h.get("outputs") or {}).get(node)) or {}).get("images") or []
            if images:
                im = images[0]
                self._set(result=self.client.view(str(im.get("filename")), str(im.get("subfolder") or ""),
                                                  str(im.get("type") or "temp")))
                return
            if self._cancel.wait(0.25):
                break
        raise ComfyError("ComfyUI finished but returned no picture: is the \"MonKap Result\" node a preview or save node?")
