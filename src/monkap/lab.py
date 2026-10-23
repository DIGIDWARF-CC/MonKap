"""The Enhance lab, server side: tasks (a list of pictures with one extra instruction), a worker that runs them through ComfyUI one
picture at a time, and the results that wait for the user's decision.

Everything is kept on disk in MonKap's own data folder (`lab/<folder key>/state.json` and the result pictures beside it), so tasks and
waiting results outlive the page, the window and MonKap itself, and go with the rest of MonKap's data when that is removed. A task that
was running or queued when MonKap went away comes back as stopped: nothing starts by itself.

A picture leaves its task the moment its result is saved (stopping or a crash never repeats finished work); a picture that fails stays
in its task, marked. Tasks and the single Enhance of the main view share one rule: only one thing talks to ComfyUI at a time.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time

from . import appdata, dataset, enhance
from .ai import comfy
from .core import captions, jobs, journal
from .core.fsutil import write_text_atomic
from .core.settings import MonkapSettings

MAX_FAILS_IN_A_ROW = 3
_lock = threading.RLock()
_queue: list = []                      # [(folder, task id)]: started tasks, the first is the running one
_worker: threading.Thread | None = None
_run: dict | None = None               # the picture being made: {"folder", "task", "name", "job", "regen"}
_stops: set = set()                    # (folder, task id) asked to stop
_last_error: dict = {}                 # folder -> the last thing that went wrong in the lab, for the page to show


# ------------------------------------------------------------------------------------------------ the files

def _key(folder: str) -> str:
    return hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:12]


def _dir(folder: str) -> str:
    return os.path.join(appdata.root(), "lab", _key(folder))


def _empty(folder: str) -> dict:
    return {"version": 1, "folder": os.path.abspath(folder), "next": 1, "tasks": [], "pending": []}


def load(folder: str) -> dict:
    """The folder's lab state. Tasks that say queued or running without a live worker behind them are stopped (the program was closed)."""
    folder = os.path.abspath(folder)
    try:
        with open(os.path.join(_dir(folder), "state.json"), encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict) or not isinstance(st.get("tasks"), list):
            raise ValueError
    except (OSError, ValueError):
        return _empty(folder)
    st.setdefault("pending", [])
    st.setdefault("next", 1)
    live = {(f, t) for f, t in _queue}
    for t in st["tasks"]:
        if t.get("state") in ("queued", "running") and (folder, t["id"]) not in live:
            t["state"] = "stopped"
    return st


def _save(folder: str, st: dict) -> None:
    d = _dir(folder)
    os.makedirs(d, exist_ok=True)
    write_text_atomic(os.path.join(d, "state.json"), json.dumps(st, ensure_ascii=False, indent=1), overwrite=True)


def _mutate(folder: str, fn):
    """Read, change, write, under the lock; returns what `fn` returns."""
    with _lock:
        st = load(folder)
        out = fn(st)
        _save(folder, st)
        return out


def _task(st: dict, tid: int) -> dict | None:
    return next((t for t in st["tasks"] if t["id"] == tid), None)


def _result_path(folder: str, fname: str) -> str:
    return os.path.join(_dir(folder), os.path.basename(fname))


def _drop_result_file(folder: str, fname: str) -> None:
    try:
        os.remove(_result_path(folder, fname))
    except OSError:
        pass


# ------------------------------------------------------------------------------------------------ what the page asks

def running() -> bool:
    """Something of the lab is going or waits to go: this blocks the single Enhance and the page's modes."""
    with _lock:
        return bool(_queue) or _run is not None


def _stat(path: str) -> tuple:
    try:
        st = os.stat(path)
        return round(st.st_mtime, 3), st.st_size
    except OSError:
        return (0, 0)


