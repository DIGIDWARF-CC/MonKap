"""MONKAP's records inside `<dataset>/.monkap/`: the names ledger and the provenance JSON.

Neither is ever a caption, and the trainer does not read the folder.
"""
from __future__ import annotations

import json
import os
import time

from .fsutil import MONKAP_DIR, monkap_dir

PROVENANCE_FILE = "provenance.jsonl"   # optional (a setting): where each crop came from
NAMES_FILE = "names.log"               # always on: every crop name ever issued, one per line


def provenance_path(folder: str) -> str:
    return os.path.join(folder, MONKAP_DIR, PROVENANCE_FILE)


def names_path(folder: str) -> str:
    return os.path.join(folder, MONKAP_DIR, NAMES_FILE)


def _append(folder: str, path: str, line: str) -> None:
    monkap_dir(folder)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def record_name(folder: str, stem: str) -> None:
    """Remember a crop's stem for good (independent of the optional provenance JSON)."""
    _append(folder, names_path(folder), stem)


def record_provenance(folder: str, entry: dict) -> None:
    """Append one JSON line. Raises OSError on failure - the crop itself is already on disk."""
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    _append(folder, provenance_path(folder), json.dumps(entry, ensure_ascii=False))


def read_provenance(folder: str) -> list[dict]:
    out: list[dict] = []
    try:
        with open(provenance_path(folder), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def history_stems(folder: str) -> list[str]:
    """Stems handed out before: the names ledger plus whatever an older provenance file lists."""
    out: list[str] = []
    try:
        with open(names_path(folder), encoding="utf-8") as f:
            out.extend(line.strip() for line in f if line.strip())
    except OSError:
        pass
    for row in read_provenance(folder):
        name = row.get("file")
        if isinstance(name, str) and name:
            out.append(os.path.splitext(name)[0])
    return out
