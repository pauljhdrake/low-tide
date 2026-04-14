from __future__ import annotations

import json
import logging
import os
import random
import threading

log = logging.getLogger(__name__)

# Shuffle mode constants
SHUFFLE_OFF = 0
SHUFFLE_RANDOM = 1
SHUFFLE_FAVOURITE = 2
SHUFFLE_DISCOVERY = 3

SHUFFLE_LABELS = ["off", "random", "favourites", "discovery"]


def _track_key(artist: str, title: str) -> str:
    return f"{artist.lower()}\x00{title.lower()}"


def _key_for(track) -> str:
    artist = getattr(getattr(track, "artist", None), "name", "") or ""
    title = getattr(track, "name", "") or ""
    return _track_key(artist, title)


class PlayCountStore:
    """
    Persists per-track play counts to ~/.config/low-tide/playcounts.json.

    Sources:
      - Local: incremented each time a track is scrobbled in this session
      - Last.fm: synced from get_top_tracks(PERIOD_OVERALL) at startup
      - Spotify: one-time import from a Spotify GDPR data export directory
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                self._counts = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("play count store load failed: %s", e)

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with self._lock:
                data = dict(self._counts)
            with open(self._path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.warning("play count store save failed: %s", e)

    # ── Read / write ──────────────────────────────────────────────────────

    def get(self, track) -> int:
        return self._counts.get(_key_for(track), 0)

    def get_by_names(self, artist: str, title: str) -> int:
        return self._counts.get(_track_key(artist, title), 0)

    def top_artists(self, limit: int = 10) -> list[tuple[str, int]]:
        """Returns [(artist_name, total_plays), ...] sorted by total plays descending."""
        from collections import defaultdict
        totals: dict[str, int] = defaultdict(int)
        with self._lock:
            for key, count in self._counts.items():
                if "\x00" in key:
                    artist = key.split("\x00", 1)[0]
                    totals[artist] += count
        return sorted(totals.items(), key=lambda x: -x[1])[:limit]

    def increment(self, track) -> None:
        key = _key_for(track)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1

    def merge(self, data: dict[str, int]) -> None:
        """Merge a {key: count} dict, keeping the higher count for each key."""
        with self._lock:
            for key, count in data.items():
                if count > self._counts.get(key, 0):
                    self._counts[key] = count

    # ── Weighted shuffle ──────────────────────────────────────────────────

    def weighted_shuffle(self, tracks: list, favourite: bool) -> list:
        """
        Return `tracks` in a weighted random order.
        favourite=True:  higher play count → more likely to appear early
        favourite=False: lower play count → more likely to appear early (discovery)
        """
        counts = [self.get(t) for t in tracks]

        if favourite:
            weights = [c + 1 for c in counts]
        else:
            max_c = max(counts) if counts else 0
            weights = [max_c - c + 1 for c in counts]

        items = list(tracks)
        weights = list(weights)
        result = []
        while items:
            total = sum(weights)
            r = random.uniform(0, total)
            cumulative = 0.0
            for i, w in enumerate(weights):
                cumulative += w
                if r <= cumulative:
                    result.append(items.pop(i))
                    weights.pop(i)
                    break
        return result

    # ── Last.fm sync ──────────────────────────────────────────────────────

    def sync_lastfm(self, network, username: str) -> int:
        """
        Fetch the user's all-time top tracks from Last.fm and merge the play
        counts into the store. Returns the number of tracks synced.
        """
        try:
            import pylast
            user = pylast.User(username, network)
            top_tracks = user.get_top_tracks(period=pylast.PERIOD_OVERALL, limit=None)
            data: dict[str, int] = {}
            for item in top_tracks:
                artist_obj = getattr(item.item, "artist", None)
                artist = getattr(artist_obj, "name", "") or ""
                title = getattr(item.item, "title", "") or ""
                count = int(item.weight) if item.weight else 0
                if artist and title and count:
                    data[_track_key(artist, title)] = count
            self.merge(data)
            self.save()
            log.info("Last.fm sync: %d tracks", len(data))
            return len(data)
        except Exception as e:
            log.warning("Last.fm sync failed: %s", e)
            return 0

    # ── Spotify import ────────────────────────────────────────────────────

    def import_spotify(self, path: str) -> int:
        """
        Import play counts from a Spotify GDPR data export.
        `path` should be the directory containing StreamingHistory*.json or
        Streaming_History_Audio*.json files, or a path to a single such file.

        Only counts streams where ms_played >= 30,000 (30 seconds), matching
        the scrobbling threshold used elsewhere in the app.

        Returns the number of distinct tracks imported.
        """
        import glob as _glob

        if os.path.isfile(path):
            files = [path]
        else:
            patterns = [
                os.path.join(path, "StreamingHistory*.json"),
                os.path.join(path, "Streaming_History_Audio*.json"),
            ]
            files = []
            for p in patterns:
                files.extend(_glob.glob(p))

        if not files:
            log.warning("Spotify import: no streaming history files found in %s", path)
            return 0

        counts: dict[str, int] = {}
        for fpath in files:
            try:
                with open(fpath) as f:
                    entries = json.load(f)
                for entry in entries:
                    ms = entry.get("ms_played", 0)
                    if ms < 30_000:
                        continue
                    # Extended history format
                    artist = entry.get("master_metadata_album_artist_name") or ""
                    title = entry.get("master_metadata_track_name") or ""
                    # Legacy format
                    if not artist:
                        artist = entry.get("artistName") or ""
                    if not title:
                        title = entry.get("trackName") or ""
                    if artist and title:
                        key = _track_key(artist, title)
                        counts[key] = counts.get(key, 0) + 1
            except Exception as e:
                log.warning("Spotify import: failed to read %s: %s", fpath, e)

        self.merge(counts)
        self.save()
        log.info("Spotify import: %d distinct tracks from %d file(s)", len(counts), len(files))
        return len(counts)
