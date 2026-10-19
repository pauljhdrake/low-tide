from __future__ import annotations

import threading
import time

from lowtide.scrobbler import Scrobbler

from conftest import make_track


class FakeNetwork:
    def __init__(self, delay=0.0, fail_first=0):
        self.delay = delay
        self.fail_first = fail_first
        self.scrobbles = []
        self.done = threading.Event()

    def update_now_playing(self, **kw):
        time.sleep(self.delay)

    def scrobble(self, **kw):
        time.sleep(self.delay)
        if self.fail_first:
            self.fail_first -= 1
            raise ConnectionError("Last.fm unreachable")
        self.scrobbles.append(kw["title"])
        self.done.set()


def _scrobbler(network):
    s = Scrobbler({})
    s._network = network
    return s


def test_network_calls_do_not_block_caller():
    net = FakeNetwork(delay=0.5)
    s = _scrobbler(net)
    start = time.monotonic()
    s.track_started(make_track(1, "Song"))
    s.update(position=200, duration=300)
    assert time.monotonic() - start < 0.1
    assert net.done.wait(3)
    s.shutdown()


def test_scrobbles_once_per_play():
    net = FakeNetwork()
    s = _scrobbler(net)
    played = []
    s.on_scrobble.append(played.append)
    s.track_started(make_track(1, "Song"))
    for pos in (100, 150, 200, 250):
        s.update(position=pos, duration=300)
    assert net.done.wait(3)
    s._executor.shutdown(wait=True)
    assert net.scrobbles == ["Song"]
    assert len(played) == 1


def test_threshold_and_short_tracks():
    net = FakeNetwork()
    s = _scrobbler(net)
    s.track_started(make_track(1, "Short"))
    s.update(position=25, duration=29)  # under 30 s: never scrobbled
    s.track_started(make_track(2, "Long"))
    s.update(position=100, duration=600)  # before min(50%, 240 s)
    s._executor.shutdown(wait=True)
    assert net.scrobbles == []


def test_failed_scrobble_is_retried():
    net = FakeNetwork(fail_first=1)
    s = _scrobbler(net)
    s.track_started(make_track(1, "Song"))
    s.update(position=200, duration=300)
    deadline = time.monotonic() + 3
    while not net.done.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
        s.update(position=201, duration=300)
    assert net.scrobbles == ["Song"]
    s.shutdown()
