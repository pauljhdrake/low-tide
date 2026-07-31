from __future__ import annotations

import datetime
import logging
import os
import stat
from types import SimpleNamespace

from tidalapi.media import Quality

from lowtide import tidal_client
from lowtide.secure_io import atomic_write_json, read_json
from lowtide.tidal_client import TidalClient


def test_config_validation(monkeypatch, tmp_path):
    path = tmp_path / "config" / "config.json"
    monkeypatch.setattr(tidal_client, "CONFIG_PATH", str(path))
    atomic_write_json(
        path,
        {
            "quality": "max",
            "replaygain": "no",
            "volume": 100,
            "crossfade": 0,
            "alsa_output": False,
            "alsa_samplerate": "192000",
            "alsa_bit_depth": 17,
        },
    )
    config = object.__new__(TidalClient)._load_config()
    assert config["quality"] == "max"
    assert config["replaygain"] == "no"
    assert config["volume"] == 100
    assert config["crossfade"] == 0
    assert config["alsa_output"] is False
    assert "alsa_samplerate" not in config
    assert "alsa_bit_depth" not in config


def test_session_write_is_private_and_contains_no_log_secret(
    monkeypatch, tmp_path, caplog
):
    path = tmp_path / "config" / "session.json"
    monkeypatch.setattr(tidal_client, "CONF_PATH", str(path))
    client = object.__new__(TidalClient)
    client.session = SimpleNamespace(
        access_token="access-secret-marker",
        refresh_token="refresh-secret-marker",
        token_type="Bearer",
        expiry_time=datetime.datetime.now(datetime.UTC),
        is_pkce=True,
    )
    with caplog.at_level(logging.DEBUG):
        client._save_tokens()
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    assert "access-secret-marker" not in caplog.text
    assert "refresh-secret-marker" not in caplog.text
    saved = read_json(path)
    assert saved["token_type"] == "Bearer"
    assert saved["is_pkce"] is True


def test_invalid_session_error_does_not_log_tokens(monkeypatch, tmp_path, caplog):
    path = tmp_path / "config" / "session.json"
    monkeypatch.setattr(tidal_client, "CONF_PATH", str(path))
    atomic_write_json(
        path,
        {
            "access_token": "never-log-this-access-token",
            "refresh_token": "never-log-this-refresh-token",
            "expiry_time": "invalid date",
        },
    )
    client = object.__new__(TidalClient)
    client.session = SimpleNamespace()
    with caplog.at_level(logging.WARNING):
        client._try_load_tokens()
    assert "never-log-this" not in caplog.text
    assert "ValueError" in caplog.text


def test_pkce_flag_is_restored(monkeypatch, tmp_path):
    path = tmp_path / "config" / "session.json"
    monkeypatch.setattr(tidal_client, "CONF_PATH", str(path))
    atomic_write_json(
        path,
        {
            "access_token": "opaque",
            "refresh_token": "opaque-refresh",
            "token_type": "Bearer",
            "is_pkce": True,
        },
    )
    loaded = {}
    client = object.__new__(TidalClient)
    client.session = SimpleNamespace(
        load_oauth_session=lambda **kwargs: loaded.update(kwargs)
    )
    client._try_load_tokens()
    assert loaded["is_pkce"] is True


def test_track_info_prefers_effective_stream_quality():
    client = object.__new__(TidalClient)
    track = SimpleNamespace(
        audio_quality="LOSSLESS",
        bpm=120,
        explicit=False,
        isrc="example",
        _lowtide_stream_info={
            "audio_quality": "HIGH",
            "bit_depth": 16,
            "sample_rate": 44_100,
        },
    )
    info = client.get_track_info(track)
    assert info["audio_quality"] == "HIGH"
    assert info["bit_depth"] == 16
    assert info["sample_rate"] == 44_100


def test_max_quality_reauthenticates_with_pkce(monkeypatch):
    calls = []
    client = object.__new__(TidalClient)
    client.session = SimpleNamespace(
        config=SimpleNamespace(quality=Quality.hi_res_lossless),
        is_pkce=False,
        check_login=lambda: True,
        login_pkce=lambda: calls.append("pkce"),
        login_oauth_simple=lambda: calls.append("device"),
    )
    monkeypatch.setattr(client, "_save_tokens", lambda: calls.append("save"))
    client.ensure_login()
    assert calls == ["pkce", "save"]
