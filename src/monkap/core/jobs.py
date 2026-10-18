"""MonKap's file jobs (Kapture and delete) as plain functions: no web server, no GPU, runnable
from a request thread and covered by ordinary unit tests. The routes only call them."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from . import captions, ledger
from .cache import purge_latent_cache
from .crops import crop_to_file
from .naming import plan_output


@dataclass
class KaptureResult:
    path: str
    size: tuple[int, int]
    entry: dict                                  # the provenance record
    caption: str | None = None                   # the new crop's caption text (None = no caption file)
    notes: list[str] = field(default_factory=list)


@dataclass
class DeleteResult:
    purged: int = 0                              # cached latent files removed
    notes: list[str] = field(default_factory=list)


def kapture(src: str, box: tuple[int, int, int, int], parent_text: str, trigger: str, template: str,
            mode: str, provenance: bool, ext: str = ".txt") -> KaptureResult:
    """Crop `box` of `src` into a new, uniquely named PNG beside it, with its caption per `mode`.

    Raises if the image itself could not be written; trouble with the extras (caption, ledger,
    provenance) is reported in `notes` because the crop is already safely on disk by then.
    """
    folder = os.path.dirname(src)
    dest, _n = plan_output(folder, template, os.path.splitext(os.path.basename(src))[0])
    size = crop_to_file(src, box, dest)
    notes: list[str] = []
    try:
        ledger.record_name(folder, os.path.splitext(os.path.basename(dest))[0])
    except OSError as exc:
        notes.append(f"name ledger not updated ({exc})")
    child_text = captions.child_caption(mode, parent_text, trigger)
    if child_text is not None:
        try:
            captions.write_new_caption(dest, child_text, ext)
        except OSError as exc:
            child_text = None
            notes.append(f"caption not written ({exc})")
    elif mode != captions.MODE_EMPTY:
        notes.append("parent has no caption to copy")
    entry = {"file": os.path.basename(dest), "source": os.path.basename(src), "box": list(box),
             "size": list(size), "caption_mode": mode if child_text is not None else captions.MODE_EMPTY}
    if child_text is not None:
        entry["caption_sha1"] = captions.text_digest(child_text)
    if provenance:
        try:
            ledger.record_provenance(folder, entry)
        except OSError as exc:
            notes.append(f"provenance not recorded ({exc})")
    return KaptureResult(dest, size, entry, child_text, notes)


def delete_permanently(path: str, cache_dir: str, ext: str = ".txt") -> DeleteResult:
    """Remove the image first (if that fails nothing else is touched), then its caption, then the
    trainer's cached latents for that exact file name. Not recoverable - the soft delete (move
    to the dataset's removed/ folder) is the default; this is the user's explicit choice."""
    last: OSError | None = None
    for _ in range(6):                           # a thumbnailer / antivirus may hold the file briefly
        try:
            os.remove(path)
            last = None
            break
        except FileNotFoundError:
            last = None
            break
        except OSError as exc:
            last = exc
            time.sleep(0.15)
    if last is not None:
        raise last
    result = DeleteResult()
    for cext in dict.fromkeys((ext, ".txt")):    # the configured caption file, and a stray .txt
        try:
            os.remove(captions.caption_path(path, cext))
        except FileNotFoundError:
            pass
        except OSError as exc:
            result.notes.append(f"caption not removed ({exc})")
    if cache_dir:
        result.purged = purge_latent_cache(cache_dir, os.path.splitext(os.path.basename(path))[0])
    return result
