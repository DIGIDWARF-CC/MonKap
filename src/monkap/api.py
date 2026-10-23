"""MonKap's routes: /api/*. Every call names a dataset `folder` and a picture `name` inside it; nothing here takes a
path to a picture anywhere else, and a name that is not a plain picture file name is refused.

The handler `h` has json(data, code=200) and _send(code, bytes, content_type); the server provides them (tests give a
fake one). Pictures are sent with no-store: nothing from a dataset lands in the browser's cache.
"""
from __future__ import annotations

import os
import re
import string
import threading
from dataclasses import replace

from PIL import Image

from . import __version__, appdata, dataset, enhance, lab, maintenance
from .ai import lmstudio, tasks
from .core import captions, jobs, journal, ledger
from .integrations import fizgig, fizgig_review
from .core.fsutil import IMAGE_EXTS
from .core.naming import TemplateError, example_names, normalize_template
from .core.settings import CAPTION_MODES, DELETE_MODES, DELETE_SOFT, RATIO_NAMES, MonkapSettings
from .dataset import MAX_EDGE, NotFound

_kapture_lock = threading.Lock()        # one crop at a time: two requests must not plan the same name
_ai_lock = threading.Lock()             # one caption at a time: the model server is one machine


# ------------------------------------------------------------------------------------------------ settings

def load_settings() -> MonkapSettings:
    return MonkapSettings.from_dict(appdata.read_json("settings.json", {}))


def save_settings(changes: dict) -> tuple[MonkapSettings, str]:
    """Merge `changes` into the saved settings; (settings as kept, a note about the template if it was changed)."""
    merged = {**load_settings().to_dict(), **(changes or {})}
    note = ""
    if "template" in (changes or {}):
        try:
            merged["template"], note = normalize_template(str(merged["template"]))
        except TemplateError as exc:
            raise ValueError(str(exc)) from exc
    s = MonkapSettings.from_dict(merged)
    appdata.write_json("settings.json", s.to_dict())
    return s, note


def effective_ext(s: MonkapSettings | None = None) -> str:
    """The caption files' extension: Fizgig's Caption Extension when MonKap is told to follow it, else MonKap's own."""
    s = s or load_settings()
    if s.fizgig_dir and s.fizgig_ext:
        return fizgig.caption_ext(s.fizgig_dir) or s.caption_ext
    return s.caption_ext


def settings_payload(s: MonkapSettings | None = None) -> dict:
    s = s or load_settings()
    return {"settings": s.to_dict(), "caption_ext": effective_ext(s), "examples": example_names(s.template), "ratios": list(RATIO_NAMES),
            "caption_modes": list(CAPTION_MODES), "delete_modes": list(DELETE_MODES), "version": __version__,
            "tasks": tasks.listing(s.ai_instructions)}


# ------------------------------------------------------------------------------------------------ folders

def _key(folder: str) -> str:
    return os.path.normcase(os.path.abspath(folder))


def folder_trigger(folder: str) -> str:
    """The folder's trigger word: the one set in MonKap, else the one Fizgig has for it (when MonKap follows Fizgig)."""
    mine = str((appdata.read_json("folders.json", {}).get(_key(folder)) or {}).get("trigger") or "")
    if mine:
        return mine
    s = load_settings()
    return fizgig.trigger_word(s.fizgig_dir, folder) if s.fizgig_dir and s.fizgig_trigger else ""


def open_folder(folder: str, trigger=None) -> dict:
    """Remember a folder as the one open (and its trigger word, when given); what the page needs to show it."""
    folder = os.path.abspath(str(folder or ""))
    if not os.path.isdir(folder):
        raise NotFound("That folder isn't there.")
    store = appdata.read_json("folders.json", {})
    entry = store.setdefault(_key(folder), {})
    if trigger is not None:
        entry["trigger"] = str(trigger).strip()
    appdata.write_json("folders.json", store)
    s = load_settings()
    recent = [folder] + [r for r in s.recent if _key(r) != _key(folder)]
    save_settings({"last_folder": folder, "recent": recent[:12]})
    return {"folder": folder, "trigger": str(entry.get("trigger") or "")}


