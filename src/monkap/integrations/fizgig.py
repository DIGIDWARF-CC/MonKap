"""A read-only look at a Fizgig install: the folder that holds `fizgig.py` and its `user_data/` folder.

MonKap reads a few of Fizgig's own files and never writes to them:

  user_data/last_used.json   the training folder, the folders opened lately, each folder's trigger word
  user_data/web_state.json   the Caption Extension (Training > Other Options)
  user_data/prefs.json       where Fizgig keeps its latent cache and its LoRAs' output
  docs/RELEASE_NOTES_v*.md   which version this is

This depends on how Fizgig stores its folders and settings. It was written against Fizgig 8.5, and every reader
returns "nothing" instead of failing when a file or a key is not where it was, so a change on Fizgig's side leaves
MonKap with its own settings rather than with an error. Watch for MonKap updates after Fizgig's.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

TESTED = "8.5.9"                           # the Fizgig this was checked against


def _json(dir_: str, *parts) -> dict:
    try:
        with open(os.path.join(dir_, *parts), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def looks_like_fizgig(dir_: str) -> bool:
    d = str(dir_ or "").strip()
    return bool(d) and os.path.isfile(os.path.join(d, "fizgig.py")) and os.path.isdir(os.path.join(d, "user_data"))


def version(dir_: str) -> str:
    """The newest release notes' version ('' when there are none): Fizgig keeps no version number in a file."""
    found = []
    try:
        for n in os.listdir(os.path.join(dir_, "docs")):
            m = re.fullmatch(r"RELEASE_NOTES_v(\d+)\.(\d+)\.(\d+)\.md", n)
            if m:
                found.append(tuple(int(x) for x in m.groups()))
    except OSError:
        return ""
    return ".".join(map(str, max(found))) if found else ""


def _resolve(dir_: str, value) -> str:
    v = str(value or "").strip()
    return v if not v or os.path.isabs(v) else os.path.normpath(os.path.join(dir_, v))


def _key(folder: str) -> str:
    return os.path.normcase(os.path.abspath(folder)) if str(folder or "").strip() else ""


def trigger_word(dir_: str, folder: str) -> str:
    """The folder's trigger word as Fizgig has it ('' when none)."""
    words = _json(dir_, "user_data", "last_used.json").get("folder_triggers")
    return str((words if isinstance(words, dict) else {}).get(_key(folder), "") or "").rstrip(",").strip()


def caption_ext(dir_: str) -> str:
    """Fizgig's Caption Extension for the model it has open ('' when it can't be told)."""
    st = _json(dir_, "user_data", "web_state.json")
    vals = st.get("values") if isinstance(st.get("values"), dict) else {}
    mine = vals.get(st.get("family")) if isinstance(vals.get(st.get("family")), dict) else {}
    ext = str(mine.get("DATASET_CAPTION_EXT") or "").strip()
    ext = ext if not ext or ext.startswith(".") else "." + ext
    return ext if re.fullmatch(r"\.[A-Za-z0-9_]{1,12}", ext) else ""


def cache_root(dir_: str) -> str:
    return _resolve(dir_, _json(dir_, "user_data", "prefs.json").get("cache_dir") or "user_data/cache")


def latent_cache_for(dir_: str, folder: str) -> str:
    """The folder Fizgig keeps this dataset's latents in: `<cache>/<folder name>-<8 hex of the sha1 of its path>`
    (families/launch.py cache_dir_for)."""
    norm = str(folder).lower().replace("\\", "/").rstrip("/")
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    nm = "".join(c if (c.isalnum() or c in "-_") else "_" for c in os.path.basename(str(folder).rstrip("/\\"))) or "dataset"
    return os.path.join(cache_root(dir_), f"{nm}-{h}")


def output_dir(dir_: str) -> str:
    return _resolve(dir_, _json(dir_, "user_data", "prefs.json").get("lora_output_dir") or "output_loras")


def summary(dir_: str) -> dict:
    """What the Fizgig tab shows."""
    d = str(dir_ or "").strip()
    if not d:
        return {"ok": False, "error": ""}
    if not looks_like_fizgig(d):
        return {"ok": False, "error": "That folder isn't a Fizgig install (no fizgig.py and user_data inside)."}
    last = _json(d, "user_data", "last_used.json")
    words = last.get("folder_triggers") if isinstance(last.get("folder_triggers"), dict) else {}
    folder = str(last.get("image_folder") or "")
    recent = [str(x) for x in (last.get("recent_folders") or []) if isinstance(x, str)][:8]
    ver = version(d)
    note = ""
    if ver and tuple(map(int, ver.split("."))) > tuple(map(int, TESTED.split("."))):
        note = f"This Fizgig is newer than the one MonKap was checked with ({TESTED}). Look for a MonKap update."
    return {"ok": True, "version": ver, "tested": TESTED, "note": note, "training_folder": folder, "recent": recent,
            "triggers": len(words), "trigger_here": trigger_word(d, folder) if folder else "",
            "caption_ext": caption_ext(d), "cache_root": cache_root(d), "output_dir": output_dir(d)}
