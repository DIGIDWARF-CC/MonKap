"""Builds the Windows program: dist/MonKap/MonKap.exe (PyInstaller, a folder, no console window), then, when Inno Setup
is installed, the installer dist/MonKap-<version>-setup.exe.

    .venv\\Scripts\\python packaging\\build.py            # the program and the installer
    .venv\\Scripts\\python packaging\\build.py --bump     # the same, with the revision (the third number) one higher first
    .venv\\Scripts\\python packaging\\build.py --no-installer

The version lives in one place, src/monkap/__init__.py; pyproject.toml reads it from there. With --bump a build that fails
puts the old number back, so only a build that worked uses up a revision.

The program is a folder on purpose, not one self-extracting exe: that would unpack itself into %TEMP% on every start.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "packaging")
DIST = os.path.join(ROOT, "dist")
INIT = os.path.join(ROOT, "src", "monkap", "__init__.py")
VERSION = re.compile(r'^(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")', re.M)

ISCC = [os.environ.get("ISCC", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]


def read_version(path: str | None = None) -> str:
    path = path or INIT                                          # looked up now, so that a test can point INIT elsewhere
    with open(path, encoding="utf-8") as f:
        m = VERSION.search(f.read())
    if not m:
        raise SystemExit(f"No __version__ = \"x.y.z\" in {path}")
    return ".".join(m.group(i) for i in (2, 3, 4))


def write_version(version: str, path: str | None = None) -> None:
    path = path or INIT
    with open(path, encoding="utf-8", newline="") as f:         # newline="": the file's own line endings come back as they were
        text = f.read()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not VERSION.search(text):
        raise SystemExit(f"Can't set version {version!r} in {path}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(VERSION.sub(lambda m: m.group(1) + version + m.group(5), text, count=1))


def bump_version(path: str | None = None) -> tuple[str, str]:
    """The revision (the third number) one higher: (old, new)."""
    path = path or INIT
    old = read_version(path)
    major, minor, rev = old.split(".")
    new = f"{major}.{minor}.{int(rev) + 1}"
    write_version(new, path)
    return old, new


def run(cmd, **kw):
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def build(version: str, no_installer: bool) -> int:
    run([sys.executable, os.path.join(PKG, "make_icon.py")])
    for d in (os.path.join(ROOT, "build"), os.path.join(DIST, "MonKap")):
        shutil.rmtree(d, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "MonKap",
         "--icon", os.path.join(PKG, "monkap.ico"), "--distpath", DIST, "--workpath", os.path.join(ROOT, "build"),
         "--specpath", os.path.join(ROOT, "build"),
         "--paths", os.path.join(ROOT, "src"),
         "--add-data", os.path.join(ROOT, "src", "monkap", "static") + os.pathsep + os.path.join("monkap", "static"),
         "--add-data", os.path.join(ROOT, "src", "monkap", "ai", "templates") + os.pathsep + os.path.join("monkap", "ai", "templates"),
         "--collect-all", "webview", "--hidden-import", "monkap.gui",
         os.path.join(PKG, "monkap_gui.py")])
    exe = os.path.join(DIST, "MonKap", "MonKap.exe")
    if not os.path.isfile(exe):
        print("The build produced no MonKap.exe", file=sys.stderr)
        return 1
    print("Built", exe)
    if no_installer:
        return 0
    iscc = next((c for c in ISCC if c and os.path.isfile(c)), None)
    if not iscc:
        print("Inno Setup 6 isn't installed (https://jrsoftware.org/isinfo.php): no installer made.")
        return 0
    run([iscc, f"/DAppVersion={version}", os.path.join(PKG, "installer.iss")])
    print("Installer:", os.path.join(DIST, f"MonKap-{version}-setup.exe"))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-installer", action="store_true")
    p.add_argument("--bump", action="store_true", help="the revision (third number) one higher before building")
    a = p.parse_args()
    old = read_version()
    if a.bump:
        old, new = bump_version()
        print(f"Version {old} -> {new}", flush=True)
    version = read_version()
    try:
        code = build(version, a.no_installer)
    except BaseException:
        code = 1
        raise
    finally:
        if a.bump and code != 0:
            write_version(old)
            print(f"The build failed: the version is back to {old}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