def _exts(raw: str) -> tuple:
    """File extensions asked for by a picker: '.json,.txt' -> ('.json', '.txt'); anything else is ignored."""
    return tuple(e for e in (x.strip().lower() for x in str(raw or "").split(",")) if re.fullmatch(r"\.[a-z0-9]{1,6}", e))


def browse(path: str, exts: tuple = ()) -> dict:
    """The sub-folders of `path` for the pickers ('' = where to start: the drives, or the home folder), and, when `exts`
    names kinds of files, those files too. A path that is a file means the folder it is in."""
    if path and os.path.isfile(path):
        path = os.path.dirname(os.path.abspath(path))
    if not path:
        if os.name == "nt":
            drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
            return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives], "pictures": 0}
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise NotFound("That folder isn't there.")
    dirs, files, pics = [], [], 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                if e.is_dir():
                    dirs.append({"name": e.name, "path": os.path.join(path, e.name)})
                elif os.path.splitext(e.name)[1].lower() in exts:
                    files.append({"name": e.name, "path": os.path.join(path, e.name)})
                elif os.path.splitext(e.name)[1].lower() in IMAGE_EXTS:
                    pics += 1
    except OSError as exc:
        raise ValueError(f"Can't read that folder ({exc.strerror or exc}).") from exc
    parent = os.path.dirname(path)
    return {"path": path, "parent": "" if parent == path else parent,
            "dirs": sorted(dirs, key=lambda d: d["name"].casefold()),
            "files": sorted(files, key=lambda d: d["name"].casefold()), "pictures": pics}


# ------------------------------------------------------------------------------------------------ pictures

def _int(qs, key, default=0, lo=-(1 << 31), hi=1 << 31) -> int:
    try:
        return min(max(int(float((qs.get(key) or [default])[0])), lo), hi)
    except (TypeError, ValueError):
        return default


def _stem(name: str) -> str:
    return os.path.splitext(name)[0]


def _caption_state(folder: str, path: str, name: str, ext: str) -> dict:
    """What the page shows of a caption: who wrote it ("none" / "ai" / "manual"), when the file changed, and the note the
    journal keeps of the picture (so that an Undo can put it back)."""
    note = journal.entry(folder, _stem(name))
    text = captions.read_caption(path, ext)
    try:
        mtime = round(os.path.getmtime(captions.caption_path(path, ext)), 3) if text is not None else 0
    except OSError:
        mtime = 0
    return {"class": journal.classify(text, note), "mtime": mtime, "enhanced": bool(note and note.get("enhanced"))}


def info(folder, name, ext) -> dict:
    folder, path = dataset.locate(folder, name)
    text = captions.read_caption(path, ext)
    row = next((r for r in reversed(ledger.read_provenance(folder)) if str(r.get("file", "")).lower() == name.lower()), None)
    inherited = bool(text and row and row.get("caption_sha1") == captions.text_digest(text))
    return {"name": name, "size": dataset.picture_size(path), "caption": text, "inherited": inherited,
            "mtime": int(os.path.getmtime(path)), **_caption_state(folder, path, name, ext)}


_UNSET = object()


def save_caption(folder, name, text, loaded, ext, mechanical=False, restore=_UNSET) -> dict:
    """Save the editor's text. `mechanical` says the text was changed by a button (the trigger word), not typed: the model's
    caption then stays the model's. `restore` (an Undo) puts the picture's journal note back exactly as it was."""
    folder, path = dataset.locate(folder, name)
    res = captions.save_caption(path, str(text or ""), None if loaded is None else str(loaded), ext)
    stem = _stem(name)
    if restore is not _UNSET:
        journal.set_entry(folder, stem, restore if isinstance(restore, dict) else None)
    elif mechanical and res.kind == "written":
        new = str(text or "").strip()
        if new == captions.ensure_trigger_prefix(loaded or "", folder_trigger(folder)):    # really the trigger word, nothing typed
            journal.keep_class(folder, stem, loaded, new)
    backup = os.path.relpath(res.backup, folder) if res.backup else ""
    return {"kind": res.kind, "backup": backup, "conflict": res.conflict,
            "has_caption": res.kind in ("written", "unchanged") and bool(str(text or "").strip()),
            **_caption_state(folder, path, name, ext)}


