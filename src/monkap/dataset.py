"""The dataset as the page sees it: the pictures of a folder in natural order, a small thumbnail, the whole picture
as a preview, a region of it at full resolution (tiles), and removing a picture with Undo.

Pictures are sent as pixels MonKap decoded itself (no EXIF turning: a trainer's Image.open doesn't turn either, so the
frame on the page is what the trainer will see). Everything is made in memory: nothing is written to disk for a preview.
"""
from __future__ import annotations

import io
import os
import re
import threading
from collections import OrderedDict

from PIL import Image

from .core.fsutil import IMAGE_EXTS

MAX_EDGE = 4096                    # the biggest picture a preview or a tile request may ask for
_FIT_EDGE = 2400                   # the whole-picture preview's long edge
REMOVED_DIR = "removed"            # a removed picture waits here, with its caption, until it is restored


class NotFound(LookupError):
    """The folder or the picture is not there (a 404, not a mistake in the request)."""


# ------------------------------------------------------------------------------------------------ pictures

class _Decoded:
    """The last pictures decoded for the page, kept so zooming into one big picture does not decode it again for
    every tile. Bounded by pixels: a 48 MP picture is ~150 MB decoded."""
    MAX_PIXELS = 160_000_000

    def __init__(self):
        self._lock = threading.Lock()
        self._items: OrderedDict = OrderedDict()
        self._gates: dict = {}

    def get(self, path) -> Image.Image:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
        with self._lock:
            im = self._items.get(key)
            if im is not None:
                self._items.move_to_end(key)
                return im
            gate = self._gates.setdefault(key, threading.Lock())
        with gate:                                    # one decode per picture, however many tiles ask at once
            with self._lock:
                im = self._items.get(key)
            if im is None:
                with Image.open(path) as src:
                    src.load()
                    im = _for_display(src)
                with self._lock:
                    self._items[key] = im
                    while len(self._items) > 1 and sum(i.width * i.height for i in self._items.values()) > self.MAX_PIXELS:
                        self._items.popitem(last=False)
            with self._lock:
                self._gates.pop(key, None)
        return im


_decoded = _Decoded()
_encoded: OrderedDict = OrderedDict()                 # whole-picture previews as sent, a handful
_encoded_lock = threading.Lock()


def _for_display(im: Image.Image) -> Image.Image:
    """RGB, or RGBA when there is transparency: what a browser can show of whatever the file holds."""
    if im.mode in ("RGB", "RGBA"):
        return im
    if im.mode in ("P", "PA"):
        return im.convert("RGBA" if ("transparency" in im.info or im.mode == "PA") else "RGB")
    return im.convert("RGBA" if "A" in im.mode else "RGB")


def _encode(im: Image.Image) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if im.mode == "RGBA":
        im.save(buf, "PNG", compress_level=1)
        return buf.getvalue(), "image/png"
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def picture_size(path) -> list[int]:
    """[width, height] as stored in the file (the trainer's view: no EXIF turning)."""
    with Image.open(path) as im:
        return [int(im.width), int(im.height)]


def fit_preview(path, edge=_FIT_EDGE) -> tuple[bytes, str]:
    """The whole picture, its long edge at most `edge` (never enlarged)."""
    edge = min(max(int(edge), 64), MAX_EDGE)
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size, edge)
    with _encoded_lock:
        hit = _encoded.get(key)
    if hit is not None:
        return hit
    im = _decoded.get(path)
    if max(im.size) > edge:
        k = edge / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS, reducing_gap=2.0)
    out = _encode(im)
    with _encoded_lock:
        _encoded[key] = out
        while len(_encoded) > 6:
            _encoded.popitem(last=False)
    return out


def region(path, box, out_w) -> tuple[bytes, str]:
    """`box` (left, top, right, bottom in the picture's own pixels) at the resolution the screen needs: at most
    `out_w` wide, and at native pixels (no resampling) when the box is smaller than that."""
    im = _decoded.get(path)
    l, t, r, b = box
    l, t, r, b = max(0, l), max(0, t), min(im.width, r), min(im.height, b)
    if r - l < 1 or b - t < 1:
        raise ValueError("That area is empty.")
    out_w = min(max(int(out_w), 1), MAX_EDGE)
    if r - l <= out_w:
        piece = im.crop((l, t, r, b))
    else:
        k = out_w / (r - l)
        piece = im.resize((out_w, max(1, round((b - t) * k))), Image.LANCZOS, box=(l, t, r, b), reducing_gap=2.0)
    return _encode(piece)


