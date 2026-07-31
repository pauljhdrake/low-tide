from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

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
        album_dir = Path(self._dir).resolve()
        for name in _COVER_NAMES:
            path = os.path.join(album_dir, name)
            try:
                file_st = os.lstat(path)
                resolved = Path(path).resolve(strict=True)
            except OSError:
                continue
            if (
                stat.S_ISREG(file_st.st_mode)
                and not stat.S_ISLNK(file_st.st_mode)
                and resolved.is_relative_to(album_dir)
            ):
                return resolved.as_uri()
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
