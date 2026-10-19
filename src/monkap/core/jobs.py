"""MonKap's file jobs (Kapture and delete) as plain functions: no web server, no GPU, runnable
from a request thread and covered by ordinary unit tests. The routes only call them."""
from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from . import captions, ledger
from .cache import purge_latent_cache
from .crops import _png_ready, crop_to_file
from .fsutil import MONKAP_DIR, monkap_dir, publish_no_overwrite
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
    return _register_child(src, dest, size, {"box": list(box)}, parent_text, trigger, mode, provenance, ext)


def _register_child(src: str, dest: str, size, extra: dict, parent_text: str, trigger: str, mode: str, provenance: bool,
                    ext: str) -> KaptureResult:
    """What a new picture made from `src` gets once it is safely on disk: its name in the ledger, its caption per `mode`,
    and its provenance record (`extra` says how it was made)."""
    folder = os.path.dirname(src)
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
    entry = {"file": os.path.basename(dest), "source": os.path.basename(src), **extra,
             "size": list(size), "caption_mode": mode if child_text is not None else captions.MODE_EMPTY}
    if child_text is not None:
        entry["caption_sha1"] = captions.text_digest(child_text)
    if provenance:
        try:
            ledger.record_provenance(folder, entry)
        except OSError as exc:
            notes.append(f"provenance not recorded ({exc})")
    return KaptureResult(dest, size, entry, child_text, notes)


# ------------------------------------------------------------------------------------------------ an enhanced picture

def _png_file(data: bytes, dest: str) -> tuple[int, int]:
    """The enhanced picture as a verified PNG at `dest` (written beside it first, then published without overwriting)."""
    tmp = f"{dest}.{os.getpid()}.part"
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            piece = _png_ready(im)
            icc = im.info.get("icc_profile")
        piece.load()
        piece.save(tmp, format="PNG", compress_level=3, **({"icc_profile": icc} if icc else {}))
        with Image.open(tmp) as chk:
            size = chk.size
        publish_no_overwrite(tmp, dest)
        return size
    except (UnidentifiedImageError, OSError) as exc:
        if isinstance(exc, FileExistsError):
            raise
        raise ValueError(f"The enhanced picture can't be read ({exc}).") from exc
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def store_enhanced(src: str, data: bytes, template: str, mode: str, provenance: bool, ext: str, parent_text: str,
                   trigger: str) -> KaptureResult:
    """"Store as Kapture": the enhanced picture becomes a new, uniquely named PNG beside `src`, named and captioned like
    a crop. The original stays as it is."""
    folder = os.path.dirname(src)
    dest, _n = plan_output(folder, template, os.path.splitext(os.path.basename(src))[0])
    size = _png_file(data, dest)
    return _register_child(src, dest, size, {"enhanced": True}, parent_text, trigger, mode, provenance, ext)


REPLACED_DIR = "replaced"


def replace_picture(src: str, data: bytes) -> dict:
    """"Replace": the enhanced picture takes the original's place, as `<same name>.png` (the caption file stays: it goes by
    the name). The original is kept in `.monkap/replaced/` so that `revert_replace` can bring it back. If the original is
    not a PNG and a PNG of that name already exists, nothing is changed."""
    folder = os.path.dirname(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    dest = os.path.join(folder, stem + ".png")
    if os.path.exists(dest) and os.path.normcase(dest) != os.path.normcase(src):
        raise ValueError(f"A picture named {stem}.png is already in the folder; use Store as Kapture instead.")
    keep_dir = monkap_dir(folder, REPLACED_DIR)
    keep, k = os.path.join(keep_dir, os.path.basename(src)), 2
    while os.path.exists(keep):
        keep, k = os.path.join(keep_dir, f"{stem} ({k}){os.path.splitext(src)[1]}"), k + 1
    staged = f"{dest}.{os.getpid()}.new"
    size = _png_file(data, staged)                 # proves the new picture is good before the original is touched
    try:
        os.replace(src, keep)
        try:
            os.replace(staged, dest)
        except OSError:
            os.replace(keep, src)                  # could not place the new one: the original goes back
            raise
    finally:
        try:
            os.remove(staged)
        except OSError:
            pass
    return {"name": os.path.basename(dest), "backup": os.path.basename(keep), "old": os.path.basename(src), "size": list(size)}


def revert_replace(folder: str, name: str, backup: str, old: str) -> str:
    """Undo `replace_picture`: the original comes back under its old name and the enhanced picture goes away.
    Returns the restored name."""
    folder = os.path.abspath(folder)
    keep = os.path.join(folder, MONKAP_DIR, REPLACED_DIR, os.path.basename(backup))
    original = os.path.join(folder, os.path.basename(old))
    current = os.path.join(folder, os.path.basename(name))
    if not os.path.isfile(keep):
        raise ValueError("The original is not in the backup any more.")
    if os.path.exists(original) and os.path.normcase(original) != os.path.normcase(current):
        raise ValueError("A picture with the original's name is there now.")
    if os.path.exists(current):
        os.remove(current)
    os.replace(keep, original)
    return os.path.basename(original)


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
