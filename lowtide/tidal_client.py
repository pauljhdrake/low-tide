from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
import threading
import time
from typing import Optional

import tidalapi
from tidalapi.media import Quality

from lowtide.local_track import LocalTrack

log = logging.getLogger(__name__)

CONF_DIR = os.path.join(os.path.expanduser("~"), ".config", "low-tide")
CONF_PATH = os.path.join(CONF_DIR, "session.json")
CONFIG_PATH = os.path.join(CONF_DIR, "config.json")

_QUALITY_MAP = {
    "low": Quality.low_96k,
    "high": Quality.low_320k,
    "lossless": Quality.high_lossless,
    "hi_res": Quality.hi_res_lossless,
    "max": Quality.hi_res_lossless,
}

# Streaming tiers highest to lowest. get_track_url walks down this list when the
# subscription can't stream a tier: requesting a tier above the account's plan
# (e.g. HI_RES without a Max subscription) returns HTTP 401/403 for the stream
# URL even though metadata calls succeed.
_QUALITY_ORDER = [
    Quality.hi_res_lossless,
    Quality.high_lossless,
    Quality.low_320k,
    Quality.low_96k,
]

# Minimum seconds between API calls (shared across all threads)
_MIN_CALL_INTERVAL = 0.3
# Retry attempts before letting TooManyRequests propagate
_MAX_RETRIES = 2


