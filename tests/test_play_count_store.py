from __future__ import annotations

import json

from lowtide.play_count_store import PlayCountStore

from conftest import make_track


def _write(path, entries):
    path.write_text(json.dumps(entries))


def test_spotify_legacy_export(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    _write(export / "StreamingHistory_music_0.json", [
        {"artistName": "A", "trackName": "Song", "msPlayed": 45_000},
        {"artistName": "A", "trackName": "Song", "msPlayed": 60_000},
        {"artistName": "A", "trackName": "Skipped", "msPlayed": 5_000},
    ])
    store = PlayCountStore(str(tmp_path / "pc.json"))
    assert store.import_spotify(str(export)) == 1
    assert store.get_by_names("A", "Song") == 2
    assert store.get_by_names("A", "Skipped") == 0


def test_spotify_extended_export(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    _write(export / "Streaming_History_Audio_2024.json", [
        {"master_metadata_album_artist_name": "B", "master_metadata_track_name": "Tune",
         "ms_played": 31_000},
    ])
    store = PlayCountStore(str(tmp_path / "pc.json"))
    assert store.import_spotify(str(export)) == 1
    assert store.get_by_names("b", "tune") == 1  # keys are case-insensitive


def test_counts_persist(tmp_path):
    path = str(tmp_path / "pc.json")
    store = PlayCountStore(path)
    track = make_track(1, "Song", "Artist")
    store.increment(track)
    store.increment(track)
    store.save()
    assert PlayCountStore(path).get(track) == 2


def test_weighted_shuffle_is_a_permutation(tmp_path):
    store = PlayCountStore(str(tmp_path / "pc.json"))
    tracks = [make_track(i) for i in range(20)]
    for favourite in (True, False):
        result = store.weighted_shuffle(tracks, favourite=favourite)
        assert sorted(t.id for t in result) == list(range(20))
