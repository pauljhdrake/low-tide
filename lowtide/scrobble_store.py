from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger(__name__)

_TWO_YEARS = 2 * 365 * 24 * 3600


class ScrobbleStore:
    """
    Fetches and caches Last.fm recent-track history (artist name + timestamp).
    Stored at ~/.config/low-tide/scrobbles.json.

    First sync fetches the past two years. Subsequent syncs are incremental —
    only tracks newer than the last fetch are fetched.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._scrobbles: list[tuple[str, int]] = []  # (artist_name, unix_ts)
        self._fetched_at: int = 0
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self._path) as f:
                data = json.load(f)
            self._fetched_at = data.get("fetched_at", 0)
            self._scrobbles = [(s["a"], s["t"]) for s in data.get("scrobbles", [])]
            log.debug("scrobble store: loaded %d entries", len(self._scrobbles))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("scrobble store: load failed: %s", e)

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(
                    {
                        "fetched_at": self._fetched_at,
                        "scrobbles": [{"a": a, "t": t} for a, t in self._scrobbles],
                    },
                    f,
                )
        except Exception as e:
            log.warning("scrobble store: save failed: %s", e)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def scrobbles(self) -> list[tuple[str, int]]:
        return self._scrobbles

    @property
    def fetched_at(self) -> int:
        return self._fetched_at

    @property
    def is_empty(self) -> bool:
        return len(self._scrobbles) == 0

    def sync(
        self,
        network,
        username: str,
        on_progress: "Callable[[int], None] | None" = None,
    ) -> int:
        """
        Fetch new scrobbles from Last.fm. Returns count of new entries added.
        First call fetches the past two years; subsequent calls are incremental.
        """
        import pylast

        now = int(time.time())
        time_from = self._fetched_at if self._fetched_at else now - _TWO_YEARS

        try:
            user = pylast.User(username, network)
            raw = user.get_recent_tracks(
                limit=None,
                time_from=time_from,
                time_to=now,
                stream=True,
            )
        except Exception as e:
            log.warning("scrobble store: fetch failed: %s", e)
            return 0

        new: list[tuple[str, int]] = []
        try:
            for item in raw:
                ts = getattr(item, "timestamp", None)
                if not ts:
                    continue  # skip now-playing entry (no timestamp)
                artist_obj = getattr(item.track, "artist", None)
                artist_name = getattr(artist_obj, "name", "") if artist_obj else ""
                if artist_name:
                    new.append((artist_name, int(ts)))
                if on_progress and len(new) % 200 == 0:
                    on_progress(len(new))
        except Exception as e:
            log.warning("scrobble store: iteration failed after %d entries: %s", len(new), e)

        if new:
            existing = [(a, t) for a, t in self._scrobbles if t < time_from]
            self._scrobbles = sorted(existing + new, key=lambda x: x[1])

        self._fetched_at = now
        self.save()
        return len(new)