class TidalClient:
    def __init__(self):
        self._api_lock = threading.Lock()
        self._quality_lock = threading.Lock()
        self._last_call_time = 0.0
        self._saved_access_token: str | None = None
        # IDs of the user's favourite tracks, fetched lazily on first lookup and
        # kept in sync by add/remove_favourite_track.
        self._fav_lock = threading.Lock()
        self._fav_track_ids: set[int] | None = None
        quality = self._load_quality()
        self.session = tidalapi.Session(tidalapi.Config(quality=quality))
        # Highest tier we'll attempt for stream URLs. Starts at the configured
        # quality and ratchets down permanently if the subscription rejects it.
        try:
            self._quality_floor = _QUALITY_ORDER.index(quality)
        except ValueError:
            self._quality_floor = 0
        # Hi-res DASH manifests are written here as temp .mpd files for mpv to
        # play. The dir is per process so a second instance doesn't delete
        # manifests the first is still playing; it's removed on exit.
        self._manifest_dir = tempfile.mkdtemp(prefix="low-tide-manifests-")
        self._try_load_tokens()
        # The loaded access token may have been refreshed during login.
        self.save_tokens_if_changed()

    def _clear_manifests(self) -> None:
        import shutil

        shutil.rmtree(self._manifest_dir, ignore_errors=True)

    # --- Rate limiting ---

    def _throttle(self) -> None:
        with self._api_lock:
            now = time.monotonic()
            wait = _MIN_CALL_INTERVAL - (now - self._last_call_time)
            if wait > 0:
                time.sleep(wait)
            self._last_call_time = time.monotonic()

    def _api_call(self, fn, *args, **kwargs):
        """Call fn with throttling and automatic retry on TooManyRequests."""
        from tidalapi.exceptions import TooManyRequests
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            try:
                return fn(*args, **kwargs)
            except TooManyRequests as e:
                wait = max(1, getattr(e, "retry_after", None) or 5)
                log.warning(
                    "TIDAL rate limit; retrying in %ss (attempt %d/%d)",
                    wait, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(wait)
        # Final attempt – let any exception propagate
        self._throttle()
        return fn(*args, **kwargs)

    # --- Config ---

    def _load_config(self) -> dict:
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_quality(self) -> str:
        data = self._load_config()
        return _QUALITY_MAP.get(data.get("quality", "lossless"), Quality.high_lossless)

    @property
    def config(self) -> dict:
        return self._load_config()

    # --- Auth ---

    def _try_load_tokens(self) -> None:
        try:
            with open(CONF_PATH) as f:
                data = json.load(f)
            expiry = None
            if data.get("expiry_time"):
                expiry = datetime.datetime.fromisoformat(data["expiry_time"])
            os.chmod(CONF_PATH, 0o600)  # older versions wrote it world-readable
            self._saved_access_token = data["access_token"]
            self.session.load_oauth_session(
                token_type=data.get("token_type", "Bearer"),
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expiry_time=expiry,
            )
        except Exception:
            pass

    def _save_tokens(self) -> None:
        os.makedirs(CONF_DIR, exist_ok=True)
        expiry = getattr(self.session, "expiry_time", None)
        data = {
            "access_token": self.session.access_token,
            "refresh_token": self.session.refresh_token,
            "token_type": getattr(self.session, "token_type", "Bearer"),
            "expiry_time": expiry.isoformat() if expiry else None,
        }
        # Credentials: create owner-only, and tighten a file that already
        # exists with looser permissions from older versions.
        fd = os.open(CONF_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        self._saved_access_token = self.session.access_token

    def save_tokens_if_changed(self) -> None:
        """Persist the session if tidalapi refreshed the access token, so the
        next start doesn't have to refresh it again."""
        token = getattr(self.session, "access_token", None)
        if token and self._saved_access_token and token != self._saved_access_token:
            try:
                self._save_tokens()
            except Exception as e:
                log.warning("Could not save refreshed TIDAL tokens: %s", e)

    def ensure_login(self) -> None:
        if self.session.check_login():
            return
        print("==== TIDAL Login ====")
        self.session.login_oauth_simple()
        self._save_tokens()

    def me(self):
        return self.session.user

    # --- Search / resolve ---

    def search(self, query: str, limit: int = 50) -> dict:
        return self._api_call(self.session.search, query, limit=limit)

    def resolve_track(self, artist: str, title: str):
        """Search TIDAL for a track by artist + title. Returns the best match or None."""
        import difflib
        import re
        import unicodedata

        def _norm(s: str) -> str:
            return re.sub(r"[^\w\s]", "", s.lower())

        def _fold(s: str) -> str:
            s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
            return re.sub(r"[^\w\s]", "", s.lower())

        def _match(a: str, b: str) -> bool:
            return a in b or b in a

        def _fuzzy(a: str, b: str, threshold: float = 0.82) -> bool:
            return difflib.SequenceMatcher(None, a, b).ratio() >= threshold

        def _candidates(query: str) -> list:
            try:
                return self.search(query, limit=25).get("tracks") or []
            except Exception:
                return []

        def _find(tracks: list) -> object:
            a_fold = _fold(artist)
            t_low, t_norm, t_fold = title.lower(), _norm(title), _fold(title)
            for track in tracks:
                r_artist = getattr(getattr(track, "artist", None), "name", "").lower()
                r_title = getattr(track, "name", "").lower()
                r_artist_fold = _fold(r_artist)
                r_title_fold = _fold(r_title)
                title_exact = t_fold == r_title_fold
                title_ok = (title_exact or _match(t_low, r_title) or _match(t_norm, _norm(r_title))
                            or _match(t_fold, r_title_fold))
                artist_threshold = 0.76 if title_exact else 0.82
                artist_ok = (_match(artist.lower(), r_artist) or _match(_norm(artist), _norm(r_artist))
                             or _match(a_fold, r_artist_fold)
                             or _fuzzy(a_fold, r_artist_fold, artist_threshold))
                if artist_ok and title_ok:
                    return track
            return None

        try:
            track = _find(_candidates(f"{artist} {title}"))
            if track is None:
                track = _find(_candidates(title))
            return track
        except Exception:
            pass
        return None

    # --- Library ---

    def get_user_playlists(self) -> list:
        return self._api_call(self.session.user.playlists)

    def get_favorite_tracks(self) -> list:
        return self._api_call(self.session.user.favorites.tracks_paginated)

    def get_favorite_albums(self, order=None, order_direction=None) -> list:
        return self._api_call(
            self.session.user.favorites.albums_paginated,
            order=order,
            order_direction=order_direction,
        )

    def get_favorite_artists(self) -> list:
        return self._api_call(self.session.user.favorites.artists_paginated)

    def get_album_tracks(self, album) -> list:
        return self._api_call(album.tracks)

    def get_playlist_tracks(self, playlist) -> list:
        fn = playlist.tracks if hasattr(playlist, "tracks") else playlist.items
        return self._api_call(fn)

    def get_artist_albums(self, artist) -> list:
        return self._api_call(artist.get_albums)

    def get_artist_top_tracks(self, artist, limit: int = 20) -> list:
        return self._api_call(artist.get_top_tracks, limit=limit)

    def get_artist_ep_singles(self, artist) -> list:
        return self._api_call(artist.get_ep_singles)

    def get_mix_tracks(self, mix) -> list:
        return self._api_call(mix.items)

    def get_track(self, track_id: int):
        return self._api_call(self.session.track, track_id)

    def get_for_you(self):
        return self._api_call(self.session.for_you)

    def get_mixes(self):
        return self._api_call(self.session.mixes)

    def get_home(self):
        return self._api_call(self.session.home)

    def get_genres(self) -> list:
        return self._api_call(self.session.genre.get_genres)

    def get_genre_tracks(self, genre) -> list:
        import tidalapi.media as _media
        return self._api_call(genre.items, _media.Track)

    # --- Playback ---

    def get_track_url(self, track) -> Optional[str]:
        """Return something mpv can play for this track: a direct stream URL for
        byte-stream (lossless and below) tracks, or a path to a temp .mpd manifest
        for segmented hi-res DASH tracks. Falls back down the quality tiers if the
        subscription rejects the configured one. Returns None if no tier works.
        Local files are returned as their path."""
        if isinstance(track, LocalTrack):
            return track.path
        name = getattr(track, "name", "?")
        for idx in range(self._quality_floor, len(_QUALITY_ORDER)):
            quality = _QUALITY_ORDER[idx]
            try:
                return self._resolve_stream(track, quality)
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                log.warning(
                    "get_track_url failed for %r at %s: %s: %s",
                    name, getattr(quality, "value", quality), type(e).__name__, e,
                )
                if status in (401, 403):
                    # Not entitled to this tier; stop attempting it going forward.
                    self._quality_floor = idx + 1
                # Fall through to the next (lower) tier.
        return None

    def _resolve_stream(self, track, quality) -> Optional[str]:
        """Fetch the playback manifest at a specific quality and turn it into a
        target mpv can open. The tier lives on the shared session config, so
        serialise the swap-call-restore to keep threads honest."""
        with self._quality_lock:
            prev = self.session.config.quality
            self.session.config.quality = quality
            try:
                stream = self._api_call(track.get_stream)
            finally:
                self.session.config.quality = prev

        manifest = stream.get_stream_manifest()
        if getattr(manifest, "manifest_mime_type", "") == "application/dash+xml":
            # Segmented hi-res: hand mpv the decoded MPD manifest as a temp file.
            return self._write_manifest(stream.manifest)
        # Byte-stream manifest: a single directly-playable URL.
        urls = manifest.get_urls()
        return urls[0] if urls else None

    def _write_manifest(self, b64_manifest: str) -> str:
        """Decode a base64 DASH manifest and write it to a temp .mpd file."""
        import base64

        os.makedirs(self._manifest_dir, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=".mpd", dir=self._manifest_dir)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(b64_manifest))
        return path

    def get_lyrics(self, track) -> tuple[str, str]:
        """Returns (plain_text, lrc_subtitles). Either may be empty string."""
        try:
            lyr = self._api_call(track.lyrics)
            return lyr.text or "", lyr.subtitles or ""
        except Exception:
            return "", ""

    def get_track_info(self, track) -> dict:
        """Returns extended track metadata for display."""
        return {
            "bpm": getattr(track, "bpm", None),
            "explicit": getattr(track, "explicit", False),
            "audio_quality": getattr(track, "audio_quality", None),
            "isrc": getattr(track, "isrc", None),
        }

    # --- Favourites ---

    def is_favourite_track(self, track_id: int) -> bool:
        """Whether track_id is in the user's favourites. The first call fetches
        the full favourites list, so call this from a worker thread."""
        with self._fav_lock:
            if self._fav_track_ids is None:
                try:
                    tracks = self.get_favorite_tracks()
                except Exception as e:
                    log.warning("Could not fetch favourite tracks: %s", e)
                    return False
                self._fav_track_ids = {t.id for t in tracks}
            return track_id in self._fav_track_ids

    def add_favourite_track(self, track_id: int) -> bool:
        try:
            ok = self._api_call(self.session.user.favorites.add_track, track_id)
        except Exception:
            return False
        if ok:
            with self._fav_lock:
                if self._fav_track_ids is not None:
                    self._fav_track_ids.add(track_id)
        return ok

    def remove_favourite_track(self, track_id: int) -> bool:
        try:
            ok = self._api_call(self.session.user.favorites.remove_track, str(track_id))
        except Exception:
            return False
        if ok:
            with self._fav_lock:
                if self._fav_track_ids is not None:
                    self._fav_track_ids.discard(track_id)
        return ok
