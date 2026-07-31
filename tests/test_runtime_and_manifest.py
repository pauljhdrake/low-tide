from __future__ import annotations

import base64
import os
import stat
import tempfile
from pathlib import Path

import pytest

from lowtide.runtime import RuntimeWorkspace
from lowtide.tidal_client import TidalClient


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_runtime_uses_xdg_and_is_unique(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="lt-xdg-", dir="/tmp") as root:
        runtime_root = Path(root)
        os.chmod(runtime_root, 0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_root))
        first = RuntimeWorkspace.create()
        second = RuntimeWorkspace.create()
        try:
            assert first.directory.startswith(str(runtime_root / "low-tide"))
            assert first.socket_path.startswith(str(runtime_root))
            assert first.socket_path != "/tmp/lowtide-mpv.sock"
            assert first.socket_path != second.socket_path
            assert len(os.fsencode(first.socket_path)) < 104
            assert _mode(first.directory) == 0o700
            assert _mode(first.manifest_dir) == 0o700
        finally:
            first.cleanup()
            second.cleanup()


def test_runtime_fallback_is_private(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    workspace = RuntimeWorkspace.create()
    try:
        assert _mode(workspace.directory) == 0o700
        assert os.path.basename(workspace.directory).startswith(
            f"low-tide-{os.getuid()}-"
        )
        assert len(os.fsencode(workspace.socket_path)) < 104
    finally:
        workspace.cleanup()


def _manifest_client(workspace):
    client = object.__new__(TidalClient)
    client.runtime = workspace
    client._manifest_dir = workspace.manifest_dir
    return client


def test_manifest_is_private_bounded_and_cleaned(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_root))
    workspace = RuntimeWorkspace.create()
    client = _manifest_client(workspace)
    xml = b"""<?xml version="1.0"?>
    <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
      <BaseURL>https://example.invalid/audio/</BaseURL>
    </MPD>"""
    path = client._write_manifest(base64.b64encode(xml).decode("ascii"))
    assert os.path.dirname(path) == workspace.manifest_dir
    assert _mode(path) == 0o600
    client._clear_manifests()
    assert not os.path.exists(path)
    workspace.cleanup()


@pytest.mark.parametrize(
    "payload",
    [
        "not base64!",
        base64.b64encode(b"<not-mpd/>").decode("ascii"),
        base64.b64encode(b"<MPD><BaseURL>http://unsafe.invalid/</BaseURL></MPD>").decode(
            "ascii"
        ),
    ],
)
def test_manifest_rejects_invalid_or_insecure_content(monkeypatch, tmp_path, payload):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_root))
    workspace = RuntimeWorkspace.create()
    try:
        with pytest.raises(ValueError):
            _manifest_client(workspace)._write_manifest(payload)
        assert not list(os.scandir(workspace.manifest_dir))
    finally:
        workspace.cleanup()