# ------------------------------------------------------------------------------------------------ the dataset

_NATURAL = re.compile(r"(\d+)")


def _natural(name: str):
    return [int(p) if p.isdigit() else p for p in _NATURAL.split(name.casefold())]


def list_folder(folder, ext) -> list[list]:
    """[[name, 1 if it has a caption file else 0], ...] for every picture the trainer reads, in natural order
    (img_2 before img_10). Names only: sizes and captions load for the picture on screen."""
    ext = ext.lower()
    pics, have = [], set()
    try:
        with os.scandir(folder) as it:                # the file type comes with the listing: no stat per file
            for e in it:
                stem, x = os.path.splitext(e.name)
                x = x.lower()
                if x == ext:
                    have.add(stem)
                elif x in IMAGE_EXTS and not e.name.startswith(".") and e.is_file():
                    pics.append(e.name)
    except OSError as exc:
        raise NotFound("That folder isn't there.") from exc
    return [[n, int(os.path.splitext(n)[0] in have)] for n in sorted(pics, key=_natural)]



# ------------------------------------------------------------------------------------------------ thumbnails

_thumbs: OrderedDict = OrderedDict()           # (path, mtime) -> a small JPEG, the oldest go past 600
_thumbs_lock = threading.Lock()
_thumb_slots = threading.Semaphore(3)           # a few decodes at once: a long filmstrip must not eat the machine


def thumb(path, size=320):
    """A small JPEG of a picture for the filmstrip, kept in memory. None when the file can't be read."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    with _thumbs_lock:
        hit = _thumbs.get(key)
    if hit is not None:
        return hit
    try:
        with _thumb_slots:
            with Image.open(path) as im:
                im.draft("RGB", (size * 2, size * 2))      # a JPEG decodes at a reduced size: much faster
                im.thumbnail((size, size))
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=82)
        data = buf.getvalue()
    except Exception:
        return None
    with _thumbs_lock:
        _thumbs[key] = data
        while len(_thumbs) > 600:
            _thumbs.popitem(last=False)
    return data


# ------------------------------------------------------------------------------------------------ remove and undo

def remove(folder, name, ext) -> list:
    """Take a picture out of the dataset without erasing it: it and its caption move into <folder>/removed/. Returns
    [[name, name there]] for Undo. Nothing in removed/ is ever overwritten."""
    folder = os.path.abspath(folder)
    src = os.path.join(folder, os.path.basename(name))
    if not os.path.isfile(src) or os.path.dirname(src) != folder:
        raise ValueError("That file isn't in the folder.")
    stem = os.path.splitext(os.path.basename(name))[0]
    names = [os.path.basename(name)]
    for cext in {ext or ".txt", ".txt"}:
        if os.path.isfile(os.path.join(folder, stem + cext)):
            names.append(stem + cext)
    dest = os.path.join(folder, REMOVED_DIR)
    os.makedirs(dest, exist_ok=True)
    moved = []
    for n in names:
        target, k = os.path.join(dest, n), 2
        while os.path.exists(target):
            s, e = os.path.splitext(n)
            target, k = os.path.join(dest, f"{s} ({k}){e}"), k + 1
        os.replace(os.path.join(folder, n), target)
        moved.append([n, os.path.basename(target)])
    return moved


def restore(folder, moved) -> list:
    """Undo a removal: [[name, name in removed/]] back where they were (never over a file that is there now)."""
    folder = os.path.abspath(folder)
    back = []
    for orig, there in moved or ():
        src = os.path.join(folder, REMOVED_DIR, os.path.basename(there))
        dst = os.path.join(folder, os.path.basename(orig))
        if os.path.isfile(src) and not os.path.exists(dst):
            os.replace(src, dst)
            back.append(orig)
    return back


def locate(folder, name) -> tuple[str, str]:
    """(folder, path of the picture): the folder is a real directory and the name a plain picture file name."""
    folder = os.path.abspath(str(folder or ""))
    name = str(name or "")
    if not os.path.isdir(folder):
        raise NotFound("That folder isn't there.")
    if not name or name != os.path.basename(name) or name.startswith("."):
        raise ValueError("Not a file name.")
    if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
        raise ValueError("MonKap opens png, jpg, webp, bmp and avif pictures.")
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        raise NotFound("That picture isn't in the folder any more.")
    return folder, path
