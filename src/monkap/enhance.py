"""Enhance a picture with a ComfyUI: one run at a time, and its result waits for the user's decision.

The result of a run is Retry (the same picture again with a new seed), Discard, Replace (the original is kept in
`.monkap/replaced/` and can be brought back) or Store as Kapture (a new picture next to the original, named and
captioned like a crop). The routes in `api.py` call these functions; the work with ComfyUI itself is in `ai/comfy.py`.
"""
from __future__ import annotations

import os
import random
import threading

from . import dataset
from .ai import comfy
from .core import jobs
from .core.settings import MonkapSettings

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai", "templates", "enhance-qwen21-reference.json")
MAX_PROMPT = 600
_lock = threading.Lock()
_run: dict | None = None                      # {"job", "folder", "name", "path", "prompt"}


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def reference_bytes() -> bytes:
    """The reference template, for the 'download' button."""
    return _read(TEMPLATE)


def load_template(s: MonkapSettings) -> comfy.Template:
    path = s.enhance_template or TEMPLATE
    try:
        raw = _read(path)
    except OSError as exc:
        raise ValueError(f"The template can't be read ({path}: {exc.strerror or exc}).") from exc
    try:
        return comfy.parse_template(raw)
    except comfy.TemplateError as exc:
        raise ValueError(str(exc)) from exc


def check(s: MonkapSettings) -> dict:
    """The settings' Check button: is the template usable, and does the server at this address have what it needs?"""
    try:
        t = load_template(s)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "problems": []}
    try:
        found = comfy.problems(t, comfy.Client(s.comfy_url, timeout=5).node_info)
    except comfy.ComfyError as exc:
        return {"ok": False, "error": str(exc), "problems": []}
    return {"ok": not found, "error": "", "problems": found, "roles": sorted(t.nodes), "version": t.version,
            "custom": bool(s.enhance_template)}


def _busy() -> bool:
    return _run is not None and _run["job"].status in ("queued", "running")


def start(s: MonkapSettings, folder: str, name: str, hint: str = "", prompt: str | None = None) -> dict:
    """Begin an enhance run on a picture of the folder. `hint` is the user's own words for this picture (it replaces the
    template's prompt); `prompt` is for a retry, which keeps the words of the first run."""
    global _run
    folder, path = dataset.locate(folder, name)
    template = load_template(s)
    with _lock:
        if _busy():
            raise ValueError("An enhance run is already going. Wait for it or cancel it.")
        words = prompt if prompt is not None else (" ".join(str(hint or "").split())[:MAX_PROMPT] or s.enhance_prompt.strip() or None)
        data = _read(path)
        seed = random.randrange(2 ** 32)
        job = comfy.Job(comfy.Client(s.comfy_url), template, data, os.path.splitext(path)[1], words, seed).start()
        _run = {"job": job, "folder": folder, "name": name, "path": path, "prompt": words, "seed": seed}
    return {"seed": seed}


def status() -> dict:
    run = _run
    if run is None:
        return {"status": "none"}
    return {**run["job"].snapshot(), "name": run["name"], "folder": run["folder"], "seed": run["seed"]}


def _kind(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if data[:2] == b"\xff\xd8" else "image/webp"


def preview():
    """The latest picture of the process: (bytes, content type), or None."""
    run = _run
    data = run["job"].preview if run else None
    return (data, _kind(data)) if data else None


def result():
    run = _run
    data = run["job"].result if run else None
    return (data, _kind(data)) if data else None


def cancel() -> dict:
    run = _run
    if run is not None:
        run["job"].cancel()
    return {"cancelled": run is not None}


def free(s: MonkapSettings) -> dict:
    """Unload ComfyUI's models and free its memory (for a trainer that needs the video memory)."""
    try:
        comfy.Client(s.comfy_url).free()
    except comfy.ComfyError as exc:
        raise ValueError(str(exc)) from exc
    return {"freed": True}


def decide(s: MonkapSettings, action: str, folder: str, name: str, parent_text: str = "", trigger: str = "", ext: str = ".txt",
           hint: str = "") -> dict:
    """What to do with the result: retry, discard, replace or kapture."""
    global _run
    run = _run
    if run is None:
        raise ValueError("There is no enhance result.")
    if os.path.normcase(os.path.abspath(str(folder or ""))) != os.path.normcase(run["folder"]) or name != run["name"]:
        raise ValueError("That result belongs to another picture.")
    job = run["job"]
    if action == "discard":
        if job.status in ("queued", "running"):
            job.cancel()
        with _lock:
            _run = None
        return {"action": "discard"}
    if action == "retry":
        if _busy():
            raise ValueError("A run is still going.")
        out = start(s, run["folder"], run["name"], prompt=run["prompt"])
        return {"action": "retry", **out}
    if job.status != "done" or job.result is None:
        raise ValueError("The picture is not ready yet.")
    data = job.result
    path = run["path"]
    if action == "replace":
        if not os.path.isfile(path):
            raise ValueError("The original picture isn't there any more.")
        out = jobs.replace_picture(path, data)
    elif action == "kapture":
        out = _store(s, path, data, parent_text, trigger, ext)
    else:
        raise ValueError("Unknown action.")
    with _lock:
        _run = None
    return {"action": action, **out}


def _store(s: MonkapSettings, path: str, data: bytes, parent_text: str, trigger: str, ext: str) -> dict:
    for attempt in range(3):                      # another program may take the planned name meanwhile
        try:
            res = jobs.store_enhanced(path, data, s.template, s.caption_mode, s.store_provenance, ext, str(parent_text or ""),
                                      str(trigger or ""))
            break
        except FileExistsError:
            if attempt == 2:
                raise
    return {"name": os.path.basename(res.path), "size": list(res.size), "caption": res.caption, "notes": res.notes}


def undo_replace(folder: str, name: str, backup: str, old: str) -> dict:
    return {"restored": jobs.revert_replace(folder, name, backup, old)}
