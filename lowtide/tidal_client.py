from __future__ import annotations

import datetime
import json
import os
from typing import Optional

import tidalapi

CONF_DIR = os.path.join(os.path.expanduser("~"), ".config", "low-tide")
CONF_PATH = os.path.join(CONF_DIR, "session.json")


class TidalClient:
    def __init__(self):
        self.session = tidalapi.Session()
        self._try_load_tokens()

    def _try_load_tokens(self) -> None:
        try:
            with open(CONF_PATH) as f:
                data = json.load(f)
            expiry = None
            if data.get("expiry_time"):
                expiry = datetime.datetime.fromisoformat(data["expiry_time"])
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
        with open(CONF_PATH, "w") as f:
            json.dump(data, f)

    def ensure_login(self) -> None:
        if self.session.check_login():
            return
        print("==== TIDAL Login ====")
        self.session.login_oauth_simple()
        self._save_tokens()

    def me(self):
        return self.session.user

    def search(self, query: str, limit: int = 50) -> dict:
        return self.session.search(query, limit=limit)

    def get_user_playlists(self) -> list:
        return self.session.user.playlists()

    def get_favorite_tracks(self) -> list:
        return self.session.user.favorites.tracks()

    def get_favorite_albums(self) -> list:
        return self.session.user.favorites.albums()

    def get_favorite_artists(self) -> list:
        return self.session.user.favorites.artists()

    def get_album_tracks(self, album) -> list:
        return album.tracks()

    def get_playlist_tracks(self, playlist) -> list:
        return playlist.tracks()

    def get_artist_albums(self, artist) -> list:
        return artist.get_albums()

    def get_mix_tracks(self, mix) -> list:
        return mix.items()

    def get_track_url(self, track) -> Optional[str]:
        try:
            return track.get_url()
        except Exception:
            return None
