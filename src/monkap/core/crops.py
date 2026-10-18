"""MONKAP crop engine: a pixel-exact, non-destructive crop of the ORIGINAL file.

No Tk and no GPU here. The invariants:

* the crop is read from the ORIGINAL file and written at native pixels - no resampling;
* the source image is never touched;
* a new file never overwrites anything: the final publish step refuses an existing path;
* nothing is reported as saved until the file is on disk and re-read at the expected size.
"""
from __future__ import annotations

import os

from PIL import Image

from .fsutil import publish_no_overwrite


def _png_ready(im: Image.Image) -> Image.Image:
    """The mode a PNG can hold without losing what the trainer will see."""
    mode = im.mode
    if mode in ("1", "L", "LA", "RGB", "RGBA", "I;16"):
        return im
    if mode in ("P", "PA"):
        return im.convert("RGBA" if ("transparency" in im.info or mode == "PA") else "RGB")
    if mode in ("I", "F"):
        return im.convert("RGB")
    return im.convert("RGBA" if "A" in mode else "RGB")


def crop_to_file(src_path: str, box: tuple[int, int, int, int], dest_path: str) -> tuple[int, int]:
    """Cut `box` out of the original image into `dest_path` at native pixels.

    Written next to the destination as `<name>.part` first, re-read to confirm the size, then
    published without overwriting. Raises on any problem and leaves no half-written file.
    """
    l, t, r, b = box
    want = (r - l, b - t)
    if want[0] < 1 or want[1] < 1:
        raise ValueError("The selection is empty.")
    tmp = f"{dest_path}.{os.getpid()}.part"
    try:
        with Image.open(src_path) as im:
            im.load()
            iw, ih = im.size
            if l < 0 or t < 0 or r > iw or b > ih:
                raise ValueError("The selection is outside the image.")
            icc = im.info.get("icc_profile")
            piece = _png_ready(im.crop(box))
        piece.load()
        kwargs = {"icc_profile": icc} if icc else {}
        piece.save(tmp, format="PNG", compress_level=3, **kwargs)
        if os.path.getsize(tmp) <= 0:
            raise OSError("The written file is empty.")
        with Image.open(tmp) as chk:
            if chk.size != want:
                raise OSError(f"Verification failed: wrote {chk.size}, expected {want}.")
        publish_no_overwrite(tmp, dest_path)
        return want
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
