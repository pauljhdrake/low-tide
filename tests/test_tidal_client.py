from __future__ import annotations

import json
import os
import stat
import types

import pytest
import tidalapi

from lowtide import tidal_client
from lowtide.local_track import LocalAlbum, LocalArtist, LocalTrack


@pytest.fixture
def client(conf_dir):
    c = tidal_client.TidalClient()
    yield c
    c._clear_manifests()


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_local_track_url_is_its_path(client):
    artist = LocalArtist("A")
    track = LocalTrack("/music/My Song.flac", "My Song", artist, LocalAlbum("Al", artist))
    assert client.get_track_url(track) == "/music/My Song.flac"


def test_manifest_dir_is_per_instance(conf_dir):
    a, b = tidal_client.TidalClient(), tidal_client.TidalClient()
    try:
        assert a._manifest_dir != b._manifest_dir
        a._clear_manifests()
        assert os.path.isdir(b._manifest_dir)  # clearing one leaves the other
    finally:
        a._clear_manifests()
        b._clear_manifests()


class TestFavourites:
    @pytest.fixture
    def fav_client(self, client, monkeypatch):
        fetches = []

        def get_favorite_tracks():
            fetches.append(1)
            return [types.SimpleNamespace(id=1), types.SimpleNamespace(id=2)]

        monkeypatch.setattr(client, "get_favorite_tracks", get_favorite_tracks)
        monkeypatch.setattr(client, "_api_call", lambda fn, *a, **k: True)
        client.session.user = types.SimpleNamespace(
            favorites=types.SimpleNamespace(add_track=None, remove_track=None)
        )
        client.fetches = fetches
        return client

    def test_lookup_fetches_once(self, fav_client):
        assert fav_client.is_favourite_track(1)
        assert not fav_client.is_favourite_track(3)
        assert len(fav_client.fetches) == 1

    def test_add_and_remove_update_cache(self, fav_client):
        fav_client.is_favourite_track(1)
        assert fav_client.add_favourite_track(3)
        assert fav_client.remove_favourite_track(1)
        assert fav_client.is_favourite_track(3)
        assert not fav_client.is_favourite_track(1)


class TestTokens:
    def _fake_session(self, client, token="TOKEN"):
        client.session.access_token = token
        client.session.refresh_token = "REFRESH"
        client.session.token_type = "Bearer"
        client.session.expiry_time = None

    def test_saved_owner_only(self, client):
        self._fake_session(client)
        client._save_tokens()
        assert _mode(tidal_client.CONF_PATH) == 0o600

    def test_existing_loose_file_tightened_on_load(self, conf_dir, monkeypatch):
        path = conf_dir / "session.json"
        path.write_text(json.dumps({"access_token": "OLD", "refresh_token": "R"}))
        os.chmod(path, 0o644)
        monkeypatch.setattr(tidalapi.Session, "load_oauth_session", lambda self, **kw: True)
        c = tidal_client.TidalClient()
        c._clear_manifests()
        assert _mode(path) == 0o600

    def test_refreshed_token_is_saved(self, conf_dir, monkeypatch):
        path = conf_dir / "session.json"
        path.write_text(json.dumps({"access_token": "OLD", "refresh_token": "R"}))

        def load_and_refresh(self, token_type, access_token, refresh_token=None, expiry_time=None, **kw):
            # tidalapi refreshes an expired access token during login
            self.access_token, self.refresh_token = "NEW", refresh_token
            self.token_type, self.expiry_time = token_type, None
            return True

        monkeypatch.setattr(tidalapi.Session, "load_oauth_session", load_and_refresh)
        c = tidal_client.TidalClient()
        c._clear_manifests()
        assert json.loads(path.read_text())["access_token"] == "NEW"

    def test_unchanged_token_not_rewritten(self, client):
        self._fake_session(client)
        client._save_tokens()
        before = os.stat(tidal_client.CONF_PATH).st_mtime_ns
        client.save_tokens_if_changed()
        assert os.stat(tidal_client.CONF_PATH).st_mtime_ns == before