def state(folder: str) -> dict:
    folder = os.path.abspath(folder)
    st = load(folder)
    run = _run if _run and _run["folder"] == folder else None
    queue = [t for f, t in _queue if f == folder]
    tasks, assigned = [], {}
    for t in st["tasks"]:
        if t.get("hidden"):
            continue
        for name in t["pictures"]:
            assigned[name] = t["id"]
        tasks.append({"id": t["id"], "name": t["name"], "instruction": t["instruction"], "override": bool(t.get("override")), "pictures": list(t["pictures"]),
                      "failed": dict(t.get("failed") or {}), "total": t["total"], "done": t["done"], "state": t["state"],
                      "error": t.get("error", ""), "position": queue.index(t["id"]) + 1 if t["id"] in queue else 0,
                      "started_at": t.get("started_at")})
    pending = []
    for p in st["pending"]:
        path = os.path.join(folder, p["name"])
        pending.append({"name": p["name"], "task": p.get("task"), "made_at": p.get("made_at"), "seed": p.get("seed"),
                        "stale": _stat(path) != (p.get("src_mtime"), p.get("src_size")), "gone": not os.path.isfile(path)})
    cur = None
    if run:
        cur = {**run["job"].snapshot(), "task": run["task"], "name": run["name"], "regen": bool(run.get("regen"))}
    return {"tasks": tasks, "pending": pending, "assigned": assigned, "running": running(), "current": cur,
            "error": _last_error.get(folder, ""), "queue": queue}


def preview():
    run = _run
    data = run["job"].preview if run else None
    return (data, enhance._kind(data)) if data else None


def result(folder: str, name: str):
    folder = os.path.abspath(folder)
    for p in load(folder)["pending"]:
        if p["name"] == name:
            try:
                with open(_result_path(folder, p["result"]), "rb") as f:
                    data = f.read()
            except OSError:
                return None
            return data, enhance._kind(data)
    return None


# ------------------------------------------------------------------------------------------------ what the gallery shows of a picture

_facts: dict = {}                      # path -> (mtime, size, width, height, blur): what the lab gallery knows of a picture
_facts_lock = threading.Lock()
_facts_job: dict = {}                  # folder -> the thread that is reading them


def _blur_score(path: str):
    """A rough sharpness number: the variance of a Laplacian over the picture shrunk to 384 px (the lower, the blurrier);
    None for a picture that can't be read. It is for ordering and a hint, not a measurement."""
    from PIL import Image, ImageFilter, ImageStat
    try:
        with Image.open(path) as im:
            im.draft("L", (768, 768))
            g = im.convert("L")
            g.thumbnail((384, 384))
            lap = g.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128))
            return round(ImageStat.Stat(lap).var[0], 1)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _read_facts(folder: str, names: list) -> None:
    for name in names:
        path = os.path.join(folder, name)
        stamp = _stat(path)
        with _facts_lock:
            have = _facts.get(path)
        if have and have[:2] == stamp:
            continue
        try:
            w, h = dataset.picture_size(path)
        except Exception:                                     # unreadable: no size, no blur
            w = h = 0
        with _facts_lock:
            _facts[path] = (*stamp, w, h, _blur_score(path) if w else None)


def facts(folder: str) -> dict:
    """{name: [width, height, blur]} for the pictures read so far, and how many are still to read. The reading goes on in the
    background (the first call starts it), so a big folder shows its gallery at once and the numbers fill in."""
    folder = os.path.abspath(folder)
    names = [r[0] for r in dataset.list_folder(folder, ".txt")]
    with _facts_lock:
        out = {n: list(_facts[os.path.join(folder, n)][2:]) for n in names
               if os.path.join(folder, n) in _facts and _facts[os.path.join(folder, n)][:2] == _stat(os.path.join(folder, n))}
        job = _facts_job.get(folder)
        if len(out) < len(names) and (job is None or not job.is_alive()):
            todo = [n for n in names if n not in out]
            job = threading.Thread(target=_read_facts, args=(folder, todo), daemon=True)
            _facts_job[folder] = job
            job.start()
    return {"items": out, "left": len(names) - len(out)}


# ------------------------------------------------------------------------------------------------ tasks

def create_task(s: MonkapSettings, folder: str, names, instruction: str = "", name: str = "", override: bool = False) -> dict:
    """A task from the picked pictures. A picture already in a task, or waiting for a decision, is left out (and named)."""
    folder = os.path.abspath(folder)
    if not isinstance(names, list) or not names:
        raise ValueError("Pick some pictures first.")
    for n in names:
        dataset.locate(folder, str(n))                      # each must be a real picture of the folder
    template = enhance.load_template(s)
    override = bool(override)
    enhance._words(s, template, instruction, override)      # refuses words a template can't take, and an override with no words
    instruction = " ".join(str(instruction or "").split())[:enhance.MAX_PROMPT]

    def make(st):
        taken = {n for t in st["tasks"] for n in t["pictures"]} | {p["name"] for p in st["pending"]}
        picked, skipped = [], []
        for n in dict.fromkeys(str(x) for x in names):
            (skipped if n in taken else picked).append(n)
        if not picked:
            raise ValueError("Every picked picture is already in a task or waiting for a decision.")
        tid = st["next"]
        st["next"] = tid + 1
        st["tasks"].append({"id": tid, "name": (str(name).strip()[:60] or f"Task {tid}"), "instruction": instruction, "override": override,
                            "pictures": picked, "failed": {}, "total": len(picked), "done": 0, "state": "idle", "error": ""})
        return {"id": tid, "added": len(picked), "skipped": skipped}
    return _mutate(folder, make)