def kapture(folder, name, box, parent_text, trigger, ext) -> dict:
    folder, path = dataset.locate(folder, name)
    try:
        box = tuple(int(v) for v in box)
        if len(box) != 4:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("The selection is not a box.") from None
    s = load_settings()
    with _kapture_lock:
        for attempt in range(3):                      # another program may take the planned name meanwhile
            try:
                res = jobs.kapture(path, box, str(parent_text or ""), str(trigger or ""), s.template,
                                   s.caption_mode, s.store_provenance, ext)
                break
            except FileExistsError:
                if attempt == 2:
                    raise
    return {"name": os.path.basename(res.path), "size": list(res.size), "caption": res.caption,
            "notes": res.notes, "mode": s.caption_mode if res.caption is not None else captions.MODE_EMPTY}


def delete(folder, name, ext) -> dict:
    """Soft (the default): into the dataset's removed/ folder, with Undo. Hard (the user's choice in the settings):
    erased, and the picture's cached latents too when a latent cache folder is set."""
    folder, path = dataset.locate(folder, name)
    s = load_settings()
    if s.delete_mode == DELETE_SOFT:
        return {"mode": DELETE_SOFT, "moved": dataset.remove(folder, name, ext)}
    cache = fizgig.latent_cache_for(s.fizgig_dir, folder) if s.fizgig_dir and s.fizgig_cache else s.latent_cache_dir
    res = jobs.delete_permanently(path, cache, ext)
    journal.forget(folder, _stem(name))                       # gone for good: its note goes too (a soft delete keeps it, for Undo)
    return {"mode": "hard", "purged": res.purged, "notes": res.notes}


MAX_MANY = 5000


def delete_many(folder, names, ext) -> dict:
    """The picked pictures, each as `delete` does it (the user's soft or hard mode). One picture failing does not stop the
    rest: it is named in `failed`. `moved` is the whole undo list of a soft delete (`/api/undelete` takes it as it is)."""
    if not isinstance(names, list) or not names or len(names) > MAX_MANY:
        raise ValueError("Pick some pictures first.")
    out = {"mode": None, "moved": [], "purged": 0, "deleted": [], "failed": [], "notes": []}
    for name in names:
        try:
            r = delete(folder, str(name), ext)
        except (NotFound, ValueError, OSError) as exc:
            out["failed"].append({"name": str(name), "error": str(exc)})
            continue
        out["mode"] = r["mode"]
        out["moved"] += r.get("moved", [])
        out["purged"] += r.get("purged", 0)
        out["notes"] += r.get("notes", [])
        out["deleted"].append(str(name))
    out["mode"] = out["mode"] or load_settings().delete_mode
    return out


# ------------------------------------------------------------------------------------------------ the training review

def review(folder: str) -> dict:
    """What Fizgig's newest run on this folder found out about each picture (by file name), with the legend and advice."""
    s = load_settings()
    base = {"legend": fizgig_review.legend()}
    if not s.fizgig_dir:
        return {**base, "available": False, "reason": "Set the Fizgig folder in the settings (the Fizgig tab) to see what a training run found out."}
    rev = fizgig_review.review(s.fizgig_dir, os.path.abspath(folder))
    if not rev.get("available"):
        return {**base, **rev}
    rows = {os.path.splitext(r[0])[0]: r for r in dataset.list_folder(folder, effective_ext(s))}
    started = rev["run"].get("started_at")
    verdicts, handled = {}, 0
    for key, v in rev["images"].items():
        row = rows.get(key)
        if row and v["verdict"] in fizgig_review.VERDICTS:
            if isinstance(started, (int, float)) and row[1] and row[2] > started:
                handled += 1                                 # its caption changed after the run read it: dealt with
                continue
            label, color, means, todo = fizgig_review.VERDICTS[v["verdict"]]
            verdicts[row[0]] = {**v, "label": label, "color": color, "means": means, "todo": todo}
    counts = {k: sum(1 for v in verdicts.values() if v["verdict"] == k) for k in fizgig_review.ORDER}
    return {**base, "available": True, "run": rev["run"], "report": rev["report"], "settings": rev["settings"],
            "verdicts": verdicts, "counts": counts, "handled": handled, "advice": fizgig_review.advice(rev)}


