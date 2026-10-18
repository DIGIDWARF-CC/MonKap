"""MonKap in its own window: the same local server and page, shown by the system's web view (WebView2 on Windows) instead
of a browser tab. Nothing is stored by the view (private mode), and the page gets a native folder picker.

Without pywebview, or when the view can't start, the page opens in the default browser instead.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

from . import __version__, api, server

TITLE = "MonKap"


class Bridge:
    """What the page may ask of the window (window.pywebview.api.*)."""

    def __init__(self):
        self._window = None          # underscore: pywebview walks every public attribute of the api object

    def pick_folder(self, start=""):
        import webview
        try:
            got = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=start or "")
        except Exception:
            return ""
        return got[0] if got else ""


def _browser(url, note="The window could not start; opening the page in the browser.") -> int:
    print(note, file=sys.stderr, flush=True)
    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="monkap-gui", description="MonKap in its own window.")
    p.add_argument("folder", nargs="?", help="a dataset folder to open (default: the one open last time)")
    p.add_argument("--browser", action="store_true", help="open the page in the default browser instead of MonKap's own window")
    p.add_argument("--version", action="version", version=f"monkap {__version__}")
    a = p.parse_args(argv)
    trace = os.environ.get("MONKAP_TRACE", "").strip()          # a file: where every thread was after 15 s (for a stuck window)
    if trace:
        import faulthandler
        faulthandler.dump_traceback_later(15, file=open(trace, "w"))
    if a.folder:
        api.open_folder(a.folder)
    srv, url = server.start(8765)
    if a.browser:
        return _browser(url, f"MonKap is at {url}")
    try:
        import webview
    except ImportError:
        return _browser(url)
    bridge = Bridge()
    try:
        bridge._window = webview.create_window(TITLE, url, width=1280, height=860, min_size=(420, 520),
                                              background_color="#1E2530", js_api=bridge, text_select=True)
        webview.start(private_mode=True)
    except Exception:
        return _browser(url)
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
