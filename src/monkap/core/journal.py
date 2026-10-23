"""MonKap's notes about each picture of a dataset, in `<dataset>/.monkap/pictures.json` (never a caption, never read by a trainer):
who wrote the caption and whether the picture was enhanced.

A caption is the model's for as long as its text still has the fingerprint of the text the model returned, and the user's as
soon as anything in it changed. A caption with no note (older data, another tool, a crop's copy) counts as the user's.

    {"<picture stem>": {"ai": "<fingerprint of the model's text>", "model": "...", "at": "...", "enhanced": true}}
"""
from __future__ import annotations

import json
import os
import threading
import time

from .captions import text_digest
from .fsutil import MONKAP_DIR, monkap_dir, write_text_atomic

FILE = "pictures.json"
_lock = threading.RLock()                    # the server answers on several threads: a read-modify-write must not interleave


def path_of(folder: str) -> str:
    return os.path.join(folder, MONKAP_DIR, FILE)


def load(folder: str) -> dict:
    try:
        with open(path_of(folder), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(folder: str, data: dict) -> None:
    monkap_dir(folder)
    write_text_atomic(path_of(folder), json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), overwrite=True)


def _change(folder: str, stem: str, fn) -> None:
    with _lock:
        data = load(folder)
        before = json.dumps(data, sort_keys=True)
        entry = fn(dict(data.get(stem) or {}))
        if entry:
            data[stem] = entry
        else:
            data.pop(stem, None)
        if json.dumps(data, sort_keys=True) != before:       # nothing to note: no file, no folder (a trainer's folder stays clean)
            _save(folder, data)


def entry(folder: str, stem: str) -> dict | None:
    return load(folder).get(stem) or None


def set_entry(folder: str, stem: str, new: dict | None) -> None:
    """Put the note of a picture back exactly as it was (Undo), or remove it (None)."""
    _change(folder, stem, lambda _old: dict(new) if new else {})


def mark_ai(folder: str, stem: str, text: str, model: str = "") -> None:
    """The caption `text` (as it is on disk now) was written by the model."""
    def fn(e):
        e.update({"ai": text_digest(text), "model": model, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return e
    _change(folder, stem, fn)


def mark_enhanced(folder: str, stem: str, on: bool = True) -> None:
    def fn(e):
        if on:
            e["enhanced"] = True
        else:
            e.pop("enhanced", None)
        return e
    _change(folder, stem, fn)


def keep_class(folder: str, stem: str, old_text: str | None, new_text: str) -> None:
    """A mechanical change of the text (the trigger word put first): the model's caption stays the model's."""
    def fn(e):
        if e.get("ai") and e["ai"] == text_digest(old_text or ""):
            e["ai"] = text_digest(new_text)
        return e
    _change(folder, stem, fn)


def drop_ai(folder: str, stems=None) -> None:
    """Forget who wrote the captions of these pictures (all of them when `stems` is None): they were cleared."""
    with _lock:
        data = load(folder)
        before = json.dumps(data, sort_keys=True)
        for stem in (list(data) if stems is None else stems):
            e = data.get(stem)
            if e:
                for k in ("ai", "model", "at"):
                    e.pop(k, None)
                if not e:
                    data.pop(stem, None)
        if json.dumps(data, sort_keys=True) != before:
            _save(folder, data)


def forget(folder: str, stem: str) -> None:
    set_entry(folder, stem, None)


def classify(text: str | None, note: dict | None) -> str:
    """"none" (no caption), "ai" (the model's text, untouched) or "manual"."""
    if not (text or "").strip():
        return "none"
    if note and note.get("ai") and note["ai"] == text_digest(text):
        return "ai"
    return "manual"
