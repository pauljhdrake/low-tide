from __future__ import annotations

import os
import subprocess
import types

import pytest

from lowtide import tidal_client


@pytest.fixture
def conf_dir(tmp_path, monkeypatch):
    """Point low-tide's config dir (~/.config/low-tide) at a temp dir."""
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setattr(tidal_client, "CONF_DIR", str(d))
    monkeypatch.setattr(tidal_client, "CONF_PATH", str(d / "session.json"))
    monkeypatch.setattr(tidal_client, "CONFIG_PATH", str(d / "config.json"))
    return d


def make_track(track_id: int, name: str = "", artist: str = "Artist", album: str = "Album"):
    """A minimal stand-in for a tidalapi Track."""
    return types.SimpleNamespace(
        id=track_id,
        name=name or f"Track {track_id}",
        duration=200,
        artist=types.SimpleNamespace(name=artist),
        album=types.SimpleNamespace(name=album, image=lambda size=320: None),
    )


@pytest.fixture
def flac_file(tmp_path):
    """Factory for small tagged FLAC files (needs ffmpeg; skips without it)."""
    def make(name: str, title: str, artist: str = "Test Artist", album: str = "Test Album"):
        path = tmp_path / "music" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                 "-i", "sine=frequency=440:duration=1",
                 "-metadata", f"title={title}", "-metadata", f"artist={artist}",
                 "-metadata", f"album={album}", str(path)],
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.skip("ffmpeg not available")
        return str(path)
    return make


@pytest.fixture(autouse=True)
def _no_real_runtime_dir(monkeypatch, tmp_path):
    # Keep Player socket paths inside the test's temp dir.
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    yield
    assert os.path.isdir(runtime)
