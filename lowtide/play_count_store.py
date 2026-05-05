from __future__ import annotations

import json
import random
from pathlib import Path

from lowtide.tidal_client import CONF_DIR

_DEFAULT_PATH = Path(CONF_DIR) / "playcounts.json"


def _track_key(track) -> str:
    artist = getattr(getattr(track, "artist", None), "name", "") or ""
    title = getattr(track, "name", "") or ""
    return f"{artist.lower().strip()}|{title.lower().strip()}"


class PlayCountStore:
    """
    Tracks how many times each track has been played.

    Two separate stores are merged when computing weights:
    - _local: indexed by TIDAL track ID (integer), incremented on each play
    - _ext:   indexed by "artist|title" (lowercased), populated from Last.fm or Spotify
    """

    def __init__(self, path: Path = _DEFAULT_PATH):
        self._path = path
        self._local: dict[int, int] = {}
        self._ext: dict[str, int] = {}
        self._load()

    # --- Persistence ---

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                data = json.load(f)
            self._local = {int(k): v for k, v in data.get("local", {}).items()}
            self._ext = data.get("ext", {})
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(
                    {"local": {str(k): v for k, v in self._local.items()}, "ext": self._ext},
                    f,
                )
        except Exception:
            pass

    # --- Public API ---

    def record_play(self, track) -> None:
        tid = getattr(track, "id", None)
        if tid is not None:
            self._local[tid] = self._local.get(tid, 0) + 1
            self._save()

    def get_count(self, track) -> int:
        tid = getattr(track, "id", None)
        local = self._local.get(tid, 0) if tid is not None else 0
        ext = self._ext.get(_track_key(track), 0)
        return local + ext

    def weighted_order(self, tracks: list, mode: int) -> list:
        """
        Return a new ordering of tracks via weighted sample-without-replacement.

        mode 2 (FAVOURITE): high play count → more likely to appear early
        mode 3 (DISCOVERY): low play count → more likely to appear early
        """
        if not tracks:
            return list(tracks)

        counts = [self.get_count(t) for t in tracks]

        if mode == 2:
            weights = [float(c + 1) for c in counts]
        else:
            weights = [1.0 / (c + 1) for c in counts]

        remaining = list(zip(weights, tracks))
        result: list = []
        while remaining:
            total = sum(w for w, _ in remaining)
            r = random.random() * total
            cumulative = 0.0
            chosen = len(remaining) - 1
            for i, (w, _) in enumerate(remaining):
                cumulative += w
                if cumulative >= r:
                    chosen = i
                    break
            result.append(remaining[chosen][1])
            remaining.pop(chosen)

        return result

    # --- External data sources ---

    def sync_lastfm(self, network, username: str, limit: int = 1000) -> int:
        """
        Pull top tracks (with play counts) from Last.fm into the ext store.
        Returns the number of entries imported.
        """
        try:
            user = network.get_user(username)
            top_tracks = user.get_top_tracks(limit=limit)
            for item in top_tracks:
                artist = item.item.artist.name.lower().strip()
                title = item.item.title.lower().strip()
                self._ext[f"{artist}|{title}"] = int(item.weight)
            self._save()
            return len(top_tracks)
        except Exception:
            return 0

    def import_spotify(self, path: str) -> int:
        """
        Import play counts from a Spotify GDPR StreamingHistory*.json export.
        Each unique artist+title combination is counted once per stream entry.
        Returns the number of distinct tracks imported.
        """
        try:
            with open(path) as f:
                entries = json.load(f)
            counts: dict[str, int] = {}
            for entry in entries:
                artist = (
                    entry.get("artistName")
                    or entry.get("master_metadata_album_artist_name")
                    or ""
                ).lower().strip()
                title = (
                    entry.get("trackName")
                    or entry.get("master_metadata_track_name")
                    or ""
                ).lower().strip()
                if artist and title:
                    key = f"{artist}|{title}"
                    counts[key] = counts.get(key, 0) + 1
            for key, count in counts.items():
                self._ext[key] = self._ext.get(key, 0) + count
            self._save()
            return len(counts)
        except Exception:
            return 0
