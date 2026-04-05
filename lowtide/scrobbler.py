from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)


class Scrobbler:
    """Last.fm scrobbler. Silently does nothing if not configured."""

    def __init__(self, config: dict):
        self._network = None
        self._current_track = None
        self._track_start_time: int = 0
        self._scrobbled: bool = False
        self._setup(config.get("lastfm", {}))

    def _setup(self, cfg: dict) -> None:
        required = ("api_key", "api_secret", "username", "password_hash")
        if not all(cfg.get(k) for k in required):
            return
        try:
            import pylast
            self._network = pylast.LastFMNetwork(
                api_key=cfg["api_key"],
                api_secret=cfg["api_secret"],
                username=cfg["username"],
                password_hash=cfg["password_hash"],
            )
            log.info("Last.fm scrobbling enabled for %s", cfg["username"])
        except Exception as e:
            log.warning("Last.fm setup failed: %s", e)

    @property
    def enabled(self) -> bool:
        return self._network is not None

    def track_started(self, track) -> None:
        if not self._network:
            return
        self._current_track = track
        self._track_start_time = int(time.time())
        self._scrobbled = False
        try:
            artist = getattr(getattr(track, "artist", None), "name", "")
            title = getattr(track, "name", "")
            album = getattr(getattr(track, "album", None), "name", "")
            self._network.update_now_playing(artist=artist, title=title, album=album)
        except Exception as e:
            log.debug("now_playing update failed: %s", e)

    def update(self, position: float, duration: float) -> None:
        """Call on each poll tick. Scrobbles when threshold is reached."""
        if not self._network or not self._current_track or self._scrobbled:
            return
        if duration < 30:
            return
        threshold = min(duration * 0.5, 240.0)
        if position >= threshold:
            self._scrobble()

    def _scrobble(self) -> None:
        track = self._current_track
        try:
            artist = getattr(getattr(track, "artist", None), "name", "")
            title = getattr(track, "name", "")
            album = getattr(getattr(track, "album", None), "name", "")
            self._network.scrobble(
                artist=artist,
                title=title,
                timestamp=self._track_start_time,
                album=album,
            )
            self._scrobbled = True
            log.info("Scrobbled: %s – %s", artist, title)
        except Exception as e:
            log.debug("Scrobble failed: %s", e)
