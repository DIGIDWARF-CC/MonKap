"""MONKAP output names: the template, safe stems, and the next free number.

The number comes from disk plus the names ledger, never from a counter in memory: it survives a
restart, and a deleted crop never gets its number back. That matters beyond tidiness - the
trainer's latent cache is keyed by file stem and size, so a NEW picture under a deleted
picture's name would silently reuse the old latent.
"""
from __future__ import annotations

import os
import re

from .ledger import history_stems

DEFAULT_TEMPLATE = "{parent}_crop_{n:03d}"
OUTPUT_EXT = ".png"

# Stem budget. Windows paths top out near 260 characters in total; the folder takes some of
# them, and a ".txt" / ".png" / ".part" suffix the rest.
MAX_STEM_LEN = 120

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
_TOKEN = re.compile(r"\{(parent|n|index)(?::0?(\d+)d)?\}")
_ANY_BRACE = re.compile(r"[{}]")


class TemplateError(ValueError):
    """The filename template cannot be used (the message is shown to the user)."""


def _clean(text: str) -> str:
    """Make a string safe as part of a Windows file name (and a no-op for safe ones)."""
    return _BAD_CHARS.sub("_", text)


def _finish_stem(stem: str) -> str:
    """Final stem hygiene: no leading/trailing dots or spaces, no reserved device names."""
    stem = stem.strip(" .")
    if not stem:
        stem = "crop"
    if stem.lower() in _RESERVED:
        stem = "_" + stem
    return stem


def parse_template(template: str) -> list[tuple]:
    """Split a template into ("lit", text) / ("parent",) / ("n", width) parts.

    Placeholders: {parent}, {n}, {n:03d} (zero-padded to 3), {index} (alias of {n}). Any other
    brace is an error - a typo must not silently become part of every file name.
    """
    parts: list[tuple] = []
    pos = 0
    for m in _TOKEN.finditer(template):
        if m.start() > pos:
            parts.append(("lit", template[pos:m.start()]))
        if m.group(1) == "parent":
            parts.append(("parent",))
        else:
            parts.append(("n", int(m.group(2) or 0)))
        pos = m.end()
    if pos < len(template):
        parts.append(("lit", template[pos:]))
    for p in parts:
        if p[0] == "lit" and _ANY_BRACE.search(p[1]):
            raise TemplateError("Unknown placeholder - use {parent} and {n} (or {n:03d}).")
    return parts


def normalize_template(template: str) -> tuple[str, str]:
    """Validate a template; returns (usable template, note for the user - may be empty).

    A template without {n} would name every crop identically, so `_{n:03d}` is appended
    rather than the template being rejected.
    """
    template = (template or "").strip()
    if not template:
        return DEFAULT_TEMPLATE, "Empty template - using the default."
    parts = parse_template(template)
    note = ""
    if not any(p[0] == "n" for p in parts):
        template += "_{n:03d}"
        note = "No {n} in the template - '_{n:03d}' is appended so every file name is unique."
    return template, note


def _parent_part(parts: list[tuple], parent: str) -> str:
    """The parent stem as it appears in names, shortened so the whole stem fits the budget."""
    literal = sum(len(_clean(p[1])) for p in parts if p[0] == "lit")
    digits = sum(max(p[1], 6) for p in parts if p[0] == "n")
    budget = max(8, MAX_STEM_LEN - literal - digits)
    return _clean(parent)[:budget]


def render_stem(template: str, parent: str, n: int) -> str:
    parts = parse_template(template)
    pp = _parent_part(parts, parent)
    out = []
    for p in parts:
        if p[0] == "lit":
            out.append(_clean(p[1]))
        elif p[0] == "parent":
            out.append(pp)
        else:
            out.append(str(n).zfill(p[1]))
    return _finish_stem("".join(out))


def _index_regex(template: str, parent: str) -> re.Pattern:
    parts = parse_template(template)
    pp = _parent_part(parts, parent)
    rx = []
    for p in parts:
        if p[0] == "lit":
            rx.append(re.escape(_clean(p[1])))
        elif p[0] == "parent":
            rx.append(re.escape(pp))
        else:
            rx.append(r"(\d+)")
    return re.compile("^" + "".join(rx) + "$", re.IGNORECASE)


def folder_stems(folder: str) -> list[str]:
    """Stems of every file in the folder, any extension.

    The trainer keys caches on the stem with the extension stripped, so `a.png` and `a.jpg`
    would collide - a new name must be free across ALL extensions (and .txt captions).
    """
    try:
        return [os.path.splitext(f)[0] for f in os.listdir(folder)]
    except OSError:
        return []


def next_stem(template: str, parent: str, taken: list[str],
              history: list[str] | tuple = ()) -> tuple[str, int]:
    """The next free (stem, index): one past the highest index on disk (`taken`) or ever issued
    (`history`). Only `taken` can collide."""
    template, _ = normalize_template(template)
    rx = _index_regex(template, parent)
    top = 0
    for s in list(taken) + list(history):
        m = rx.match(s)
        if m:
            top = max(top, int(m.group(1)))
    taken_lower = {s.lower() for s in taken}
    n = top + 1
    while True:
        stem = render_stem(template, parent, n)
        if stem.lower() not in taken_lower:
            return stem, n
        n += 1


def plan_output(folder: str, template: str, parent_stem: str) -> tuple[str, int]:
    """(absolute .png path, index) for the next crop of `parent_stem` in `folder`."""
    stem, n = next_stem(template, parent_stem, folder_stems(folder), history_stems(folder))
    return os.path.join(folder, stem + OUTPUT_EXT), n


def example_names(template: str, parent: str = "image_001", count: int = 2) -> list[str]:
    """What the template would produce for an empty folder - the live preview in Settings."""
    template, _ = normalize_template(template)
    return [render_stem(template, parent, i) + OUTPUT_EXT for i in range(1, count + 1)]
