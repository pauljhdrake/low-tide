from __future__ import annotations

import asyncio

import pytest
from dbus_next import Variant

from lowtide.mpris import MediaPlayer2PlayerInterface, _mpris_track_id


@pytest.mark.parametrize("raw_id", [12345, None, "/home/u/Music/My Song (Live).flac", ""])
def test_track_id_is_valid_object_path(raw_id):
    Variant("o", _mpris_track_id(raw_id))  # raises if not a valid D-Bus path


def test_local_track_ids_are_distinct():
    assert _mpris_track_id("/a.flac") != _mpris_track_id("/b.flac")


def _iface(calls):
    async def record(name, *args):
        calls.append((name, *args))

    def cb(name):
        return lambda *args: record(name, *args)

    return MediaPlayer2PlayerInterface(
        play_pause_cb=cb("toggle"), set_paused_cb=cb("set_paused"),
        next_cb=cb("next"), prev_cb=cb("prev"), seek_cb=cb("seek"),
        set_volume_cb=cb("volume"), set_shuffle_cb=cb("shuffle"), set_loop_cb=cb("loop"),
    )


def test_play_and_pause_set_state_instead_of_toggling():
    calls = []

    async def run():
        iface = _iface(calls)
        iface.Pause()
        iface.Pause()
        iface.Play()
        await asyncio.sleep(0)

    asyncio.run(run())
    assert calls == [("set_paused", True), ("set_paused", True), ("set_paused", False)]


def test_setters_forward_to_app():
    calls = []

    async def run():
        iface = _iface(calls)
        props = type(iface)
        await props.Volume.prop_setter(iface, 0.35)
        await props.Volume.prop_setter(iface, 1.7)  # clamped
        await props.Shuffle.prop_setter(iface, True)
        await props.LoopStatus.prop_setter(iface, "Playlist")

    asyncio.run(run())
    assert calls == [("volume", 35), ("volume", 100), ("shuffle", True), ("loop", "Playlist")]


def test_seek_is_relative_to_tracked_position():
    calls = []

    async def run():
        iface = _iface(calls)
        iface._position = 30_000_000
        iface.Seek(-40_000_000)  # clamps at 0
        iface.Seek(5_000_000)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert calls == [("seek", 0.0), ("seek", 35.0)]