def update_task(folder: str, tid: int, instruction=None, remove=None, name=None, override=None) -> dict:
    """Change a task that is not queued or running: its instruction, its name, which pictures it holds."""
    def change(st):
        t = _task(st, tid)
        if t is None:
            raise ValueError("That task isn't there.")
        if t["state"] in ("queued", "running"):
            raise ValueError("A running task can't be edited: stop it first.")
        if instruction is not None:
            t["instruction"] = " ".join(str(instruction).split())[:enhance.MAX_PROMPT]
        if override is not None:
            t["override"] = bool(override)
        if t.get("override") and not t["instruction"]:
            raise ValueError("Write the instruction: with the defaults overridden it is the whole prompt.")
        if name is not None and str(name).strip():
            t["name"] = str(name).strip()[:60]
        for n in remove or []:
            if n in t["pictures"]:
                t["pictures"].remove(n)
                t["total"] = max(t["done"], t["total"] - 1)
                (t.get("failed") or {}).pop(n, None)
        if t["state"] in ("done", "stopped") and t["pictures"]:
            t["state"] = "idle"
        return {"ok": True}
    return _mutate(folder, change)


def delete_task(folder: str, tid: int) -> dict:
    def drop(st):
        t = _task(st, tid)
        if t is None:
            return {"ok": True}
        if t["state"] in ("queued", "running"):
            raise ValueError("A running task can't be removed: stop it first.")
        st["tasks"].remove(t)
        return {"ok": True}
    return _mutate(folder, drop)


def start_task(s: MonkapSettings, folder: str, tid: int) -> dict:
    """Put a task in the queue (it starts at once when nothing else is going)."""
    folder = os.path.abspath(folder)
    if enhance.single_busy():
        raise ValueError("An enhance run of the main view is going: wait for it or cancel it.")
    enhance.load_template(s)                                 # a broken template is said now, not after the queue moved
    with _lock:
        def mark(st):
            t = _task(st, tid)
            if t is None:
                raise ValueError("That task isn't there.")
            if t["state"] in ("queued", "running"):
                raise ValueError("That task is already started.")
            if not [n for n in t["pictures"] if n not in (t.get("failed") or {})] and t.get("failed"):
                t["failed"] = {}                             # started again: the failed ones get another go
            if not t["pictures"]:
                raise ValueError("That task has no pictures left.")
            t["state"], t["error"] = "queued", ""
            t["started_at"] = time.time()
        _mutate(folder, mark)
        _queue.append((folder, tid))
        _last_error.pop(folder, None)
        _ensure_worker()
    return {"ok": True}


def stop_task(folder: str, tid: int) -> dict:
    folder = os.path.abspath(folder)
    with _lock:
        if (folder, tid) not in _queue:
            return {"ok": True}
        _stops.add((folder, tid))
        if _run and _run["folder"] == folder and _run["task"] == tid:
            _run["job"].cancel()                              # the picture being made is dropped, it stays in its task
        else:
            _queue.remove((folder, tid))                      # waiting its turn: simply not queued any more
            _stops.discard((folder, tid))

            def mark(st):
                t = _task(st, tid)
                if t:
                    t["state"] = "stopped"
            _mutate(folder, mark)
    return {"ok": True}


def shutdown() -> None:
    """MonKap is closing: the picture in ComfyUI is interrupted and the tasks are left stopped (they never resume by themselves)."""
    with _lock:
        for f, t in list(_queue):
            _stops.add((f, t))
        if _run:
            _run["job"].cancel()
    for _ in range(50):
        if not running():
            break
        time.sleep(0.1)


# ------------------------------------------------------------------------------------------------ the worker

def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True)
            _worker.start()


