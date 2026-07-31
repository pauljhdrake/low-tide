from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable

import mutagen

from lowtide.local_track import LocalAlbum, LocalArtist, LocalTrack
from lowtide.secure_io import atomic_write_json, read_json
from lowtide.tidal_client import CONF_DIR

CACHE_PATH = os.path.join(CONF_DIR, "local_index.json")
CACHE_VERSION = 1
AUDIO_EXTENSIONS = {".flac", ".mp3", ".opus", ".ogg", ".m4a", ".aac", ".wav", ".aiff"}


def _read_tags(path: str) -> dict | None:
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return None

        def first(key: str) -> str:
            v = f.get(key, [])
            return str(v[0]) if v else ""

        title = first("title") or os.path.splitext(os.path.basename(path))[0]
        artist = first("artist") or "Unknown Artist"
        album_artist = first("albumartist") or artist
        album = first("album") or "Unknown Album"
        duration = int(f.info.length) if hasattr(f, "info") else 0

        track_num = 1
        if raw := first("tracknumber"):
            try:
                track_num = int(raw.split("/")[0])
            except ValueError:
                pass

        disc_num = 1
        if raw := first("discnumber"):
            try:
                disc_num = int(raw.split("/")[0])
            except ValueError:
                pass

        year = None
        if raw := first("date"):
            try:
                year = int(raw[:4])
            except ValueError:
                pass

        bpm = None
        if raw := first("bpm"):
            try:
                bpm = float(raw)
            except ValueError:
                pass

        return {
            "title": title,
            "artist": artist,
            "album_artist": album_artist,
            "album": album,
            "track_num": track_num,
            "disc_num": disc_num,
            "year": year,
            "genre": first("genre"),
            "bpm": bpm,
            "duration": duration,
        }
    except Exception:
        return None


class LocalLibrary:
    def __init__(self, music_dirs: list[str]):
        self._dirs: list[str] = []
        for raw in music_dirs:
            try:
                resolved = Path(os.path.expanduser(raw)).resolve(strict=True)
            except OSError:
                continue
            if resolved.is_dir():
                self._dirs.append(str(resolved))
        self._tracks: list[LocalTrack] = []

    def load(self, progress_cb: Callable[[str], None] | None = None) -> None:
        """Load library from cache, only re-reading files whose mtime has changed."""
        cache = self._read_cache()
        cache_by_path = {e["path"]: e for e in cache.get("tracks", [])}

        found_paths: set[str] = set()
        updated: list[dict] = []

        for music_dir in self._dirs:
            if not os.path.isdir(music_dir):
                continue
            for root, _, files in os.walk(music_dir):
                for fname in sorted(files):
                    if os.path.splitext(fname)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    path = os.path.join(root, fname)
                    try:
                        file_st = os.lstat(path)
                        resolved = os.path.realpath(path)
                        if (
                            not stat.S_ISREG(file_st.st_mode)
                            or stat.S_ISLNK(file_st.st_mode)
                            or not Path(resolved).is_relative_to(Path(music_dir))
                        ):
                            continue
                        path = resolved
                    except OSError:
                        continue
                    found_paths.add(path)
                    mtime = os.path.getmtime(path)
                    cached = cache_by_path.get(path)
                    if cached and cached.get("mtime") == mtime:
                        updated.append(cached)
                    else:
                        if progress_cb:
                            progress_cb(fname)
                        tags = _read_tags(path)
                        if tags:
                            updated.append({"path": path, "mtime": mtime, **tags})

        # Drop entries for files that no longer exist
        updated = [e for e in updated if e["path"] in found_paths]
        self._write_cache(updated)
        self._tracks = [self._entry_to_track(e) for e in updated]

    def _entry_to_track(self, e: dict) -> LocalTrack:
        artist = LocalArtist(name=e.get("artist", "Unknown Artist"))
        album_artist = LocalArtist(name=e.get("album_artist", artist.name))
        album = LocalAlbum(
            name=e.get("album", "Unknown Album"),
            artist=album_artist,
            year=e.get("year"),
            _dir=os.path.dirname(e["path"]),
        )
        return LocalTrack(
            path=e["path"],
            name=e.get("title", os.path.basename(e["path"])),
            artist=artist,
            album=album,
            duration=e.get("duration", 0),
            track_num=e.get("track_num", 1),
            disc_num=e.get("disc_num", 1),
            year=e.get("year"),
            genre=e.get("genre", ""),
            bpm=e.get("bpm"),
        )

    def _read_cache(self) -> dict:
        try:
            data = read_json(CACHE_PATH, default={})
            if not isinstance(data, dict):
                raise ValueError("local library cache is not an object")
            if data.get("version") != CACHE_VERSION:
                return {}
            return data
        except (OSError, RuntimeError, TypeError, ValueError):
            return {}

    def _write_cache(self, tracks: list[dict]) -> None:
        try:
            atomic_write_json(
                CACHE_PATH,
                {"version": CACHE_VERSION, "tracks": tracks},
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            import logging
            logging.getLogger(__name__).warning(
                "local library cache save failed (%s)",
                type(error).__name__,
            )

    @property
    def tracks(self) -> list[LocalTrack]:
        return self._tracks

    def artists(self) -> list[str]:
        seen: set[str] = set()
        result = []
        for t in self._tracks:
            name = t.album.artist.name
            if name not in seen:
                seen.add(name)
                result.append(name)
        return sorted(result, key=str.casefold)

    def albums_for_artist(self, artist_name: str) -> list[LocalAlbum]:
        seen: set[str] = set()
        result = []
        for t in self._tracks:
            if t.album.artist.name == artist_name and t.album.name not in seen:
                seen.add(t.album.name)
                result.append(t.album)
        return sorted(result, key=lambda a: a.year or 0)

    def tracks_for_album(self, artist_name: str, album_name: str) -> list[LocalTrack]:
        tracks = [
            t for t in self._tracks
            if t.album.artist.name == artist_name and t.album.name == album_name
        ]
        return sorted(tracks, key=lambda t: (t.disc_num, t.track_num))
