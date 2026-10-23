"""python -m monkap [folder] [--port N] [--no-browser]"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

from . import __version__, api, appdata, lab, server


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="monkap", description="A workbench for preparing image datasets.")
    p.add_argument("folder", nargs="?", help="a dataset folder to open (default: the one open last time)")
    p.add_argument("--port", type=int, default=8765, help="where to listen on 127.0.0.1 (the next free one if taken)")
    p.add_argument("--no-browser", action="store_true", help="only print the address")
    p.add_argument("--forget", action="store_true",
                   help="remove MonKap's own files (settings, remembered folders) and exit; datasets are not touched")
    p.add_argument("--version", action="version", version=f"monkap {__version__}")
    a = p.parse_args(argv)
    if a.forget:
        print(f"Removed {appdata.forget()} file(s) from {appdata.root()}")
        return 0
    if a.folder:
        api.open_folder(a.folder)
    srv, url = server.start(a.port)
    print(f"MonKap {__version__}\n  {url}\n  (Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    lab.shutdown()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
