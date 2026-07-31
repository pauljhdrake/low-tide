from __future__ import annotations

import pytest

from lowtide.app import _MAX_SAVED_QUEUE_TRACKS, _SavedTrack, _parse_saved_queue


def test_saved_queue_is_parsed_without_network_resolution():
    tracks, index = _parse_saved_queue(
        {
            "tracks": [
                {
                    "id": 123,
                    "name": "Track",
                    "artist": "Artist",
                    "album": "Album",
                    "art_url": "https://example.invalid/cover.jpg",
                },
                {
                    "id": "456",
                    "name": "Next",
                    "artist": "Artist",
                    "album": "Album",
                    "art_url": None,
                },
            ],
            "current_idx": 1,
        }
    )
    assert all(isinstance(track, _SavedTrack) for track in tracks)
    assert [track.id for track in tracks] == [123, 456]
    assert index == 1


def test_saved_queue_rejects_unbounded_or_malformed_state():
    with pytest.raises(ValueError):
        _parse_saved_queue({"tracks": [{}] * (_MAX_SAVED_QUEUE_TRACKS + 1)})
    with pytest.raises(ValueError):
        _parse_saved_queue({"tracks": ["not-an-object"]})
