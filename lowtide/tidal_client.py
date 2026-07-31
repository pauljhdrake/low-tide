from __future__ import annotations

import datetime
import logging
import os
import stat
import tempfile
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import tidalapi
from tidalapi.media import Quality

from lowtide.runtime import RuntimeWorkspace
from lowtide.secure_io import (
    UnsafePathError,
    atomic_write_json,
    ensure_private_config_dir,
    get_config_dir,
    read_json,
    repair_private_json_files,
)

log = logging.getLogger(__name__)

CONF_DIR = get_config_dir()
CONF_PATH = os.path.join(CONF_DIR, "session.json")
CONFIG_PATH = os.path.join(CONF_DIR, "config.json")

_MAX_MANIFEST_BYTES = 2 * 1024 * 1024

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
        ensure_private_config_dir()
        repair_private_json_files(CONF_DIR)
        self.runtime = RuntimeWorkspace.create()
        self._api_lock = threading.Lock()
        self._quality_lock = threading.Lock()
        self._last_call_time = 0.0
        quality = self._load_quality()
        self.session = tidalapi.Session(tidalapi.Config(quality=quality))
        # Highest tier we'll attempt for stream URLs. Starts at the configured
        # quality and ratchets down permanently if the subscription rejects it.
        try:
            self._quality_floor = _QUALITY_ORDER.index(quality)
        except ValueError:
            self._quality_floor = 0
        # Hi-res DASH manifests live in this instance's private runtime directory.
        self._manifest_dir = self.runtime.manifest_dir
        try:
            self._try_load_tokens()
        except Exception:
            self.runtime.cleanup()
            raise

    def _clear_manifests(self) -> None:
        self.runtime.cleanup_manifests()

    @property
    def runtime_dir(self) -> str:
        return self.runtime.directory

    def close(self) -> None:
        self.runtime.cleanup()

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
            raw = read_json(CONFIG_PATH, default={})
        except UnsafePathError:
            raise
        except (OSError, ValueError, TypeError) as error:
            log.warning("config load failed (%s)", type(error).__name__)
            return {}
        if not isinstance(raw, dict):
            log.warning("config load failed (top-level value is not an object)")
            return {}

        config: dict = {}
        quality = raw.get("quality", "lossless")
        config["quality"] = quality if quality in _QUALITY_MAP else "lossless"
        replaygain = raw.get("replaygain", "album")
        config["replaygain"] = replaygain if replaygain in {"album", "track", "no"} else "album"
        config["alsa_output"] = raw.get("alsa_output") is True

        volume = raw.get("volume", 80)
        config["volume"] = volume if isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100 else 80
        crossfade = raw.get("crossfade", 0)
        config["crossfade"] = crossfade if isinstance(crossfade, int) and not isinstance(crossfade, bool) and 0 <= crossfade <= 30 else 0

        device = raw.get("alsa_device")
        if isinstance(device, str) and 0 < len(device) <= 128 and all(
            ch.isalnum() or ch in "_.:,=+-" for ch in device
        ):
            config["alsa_device"] = device
        bit_depth = raw.get("alsa_bit_depth")
        if bit_depth in (16, 24, 32):
            config["alsa_bit_depth"] = bit_depth
        samplerate = raw.get("alsa_samplerate")
        if (
            isinstance(samplerate, int)
            and not isinstance(samplerate, bool)
            and 8_000 <= samplerate <= 768_000
        ):
            config["alsa_samplerate"] = samplerate

        music_dir = raw.get("music_dir")
        if isinstance(music_dir, str):
            config["music_dir"] = music_dir
        elif isinstance(music_dir, list) and all(isinstance(item, str) for item in music_dir):
            config["music_dir"] = music_dir

        theme = raw.get("eq_theme", "mono")
        config["eq_theme"] = theme if theme in {"classic", "fire", "ice", "mono", "neon"} else "mono"
        config["eq_labels"] = raw.get("eq_labels") is True
        background = raw.get("background", "ansi_default")
        config["background"] = (
            background
            if isinstance(background, str)
            and len(background) <= 128
            and not any(ord(ch) < 32 for ch in background)
            else "ansi_default"
        )
        lastfm = raw.get("lastfm")
        if isinstance(lastfm, dict):
            config["lastfm"] = lastfm
        return config

    def _load_quality(self) -> str:
        data = self._load_config()
        return _QUALITY_MAP.get(data.get("quality", "lossless"), Quality.high_lossless)

    @property
    def config(self) -> dict:
        return self._load_config()

    # --- Auth ---

    def _try_load_tokens(self) -> None:
        try:
            data = read_json(CONF_PATH, default=None)
            if data is None:
                return
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                raise ValueError("invalid OAuth session structure")
            expiry = None
            if data.get("expiry_time"):
                expiry = datetime.datetime.fromisoformat(data["expiry_time"])
            is_pkce = data.get("is_pkce", False)
            if not isinstance(is_pkce, bool):
                raise ValueError("invalid OAuth session structure")
            self.session.load_oauth_session(
                token_type=data.get("token_type", "Bearer"),
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expiry_time=expiry,
                is_pkce=is_pkce,
            )
        except UnsafePathError:
            raise
        except (KeyError, OSError, TypeError, ValueError) as error:
            log.warning("OAuth session could not be loaded (%s)", type(error).__name__)

    def _save_tokens(self) -> None:
        expiry = getattr(self.session, "expiry_time", None)
        data = {
            "access_token": self.session.access_token,
            "refresh_token": self.session.refresh_token,
            "token_type": getattr(self.session, "token_type", "Bearer"),
            "expiry_time": expiry.isoformat() if expiry else None,
            "is_pkce": bool(getattr(self.session, "is_pkce", False)),
        }
        atomic_write_json(CONF_PATH, data)

    def ensure_login(self) -> None:
        logged_in = self.session.check_login()
        needs_pkce = (
            self.session.config.quality == Quality.hi_res_lossless
            and not getattr(self.session, "is_pkce", False)
        )
        if logged_in and not needs_pkce:
            # Persist a refresh performed by check_login without printing token data.
            self._save_tokens()
            return
        print("==== TIDAL Login ====")
        if needs_pkce:
            print("La calidad MAX requiere volver a autenticar con PKCE.")
            self.session.login_pkce()
        else:
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
        except Exception as error:
            log.debug("track resolution failed (%s)", type(error).__name__)
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
        subscription rejects the configured one. Returns None if no tier works."""
        for idx in range(self._quality_floor, len(_QUALITY_ORDER)):
            quality = _QUALITY_ORDER[idx]
            try:
                return self._resolve_stream(track, quality)
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                log.warning(
                    "get_track_url failed at %s (%s, status=%s)",
                    getattr(quality, "value", quality), type(e).__name__, status,
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

        stream_info = {
            "audio_quality": getattr(stream, "audio_quality", None),
            "bit_depth": getattr(stream, "bit_depth", None),
            "sample_rate": getattr(stream, "sample_rate", None),
        }
        setattr(track, "_lowtide_stream_info", stream_info)
        actual = stream_info["audio_quality"]
        if actual is not None and actual != quality:
            log.warning(
                "TIDAL delivered %s for requested %s",
                getattr(actual, "value", actual),
                getattr(quality, "value", quality),
            )

        manifest = stream.get_stream_manifest()
        if getattr(manifest, "manifest_mime_type", "") == "application/dash+xml":
            # Segmented hi-res: hand mpv the decoded MPD manifest as a temp file.
            return self._write_manifest(stream.manifest)
        # Byte-stream manifest: a single directly-playable URL.
        urls = manifest.get_urls()
        if not urls:
            return None
        url = urls[0]
        if urlsplit(url).scheme.lower() != "https":
            raise ValueError("TIDAL stream URL did not use HTTPS")
        return url

    def _write_manifest(self, b64_manifest: str) -> str:
        """Decode a base64 DASH manifest and write it to a temp .mpd file."""
        import base64
        import binascii
        from defusedxml import ElementTree as ET

        if not isinstance(b64_manifest, (str, bytes)):
            raise ValueError("DASH manifest was not base64 text")
        encoded = b64_manifest.encode("ascii") if isinstance(b64_manifest, str) else b64_manifest
        if len(encoded) > ((_MAX_MANIFEST_BYTES + 2) // 3) * 4 + 4:
            raise ValueError("DASH manifest exceeds size limit")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("DASH manifest had invalid base64") from error
        if not decoded or len(decoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("DASH manifest exceeds size limit")
        if b"<!DOCTYPE" in decoded.upper() or b"<!ENTITY" in decoded.upper():
            raise ValueError("DASH manifest contained forbidden declarations")
        try:
            root = ET.fromstring(decoded)
        except ET.ParseError as error:
            raise ValueError("DASH manifest was not valid XML") from error
        if root.tag.rsplit("}", 1)[-1] != "MPD":
            raise ValueError("DASH manifest root was not MPD")
        uri_attributes = {"href", "initialization", "media", "sourceURL"}
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "BaseURL" and element.text:
                scheme = urlsplit(element.text.strip()).scheme.lower()
                if scheme and scheme != "https":
                    raise ValueError("DASH manifest contained a non-HTTPS BaseURL")
            for key, value in element.attrib.items():
                if key.rsplit("}", 1)[-1] in uri_attributes:
                    scheme = urlsplit(value.strip()).scheme.lower()
                    if scheme and scheme != "https":
                        raise ValueError("DASH manifest contained a non-HTTPS URL")

        fd, path = tempfile.mkstemp(suffix=".mpd", dir=self._manifest_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                fd = -1
                f.write(decoded)
                f.flush()
                os.fsync(f.fileno())
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                raise RuntimeError("DASH manifest file validation failed")
            self.runtime.track_manifest(path)
        except Exception:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            raise
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
        stream_info = getattr(track, "_lowtide_stream_info", {})
        if not isinstance(stream_info, dict):
            stream_info = {}
        return {
            "bpm": getattr(track, "bpm", None),
            "explicit": getattr(track, "explicit", False),
            "audio_quality": stream_info.get("audio_quality")
            or getattr(track, "audio_quality", None),
            "bit_depth": stream_info.get("bit_depth"),
            "sample_rate": stream_info.get("sample_rate"),
            "isrc": getattr(track, "isrc", None),
        }

    # --- Favourites ---

    def add_favourite_track(self, track_id: int) -> bool:
        try:
            return self._api_call(self.session.user.favorites.add_track, track_id)
        except Exception:
            return False

    def remove_favourite_track(self, track_id: int) -> bool:
        try:
            return self._api_call(self.session.user.favorites.remove_track, str(track_id))
        except Exception:
            return False
