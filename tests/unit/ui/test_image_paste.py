"""Clipboard image reader backends."""

import subprocess

from oi.ui import image_paste


def _completed(stdout: bytes, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


def _no_linux_tools(monkeypatch, present: str | None = None) -> None:
    monkeypatch.setattr(
        image_paste.shutil,
        "which",
        lambda name: "/usr/local/bin/" + name if name == present else None,
    )


def test_pngpaste_is_used_when_installed(monkeypatch):
    _no_linux_tools(monkeypatch, present="pngpaste")
    monkeypatch.setattr(
        image_paste, "_run_capture", lambda argv, timeout=2.0: _completed(b"PNGDATA")
    )
    assert image_paste.read_clipboard_image() == (b"PNGDATA", "image/png")


def test_osascript_hex_output_is_decoded(monkeypatch):
    _no_linux_tools(monkeypatch)
    monkeypatch.setattr(image_paste.sys, "platform", "darwin")
    payload = b"\x89PNG\r\n\x1a\n"
    stdout = f"«data PNGf{payload.hex().upper()}»\n".encode()
    monkeypatch.setattr(
        image_paste, "_run_capture", lambda argv, timeout=2.0: _completed(stdout)
    )
    assert image_paste.read_clipboard_image() == (payload, "image/png")


def test_osascript_error_yields_none(monkeypatch):
    _no_linux_tools(monkeypatch)
    monkeypatch.setattr(image_paste.sys, "platform", "darwin")
    monkeypatch.setattr(
        image_paste, "_run_capture", lambda argv, timeout=2.0: _completed(b"", 1)
    )
    assert image_paste.read_clipboard_image() is None