def _work() -> None:
    global _worker
    while True:
        with _lock:
            if not _queue:
                _worker = None
                return
            folder, tid = _queue[0]
        try:
            _run_task(folder, tid)
        except Exception as exc:                              # a bug here must show on the page, not kill the queue silently
            _last_error[folder] = f"{type(exc).__name__}: {exc}"
            _set_state(folder, tid, "stopped", _last_error[folder])
        with _lock:
            if _queue and _queue[0] == (folder, tid):
                _queue.pop(0)
            _stops.discard((folder, tid))


def _set_state(folder: str, tid: int, state: str, error: str = "") -> None:
    def mark(st):
        t = _task(st, tid)
        if t:
            t["state"], t["error"] = state, error
            if t.get("hidden"):
                st["tasks"].remove(t)
    _mutate(folder, mark)


def _stopped(folder: str, tid: int) -> bool:
    with _lock:
        return (folder, tid) in _stops


def _run_task(folder: str, tid: int) -> None:
    global _run
    s = _settings()
    try:
        template = enhance.load_template(s)
    except ValueError as exc:
        _last_error[folder] = str(exc)
        return _set_state(folder, tid, "stopped", str(exc))
    client = comfy.Client(s.comfy_url)
    _mutate(folder, lambda st: _task(st, tid).update({"state": "running"}) if _task(st, tid) else None)
    streak, hidden = 0, False
    while not _stopped(folder, tid):
        pick = {}

        def choose(st):
            t = _task(st, tid)
            if t is None:
                return
            hidden = bool(t.get("hidden"))
            todo = [n for n in t["pictures"] if n not in (t.get("failed") or {})]
            pick.update({"name": todo[0] if todo else None, "words": t["instruction"], "override": bool(t.get("override")), "hidden": hidden, "regen": t.get("regen")})
        _mutate(folder, choose)
        name = pick.get("name")
        if not name:
            break
        hidden = bool(pick["hidden"])
        path = os.path.join(folder, name)
        if not os.path.isfile(path):                          # gone meanwhile: it leaves the task, nothing to make
            _mutate(folder, lambda st: _leave(st, tid, name, gone=True))
            continue
        try:
            words = pick["words"] if hidden else enhance._words(s, template, pick["words"], pick["override"])
            with open(path, "rb") as f:
                data = f.read()
            w, h = dataset.picture_size(path)
            seed = random.randrange(2 ** 32)
            job = comfy.Job(client, template, data, os.path.splitext(path)[1], words, seed,
                            enhance.size_side(s.enhance_size, w, h)).start()
        except (ValueError, OSError) as exc:
            _mutate(folder, lambda st: _fail(st, tid, name, str(exc)))
            streak += 1
            if streak >= MAX_FAILS_IN_A_ROW:
                break
            continue
        with _lock:
            _run = {"folder": folder, "task": tid, "name": name, "job": job, "regen": bool(pick.get("regen"))}
        job.join()
        with _lock:
            _run = None
        if job.status == "done" and job.result is not None:
            streak = 0
            _mutate(folder, lambda st: _finish(st, folder, tid, name, job, words, path, pick.get("regen")))
        elif job.status == "cancelled" or _stopped(folder, tid):
            break
        else:
            msg = job.error or "ComfyUI did not return the picture."
            _mutate(folder, lambda st: _fail(st, tid, name, msg))
            streak += 1
            if streak >= MAX_FAILS_IN_A_ROW or "Can't reach ComfyUI" in msg:
                _last_error[folder] = msg if "Can't reach" in msg else f"{streak} failures in a row, the task stopped: {msg}"
                break
    _finish_task(folder, tid)


def _settings() -> MonkapSettings:
    from . import api                                         # (late: api imports this module)
    return api.load_settings()


def _leave(st: dict, tid: int, name: str, gone: bool = False) -> None:
    t = _task(st, tid)
    if t and name in t["pictures"]:
        t["pictures"].remove(name)
        (t.get("failed") or {}).pop(name, None)
        if gone:
            t["total"] = max(t["done"], t["total"] - 1)


def _fail(st: dict, tid: int, name: str, error: str) -> None:
    t = _task(st, tid)
    if t:
        t.setdefault("failed", {})[name] = error[:300]


