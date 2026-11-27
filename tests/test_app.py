"""App-level behaviour, using stand-in objects for the app where a full
LowTideApp (TIDAL session, mpv, D-Bus) isn't needed."""
from __future__ import annotations

import asyncio
import types

import pytest

from lowtide.app import LowTideApp
from lowtide.local_track import LocalTrack

from conftest import make_track

NS = types.SimpleNamespace


# ── Queue save / restore ─────────────────────────────────────────────────


def _restore(queue, current_idx, get_track):
    """Save `queue`, then run the restore worker body and return
    (restored tracks, index playback starts at)."""
    LowTideApp._save_queue(NS(_queue=queue, _current_idx=current_idx))
    result = {}

    def call_from_thread(fn, *args):
        result[fn.__name__] = args

    def _show_restored_ui(*a): pass
    def _apply_restored_queue(*a): pass

    app = NS(client=NS(get_track=get_track), call_from_thread=call_from_thread,
             _show_restored_ui=_show_restored_ui, _apply_restored_queue=_apply_restored_queue)
    LowTideApp._restore_queue.__wrapped__(app)
    return result.get("_apply_restored_queue")


def _fetch_except(failing):
    def get_track(track_id):
        if track_id in failing:
            raise RuntimeError("not available")
        return make_track(track_id)
    return get_track


@pytest.mark.parametrize("failing, expected_ids, expected_current", [
    (set(), [1, 2, 3, 4, 5], 4),
    ({1, 2}, [3, 4, 5], 4),        # failures before the saved track shift it
    ({4}, [1, 2, 3, 5], 5),        # saved track itself failed: next one plays
    ({4, 5}, [1, 2, 3], 1),        # nothing after it: start from the top
])
def test_restore_resumes_on_saved_track(conf_dir, failing, expected_ids, expected_current):
    queue = [make_track(i) for i in range(1, 6)]
    tracks, idx = _restore(queue, 3, _fetch_except(failing))
    assert [t.id for t in tracks] == expected_ids
    assert tracks[idx].id == expected_current


def test_restore_rebuilds_local_tracks_from_files(conf_dir, flac_file):
    from lowtide.local_library import LocalLibrary

    path = flac_file("01 Song.flac", "Local Song")
    missing = flac_file("02 Gone.flac", "Gone")
    queue = [LocalLibrary.track_from_path(path), make_track(7), LocalLibrary.track_from_path(missing)]
    import os
    os.remove(missing)

    tracks, idx = _restore(queue, 0, _fetch_except(set()))
    assert isinstance(tracks[0], LocalTrack)
    assert tracks[0].name == "Local Song"
    assert tracks[0].artist.name == "Test Artist"
    assert [getattr(t, "id", None) for t in tracks[1:]] == [7]  # missing file dropped
    assert idx == 0


# ── Crossfade volume ─────────────────────────────────────────────────────


class FakePlayer:
    crossfade_secs = 5

    def __init__(self):
        self.volume, self.pos, self.paused = 80, 0.0, False

    async def get_position(self): return self.pos
    async def get_duration(self): return 100.0
    async def get_paused(self): return self.paused
    async def _cmd(self, command): self.volume = command[2]
    async def set_volume(self, level): self.volume = level


def test_crossfade_volume_restored_after_seeking_back():
    player = FakePlayer()
    ui = NS()
    app = NS(player=player, _target_volume=80, _crossfading=False, query_one=lambda *_: ui,
             mpris=NS(update_position=lambda *_: None, update_playback_status=lambda *_: None),
             scrobbler=NS(update=lambda *_: None))
    app._end_crossfade = lambda: LowTideApp._end_crossfade(app)

    async def poll(pos, paused=False):
        player.pos, player.paused = pos, paused
        await LowTideApp._poll_player(app)
        return player.volume

    async def run():
        assert await poll(50) == 80
        assert await poll(97) < 80                 # fading
        assert await poll(30, paused=True) < 80    # stays faded while paused
        assert await poll(30) == 80                # restored on resume

    asyncio.run(run())
