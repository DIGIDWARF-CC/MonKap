"""Builds the Windows program: dist/MonKap/MonKap.exe (PyInstaller, a folder, no console window), then, when Inno Setup
is installed, the installer dist/MonKap-<version>-setup.exe.

    .venv\\Scripts\\python packaging\\build.py            # the program and the installer
    .venv\\Scripts\\python packaging\\build.py --no-installer

The program is a folder on purpose, not one self-extracting exe: that would unpack itself into %TEMP% on every start.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "packaging")
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, os.path.join(ROOT, "src"))
from monkap import __version__  # noqa: E402

ISCC = [os.environ.get("ISCC", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]


def run(cmd, **kw):
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-installer", action="store_true")
    a = p.parse_args()
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
    if a.no_installer:
        return 0
    iscc = next((c for c in ISCC if c and os.path.isfile(c)), None)
    if not iscc:
        print("Inno Setup 6 isn't installed (https://jrsoftware.org/isinfo.php): no installer made.")
        return 0
    run([iscc, f"/DAppVersion={__version__}", os.path.join(PKG, "installer.iss")])
    print("Installer:", os.path.join(DIST, f"MonKap-{__version__}-setup.exe"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
