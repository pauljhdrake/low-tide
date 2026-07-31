from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
from typing import Optional

log = logging.getLogger(__name__)

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

# Number of track URLs to keep buffered ahead of the current position in mpv's
# playlist. Keeping this small prevents blasting the API when switching playlists.
_LOOKAHEAD = 8

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


def _now_playing_title(track) -> str:
    """Format track metadata for mpv's force-media-title (macOS Now Playing)."""
    artist = getattr(getattr(track, "artist", None), "name", "") or ""
    title = getattr(track, "name", "") or ""
    if artist and title:
        return f"{artist} – {title}"
    return title or artist or "Unknown"


def _try_unlink_private_file(path: str, directory: str) -> None:
    """Remove only a regular file owned by this user in the expected directory."""
    try:
        candidate = os.path.abspath(path)
        if os.path.dirname(candidate) != os.path.abspath(directory):
            return
        st = os.lstat(candidate)
        if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
            os.unlink(candidate)
    except FileNotFoundError:
        return


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

_NAV_BASE = [
    ("library", "Library"),
    ("search", "Search"),
    ("favorites", "Favorites"),
    ("new-for-you", "New For You"),
    ("ride-the-tide", "Ride the Tide"),
    ("genre-radio", "Genre Radio"),
    ("listening-journey", "Listening Journey"),
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


_MAX_SAVED_QUEUE_TRACKS = 10_000


def _parse_saved_queue(data: dict) -> tuple[list[_SavedTrack], int]:
    """Validate private queue state without resolving every item over the network."""
    raw = data.get("tracks")
    placeholders: list[_SavedTrack] = []
    if raw is not None:
        if not isinstance(raw, list) or len(raw) > _MAX_SAVED_QUEUE_TRACKS:
            raise ValueError("saved queue tracks are invalid")
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("saved queue track is invalid")
            track_id = item.get("id")
            if isinstance(track_id, str) and track_id.isascii() and track_id.isdigit():
                track_id = int(track_id)
            if (
                not isinstance(track_id, int)
                or isinstance(track_id, bool)
                or track_id <= 0
            ):
                continue
            fields = [item.get(key, "") for key in ("name", "artist", "album")]
            if not all(isinstance(value, str) for value in fields):
                raise ValueError("saved queue metadata is invalid")
            art_url = item.get("art_url")
            if art_url is not None and not isinstance(art_url, str):
                raise ValueError("saved queue artwork is invalid")
            placeholders.append(
                _SavedTrack(track_id, fields[0], fields[1], fields[2], art_url)
            )
    else:
        track_ids = data.get("track_ids", [])
        if not isinstance(track_ids, list) or len(track_ids) > _MAX_SAVED_QUEUE_TRACKS:
            raise ValueError("saved queue track IDs are invalid")
        for track_id in track_ids:
            if isinstance(track_id, str) and track_id.isascii() and track_id.isdigit():
                track_id = int(track_id)
            if (
                isinstance(track_id, int)
                and not isinstance(track_id, bool)
                and track_id > 0
            ):
                placeholders.append(
                    _SavedTrack(track_id, f"Track {track_id}", "", "", None)
                )

    saved_idx = data.get("current_idx", 0)
    if (
        not isinstance(saved_idx, int)
        or isinstance(saved_idx, bool)
        or not 0 <= saved_idx < len(placeholders)
    ):
        saved_idx = 0
    return placeholders, saved_idx


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class LowTideApp(App):
    CSS = """
    App {
        background: ansi_default;
    }
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
        Binding("y", "toggle_lyrics", "Lyrics", show=False),
        Binding("escape", "go_back", "Back", show=False),
        Binding("ctrl+s", "focus_search", "Search"),
        Binding("ctrl+l", "focus_library", "Library"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, client: TidalClient):
        super().__init__()
        self.client = client
        cfg = client.config
        self.player = Player(config=cfg, runtime=client.runtime)
        self.mpris = MPRISService(self)
        self.scrobbler = Scrobbler(cfg)
        from lowtide.tidal_client import CONF_DIR
        from lowtide.recommender import Recommender
        from lowtide.scrobble_store import ScrobbleStore
        store_path = os.path.join(CONF_DIR, "playcounts.json")
        self._play_count_store = PlayCountStore(store_path)
        self._scrobble_store = ScrobbleStore(os.path.join(CONF_DIR, "scrobbles.json"))
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
        self._background = cfg.get("background", "ansi_default")
        self._nav = list(_NAV_BASE)
        if self._local_library:
            self._nav.append(("local", "Local"))
        self._queue: list = []
        self._current_idx: int = -1
        self._queue_gen: int = 0  # incremented on each new enqueue to cancel stale workers
        self._load_gen: int = 0   # incremented on each new URL-loading operation
        self._mpv_loaded_count: int = 0  # high-water mark: next queue index to attempt loading
        # Maps mpv playlist position -> queue index. Tracks whose URL failed to
        # load are never appended to mpv, so mpv positions do not line up 1:1 with
        # queue indices; this list is the source of truth for that mapping.
        self._mpv_playlist: list[int] = []
        self._extending_lookahead: bool = False
        self._current_track = None
        self._current_favourited: bool = False
        self._target_volume: int = 80
        self._crossfading: bool = False
        self._ride_the_tide_cache: tuple[list, str | None] | None = None

        # macOS: push track title to mpv's Now Playing widget
        self._macos_now_playing = sys.platform == "darwin"
        self._macos_art_temp_path: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield Sidebar(nav=self._nav)
            yield ContentArea()
            yield QueuePanel()
        yield NowPlayingBar()

    async def on_mount(self) -> None:
        from textual.color import Color
        self.styles.background = Color.parse(self._background)
        try:
            await self.player.start()
        except RuntimeError as e:
            self.notify(str(e), severity="error", timeout=10)
            return
        await self.mpris.start()
        self.mpris.update_volume(self.player.volume)
        eq = self.query_one(EQVisualizer)
        eq.set_theme(self._eq_theme)
        eq._show_labels = self._eq_labels
        self._target_volume = self.player.volume
        if self.player.crossfade_secs > 0:
            self.query_one(NowPlayingBar).crossfade = True
        self.player.on_track_start.append(self._on_mpv_track_start)
        self.player.on_track_end.append(self._on_mpv_track_end)
        self.set_interval(1.0, self._poll_player)
        self._restore_queue()
        self._sync_lastfm_counts()

    # --- Player polling ---

    async def _poll_player(self) -> None:
        bar = self.query_one(NowPlayingBar)
        position = await self.player.get_position()
        duration = await self.player.get_duration()
        paused = await self.player.get_paused()
        stopped = bool(await self.player.get_property("idle-active"))
        bar.position = position
        bar.duration = duration
        bar.paused = paused or stopped
        bar.volume = self._target_volume
        self.query_one(EQVisualizer).paused = paused or stopped
        self.mpris.update_position(position)
        self.mpris.update_playback_status(paused, stopped=stopped)
        self.scrobbler.update(position, duration)

        # Crossfade: fade out as current track approaches its end
        if self.player.crossfade_secs > 0 and duration > 0 and not paused:
            remaining = duration - position
            if 0 < remaining <= self.player.crossfade_secs:
                fade_vol = max(0, int((remaining / self.player.crossfade_secs) * self._target_volume))
                await self.player._cmd(["set_property", "volume", fade_vol])
                self._crossfading = True

    async def _on_mpv_track_end(self) -> None:
        """Called when a track ends. Handles queue exhaustion in weighted shuffle modes."""
        mode = self.player.shuffle_mode
        if mode not in (SHUFFLE_FAVOURITE, SHUFFLE_DISCOVERY):
            return
        if not self._queue or self._current_idx < 0:
            return
        # Only act when all queue tracks have been played.
        if self._current_idx + 1 < len(self._queue):
            return
        remaining = [t for i, t in enumerate(self._queue) if i != self._current_idx]
        if not remaining:
            return
        picks = self._play_count_store.weighted_shuffle(
            remaining, favourite=(mode == SHUFFLE_FAVOURITE)
        )
        if picks:
            new_idx = self._queue.index(picks[0])
            self.enqueue_and_play(self._queue, start_index=new_idx)
            self.notify("Wrapping queue")

    async def _on_mpv_track_start(self) -> None:
        # Restore volume after a crossfade fade-out
        if self._crossfading:
            self._crossfading = False
            await self.player.set_volume(self._target_volume)

        pos = await self.player.get_playlist_pos()
        if 0 <= pos < len(self._mpv_playlist):
            qidx = self._mpv_playlist[pos]
            self._current_idx = qidx
            track = self._queue[qidx]
            self._set_current_track(track)
            self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
            self.scrobbler.track_started(track)
            self._load_track_extras(track)
            if self._macos_now_playing:
                await self._set_mpv_title(_now_playing_title(track))
                await self._set_macos_album_art(track)
            self._maybe_extend_lookahead()

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
        self._load_gen += 1
        self._mpv_loaded_count = 0
        self._mpv_playlist = []
        self._extending_lookahead = False
        asyncio.ensure_future(self.player.stop())
        self._load_window(0, self._queue_gen, self._load_gen)

    def append_to_queue(self, tracks: list) -> None:
        if not self._queue:
            # Nothing playing yet – just start playback
            self.enqueue_and_play(tracks)
            return
        self._queue.extend(tracks)
        self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
        # The windowed loader will pick up newly added tracks when it extends;
        # trigger an immediate extension in case they fall in the lookahead window.
        self._maybe_extend_lookahead()
        count = len(tracks)
        label = tracks[0].name if count == 1 else f"{count} tracks"
        self.notify(f"Added {label} to queue")

    @work(thread=True)
    def _load_window(self, start_idx: int, queue_gen: int, load_gen: int) -> None:
        """Load up to _LOOKAHEAD track URLs into mpv starting at start_idx; the rest
        are fetched on demand. mpv's playlist is assumed empty (freshly stopped), so
        the first track that loads successfully replaces, and the rest append."""
        end = min(start_idx + _LOOKAHEAD, len(self._queue))
        first = True
        for i in range(start_idx, end):
            if self._queue_gen != queue_gen or self._load_gen != load_gen:
                return
            track = self._resolve_saved_track(i, queue_gen, load_gen)
            if track is None:
                continue
            url = self.client.get_track_url(track)
            if not url or self._queue_gen != queue_gen or self._load_gen != load_gen:
                continue
            if first:
                self.call_from_thread(self._play_if_current, url, queue_gen, track, i)
                first = False
            else:
                self.call_from_thread(self._append_if_current, url, queue_gen, i)
        if self._queue_gen == queue_gen and self._load_gen == load_gen:
            if first and end > start_idx:
                self.call_from_thread(
                    self.notify,
                    "Couldn't load any tracks to play. Check your connection or "
                    "lower the quality setting in config.json.",
                    severity="error",
                )
            self.call_from_thread(self._mark_initial_loaded, queue_gen, load_gen, end)
            self.call_from_thread(
                lambda: self.query_one(QueuePanel).refresh_queue(self._queue, self._current_idx)
            )

    def _resolve_saved_track(
        self, index: int, queue_gen: int, load_gen: int
    ):
        track = self._queue[index]
        if not isinstance(track, _SavedTrack):
            return track
        try:
            resolved = self.client.get_track(track.id)
        except Exception as error:
            log.warning(
                "saved queue track could not be restored (%s)",
                type(error).__name__,
            )
            return None
        if self._queue_gen != queue_gen or self._load_gen != load_gen:
            return None
        self.call_from_thread(
            self._replace_saved_track,
            index,
            track,
            resolved,
            queue_gen,
            load_gen,
        )
        return resolved

    def _replace_saved_track(
        self,
        index: int,
        placeholder: _SavedTrack,
        resolved,
        queue_gen: int,
        load_gen: int,
    ) -> None:
        if (
            self._queue_gen == queue_gen
            and self._load_gen == load_gen
            and 0 <= index < len(self._queue)
            and self._queue[index] is placeholder
        ):
            self._queue[index] = resolved

    def _mark_initial_loaded(self, queue_gen: int, load_gen: int, count: int) -> None:
        if self._queue_gen == queue_gen and self._load_gen == load_gen:
            self._mpv_loaded_count = count
            self._extending_lookahead = False
            self._maybe_extend_lookahead()

    def _maybe_extend_lookahead(self) -> None:
        """If the lookahead window doesn't reach far enough, fetch the next URL."""
        if self._extending_lookahead:
            return
        target = min(self._current_idx + _LOOKAHEAD, len(self._queue))
        if self._mpv_loaded_count < target:
            start = self._mpv_loaded_count
            self._extending_lookahead = True
            self._load_gen += 1
            self._extend_lookahead(self._queue_gen, self._load_gen, start, target)

    @work(thread=True)
    def _extend_lookahead(
        self, queue_gen: int, load_gen: int, start: int, end: int
    ) -> None:
        """Fetch URLs for queue[start:end] and append them to mpv."""
        for i in range(start, end):
            if self._queue_gen != queue_gen or self._load_gen != load_gen:
                self.call_from_thread(self._on_lookahead_cancelled, load_gen)
                return
            track = self._resolve_saved_track(i, queue_gen, load_gen)
            if track is None:
                self.call_from_thread(
                    self._advance_loaded_count, queue_gen, load_gen, i + 1
                )
                continue
            url = self.client.get_track_url(track)
            if self._queue_gen != queue_gen or self._load_gen != load_gen:
                self.call_from_thread(self._on_lookahead_cancelled, load_gen)
                return
            if url:
                self.call_from_thread(self._on_url_appended, queue_gen, load_gen, i + 1, url, i)
            else:
                # Skip failed URLs but still advance the loaded count
                self.call_from_thread(self._advance_loaded_count, queue_gen, load_gen, i + 1)
        self.call_from_thread(self._on_lookahead_done, queue_gen, load_gen)

    def _on_url_appended(
        self, queue_gen: int, load_gen: int, new_count: int, url: str, qidx: int
    ) -> None:
        if self._queue_gen == queue_gen and self._load_gen == load_gen:
            asyncio.ensure_future(self.player.append(url))
            self._mpv_playlist.append(qidx)
            self._mpv_loaded_count = new_count

    def _advance_loaded_count(
        self, queue_gen: int, load_gen: int, new_count: int
    ) -> None:
        if self._queue_gen == queue_gen and self._load_gen == load_gen:
            self._mpv_loaded_count = new_count

    def _on_lookahead_done(self, queue_gen: int, load_gen: int) -> None:
        if self._queue_gen == queue_gen and self._load_gen == load_gen:
            self._extending_lookahead = False
            self._maybe_extend_lookahead()

    def _on_lookahead_cancelled(self, load_gen: int) -> None:
        if self._load_gen == load_gen:
            self._extending_lookahead = False

    def _play_if_current(self, url: str, gen: int, track, qidx: int) -> None:
        if self._queue_gen != gen:
            return
        asyncio.ensure_future(self.player.play(url))
        self._mpv_playlist = [qidx]
        self._set_current_track(track)

    def _append_if_current(self, url: str, gen: int, qidx: int) -> None:
        if self._queue_gen == gen:
            asyncio.ensure_future(self.player.append(url))
            self._mpv_playlist.append(qidx)

    def on_track_list_track_append_requested(self, event) -> None:
        self.append_to_queue([event.track])

    async def jump_to_queue_index(self, idx: int) -> None:
        """Skip playback to a specific queue position."""
        if not (0 <= idx < len(self._queue)):
            return
        if idx in self._mpv_playlist:
            # Already buffered in mpv, so jump to its actual playlist position.
            await self.player._cmd(
                ["set_property", "playlist-pos", self._mpv_playlist.index(idx)]
            )
            # If mpv went idle after the playlist ended, the above restores the
            # position but leaves it paused — ensure playback actually starts.
            await self.player._cmd(["set_property", "pause", False])
        else:
            # Not in mpv's playlist (beyond the lookahead window, or its URL
            # failed to load earlier). Rebuild the window starting from here.
            self._play_from_index(idx)

    def _play_from_index(self, idx: int) -> None:
        """Stop and rebuild mpv's playlist starting at queue index idx, then play.
        Preserves queue order and display; used for jumps outside the loaded window."""
        self._current_idx = idx
        self._queue_gen += 1
        self._load_gen += 1
        self._mpv_loaded_count = idx
        self._mpv_playlist = []
        self._extending_lookahead = False
        asyncio.ensure_future(self.player.stop())
        self._load_window(idx, self._queue_gen, self._load_gen)

    # --- Open objects (albums, artists, mixes, playlists) by type ---

    async def open_object(self, obj) -> None:
        """Route any tidalapi object to the appropriate screen."""
        from lowtide.screens.album import AlbumScreen
        from lowtide.screens.artist import ArtistScreen
        from lowtide.screens.playlist import PlaylistScreen

        type_name = type(obj).__name__.lower()

        if "album" in type_name:
            await self.push_view(AlbumScreen(obj))
        elif "artist" in type_name:
            await self.push_view(ArtistScreen(obj))
        elif "playlist" in type_name or "userplaylist" in type_name:
            await self.push_view(PlaylistScreen(obj))
        elif "mix" in type_name:
            await self.push_view(PlaylistScreen(obj, autoplay=True))
        else:
            await self.push_view(PlaylistScreen(obj))

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
            elif key == "new-for-you":
                await self._open_new_for_you()
            elif key == "ride-the-tide":
                await self._open_ride_the_tide()
            elif key == "genre-radio":
                await self._open_genre_radio()
            elif key == "listening-journey":
                await self._open_journey()
            elif key == "local":
                await self._open_local()

    async def _open_journey(self) -> None:
        from lowtide.screens.journey import JourneyScreen
        await self._switch_root(JourneyScreen(), "listening-journey")

    async def _open_favorites(self) -> None:
        from lowtide.screens.favorites import FavoritesScreen
        await self._switch_root(FavoritesScreen(), "favorites")

    async def _open_new_for_you(self) -> None:
        from lowtide.screens.new_for_you import NewForYouScreen
        await self._switch_root(NewForYouScreen(), "new-for-you")

    async def _open_ride_the_tide(self) -> None:
        from lowtide.screens.radio import RadioScreen
        await self._switch_root(RadioScreen(cached=self._ride_the_tide_cache), "ride-the-tide")

    async def on_track_list_track_radio_requested(self, event) -> None:
        from lowtide.screens.radio import RadioScreen
        await self.push_view(RadioScreen(seed_track=event.track))

    async def _open_genre_radio(self) -> None:
        from lowtide.screens.genre import GenreScreen
        await self._switch_root(GenreScreen(), "genre-radio")

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

    async def _set_mpv_title(self, title: str) -> None:
        """Set force-media-title via mpv IPC (macOS Now Playing widget)."""
        await self.player._cmd(["set_property", "force-media-title", title])

    async def _set_macos_album_art(self, track) -> None:
        """Download album art and add as external video track for macOS Now Playing.

        mpv v0.41.0+ detects external image tracks in the track list and
        populates MPMediaItemPropertyArtwork in MPNowPlayingInfoCenter.
        """
        if not self._macos_now_playing:
            return

        try:
            art_url = track.album.image(320)
        except Exception:
            return
        if not art_url:
            return

        path = await asyncio.get_running_loop().run_in_executor(
            None, self._download_album_art, art_url,
        )
        if not path:
            return

        # Track may have advanced while we were downloading
        if self._current_track is not track:
            _try_unlink_private_file(path, self.client.runtime_dir)
            return

        # Clean up previous temp file
        self._cleanup_macos_art_temp()
        self._macos_art_temp_path = path

        # Add the cover as an external video track. mpv's macOS backend
        # detects external image tracks in the track list and forwards
        # them to MPNowPlayingInfoCenter.
        #
        # Suppress the floating window BEFORE adding the track: on macOS
        # video-add creates a visible window despite --no-video; setting
        # vid=no first prevents it from ever appearing.
        # (Also see --vo=null in player.py, which prevents window creation
        # at the VO level regardless.)
        await self.player._cmd(["set_property", "vid", "no"])
        await self.player._cmd(["video-add", path])

    def _download_album_art(self, url: str) -> str | None:
        """Return local path to downloaded album art, or None."""
        import tempfile

        from lowtide.artwork import load_album_art

        try:
            image = load_album_art(url)
            fd, path = tempfile.mkstemp(
                suffix=".jpg",
                prefix="art-",
                dir=self.client.runtime_dir,
            )
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                image.save(f, format="JPEG", quality=90)
                f.flush()
                os.fsync(f.fileno())
            return path
        except (OSError, ValueError):
            return None

    def _cleanup_macos_art_temp(self) -> None:
        if self._macos_art_temp_path:
            _try_unlink_private_file(
                self._macos_art_temp_path,
                self.client.runtime_dir,
            )
            self._macos_art_temp_path = None

    async def action_next_track(self) -> None:
        mode = self.player.shuffle_mode
        if mode in (SHUFFLE_FAVOURITE, SHUFFLE_DISCOVERY) and self._current_idx >= 0 and self._queue:
            remaining = self._queue[self._current_idx + 1:]
            if not remaining:
                remaining = [t for i, t in enumerate(self._queue) if i != self._current_idx]
                self.notify("Wrapping queue")
            if remaining:
                picks = self._play_count_store.weighted_shuffle(
                    remaining, favourite=(mode == SHUFFLE_FAVOURITE)
                )
                if picks:
                    new_idx = self._queue.index(picks[0])
                    # jump_to_queue_index maps through _mpv_playlist, so it works
                    # whether or not the picked track is already buffered in mpv.
                    await self.jump_to_queue_index(new_idx)
                    return
        await self.player.next()

    async def action_prev_track(self) -> None:
        await self.player.prev()

    async def action_volume_up(self) -> None:
        await self.set_volume(min(100, self._target_volume + 5))

    async def action_volume_down(self) -> None:
        await self.set_volume(max(0, self._target_volume - 5))

    async def set_volume(self, level: int) -> None:
        self._target_volume = max(0, min(100, level))
        await self.player.set_volume(self._target_volume)
        self.mpris.update_volume(self._target_volume)

    async def action_toggle_queue(self) -> None:
        panel = self.query_one(QueuePanel)
        panel.display = not panel.display

    async def action_toggle_eq(self) -> None:
        self.query_one(NowPlayingBar).toggle_eq()

    def action_toggle_lyrics(self) -> None:
        self.query_one(NowPlayingBar).toggle_lyrics()

    def _save_queue(self) -> None:
        from lowtide.tidal_client import CONF_DIR
        from lowtide.secure_io import atomic_write_json
        if not self._queue:
            return
        try:
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
            atomic_write_json(path, data)
        except (OSError, TypeError, ValueError) as error:
            log.warning("queue save failed (%s)", type(error).__name__)

    @work(thread=True)
    def _restore_queue(self) -> None:
        from lowtide.tidal_client import CONF_DIR
        from lowtide.secure_io import read_json
        path = os.path.join(CONF_DIR, "queue.json")
        try:
            data = read_json(path, default=None)
            if data is None:
                return
            if not isinstance(data, dict):
                raise ValueError("queue state is not an object")
        except (OSError, TypeError, ValueError) as error:
            log.warning("queue restore failed (%s)", type(error).__name__)
            return

        try:
            placeholders, saved_idx = _parse_saved_queue(data)
        except (TypeError, ValueError) as error:
            log.warning("queue restore failed (%s)", type(error).__name__)
            return
        if not placeholders:
            return

        # Show the queue immediately from saved metadata — no TIDAL calls yet
        self.call_from_thread(self._show_restored_ui, placeholders, saved_idx)
        # Resolve only the bounded playback lookahead. Remaining placeholders
        # are fetched lazily if playback reaches or jumps to them.
        self.call_from_thread(self._apply_restored_queue, placeholders, saved_idx)

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
            except Exception as error:
                log.debug(
                    "saved queue artwork unavailable (%s)",
                    type(error).__name__,
                )

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
        self._cleanup_macos_art_temp()
        self.client._clear_manifests()
        await self.mpris.stop()
        await self.player.shutdown()
        self.client.close()
