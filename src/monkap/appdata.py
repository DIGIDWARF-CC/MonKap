"""Where MonKap keeps its own files, and nowhere else: settings.json (settings, the folders you opened, the trigger word
of each) in one application folder. Pictures, previews and tiles are made in memory and never written out; what MonKap
writes into a dataset is a `.monkap` folder (the crop ledger and caption backups) and the pictures and captions you
ask for.

The installed program keeps them in `user_data` next to MonKap.exe, so deleting the program's folder removes everything.
Run from source, or when that folder can't be written, the platform's usual place for application data is used. The
folder in MONKAP_HOME wins over both.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

from .core.fsutil import write_text_atomic


def _writable(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-test")
        with open(probe, "w") as f:
            f.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def root() -> str:
    env = os.environ.get("MONKAP_HOME", "").strip()
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):                  # the installed program: next to MonKap.exe
        beside = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "user_data")
        if _writable(beside):
            return beside
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "MonKap")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "MonKap")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"), "monkap")


def user_file(name: str) -> str:
    return os.path.join(root(), name)


def read_json(name: str, default):
    try:
        with open(user_file(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name: str, data) -> None:
    os.makedirs(root(), exist_ok=True)
    write_text_atomic(user_file(name), json.dumps(data, indent=2, ensure_ascii=False), overwrite=True)


def inventory() -> list[tuple[str, int]]:
    """[(path, bytes)] of everything MonKap keeps in its own folder."""
    out = []
    for dirpath, _dirs, files in os.walk(root()):
        for n in files:
            p = os.path.join(dirpath, n)
            try:
                out.append((p, os.path.getsize(p)))
            except OSError:
                pass
    return out


def forget() -> int:
    """Remove MonKap's own folder (settings, remembered folders). Datasets are not touched. Returns the files removed."""
    n = len(inventory())
    shutil.rmtree(root(), ignore_errors=True)
    return n
