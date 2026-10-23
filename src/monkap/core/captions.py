"""MonKap caption files: reading, saving, the clear-on-purpose rule, Kapture caption modes.

The trainers' contract (what a LoRA trainer's dataset loader and caption writer do):

* an image with NO caption file is left out of training, loudly - and "caption only the missing ones"
  picks it up;
* an image with an EMPTY caption file trains with an empty caption, and "only the missing ones"
  skips it as already done.

So an empty box is never silently "saved as empty". Clearing a caption deliberately removes the
file (after backing the old text up), which puts the image back in the "needs a caption" pile.

The caption file's extension is the Caption Extension setting (`.txt` unless changed), so every
function here takes it as `ext`.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass

from .fsutil import monkap_dir, write_text_atomic

MODE_EMPTY = "empty"        # new image gets no caption file - it is left out until captioned
MODE_COPY = "copy"          # new image gets a copy of the parent caption
MODE_TRIGGER = "trigger"    # copy + make sure the Trigger Word leads the caption
CAPTION_MODES = (MODE_EMPTY, MODE_COPY, MODE_TRIGGER)

BACKUP_DIR = "backups"      # under .monkap: texts that were cleared or replaced behind your back


def caption_path(img_path: str, ext: str = ".txt") -> str:
    return os.path.splitext(img_path)[0] + ext


def read_caption(img_path: str, ext: str = ".txt") -> str | None:
    """The caption text, or None when there is no caption file. utf-8-sig tolerates a BOM."""
    try:
        with open(caption_path(img_path, ext), "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def text_digest(text: str) -> str:
    """Fingerprint of a caption (whitespace at the ends ignored)."""
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()


def ensure_trigger_prefix(text: str, trigger: str) -> str:
    """`trigger, text` the way the caption worker writes it - unless text already leads with it."""
    trigger = (trigger or "").strip()
    text = (text or "").strip()
    if not trigger:
        return text
    if not text:
        return trigger
    low, trig = text.lower(), trigger.lower()
    if low == trig or (low.startswith(trig) and low[len(trig):len(trig) + 1] in (",", " ")):
        return text
    return f"{trigger}, {text}"


def child_caption(mode: str, parent_text: str | None, trigger: str) -> str | None:
    """The text for a fresh crop's caption file per the chosen mode; None means "write no file"."""
    parent_text = (parent_text or "").strip()
    if mode == MODE_COPY:
        return parent_text or None
    if mode == MODE_TRIGGER:
        return ensure_trigger_prefix(parent_text, trigger) or None
    return None


def write_new_caption(img_path: str, text: str, ext: str = ".txt") -> None:
    """Create the caption file of a brand-new image. Refuses to replace an existing one."""
    write_text_atomic(caption_path(img_path, ext), text, overwrite=False)


@dataclass
class SaveResult:
    kind: str            # "unchanged" | "written" | "cleared" | "none"
    backup: str = ""     # where the previous text on disk went (cleared, or changed behind our back)
    conflict: bool = False   # the file on disk differed from what the editor had loaded


def _same(a: str | None, b: str | None) -> bool:
    """A missing file and an empty box are the same state; whitespace at the ends is ignored."""
    return (a or "").strip() == (b or "").strip()


def _backup_text(img_path: str, old_text: str) -> str:
    d = monkap_dir(os.path.dirname(img_path), BACKUP_DIR)
    stem = os.path.splitext(os.path.basename(img_path))[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = os.path.join(d, f"{stem}.{stamp}.txt"), 1
    while os.path.exists(dest):
        dest, n = os.path.join(d, f"{stem}.{stamp}-{n}.txt"), n + 1
    write_text_atomic(dest, old_text, overwrite=False)
    return dest


backup_text = _backup_text          # for other modules: keep a caption's text before it is cleared


def save_caption(img_path: str, text: str, loaded: str | None, ext: str = ".txt") -> SaveResult:
    """Persist the editor's text for `img_path`.

    `loaded` is what the editor read from disk (None = there was no caption file). If the file
    changed on disk since then (a captioning job, another tool), the text that is about to be
    replaced is backed up first and `conflict` is set - nothing is lost silently, and nothing
    blocks. Raises OSError when the write fails - the editor keeps the text.
    """
    text = (text or "").strip()
    if _same(text, loaded):
        return SaveResult("unchanged")
    disk = read_caption(img_path, ext)
    conflict = not _same(disk, loaded)
    path = caption_path(img_path, ext)
    if text:
        backup = _backup_text(img_path, disk) if (conflict and disk) else ""
        write_text_atomic(path, text, overwrite=True)
        return SaveResult("written", backup, conflict)
    if disk is not None:                                     # the box was emptied on purpose
        backup = _backup_text(img_path, disk) if disk else ""
        os.unlink(path)
        return SaveResult("cleared", backup, conflict)
    return SaveResult("none")
