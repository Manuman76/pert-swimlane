"""Desktop launcher: local Flask server + pywebview window."""

from __future__ import annotations

import base64
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview

from app import create_app

HOST = "127.0.0.1"
STARTUP_TIMEOUT_S = 15.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _wait_until_ready(url: str, timeout_s: float = STARTUP_TIMEOUT_S) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.05)
    raise RuntimeError(f"Flask server did not become ready at {url}")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(".")
    return cleaned or "download"


class DesktopApi:
    """JS bridge for actions the embedded WebView2 cannot do alone."""

    def save_bytes(self, filename: str, data_base64: str) -> dict:
        """Open a Save As dialog and write base64-decoded file contents."""
        if not webview.windows:
            return {"ok": False, "error": "no window"}
        result = webview.windows[0].create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=_safe_filename(filename),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0]
        Path(path).write_bytes(base64.b64decode(data_base64))
        return {"ok": True, "path": path}


def main() -> None:
    # WebView2 cancels Content-Disposition downloads unless this is enabled
    # (CSV export relies on it).
    webview.settings["ALLOW_DOWNLOADS"] = True

    app = create_app()
    port = _free_port()
    url = f"http://{HOST}:{port}/"

    thread = threading.Thread(
        target=lambda: app.run(
            host=HOST,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        ),
        daemon=True,
        name="flask-server",
    )
    thread.start()
    _wait_until_ready(url)

    webview.create_window(
        "PERT Swimlane",
        url,
        width=1280,
        height=800,
        js_api=DesktopApi(),
    )
    webview.start()


if __name__ == "__main__":
    main()
