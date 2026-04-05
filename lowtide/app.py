from __future__ import annotations

import asyncio
from typing import Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from lowtide.mpris import MPRISService
from lowtide.player import Player
from lowtide.screens.library import LibraryScreen
from lowtide.screens.search import SearchScreen
from lowtide.tidal_client import TidalClient
from lowtide.widgets.album_art import AlbumArt
from lowtide.widgets.now_playing import NowPlayingBar


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

_NAV = [
    ("library", "Library"),
    ("search", "Search"),
    ("favorites", "Favorites"),
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

    def compose(self) -> ComposeResult:
        yield AlbumArt(id="sidebar-art")
        yield Label("low-tide", id="app-title")
        with ListView(id="nav"):
            for key, label in _NAV:
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
        Binding("l", "toggle_favourite", "Love"),
        Binding("q", "toggle_queue", "Queue"),
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+s", "focus_search", "Search"),
        Binding("ctrl+l", "focus_library", "Library"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, client: TidalClient):
        super().__init__()
        self.client = client
        self.player = Player(config=client.config)
        self.mpris = MPRISService(self)
        self._queue: list = []
        self._current_idx: int = -1
        self._queue_gen: int = 0  # incremented on each new enqueue to cancel stale workers
        self._current_track = None
        self._current_favourited: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Sidebar()
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
        self.player.on_track_start.append(self._on_mpv_track_start)
        self.set_interval(1.0, self._poll_player)
        self._restore_queue()

    # --- Player polling ---

    async def _poll_player(self) -> None:
        bar = self.query_one(NowPlayingBar)
        position = await self.player.get_position()
        paused = await self.player.get_paused()
        bar.position = position
        bar.duration = await self.player.get_duration()
        bar.paused = paused
        bar.volume = self.player.volume
        self.mpris.update_position(position)
        self.mpris.update_playback_status(paused)

    async def _on_mpv_track_start(self) -> None:
        pos = await self.player.get_playlist_pos()
        if 0 <= pos < len(self._queue):
            self._current_idx = pos
            self._set_current_track(self._queue[pos])
            self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)

    def _set_current_track(self, track) -> None:
        self._current_track = track
        self._current_favourited = False
        bar = self.query_one(NowPlayingBar)
        bar.set_track(track)
        bar.favourited = False
        try:
            art_url = track.album.image(320)
        except Exception:
            art_url = None
        self.query_one(Sidebar).update_art(art_url)
        self.mpris.update_track(track)

    # --- Public: enqueue & play ---

    def enqueue_and_play(self, tracks: list, start_index: int = 0) -> None:
        # Rotate so the selected track is at position 0, matching mpv's playlist order.
        # self._queue[N] will always equal mpv playlist position N.
        rotated = tracks[start_index:] + tracks[:start_index]
        if self.player.shuffle:
            import random
            first = rotated[:1]
            rest = rotated[1:]
            random.shuffle(rest)
            rotated = first + rest
        self._queue = rotated
        self._current_idx = 0
        self._queue_gen += 1
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
                return  # a newer enqueue_and_play was called – abort
            url = self.client.get_track_url(track)
            if not url or self._queue_gen != gen:
                continue
            if i == 0:
                self.call_from_thread(
                    lambda u=url: asyncio.ensure_future(self.player.play(u))
                )
                self.call_from_thread(self._set_current_track, track)
            else:
                self.call_from_thread(
                    lambda u=url: asyncio.ensure_future(self.player.append(u))
                )
        if self._queue_gen == gen:
            self.call_from_thread(
                lambda: self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
            )

    @work(thread=True)
    def _append_tracks(self, tracks: list, gen: int) -> None:
        for track in tracks:
            if self._queue_gen != gen:
                return
            url = self.client.get_track_url(track)
            if url and self._queue_gen == gen:
                self.call_from_thread(
                    lambda u=url: asyncio.ensure_future(self.player.append(u))
                )

    def on_track_list_track_append_requested(self, event) -> None:
        self.append_to_queue([event.track])

    async def jump_to_queue_index(self, idx: int) -> None:
        """Skip playback to a specific queue position."""
        if 0 <= idx < len(self._queue):
            # mpv playlist-play-index jumps to a position
            await self.player._cmd(["set_property", "playlist-pos", idx])

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

    async def _open_favorites(self) -> None:
        from lowtide.screens.favorites import FavoritesScreen
        await self._switch_root(FavoritesScreen(), "favorites")

    # --- Playback actions ---

    async def action_toggle_shuffle(self) -> None:
        self.player.shuffle = not self.player.shuffle
        self.query_one(NowPlayingBar).shuffle = self.player.shuffle
        self.mpris.update_shuffle(self.player.shuffle)
        state = "on" if self.player.shuffle else "off"
        self.notify(f"Shuffle {state}")

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
        await self.player.next()

    async def action_prev_track(self) -> None:
        await self.player.prev()

    async def action_volume_up(self) -> None:
        await self.player.set_volume(self.player.volume + 5)
        self.mpris.update_volume(self.player.volume)

    async def action_volume_down(self) -> None:
        await self.player.set_volume(self.player.volume - 5)
        self.mpris.update_volume(self.player.volume)

    async def action_toggle_queue(self) -> None:
        panel = self.query_one(QueuePanel)
        panel.display = not panel.display

    def _save_queue(self) -> None:
        from lowtide.tidal_client import CONF_DIR
        import os, json as _json
        if not self._queue:
            return
        try:
            os.makedirs(CONF_DIR, exist_ok=True)
            path = os.path.join(CONF_DIR, "queue.json")
            data = {
                "track_ids": [getattr(t, "id", None) for t in self._queue],
                "current_idx": self._current_idx,
            }
            with open(path, "w") as f:
                _json.dump(data, f)
        except Exception:
            pass

    @work(thread=True)
    def _restore_queue(self) -> None:
        from lowtide.tidal_client import CONF_DIR
        import os, json as _json
        path = os.path.join(CONF_DIR, "queue.json")
        try:
            with open(path) as f:
                data = _json.load(f)
            track_ids = [i for i in data.get("track_ids", []) if i is not None]
            if not track_ids:
                return
            tracks = []
            for tid in track_ids:
                try:
                    tracks.append(self.client.session.track(tid))
                except Exception:
                    pass
            if not tracks:
                return
            saved_idx = data.get("current_idx", 0)
            self.call_from_thread(self._apply_restored_queue, tracks, saved_idx)
        except Exception:
            pass

    def _apply_restored_queue(self, tracks: list, current_idx: int) -> None:
        self._queue = tracks
        self._current_idx = current_idx
        self.query_one(QueuePanel).refresh_queue(tracks, current_idx)
        if 0 <= current_idx < len(tracks):
            self._set_current_track(tracks[current_idx])
        self.notify(f"Restored queue ({len(tracks)} tracks)", timeout=3)

    async def on_unmount(self) -> None:
        self._save_queue()
        await self.mpris.stop()
        await self.player.shutdown()
