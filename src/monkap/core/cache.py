"""The trainer's latent cache, as far as MONKAP touches it (only when an image is deleted)."""
from __future__ import annotations

import os
import re


def purge_latent_cache(cache_dir: str, stem: str) -> int:
    """Delete the trainer's cached latents of ONE image: `<stem>_<WWWW>x<HHHH>_<arch>.safetensors`.

    The pattern is exact on purpose: after `<stem>_` must come the 4-digit size token, so the
    caches of a crop named `<stem>_crop_001` (or any other stem that merely starts with this
    one) are never touched. Text-encoder caches are left alone - the trainer checks those
    against the caption text. Returns the number of files removed; never raises.
    """
    if not cache_dir or not os.path.isdir(cache_dir):
        return 0
    rx = re.compile("^" + re.escape(stem) + r"_\d{4}x\d{4}_.+\.safetensors$", re.IGNORECASE)
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if rx.match(name):
            try:
                os.remove(os.path.join(cache_dir, name))
                removed += 1
            except OSError:
                pass
    return removed
