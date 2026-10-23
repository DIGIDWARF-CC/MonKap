"""Housekeeping of the open dataset folder (the settings' "Current dataset maintenance" tab). Every action can be asked with
`dry`: what it would do, counted, for the confirmation the page always shows before the real thing.

    clear_captions    every caption file goes (each text is kept in .monkap/backups, and returned so that the page can undo it)
    restore_captions  puts texts returned by clear_captions back (where no caption has appeared since)
    empty_captions    caption files with nothing in them go
    empty_removed     the folder's removed/ (pictures taken out with the soft delete) is erased for good
    clear_notes       MonKap's own notes of the folder (crop names, where crops came from, who wrote the captions); the
                      caption backups and the replaced originals stay
"""
from __future__ import annotations

import os
import shutil

from . import dataset
from .core import captions, journal, ledger
from .core.fsutil import MONKAP_DIR


def _folder(folder) -> str:
    folder = os.path.abspath(str(folder or ""))
    if not os.path.isdir(folder):
        raise dataset.NotFound("That folder isn't there.")
    return folder


def _captioned(folder: str, ext: str) -> list[str]:
    return [r[0] for r in dataset.list_folder(folder, ext) if r[1]]


def _tree_size(path: str) -> tuple[int, int]:
    files = size = 0
    for d, _s, fs in os.walk(path):
        for f in fs:
            files += 1
            try:
                size += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return files, size


def run(folder, action: str, ext: str, dry: bool = False, items=None) -> dict:
    folder = _folder(folder)
    if action == "clear_captions":
        return _clear_captions(folder, ext, dry)
    if action == "restore_captions":
        return _restore_captions(folder, ext, items)
    if action == "empty_captions":
        return _empty_captions(folder, ext, dry)
    if action == "empty_removed":
        return _empty_removed(folder, dry)
    if action == "clear_notes":
        return _clear_notes(folder, dry)
    raise ValueError("Unknown action.")


def _clear_captions(folder: str, ext: str, dry: bool) -> dict:
    names = _captioned(folder, ext)
    if dry:
        return {"count": len(names)}
    cleared, failed = [], []
    for name in names:
        path = os.path.join(folder, name)
        stem = os.path.splitext(name)[0]
        try:
            text = captions.read_caption(path, ext) or ""
            if text:
                captions.backup_text(path, text)
            note = journal.entry(folder, stem)
            os.unlink(captions.caption_path(path, ext))
            cleared.append([name, text, note])
        except OSError as exc:
            failed.append({"name": name, "error": str(exc)})
    journal.drop_ai(folder, [os.path.splitext(n)[0] for n, _t, _e in cleared])
    return {"count": len(cleared), "items": cleared, "failed": failed}


def _restore_captions(folder: str, ext: str, items) -> dict:
    back = 0
    for row in items if isinstance(items, list) else []:
        try:
            name, text, note = str(row[0]), str(row[1] or ""), row[2] if len(row) > 2 else None
            path = os.path.join(folder, os.path.basename(name))
            if not text or not os.path.isfile(path) or captions.read_caption(path, ext) is not None:
                continue                                     # no text, no picture, or a caption has appeared meanwhile
            captions.write_new_caption(path, text, ext)
            if isinstance(note, dict):
                journal.set_entry(folder, os.path.splitext(name)[0], note)
            back += 1
        except (OSError, IndexError, TypeError):
            continue
    return {"count": back}


def _empty_captions(folder: str, ext: str, dry: bool) -> dict:
    empty = []
    for name in _captioned(folder, ext):
        if not (captions.read_caption(os.path.join(folder, name), ext) or "").strip():
            empty.append(name)
    if dry:
        return {"count": len(empty)}
    done = 0
    for name in empty:
        try:
            os.unlink(captions.caption_path(os.path.join(folder, name), ext))
            done += 1
        except OSError:
            continue
    return {"count": done}


def _empty_removed(folder: str, dry: bool) -> dict:
    path = os.path.join(folder, dataset.REMOVED_DIR)
    if not os.path.isdir(path):
        return {"count": 0, "bytes": 0}
    files, size = _tree_size(path)
    if not dry:
        shutil.rmtree(path)
    return {"count": files, "bytes": size}


def _clear_notes(folder: str, dry: bool) -> dict:
    files = [ledger.NAMES_FILE, ledger.PROVENANCE_FILE, journal.FILE]
    present = [f for f in files if os.path.isfile(os.path.join(folder, MONKAP_DIR, f))]
    if not dry:
        for f in present:
            try:
                os.unlink(os.path.join(folder, MONKAP_DIR, f))
            except OSError:
                pass
    return {"count": len(present)}
