from __future__ import annotations

from types import SimpleNamespace

from lowtide.mpris import MPRISService


def _service_with_fake_interface():
    service = object.__new__(MPRISService)
    service._player_iface = SimpleNamespace(
        _playback_status="Playing",
        _volume=0.0,
    )
    emitted = []
    service._emit_properties_changed = emitted.append
    return service, emitted


def test_idle_mpris_state_is_stopped():
    service, emitted = _service_with_fake_interface()
    service.update_playback_status(False, stopped=True)
    assert service._player_iface._playback_status == "Stopped"
    assert emitted[-1] == {"PlaybackStatus": "Stopped"}


def test_initial_volume_can_be_synchronized():
    service, emitted = _service_with_fake_interface()
    service.update_volume(100)
    assert service._player_iface._volume == 1.0
    assert emitted[-1] == {"Volume": 1.0}
