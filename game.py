"""Players Launcher — embedded Bloxd.io game window.

Thin pywebview wrapper: `python game.py` (or the Play button in the hub) opens
Bloxd.io in a real Chromium/CEF webview so the player runs inside the desktop
app instead of a browser tab.

NOTE ON THREADING
-----------------
pywebview owns the GUI thread on Windows (EdgeChromium), and tkinter owns it
too, so the game window is started with `webview.start()` on the calling
thread. While the game window is open the hub does not repaint; closing the
game window returns you to the hub. For a side-by-side setup run
`python game.py` as a second process.
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appdata  # noqa: E402

WINDOW_TITLE = "Players — Bloxd.io"


def webview_module():
    """Import pywebview lazily so `import game` never explodes on a headless box."""
    try:
        import webview  # noqa: WPS433
        return webview
    except Exception:
        return None


def game_url() -> str:
    url = str(appdata.config().get("bloxdUrl") or appdata.LINKS["bloxd"]).strip()
    return url or appdata.LINKS["bloxd"]


def available() -> bool:
    return webview_module() is not None


def launch(url: str = "", *, width: int = 1280, height: int = 800, on_closed=None,
           debug: bool = False) -> bool:
    """Open the player window. Blocks until the window is closed."""
    webview = webview_module()
    if webview is None:
        fallback(url or game_url())
        if on_closed:
            on_closed()
        return False

    target = url or game_url()
    try:
        window = webview.create_window(WINDOW_TITLE, target, width=width, height=height)
    except Exception as err:  # no GUI backend available
        print(f"[game] could not create the game window ({err}); opening the browser instead.")
        webbrowser.open(target)
        if on_closed:
            on_closed()
        return False

    try:
        webview.start(debug=debug)
    except Exception as err:
        print(f"[game] webview failed to start ({err}); opening the browser instead.")
        webbrowser.open(target)
    finally:
        if on_closed:
            on_closed()
    return True


def launch_in_thread(**kwargs) -> bool:
    """Best-effort side-by-side mode. Windows may refuse a non-main GUI thread."""
    if not available():
        return launch(**kwargs)
    thread = threading.Thread(target=launch, kwargs=kwargs, daemon=True)
    thread.start()
    return True


def fallback(url: str = "") -> bool:
    return webbrowser.open(url or game_url())


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"[game] opening {url or game_url()}")
    launch(url or game_url())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