def list_items(folder: str, ext: str) -> list[list]:
    """The pictures with what the strip shows of each: [name, has a caption, who wrote it ("none" / "ai" / "manual"), the caption's
    change time, was enhanced]. The journal is read once; a caption is read only for a picture the model has written for."""
    rows = dataset.list_folder(folder, ext)
    notes = journal.load(folder)
    out = []
    for name, has, mtime in rows:
        note = notes.get(_stem(name)) or {}
        klass = "none"
        if has:
            text = captions.read_caption(os.path.join(folder, name), ext) if note.get("ai") else None
            klass = journal.classify(text, note) if note.get("ai") else "manual"
        out.append([name, has, klass, mtime, 1 if note.get("enhanced") else 0])
    return out


# ------------------------------------------------------------------------------------------------ what changed meanwhile

def live(folder: str, name: str = "") -> dict:
    """What the open page compares with what it has, while it is open (it asks every few seconds): the folder's stamp, the
    Fizgig run's stamp, the extension and trigger word now in force, and the open picture's caption on disk. All of it is
    read from disk each time, so a change made by Fizgig, another program or the settings shows within one question."""
    s = load_settings()
    ext = effective_ext(s)
    folder = os.path.abspath(folder)
    try:
        st = os.stat(folder)
        folder_stamp = f"{st.st_mtime_ns}"
    except OSError:
        folder_stamp = ""
    caption, state = None, {}
    if name:
        try:
            _f, path = dataset.locate(folder, name)
            caption = captions.read_caption(path, ext)
            state = _caption_state(folder, path, name, ext)
        except (NotFound, ValueError, OSError):
            caption = None
    return {"folder": folder_stamp, "review": fizgig_review.stamp(s.fizgig_dir, folder) if s.fizgig_dir else "",
            "ext": ext, "trigger": folder_trigger(folder), "caption": caption, "name": name,
            "busy": {"single": enhance.single_busy(), "lab": lab.running()}, **state}


# ------------------------------------------------------------------------------------------------ enhance

def _lab_post(action: str, b: dict, ext: str) -> dict:
    folder = os.path.abspath(str(b.get("folder") or ""))
    tid = b.get("task")
    if action == "task":
        return lab.create_task(load_settings(), folder, b.get("names"), str(b.get("instruction") or ""), str(b.get("title") or ""),
                               bool(b.get("override")))
    if action == "task/update":
        return lab.update_task(folder, _task_id(tid), b.get("instruction"), b.get("remove"), b.get("title"),
                               b.get("override"))
    if action == "task/delete":
        return lab.delete_task(folder, _task_id(tid))
    if action == "start":
        return lab.start_task(load_settings(), folder, _task_id(tid))
    if action == "stop":
        return lab.stop_task(folder, _task_id(tid))
    if action == "regenerate":
        return lab.regenerate(load_settings(), folder, str(b.get("name") or ""))
    if action == "resolve":
        return lab.resolve(load_settings(), folder, str(b.get("name") or ""), str(b.get("action") or ""), ext, bool(b.get("force")))
    raise NotFound("Unknown lab action.")