def _finish(st: dict, folder: str, tid: int, name: str, job, words, path: str, regen) -> None:
    """The picture's result is on disk and noted before it leaves its task, so an interruption never loses or repeats it."""
    n = st["next"]
    st["next"] = n + 1
    fname = f"{n}.png"
    os.makedirs(_dir(folder), exist_ok=True)
    with open(_result_path(folder, fname), "wb") as f:
        f.write(job.result)
    mtime, size = _stat(path)
    entry = {"name": name, "result": fname, "task": tid, "seed": job.seed, "words": words, "made_at": time.time(),
             "src_mtime": mtime, "src_size": size}
    old = next((p for p in st["pending"] if p["name"] == name), None)
    if old:                                                    # a regenerated result takes the place of the one before it
        _drop_result_file(folder, old["result"])
        st["pending"][st["pending"].index(old)] = entry
    else:
        st["pending"].append(entry)
    t = _task(st, tid)
    if t:
        _leave(st, tid, name)
        t["done"] += 1


def _finish_task(folder: str, tid: int) -> None:
    def end(st):
        t = _task(st, tid)
        if t is None:
            return
        if t.get("hidden"):
            st["tasks"].remove(t)
            return
        if t["state"] in ("queued", "running"):
            t["state"] = "done" if not t["pictures"] else "stopped"
            if t["pictures"] and not t["error"] and t.get("failed") and not _stopped(folder, tid):
                t["error"] = f"{len(t['failed'])} picture(s) failed; start the task again to retry them."
    _mutate(folder, end)


# ------------------------------------------------------------------------------------------------ the decisions

def regenerate(s: MonkapSettings, folder: str, name: str) -> dict:
    """Make the result of a waiting picture again (a new seed, the same words). Only when no task is going or queued."""
    folder = os.path.abspath(folder)
    if running() or enhance.single_busy():
        raise ValueError("Regenerate will be enabled after all tasks are done or stopped.")
    enhance.load_template(s)

    def make(st):
        p = next((p for p in st["pending"] if p["name"] == name), None)
        if p is None:
            raise ValueError("Nothing waits for that picture.")
        tid = st["next"]
        st["next"] = tid + 1
        st["tasks"].append({"id": tid, "name": "(regenerate)", "instruction": p.get("words") or "", "pictures": [name], "failed": {},
                            "total": 1, "done": 0, "state": "idle", "error": "", "hidden": True, "regen": True})
        return tid
    tid = _mutate(folder, make)
    with _lock:
        _mutate(folder, lambda st: _task(st, tid).update({"state": "queued"}))
        _queue.append((folder, tid))
        _last_error.pop(folder, None)
        _ensure_worker()
    return {"ok": True}


def resolve(s: MonkapSettings, folder: str, name: str, action: str, ext: str, force: bool = False) -> dict:
    """What to do with a waiting result: discard, save (as a new picture), replace, delete (the original, as unfixable)."""
    from . import api
    folder = os.path.abspath(folder)
    entry = next((p for p in load(folder)["pending"] if p["name"] == name), None)
    if entry is None:
        raise ValueError("Nothing waits for that picture.")
    path = os.path.join(folder, name)
    out: dict = {}
    if action == "discard":
        pass
    elif action in ("save", "replace"):
        if not os.path.isfile(path):
            raise ValueError("The original picture isn't there any more: discard this result or save it as a new picture elsewhere.")
        got = result(folder, name)
        if got is None:
            raise ValueError("The result picture is missing: discard it and make it again.")
        data = got[0]
        if action == "replace":
            if not force and _stat(path) != (entry.get("src_mtime"), entry.get("src_size")):
                return {"stale": True}                        # the original changed since this result was made: the page asks
            out = jobs.replace_picture(path, data)
            stem = os.path.splitext(out["name"])[0]
            before = journal.entry(folder, stem)
            out["was_enhanced"] = bool(before and before.get("enhanced"))
            journal.mark_enhanced(folder, stem, True)
        else:
            parent = captions.read_caption(path, ext) or ""
            out = enhance._store(s, path, data, parent, api.folder_trigger(folder), ext)
    elif action == "delete":
        out = api.delete(folder, name, ext)
    else:
        raise ValueError("Unknown action.")

    def drop(st):
        p = next((p for p in st["pending"] if p["name"] == name), None)
        if p:
            st["pending"].remove(p)
            _drop_result_file(folder, p["result"])
    _mutate(folder, drop)
    return {"action": action, **out}
