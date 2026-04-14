from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from lowtide.lyrics import parse_lrc
from lowtide.mpris import MPRISService
from lowtide.play_count_store import (
    SHUFFLE_DISCOVERY, SHUFFLE_FAVOURITE, SHUFFLE_LABELS, SHUFFLE_OFF,
    SHUFFLE_RANDOM, PlayCountStore,
)
from lowtide.player import Player
from lowtide.scrobbler import Scrobbler
from lowtide.screens.library import LibraryScreen
from lowtide.screens.search import SearchScreen
from lowtide.tidal_client import TidalClient
from lowtide.widgets.album_art import AlbumArt
from lowtide.widgets.eq_visualizer import EQVisualizer
from lowtide.widgets.now_playing import NowPlayingBar


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

_NAV_BASE = [
    ("library", "Library"),
    ("search", "Search"),
    ("favorites", "Favorites"),
    ("ride-the-tide", "Ride the Tide"),
]


class Sidebar(Widget):
    DEFAULT_CSS = """
    Sidebar {
        width: 24;
        height: 100%;
        background: transparent;
        border-right: tall $primary-darken-3;
        padding: 1 0;
    }
    Sidebar AlbumArt {
        width: 22;
        height: 11;
        margin: 0 1 1 1;
    }
    Sidebar #app-title {
        padding: 0 2;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    Sidebar ListView {
        background: transparent;
        height: 1fr;
    }
    Sidebar ListItem {
        padding: 0 2;
        background: transparent;
    }
    """

    def __init__(self, nav: list, **kwargs):
        super().__init__(**kwargs)
        self._nav = nav

    def compose(self) -> ComposeResult:
        yield AlbumArt(id="sidebar-art")
        yield Label("low-tide", id="app-title")
        with ListView(id="nav"):
            for key, label in self._nav:
                item = ListItem(Label(label))
                item._nav_key = key
                yield item

    def on_mount(self) -> None:
        self.query_one(ListView).index = 0

    def select(self, key: str) -> None:
        lv = self.query_one(ListView)
        for i, item in enumerate(lv._nodes):
            if getattr(item, "_nav_key", None) == key:
                lv.index = i
                break

    def update_art(self, url: str | None) -> None:
        self.query_one(AlbumArt).load(url)


# ---------------------------------------------------------------------------
# Content area
# ---------------------------------------------------------------------------