def _task_id(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("Which task?")
    return v


def _enhance_settings(url=None, template=None) -> MonkapSettings:
    """The settings, with the address and the template as typed in the settings window when they are not saved yet."""
    s = load_settings()
    if url is not None:
        s = replace(s, comfy_url=str(url).strip())
    if template is not None:
        s = replace(s, enhance_template=str(template).strip())
    return s


# ------------------------------------------------------------------------------------------------ the caption model

def models(url=None) -> dict:
    try:
        return {"models": lmstudio.list_models(load_settings().lm_url if url is None else url)}
    except (OSError, ValueError) as exc:
        return {"models": [], "error": str(exc)}


HINT_NOTE = ("The user adds this note about this picture. Where it differs from what you think you see, trust the note: ")
MAX_HINT = 600


def recaption(folder, name, ext, hint="") -> dict:
    """Write this picture's caption again with the chosen model and task (the old text is backed up first). `hint` is a
    short note from the user about this picture, passed to the model with the task."""
    folder, path = dataset.locate(folder, name)
    s = load_settings()
    if not s.lm_model:
        raise ValueError("Pick a model in the settings (the gear) first.")
    instruction = tasks.instruction(s.ai_task, s.ai_instructions)
    hint = " ".join(str(hint or "").split())[:MAX_HINT]
    if hint:
        instruction = f"{instruction}\n\n{HINT_NOTE}{hint}"
    with _ai_lock:
        text = lmstudio.caption_image(s.lm_url, s.lm_model, path, instruction, s.ai_max_tokens, s.ai_think)
    if not text:
        raise ValueError("The model wrote nothing.")
    trigger = folder_trigger(folder)
    if trigger:
        text = captions.ensure_trigger_prefix(text, trigger)
    before = captions.read_caption(path, ext)
    note_before = journal.entry(folder, _stem(name))
    res = captions.save_caption(path, text, before, ext)
    journal.mark_ai(folder, _stem(name), captions.read_caption(path, ext) or text, s.lm_model)
    return {"caption": text, "kind": res.kind, "before": before, "note_before": note_before,
            **_caption_state(folder, path, name, ext)}


def unload_model() -> dict:
    """Unload the caption model from LM Studio (its instances only; the other models loaded there are left alone). A model that
    is not loaded is not an error."""
    s = load_settings()
    if not s.lm_model:
        raise ValueError("Pick a model in the settings (the gear) first.")
    try:
        return {"unloaded": lmstudio.unload(s.lm_url, s.lm_model)}
    except OSError as exc:
        raise ValueError(str(exc)) from exc


# ------------------------------------------------------------------------------------------------ routes

def get(h, path, qs) -> None:
    one = lambda k, d="": (qs.get(k) or [d])[0]
    try:
        if path == "/api/state":
            return h.json(settings_payload())
        if path == "/api/list":
            ext = effective_ext()
            folder = os.path.abspath(one("folder"))
            return h.json({"folder": folder, "ext": ext, "trigger": folder_trigger(folder),
                           "items": list_items(folder, ext)})
        if path == "/api/info":
            return h.json(info(one("folder"), one("name"), effective_ext()))
        if path in ("/api/thumb", "/api/view", "/api/tile"):
            folder, pic = dataset.locate(one("folder"), one("name"))
            if path == "/api/thumb":
                data = dataset.thumb(pic)
                if data is None:
                    raise NotFound("No thumbnail for that picture.")
                return h._send(200, data, "image/jpeg")
            if path == "/api/view":
                data, ctype = dataset.fit_preview(pic, _int(qs, "edge", dataset._FIT_EDGE, 64, MAX_EDGE))
            else:
                box = (_int(qs, "x0"), _int(qs, "y0"), _int(qs, "x1"), _int(qs, "y1"))
                data, ctype = dataset.region(pic, box, _int(qs, "w", 1024, 1, MAX_EDGE))
            return h._send(200, data, ctype)
        if path == "/api/template":
            try:
                tpl, note = normalize_template(one("t"))
            except TemplateError as exc:
                return h.json({"ok": False, "error": str(exc)})
            return h.json({"ok": True, "template": tpl, "note": note, "examples": example_names(tpl)})
        if path == "/api/browse":
            return h.json(browse(one("path"), _exts(one("files"))))
        if path == "/api/live":
            return h.json(live(one("folder"), one("name")))
        if path == "/api/review":
            return h.json(review(os.path.abspath(one("folder"))))
        if path == "/api/fizgig":
            return h.json(fizgig.summary(qs["dir"][0] if "dir" in qs else load_settings().fizgig_dir))
        if path == "/api/ai/models":
            return h.json(models(qs["url"][0] if "url" in qs else None))
        if path == "/api/enhance/status":
            return h.json(enhance.status())
        if path in ("/api/enhance/preview", "/api/enhance/result"):
            got = enhance.preview() if path.endswith("preview") else enhance.result()
            if got is None:
                raise NotFound("Nothing to show yet.")
            return h._send(200, got[0], got[1])
        if path == "/api/enhance/check":
            return h.json(enhance.check(_enhance_settings(one("url") if "url" in qs else None,
                                                         one("template") if "template" in qs else None)))
        if path == "/api/enhance/template":
            return h._send(200, enhance.reference_bytes(), "application/json")
        if path == "/api/lab/state":
            return h.json(lab.state(one("folder")))
        if path == "/api/lab/facts":
            return h.json(lab.facts(one("folder")))
        if path in ("/api/lab/preview", "/api/lab/result"):
            got = lab.preview() if path.endswith("preview") else lab.result(one("folder"), one("name"))
            if got is None:
                raise NotFound("Nothing to show yet.")
            return h._send(200, got[0], got[1])
        if path == "/api/data":
            files = appdata.inventory()
            return h.json({"root": appdata.root(), "files": [[os.path.relpath(p, appdata.root()), n] for p, n in files]})
    except NotFound as exc:
        return h.json({"error": str(exc)}, 404)
    except ValueError as exc:
        return h.json({"error": str(exc)}, 400)
    except (OSError, Image.DecompressionBombError) as exc:
        return h.json({"error": f"MonKap could not read that ({type(exc).__name__}: {exc})."}, 500)
    return h.json({"error": "not found"}, 404)


def post(h, path, b) -> None:
    try:
        ext = effective_ext()
        if path == "/api/caption":
            return h.json(save_caption(b.get("folder"), b.get("name"), b.get("text"), b.get("loaded"), ext,
                                       bool(b.get("mechanical")), b["restore"] if "restore" in b else _UNSET))
        if path == "/api/kapture":
            return h.json(kapture(b.get("folder"), b.get("name"), b.get("box"), b.get("text"), b.get("trigger"), ext))
        if path == "/api/delete":
            return h.json(delete(b.get("folder"), b.get("name"), ext))
        if path == "/api/ai/unload":
            return h.json(unload_model())
        if path == "/api/maintenance":
            return h.json(maintenance.run(b.get("folder"), str(b.get("action") or ""), ext, bool(b.get("dry")), b.get("items")))
        if path == "/api/delete_many":
            return h.json(delete_many(b.get("folder"), b.get("names"), ext))
        if path == "/api/undelete":
            return h.json({"restored": dataset.restore(str(b.get("folder") or ""), b.get("moved") or [])})
        if path == "/api/settings":
            s, note = save_settings(b.get("settings") or {})
            return h.json({**settings_payload(s), "note": note})
        if path == "/api/folder":
            return h.json(open_folder(b.get("folder"), b.get("trigger")))
        if path == "/api/recaption":
            return h.json(recaption(b.get("folder"), b.get("name"), ext, b.get("hint")))
        if path == "/api/ai/instruction":
            s = load_settings()
            key, text = str(b.get("key") or ""), str(b.get("text") or "").strip()
            edited = dict(s.ai_instructions)
            builtin = next((t["builtin"] for t in tasks.listing({}) if t["key"] == key), None)
            if builtin is None:
                raise ValueError("Unknown task.")
            if not text or text == builtin.strip():
                edited.pop(key, None)
            else:
                edited[key] = text
            s, _ = save_settings({"ai_instructions": edited})
            return h.json(settings_payload(s))
        if path == "/api/enhance/start":
            return h.json(enhance.start(load_settings(), b.get("folder"), b.get("name"), b.get("hint")))
        if path == "/api/enhance/cancel":
            return h.json(enhance.cancel())
        if path == "/api/enhance/free":
            return h.json(enhance.free(_enhance_settings(b.get("url"), None)))
        if path == "/api/enhance/decide":
            return h.json(enhance.decide(load_settings(), str(b.get("action") or ""), b.get("folder"), b.get("name"),
                                         b.get("text"), b.get("trigger"), ext))
        if path == "/api/enhance/undo":
            return h.json(enhance.undo_replace(str(b.get("folder") or ""), str(b.get("name") or ""), str(b.get("backup") or ""),
                                               str(b.get("old") or ""), bool(b.get("was_enhanced"))))
        if path.startswith("/api/lab/"):
            return h.json(_lab_post(path[len("/api/lab/"):], b, ext))
        if path == "/api/data/forget":
            if lab.running():
                raise ValueError("Enhance lab tasks are running: stop them first.")
            return h.json({"removed": appdata.forget()})
    except NotFound as exc:
        return h.json({"error": str(exc)}, 404)
    except ValueError as exc:
        return h.json({"error": str(exc)}, 400)
    except RuntimeError as exc:
        return h.json({"error": str(exc)}, 502)
    except OSError as exc:
        return h.json({"error": f"MonKap could not do that ({type(exc).__name__}: {exc})."}, 500)
    return h.json({"error": "not found"}, 404)
