from __future__ import annotations

import os
from dataclasses import dataclass

_COVER_NAMES = (
    "cover.jpg", "cover.png", "folder.jpg", "folder.png",
    "artwork.jpg", "artwork.png", "front.jpg", "front.png",
)


@dataclass
class LocalArtist:
    name: str
    id: str = ""


@dataclass
class LocalAlbum:
    name: str
    artist: LocalArtist
    year: int | None = None
    _dir: str = ""
    id: str = ""

    def image(self, size: int = 320) -> str:
        if not self._dir:
            return ""
        for name in _COVER_NAMES:
            path = os.path.join(self._dir, name)
            if os.path.exists(path):
                return f"file://{path}"
        return ""


@dataclass
class LocalTrack:
    path: str
    name: str
    artist: LocalArtist
    album: LocalAlbum
    duration: int = 0
    track_num: int = 1
    disc_num: int = 1
    year: int | None = None
    genre: str = ""
    bpm: float | None = None
    explicit: bool = False
    audio_quality: str = "LOCAL"
    isrc: str = ""

    @property
    def id(self) -> str:
        return self.path

    def get_url(self) -> str:
        return self.path
