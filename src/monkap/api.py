"""MonKap's routes: /api/*. Every call names a dataset `folder` and a picture `name` inside it; nothing here takes a
path to a picture anywhere else, and a name that is not a plain picture file name is refused.

The handler `h` has json(data, code=200) and _send(code, bytes, content_type); the server provides them (tests give a
fake one). Pictures are sent with no-store: nothing from a dataset lands in the browser's cache.
"""
from __future__ import annotations

import os
import string
import threading

from PIL import Image

from . import __version__, appdata, dataset
from .ai import lmstudio, tasks
from .core import captions, jobs, ledger
from .integrations import fizgig
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


def browse(path: str) -> dict:
    """The sub-folders of `path` for the folder picker ('' = where to start: the drives, or the home folder)."""
    if not path:
        if os.name == "nt":
            drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
            return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives], "pictures": 0}
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise NotFound("That folder isn't there.")
    dirs, pics = [], 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                if e.is_dir():
                    dirs.append({"name": e.name, "path": os.path.join(path, e.name)})
                elif os.path.splitext(e.name)[1].lower() in IMAGE_EXTS:
                    pics += 1
    except OSError as exc:
        raise ValueError(f"Can't read that folder ({exc.strerror or exc}).") from exc
    parent = os.path.dirname(path)
    return {"path": path, "parent": "" if parent == path else parent,
            "dirs": sorted(dirs, key=lambda d: d["name"].casefold()), "pictures": pics}


# ------------------------------------------------------------------------------------------------ pictures

def _int(qs, key, default=0, lo=-(1 << 31), hi=1 << 31) -> int:
    try:
        return min(max(int(float((qs.get(key) or [default])[0])), lo), hi)
    except (TypeError, ValueError):
        return default


def info(folder, name, ext) -> dict:
    folder, path = dataset.locate(folder, name)
    text = captions.read_caption(path, ext)
    row = next((r for r in reversed(ledger.read_provenance(folder)) if str(r.get("file", "")).lower() == name.lower()), None)
    inherited = bool(text and row and row.get("caption_sha1") == captions.text_digest(text))
    return {"name": name, "size": dataset.picture_size(path), "caption": text, "inherited": inherited,
            "mtime": int(os.path.getmtime(path))}


def save_caption(folder, name, text, loaded, ext) -> dict:
    _, path = dataset.locate(folder, name)
    res = captions.save_caption(path, str(text or ""), None if loaded is None else str(loaded), ext)
    backup = os.path.relpath(res.backup, folder) if res.backup else ""
    return {"kind": res.kind, "backup": backup, "conflict": res.conflict,
            "has_caption": res.kind in ("written", "unchanged") and bool(str(text or "").strip())}


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
    return {"mode": "hard", "purged": res.purged, "notes": res.notes}


# ------------------------------------------------------------------------------------------------ the caption model

def models(url=None) -> dict:
    try:
        return {"models": lmstudio.list_models(load_settings().lm_url if url is None else url)}
    except (OSError, ValueError) as exc:
        return {"models": [], "error": str(exc)}


def recaption(folder, name, ext) -> dict:
    """Write this picture's caption again with the chosen model and task (the old text is backed up first)."""
    folder, path = dataset.locate(folder, name)
    s = load_settings()
    if not s.lm_model:
        raise ValueError("Pick a model in the settings (the gear) first.")
    with _ai_lock:
        text = lmstudio.caption_image(s.lm_url, s.lm_model, path, tasks.instruction(s.ai_task, s.ai_instructions),
                                      s.ai_max_tokens, s.ai_think)
    if not text:
        raise ValueError("The model wrote nothing.")
    trigger = folder_trigger(folder)
    if trigger:
        text = captions.ensure_trigger_prefix(text, trigger)
    before = captions.read_caption(path, ext)
    res = captions.save_caption(path, text, before, ext)
    return {"caption": text, "kind": res.kind}


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
                           "items": dataset.list_folder(folder, ext)})
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
            return h.json(browse(one("path")))
        if path == "/api/fizgig":
            return h.json(fizgig.summary(qs["dir"][0] if "dir" in qs else load_settings().fizgig_dir))
        if path == "/api/ai/models":
            return h.json(models(qs["url"][0] if "url" in qs else None))
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
            return h.json(save_caption(b.get("folder"), b.get("name"), b.get("text"), b.get("loaded"), ext))
        if path == "/api/kapture":
            return h.json(kapture(b.get("folder"), b.get("name"), b.get("box"), b.get("text"), b.get("trigger"), ext))
        if path == "/api/delete":
            return h.json(delete(b.get("folder"), b.get("name"), ext))
        if path == "/api/undelete":
            return h.json({"restored": dataset.restore(str(b.get("folder") or ""), b.get("moved") or [])})
        if path == "/api/settings":
            s, note = save_settings(b.get("settings") or {})
            return h.json({**settings_payload(s), "note": note})
        if path == "/api/folder":
            return h.json(open_folder(b.get("folder"), b.get("trigger")))
        if path == "/api/recaption":
            return h.json(recaption(b.get("folder"), b.get("name"), ext))
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
        if path == "/api/data/forget":
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
