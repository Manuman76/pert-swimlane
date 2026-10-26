"""Unit tests for desktop save helpers (no GUI)."""

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

from desktop import DesktopApi, _safe_filename


def test_safe_filename_strips_illegal_chars():
    assert _safe_filename('foo:bar/baz?.csv') == "foo_bar_baz_.csv"
    assert _safe_filename("   ") == "download"


def test_save_bytes_writes_file(tmp_path):
    target = tmp_path / "out.png"
    payload = b"\x89PNG\r\n"
    api = DesktopApi()
    fake_window = MagicMock()
    fake_window.create_file_dialog.return_value = (str(target),)

    with patch("desktop.webview.windows", [fake_window]):
        result = api.save_bytes("out.png", base64.b64encode(payload).decode("ascii"))

    assert result == {"ok": True, "path": str(target)}
    assert Path(target).read_bytes() == payload


def test_save_bytes_cancelled():
    api = DesktopApi()
    fake_window = MagicMock()
    fake_window.create_file_dialog.return_value = None

    with patch("desktop.webview.windows", [fake_window]):
        result = api.save_bytes("out.png", base64.b64encode(b"x").decode("ascii"))

    assert result == {"ok": False, "cancelled": True}