class ContentArea(Widget):
    DEFAULT_CSS = """
    ContentArea {
        width: 1fr;
        height: 100%;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield LibraryScreen()

    def on_mount(self) -> None:
        self._stack: list[Widget] = list(self.children)

    async def push(self, widget: Widget) -> None:
        if self._stack:
            self._stack[-1].display = False
        self._stack.append(widget)
        await self.mount(widget)

    async def pop(self) -> bool:
        if len(self._stack) > 1:
            old = self._stack.pop()
            await old.remove()
            if self._stack:
                self._stack[-1].display = True
            return True
        return False

    async def replace(self, widget: Widget) -> None:
        for w in list(self._stack):
            await w.remove()
        self._stack = [widget]
        await self.mount(widget)


# ---------------------------------------------------------------------------
# Queue panel
# ---------------------------------------------------------------------------

class QueuePanel(Widget):
    DEFAULT_CSS = """
    QueuePanel {
        width: 30;
        height: 100%;
        background: transparent;
        border-left: tall $primary-darken-3;
        padding: 1;
        display: none;
    }
    QueuePanel #queue-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    QueuePanel ListView {
        height: 1fr;
        background: transparent;
    }
    QueuePanel ListItem {
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Queue", id="queue-heading")
        yield ListView(id="queue-list")

    def refresh_queue(self, tracks: list, current_idx: int) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        for i, t in enumerate(tracks):
            name = getattr(t, "name", "?")
            artist = getattr(getattr(t, "artist", None), "name", "–")
            marker = "▶ " if i == current_idx else "  "
            item = ListItem(Label(f"{marker}[b]{name}[/b]\n[dim]{artist}[/dim]"))
            item._queue_index = i
            lv.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = getattr(event.item, "_queue_index", None)
        if idx is not None:
            asyncio.ensure_future(self.app.jump_to_queue_index(idx))


# ---------------------------------------------------------------------------
# Saved-queue placeholder
# ---------------------------------------------------------------------------

class _SavedTrack:
    """
    Lightweight stand-in populated immediately from queue.json metadata so the
    UI can be shown before real tidalapi objects are fetched from TIDAL.
    """

    class _Artist:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Album:
        def __init__(self, name: str, art_url: str | None) -> None:
            self.name = name
            self._art_url = art_url

        def image(self, size: int = 320) -> str | None:
            return self._art_url

    def __init__(
        self,
        track_id: int | None,
        name: str,
        artist_name: str,
        album_name: str,
        art_url: str | None,
    ) -> None:
        self.id = track_id
        self.name = name
        self.artist = self._Artist(artist_name)
        self.album = self._Album(album_name, art_url)
        self.duration = 0
        self.explicit = False
        self.audio_quality = None
        self.bpm = None


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class LowTideApp(App):
    CSS = """
    Screen {
        background: transparent;
        layout: vertical;
    }
    #main {
        layout: horizontal;
        height: 1fr;
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_pause", "Play/Pause"),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Prev"),
        Binding("]", "volume_up", "Vol+", show=False),
        Binding("[", "volume_down", "Vol-", show=False),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("r", "toggle_repeat", "Repeat"),
        Binding("x", "toggle_crossfade", "Crossfade"),
        Binding("l", "toggle_favourite", "Love"),
        Binding("q", "toggle_queue", "Queue"),
        Binding("e", "toggle_eq", "EQ"),
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+s", "focus_search", "Search"),
        Binding("ctrl+l", "focus_library", "Library"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, client: TidalClient):
        super().__init__()
        self.client = client
        cfg = client.config
        self.player = Player(config=cfg)
        self.mpris = MPRISService(self)
        self.scrobbler = Scrobbler(cfg)

        from lowtide.tidal_client import CONF_DIR
        from lowtide.recommender import Recommender
        store_path = os.path.join(CONF_DIR, "playcounts.json")
        self._play_count_store = PlayCountStore(store_path)
        self.scrobbler.on_scrobble.append(self._play_count_store.increment)
        self.recommender = Recommender(
            network=self.scrobbler._network,
            store=self._play_count_store,
            client=client,
        )

        # Local library – only if music_dir is configured
        from lowtide.local_library import LocalLibrary
        raw = cfg.get("music_dir")
        if raw:
            dirs = raw if isinstance(raw, list) else [raw]
            self._local_library: LocalLibrary | None = LocalLibrary(dirs)
        else:
            self._local_library = None
        self._eq_theme = cfg.get("eq_theme", "mono")
        self._eq_labels = bool(cfg.get("eq_labels", False))
        self._nav = list(_NAV_BASE)
        if self._local_library:
            self._nav.append(("local", "Local"))
        self._queue: list = []
        self._current_idx: int = -1
        self._queue_gen: int = 0  # incremented on each new enqueue to cancel stale workers
        self._current_track = None
        self._current_favourited: bool = False
        self._target_volume: int = 80
        self._crossfading: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Sidebar(nav=self._nav)
            yield ContentArea()
            yield QueuePanel()
        yield NowPlayingBar()

    async def on_mount(self) -> None:
        try:
            await self.player.start()
        except RuntimeError as e:
            self.notify(str(e), severity="error", timeout=10)
            return
        await self.mpris.start()
        eq = self.query_one(EQVisualizer)
        eq.set_theme(self._eq_theme)
        eq._show_labels = self._eq_labels
        self._target_volume = self.player.volume
        if self.player.crossfade_secs > 0:
            self.query_one(NowPlayingBar).crossfade = True
        self.player.on_track_start.append(self._on_mpv_track_start)
        self.set_interval(1.0, self._poll_player)
        self._restore_queue()
        self._sync_lastfm_counts()

    # --- Player polling ---

    async def _poll_player(self) -> None:
        bar = self.query_one(NowPlayingBar)
        position = await self.player.get_position()
        duration = await self.player.get_duration()
        paused = await self.player.get_paused()
        bar.position = position
        bar.duration = duration
        bar.paused = paused
        bar.volume = self._target_volume
        self.query_one(EQVisualizer).paused = paused
        self.mpris.update_position(position)
        self.mpris.update_playback_status(paused)
        self.scrobbler.update(position, duration)

        # Crossfade: fade out as current track approaches its end
        if self.player.crossfade_secs > 0 and duration > 0 and not paused:
            remaining = duration - position
            if 0 < remaining <= self.player.crossfade_secs:
                fade_vol = max(0, int((remaining / self.player.crossfade_secs) * self._target_volume))
                await self.player._cmd(["set_property", "volume", fade_vol])
                self._crossfading = True

    async def _on_mpv_track_start(self) -> None:
        # Restore volume after a crossfade fade-out
        if self._crossfading:
            self._crossfading = False
            await self.player.set_volume(self._target_volume)

        pos = await self.player.get_playlist_pos()
        if 0 <= pos < len(self._queue):
            self._current_idx = pos
            track = self._queue[pos]
            self._set_current_track(track)
            self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
            self.scrobbler.track_started(track)
            self._load_track_extras(track)

    def _set_current_track(self, track) -> None:
        self._current_track = track
        self._current_favourited = False
        bar = self.query_one(NowPlayingBar)
        bar.set_track(track)
        bar.set_lyrics([])   # clear until loaded
        bar.track_info = ""
        bar.favourited = False
        try:
            art_url = track.album.image(320)
        except Exception:
            art_url = None
        self.query_one(Sidebar).update_art(art_url)
        self.mpris.update_track(track)

    @work(thread=True)
    def _load_track_extras(self, track) -> None:
        """Fetch lyrics and track info in the background."""
        from lowtide.local_track import LocalTrack
        info = self.client.get_track_info(track)
        if isinstance(track, LocalTrack):
            lines = []
        else:
            _, lrc = self.client.get_lyrics(track)
            lines = parse_lrc(lrc) if lrc else []
        self.call_from_thread(self._apply_track_extras, info, lines)

    def _apply_track_extras(self, info: dict, lines: list) -> None:
        bar = self.query_one(NowPlayingBar)
        bar.set_track_info(info)
        bar.set_lyrics(lines)
        self.query_one(EQVisualizer).set_bpm(info.get("bpm"))

    # --- Public: enqueue & play ---

    def enqueue_and_play(self, tracks: list, start_index: int = 0) -> None:
        # Rotate so the selected track is at position 0, matching mpv's playlist order.
        # self._queue[N] will always equal mpv playlist position N.
        rotated = tracks[start_index:] + tracks[:start_index]
        mode = self.player.shuffle_mode
        if mode != SHUFFLE_OFF:
            import random
            first = rotated[:1]
            rest = rotated[1:]
            if mode == SHUFFLE_RANDOM:
                random.shuffle(rest)
            elif mode == SHUFFLE_FAVOURITE:
                rest = self._play_count_store.weighted_shuffle(rest, favourite=True)
            elif mode == SHUFFLE_DISCOVERY:
                rest = self._play_count_store.weighted_shuffle(rest, favourite=False)
            rotated = first + rest
        self._queue = rotated
        self._current_idx = 0
        self._queue_gen += 1
        asyncio.ensure_future(self.player.stop())
        self._load_queue(rotated, self._queue_gen)

    def append_to_queue(self, tracks: list) -> None:
        if not self._queue:
            # Nothing playing yet – just start playback
            self.enqueue_and_play(tracks)
            return
        self._queue.extend(tracks)
        self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
        self._append_tracks(tracks, self._queue_gen)
        count = len(tracks)
        label = tracks[0].name if count == 1 else f"{count} tracks"
        self.notify(f"Added {label} to queue")

    @work(thread=True)
    def _load_queue(self, tracks: list, gen: int) -> None:
        for i, track in enumerate(tracks):
            if self._queue_gen != gen:
                return
            url = self.client.get_track_url(track)
            if not url or self._queue_gen != gen:
                continue
            if i == 0:
                self.call_from_thread(self._play_if_current, url, gen, track)
            else:
                self.call_from_thread(self._append_if_current, url, gen)
        if self._queue_gen == gen:
            self.call_from_thread(
                lambda: self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
            )

    def _play_if_current(self, url: str, gen: int, track) -> None:
        if self._queue_gen != gen:
            return
        asyncio.ensure_future(self.player.play(url))
        self._set_current_track(track)

    def _append_if_current(self, url: str, gen: int) -> None:
        if self._queue_gen == gen:
            asyncio.ensure_future(self.player.append(url))

    @work(thread=True)
    def _append_tracks(self, tracks: list, gen: int) -> None:
        for track in tracks:
            if self._queue_gen != gen:
                return
            url = self.client.get_track_url(track)
            if url:
                self.call_from_thread(self._append_if_current, url, gen)

    def on_track_list_track_append_requested(self, event) -> None:
        self.append_to_queue([event.track])

    async def jump_to_queue_index(self, idx: int) -> None:
        """Skip playback to a specific queue position."""
        if 0 <= idx < len(self._queue):
            await self.player._cmd(["set_property", "playlist-pos", idx])
            # If mpv went idle after the playlist ended, the above restores the
            # position but leaves it paused — ensure playback actually starts.
            await self.player._cmd(["set_property", "pause", False])

    # --- Open objects (albums, artists, mixes, playlists) by type ---

    def open_object(self, obj) -> None:
        """Route any tidalapi object to the appropriate screen."""
        from lowtide.screens.album import AlbumScreen
        from lowtide.screens.artist import ArtistScreen
        from lowtide.screens.playlist import PlaylistScreen

        type_name = type(obj).__name__.lower()

        if "album" in type_name:
            asyncio.ensure_future(self.push_view(AlbumScreen(obj)))
        elif "artist" in type_name:
            asyncio.ensure_future(self.push_view(ArtistScreen(obj)))
        elif "playlist" in type_name or "userplaylist" in type_name:
            asyncio.ensure_future(self.push_view(PlaylistScreen(obj)))
        elif "mix" in type_name:
            asyncio.ensure_future(self.push_view(PlaylistScreen(obj, autoplay=True)))
        else:
            # Fallback: try playlist-style tracks() call
            asyncio.ensure_future(self.push_view(PlaylistScreen(obj)))

    # --- Navigation ---

    async def push_view(self, widget: Widget) -> None:
        await self.query_one(ContentArea).push(widget)

    async def action_go_back(self) -> None:
        await self.query_one(ContentArea).pop()

    async def _switch_root(self, widget: Widget, nav_key: str) -> None:
        await self.query_one(ContentArea).replace(widget)
        self.query_one(Sidebar).select(nav_key)

    async def action_focus_search(self) -> None:
        await self._switch_root(SearchScreen(), "search")

    async def action_focus_library(self) -> None:
        await self._switch_root(LibraryScreen(), "library")

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        key = getattr(event.item, "_nav_key", None)
        if key and event.list_view.id == "nav":
            if key == "library":
                await self.action_focus_library()
            elif key == "search":
                await self.action_focus_search()
            elif key == "favorites":
                await self._open_favorites()
            elif key == "ride-the-tide":
                await self._open_ride_the_tide()
            elif key == "local":
                await self._open_local()

    async def _open_favorites(self) -> None:
        from lowtide.screens.favorites import FavoritesScreen
        await self._switch_root(FavoritesScreen(), "favorites")

    async def _open_ride_the_tide(self) -> None:
        from lowtide.screens.radio import RadioScreen
        await self._switch_root(RadioScreen(), "ride-the-tide")

    async def on_track_list_track_radio_requested(self, event) -> None:
        from lowtide.screens.radio import RadioScreen
        await self.push_view(RadioScreen(seed_track=event.track))

    async def _open_local(self) -> None:
        from lowtide.screens.local import LocalLibraryScreen
        if self._local_library:
            await self._switch_root(LocalLibraryScreen(self._local_library), "local")

    # --- Playback actions ---

    async def action_toggle_crossfade(self) -> None:
        if self.player.crossfade_secs > 0:
            self.player.crossfade_secs = 0
            self.query_one(NowPlayingBar).crossfade = False
            self.notify("Crossfade off")
        else:
            cfg = self.client.config
            secs = int(cfg.get("crossfade", 5))
            self.player.crossfade_secs = max(1, secs)
            self.query_one(NowPlayingBar).crossfade = True
            self.notify(f"Crossfade {self.player.crossfade_secs}s")

    async def action_toggle_shuffle(self) -> None:
        self.player.shuffle_mode = (self.player.shuffle_mode + 1) % 4
        mode = self.player.shuffle_mode
        self.query_one(NowPlayingBar).shuffle_mode = mode
        self.mpris.update_shuffle(mode != SHUFFLE_OFF)
        self.notify(f"Shuffle: {SHUFFLE_LABELS[mode]}")

    async def action_toggle_repeat(self) -> None:
        await self.player.toggle_repeat()
        self.query_one(NowPlayingBar).repeat = self.player.repeat
        self.mpris.update_loop_status(self.player.repeat)
        state = "on" if self.player.repeat else "off"
        self.notify(f"Repeat {state}")

    @work(thread=True)
    def action_toggle_favourite(self) -> None:
        if self._current_track is None:
            return
        track_id = getattr(self._current_track, "id", None)
        if track_id is None:
            return
        if self._current_favourited:
            ok = self.client.remove_favourite_track(track_id)
            if ok:
                self._current_favourited = False
                self.call_from_thread(self._apply_favourite_state, False, "Removed from favourites")
        else:
            ok = self.client.add_favourite_track(track_id)
            if ok:
                self._current_favourited = True
                self.call_from_thread(self._apply_favourite_state, True, "Added to favourites")

    def _apply_favourite_state(self, state: bool, message: str) -> None:
        self.query_one(NowPlayingBar).favourited = state
        self.notify(message)

    async def action_toggle_pause(self) -> None:
        await self.player.toggle_pause()

    async def action_next_track(self) -> None:
        mode = self.player.shuffle_mode
        if mode in (SHUFFLE_FAVOURITE, SHUFFLE_DISCOVERY) and self._current_idx >= 0 and self._queue:
            remaining = self._queue[self._current_idx + 1:]
            if not remaining:
                # Exhausted the queue — wrap around using all tracks except current
                remaining = [t for i, t in enumerate(self._queue) if i != self._current_idx]
                self.notify("Wrapping queue")
            if remaining:
                picks = self._play_count_store.weighted_shuffle(
                    remaining, favourite=(mode == SHUFFLE_FAVOURITE)
                )
                if picks:
                    await self.jump_to_queue_index(self._queue.index(picks[0]))
                    return
        await self.player.next()

    async def action_prev_track(self) -> None:
        await self.player.prev()

    async def action_volume_up(self) -> None:
        self._target_volume = min(100, self._target_volume + 5)
        await self.player.set_volume(self._target_volume)
        self.mpris.update_volume(self._target_volume)

    async def action_volume_down(self) -> None:
        self._target_volume = max(0, self._target_volume - 5)
        await self.player.set_volume(self._target_volume)
        self.mpris.update_volume(self._target_volume)

    async def action_toggle_queue(self) -> None:
        panel = self.query_one(QueuePanel)
        panel.display = not panel.display

    async def action_toggle_eq(self) -> None:
        self.query_one(NowPlayingBar).toggle_eq()

    def _save_queue(self) -> None:
        from lowtide.tidal_client import CONF_DIR
        import json as _json
        if not self._queue:
            return
        try:
            os.makedirs(CONF_DIR, exist_ok=True)
            path = os.path.join(CONF_DIR, "queue.json")
            tracks = []
            for t in self._queue:
                try:
                    art_url = t.album.image(320)
                except Exception:
                    art_url = None
                tracks.append({
                    "id": getattr(t, "id", None),
                    "name": getattr(t, "name", ""),
                    "artist": getattr(getattr(t, "artist", None), "name", ""),
                    "album": getattr(getattr(t, "album", None), "name", ""),
                    "art_url": art_url,
                })
            data = {"tracks": tracks, "current_idx": self._current_idx}
            with open(path, "w") as f:
                _json.dump(data, f)
        except Exception:
            pass

    @work(thread=True)
    def _restore_queue(self) -> None:
        from lowtide.tidal_client import CONF_DIR
        import json as _json
        path = os.path.join(CONF_DIR, "queue.json")
        try:
            with open(path) as f:
                data = _json.load(f)
        except Exception:
            return

        saved_idx = data.get("current_idx", 0)

        # Support both new format (tracks[]) and old format (track_ids[])
        raw = data.get("tracks")
        if raw:
            placeholders = [
                _SavedTrack(
                    t.get("id"),
                    t.get("name", ""),
                    t.get("artist", ""),
                    t.get("album", ""),
                    t.get("art_url"),
                )
                for t in raw
            ]
            track_ids = [t.get("id") for t in raw if t.get("id") is not None]
        else:
            # Legacy format
            track_ids = [i for i in data.get("track_ids", []) if i is not None]
            placeholders = [_SavedTrack(tid, f"Track {tid}", "", "", None) for tid in track_ids]

        if not track_ids:
            return

        # Show the queue immediately from saved metadata — no TIDAL calls yet
        self.call_from_thread(self._show_restored_ui, placeholders, saved_idx)

        # Fetch real track objects in the background for playback
        tracks = []
        for tid in track_ids:
            try:
                tracks.append(self.client.session.track(tid))
            except Exception:
                pass
        if tracks:
            self.call_from_thread(self._apply_restored_queue, tracks, saved_idx)

    def _show_restored_ui(self, placeholders: list, current_idx: int) -> None:
        """Populate the UI immediately from saved metadata before playback is ready."""
        self._queue = placeholders
        self._current_idx = current_idx
        panel = self.query_one(QueuePanel)
        panel.refresh_queue(placeholders, current_idx)
        panel.display = True
        if 0 <= current_idx < len(placeholders):
            track = placeholders[current_idx]
            bar = self.query_one(NowPlayingBar)
            bar.track_name = track.name
            bar.artist_name = track.artist.name
            try:
                art_url = track.album.image(320)
                self.query_one(Sidebar).update_art(art_url)
            except Exception:
                pass

    def _apply_restored_queue(self, tracks: list, current_idx: int) -> None:
        self.notify(f"Restored queue ({len(tracks)} tracks)", timeout=3)
        self.enqueue_and_play(tracks, start_index=current_idx)

    @work(thread=True)
    def _sync_lastfm_counts(self) -> None:
        if not self.scrobbler.enabled:
            return
        cfg = self.client.config.get("lastfm", {})
        username = cfg.get("username", "")
        if not username:
            return
        n = self._play_count_store.sync_lastfm(self.scrobbler._network, username)
        if n:
            log.debug("Last.fm play count sync: %d tracks", n)

    async def on_unmount(self) -> None:
        self._save_queue()
        self._play_count_store.save()
        await self.mpris.stop()
        await self.player.shutdown()
