from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, Callable

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method, signal
from dbus_next.constants import PropertyAccess
from dbus_next import BusType, Variant

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.low-tide"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"


class MediaPlayer2Interface(ServiceInterface):
    def __init__(self, quit_cb: Callable):
        super().__init__("org.mpris.MediaPlayer2")
        self._quit_cb = quit_cb

    @method()
    def Raise(self):
        pass

    @method()
    def Quit(self):
        self._quit_cb()

    @dbus_property(access=PropertyAccess.READ)
    def CanQuit(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanRaise(self) -> "b":  # noqa: F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasTrackList(self) -> "b":  # noqa: F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":  # noqa: F821
        return "low-tide"

    @dbus_property(access=PropertyAccess.READ)
    def Fullscreen(self) -> "b":  # noqa: F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def CanSetFullscreen(self) -> "b":  # noqa: F821
        return False

    @dbus_property(access=PropertyAccess.READ)
    def DesktopEntry(self) -> "s":  # noqa: F821
        return "low-tide"

    @dbus_property(access=PropertyAccess.READ)
    def SupportedUriSchemes(self) -> "as":  # noqa: F821
        return []

    @dbus_property(access=PropertyAccess.READ)
    def SupportedMimeTypes(self) -> "as":  # noqa: F821
        return []


class MediaPlayer2PlayerInterface(ServiceInterface):
    def __init__(
        self,
        toggle_pause_cb: Callable,
        play_cb: Callable,
        pause_cb: Callable,
        stop_cb: Callable,
        next_cb: Callable,
        prev_cb: Callable,
        seek_cb: Callable,
        volume_cb: Callable,
    ):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self._toggle_pause_cb = toggle_pause_cb
        self._play_cb = play_cb
        self._pause_cb = pause_cb
        self._stop_cb = stop_cb
        self._next_cb = next_cb
        self._prev_cb = prev_cb
        self._seek_cb = seek_cb
        self._volume_cb = volume_cb

        self._playback_status = "Stopped"
        self._metadata: dict = {}
        self._volume: float = 0.8
        self._position: int = 0
        self._shuffle: bool = False
        self._loop_status: str = "None"

    # --- Methods ---

    @method()
    def PlayPause(self):
        asyncio.ensure_future(self._toggle_pause_cb())

    @method()
    def Play(self):
        asyncio.ensure_future(self._play_cb())

    @method()
    def Pause(self):
        asyncio.ensure_future(self._pause_cb())

    @method()
    def Stop(self):
        asyncio.ensure_future(self._stop_cb())

    @method()
    def Next(self):
        asyncio.ensure_future(self._next_cb())

    @method()
    def Previous(self):
        asyncio.ensure_future(self._prev_cb())

    @method()
    def Seek(self, offset: "x"):  # noqa: F821
        # MPRIS Seek supplies a relative offset (microseconds); the player
        # performs an absolute seek, so convert against the tracked position.
        current_seconds = self._position / 1_000_000
        target_seconds = max(0.0, current_seconds + offset / 1_000_000)
        asyncio.ensure_future(self._seek_cb(target_seconds))

    @method()
    def SetPosition(self, track_id: "o", position: "x"):  # noqa: F821
        # Ignore stale requests aimed at a track that is no longer current.
        current_track_id = self._metadata.get("mpris:trackid")
        if current_track_id is None or current_track_id.value != track_id:
            return
        target_seconds = max(0.0, position / 1_000_000)
        asyncio.ensure_future(self._seek_cb(target_seconds))

    @method()
    def OpenUri(self, uri: "s"):  # noqa: F821
        pass

    # --- Signals ---

    @signal()
    def Seeked(self) -> "x":  # noqa: F821
        return 0

    # --- Properties ---

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":  # noqa: F821
        return self._playback_status

    @dbus_property()
    def LoopStatus(self) -> "s":  # noqa: F821
        return self._loop_status

    @LoopStatus.setter
    def LoopStatus(self, val: "s"):  # noqa: F821
        self._loop_status = val

    @dbus_property()
    def Shuffle(self) -> "b":  # noqa: F821
        return self._shuffle

    @Shuffle.setter
    def Shuffle(self, val: "b"):  # noqa: F821
        self._shuffle = val

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":  # noqa: F821
        return self._metadata

    @dbus_property()
    def Volume(self) -> "d":  # noqa: F821
        return self._volume

    @Volume.setter
    def Volume(self, val: "d"):  # noqa: F821
        self._volume = max(0.0, min(1.0, val))
        asyncio.ensure_future(self._volume_cb(round(self._volume * 100)))

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":  # noqa: F821
        return self._position

    @dbus_property(access=PropertyAccess.READ)
    def MinimumRate(self) -> "d":  # noqa: F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def MaximumRate(self) -> "d":  # noqa: F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":  # noqa: F821
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":  # noqa: F821
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> "b":  # noqa: F821
        return True


class MPRISService:
    """Registers the app on D-Bus as an MPRIS2 media player."""

    def __init__(self, app):
        self._app = app
        self._bus: MessageBus | None = None
        self._player_iface: MediaPlayer2PlayerInterface | None = None

    async def start(self) -> None:
        try:
            self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
            root_iface = MediaPlayer2Interface(quit_cb=self._app.exit)
            self._player_iface = MediaPlayer2PlayerInterface(
                toggle_pause_cb=self._app.player.toggle_pause,
                play_cb=self._app.player.resume,
                pause_cb=self._app.player.pause,
                stop_cb=self._app.player.stop,
                next_cb=self._app.player.next,
                prev_cb=self._app.player.prev,
                seek_cb=self._app.player.seek,
                volume_cb=self._app.set_volume,
            )
            self._bus.export(MPRIS_OBJECT_PATH, root_iface)
            self._bus.export(MPRIS_OBJECT_PATH, self._player_iface)
            await self._bus.request_name(MPRIS_BUS_NAME)
        except Exception as e:
            log.warning("MPRIS unavailable: %s", e)
            self._bus = None

    def update_track(self, track) -> None:
        if not self._player_iface:
            return
        raw_id = str(getattr(track, "id", 0)).encode("utf-8", errors="replace")
        safe_id = hashlib.sha256(raw_id).hexdigest()[:24]
        track_id = f"/org/mpris/MediaPlayer2/track/{safe_id}"
        title = getattr(track, "name", "")
        artist = getattr(getattr(track, "artist", None), "name", "")
        album = getattr(getattr(track, "album", None), "name", "")
        duration_us = int(getattr(track, "duration", 0)) * 1_000_000
        try:
            art_url = track.album.image(320)
        except Exception:
            art_url = ""

        self._player_iface._metadata = {
            "mpris:trackid": Variant("o", track_id),
            "mpris:length": Variant("x", duration_us),
            "mpris:artUrl": Variant("s", art_url or ""),
            "xesam:title": Variant("s", title),
            "xesam:artist": Variant("as", [artist] if artist else []),
            "xesam:album": Variant("s", album),
        }
        self._emit_properties_changed({"Metadata": self._player_iface._metadata})

    def update_playback_status(self, paused: bool, *, stopped: bool = False) -> None:
        if not self._player_iface:
            return
        status = "Stopped" if stopped else "Paused" if paused else "Playing"
        self._player_iface._playback_status = status
        self._emit_properties_changed({"PlaybackStatus": status})

    def update_position(self, position_s: float) -> None:
        if not self._player_iface:
            return
        self._player_iface._position = int(position_s * 1_000_000)

    def update_volume(self, volume_pct: int) -> None:
        if not self._player_iface:
            return
        self._player_iface._volume = volume_pct / 100.0
        self._emit_properties_changed({"Volume": self._player_iface._volume})

    def update_shuffle(self, shuffle: bool) -> None:
        if not self._player_iface:
            return
        self._player_iface._shuffle = shuffle
        self._emit_properties_changed({"Shuffle": shuffle})

    def update_loop_status(self, repeat: bool) -> None:
        if not self._player_iface:
            return
        status = "Playlist" if repeat else "None"
        self._player_iface._loop_status = status
        self._emit_properties_changed({"LoopStatus": status})

    def _emit_properties_changed(self, changed: dict) -> None:
        if not self._player_iface:
            return
        try:
            self._player_iface.emit_properties_changed(changed)
        except Exception as error:
            log.warning("Failed to emit MPRIS property changes: %s", error)

    async def stop(self) -> None:
        if self._bus:
            try:
                self._bus.disconnect()
            except Exception as error:
                log.warning("MPRIS shutdown failed (%s)", type(error).__name__)
