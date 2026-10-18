"""Small file-system helpers shared by the MONKAP modules (no Tk, no Pillow)."""
from __future__ import annotations

import os

# MONKAP's own sub-folder inside a dataset folder. The trainer's globs are not recursive, so
# nothing in here is ever read as an image or a caption.
MONKAP_DIR = ".monkap"

# The pictures the trainer reads (dataset/image_dataset.py IMAGE_EXTENSIONS), lower case.
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif"})


def hide(path: str) -> None:
    """Best effort: the Windows 'hidden' attribute."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(path, 0x02)
    except Exception:
        pass


def monkap_dir(folder: str, *sub: str) -> str:
    """`<folder>/.monkap[/sub...]`, created on demand (the .monkap folder itself is hidden)."""
    root = os.path.join(folder, MONKAP_DIR)
    fresh = not os.path.isdir(root)
    path = os.path.join(root, *sub)
    os.makedirs(path, exist_ok=True)
    if fresh:
        hide(root)
    return path


def publish_no_overwrite(tmp: str, final: str) -> None:
    """Move tmp -> final, refusing to replace anything that already exists there."""
    try:
        os.link(tmp, final)                 # atomic and fails if `final` exists
    except FileExistsError:
        raise
    except (OSError, NotImplementedError, AttributeError):
        if os.path.exists(final):
            raise FileExistsError(final)
        os.rename(tmp, final)               # Windows: refuses an existing target as well
        return
    os.unlink(tmp)


def write_text_atomic(path: str, text: str, *, overwrite: bool) -> None:
    """Write via `<path>.<pid>.part`; with overwrite=False an existing file is never replaced."""
    tmp = f"{path}.{os.getpid()}.part"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if overwrite:
            os.replace(tmp, path)
        else:
            publish_no_overwrite(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
