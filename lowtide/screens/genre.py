from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView

from lowtide.recommender import DiscoveryMode
from lowtide.widgets.track_list import TrackList

log = logging.getLogger(__name__)

_MODE_LABELS = {
    DiscoveryMode.ESSENTIAL:   "Essential",
    DiscoveryMode.BALANCED:    "Balanced",
    DiscoveryMode.ADVENTUROUS: "Adventurous",
}


class GenreScreen(Widget):
    BINDINGS = [
        Binding("A", "append_all", "Add all to queue", show=False),
        Binding("D", "cycle_dial", "Discovery dial", show=True),
    ]

    DEFAULT_CSS = """
    GenreScreen {
        height: 1fr;
        padding: 1 2;
    }
    GenreScreen #genre-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    GenreScreen #genre-input {
        margin-bottom: 1;
    }
    GenreScreen #genre-genres {
        height: auto;
        max-height: 7;
        background: transparent;
        margin-bottom: 1;
    }
    GenreScreen #genre-genres ListItem {
        padding: 0 1;
    }
    GenreScreen #genre-dial {
        margin-bottom: 1;
    }
    GenreScreen #genre-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    GenreScreen #genre-nudge {
        color: $warning;
        margin-bottom: 1;
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode = DiscoveryMode.BALANCED
        self._active_tag: str | None = None

    def compose(self) -> ComposeResult:
        yield Label("Genre Radio", id="genre-heading")
        yield Input(placeholder="Last.fm tag, e.g. shoegaze, drum and bass…", id="genre-input")
        yield ListView(id="genre-genres")
        yield Label(self._dial_label(), id="genre-dial")
        yield Label("", id="genre-status")
        yield Label("", id="genre-nudge")
        yield TrackList(id="genre-tracks")

    def _dial_label(self) -> str:
        parts = []
        for mode, name in _MODE_LABELS.items():
            parts.append(f"[bold]{name}[/bold]" if mode == self._mode else f"[dim]{name}[/dim]")
        return "  ".join(parts) + "  [dim](D)[/dim]"

    def action_cycle_dial(self) -> None:
        self._mode = DiscoveryMode((self._mode + 1) % len(DiscoveryMode))
        self.query_one("#genre-dial", Label).update(self._dial_label())
        if self._active_tag:
            self._load_lastfm_tag(self._active_tag)

    def on_mount(self) -> None:
        self._load_tidal_genres()

    @work(thread=True)
    def _load_tidal_genres(self) -> None:
        self.app.call_from_thread(
            self.query_one("#genre-status", Label).update, "[dim]Loading TIDAL genres…[/dim]"
        )
        try:
            genres = self.app.client.get_genres()
            self.app.call_from_thread(self._populate_genre_list, genres)
        except Exception as e:
            log.debug("genre screen: get_genres failed: %s", e)
            self.app.call_from_thread(
                self.query_one("#genre-status", Label).update, "[dim]Type a tag above to search[/dim]"
            )

    def _populate_genre_list(self, genres: list) -> None:
        lv = self.query_one("#genre-genres", ListView)
        lv.clear()
        for g in genres:
            if g.tracks:
                item = ListItem(Label(g.name))
                item._tidal_genre = g
                lv.append(item)
        self.query_one("#genre-status", Label).update(
            "[dim]Select a TIDAL genre, or type a tag above[/dim]"
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        tag = event.value.strip()
        if tag:
            self._active_tag = tag
            self._load_lastfm_tag(tag)

    @work(thread=True)
    def _load_lastfm_tag(self, tag: str) -> None:
        mode = self._mode
        self.app.call_from_thread(
            self.query_one("#genre-status", Label).update, "[dim]Building…[/dim]"
        )
        try:
            tracks, nudge = self.app.recommender.build_genre_playlist(tag, mode=mode)
            self.app.call_from_thread(
                self._populate_tracks, tracks, nudge, f"Tag: {tag}"
            )
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.query_one("#genre-status", Label).update(f"[red]Error: {e}[/red]")
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        genre = getattr(event.item, "_tidal_genre", None)
        if genre:
            self._load_tidal_genre(genre)

    @work(thread=True)
    def _load_tidal_genre(self, genre) -> None:
        self.app.call_from_thread(
            self.query_one("#genre-status", Label).update, "[dim]Loading…[/dim]"
        )
        try:
            tracks = self.app.client.get_genre_tracks(genre)
            self.app.call_from_thread(
                self._populate_tracks, tracks, None, f"TIDAL: {genre.name}"
            )
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.query_one("#genre-status", Label).update(f"[red]Error: {e}[/red]")
            )

    def _populate_tracks(self, tracks: list, nudge: str | None, heading: str) -> None:
        self.query_one("#genre-heading", Label).update(heading)
        self.query_one("#genre-status", Label).update(
            f"[dim]{len(tracks)} tracks[/dim]" if tracks else "[dim]No results found[/dim]"
        )
        nudge_label = self.query_one("#genre-nudge", Label)
        if nudge:
            nudge_label.update(f"[yellow]⚡ {nudge}[/yellow]")
            nudge_label.display = True
        else:
            nudge_label.display = False
        self.query_one(TrackList).load(tracks)

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        all_tracks = self.query_one(TrackList).tracks
        self.app.enqueue_and_play(all_tracks, start_index=event.index)

    def on_track_list_track_append_requested(self, event: TrackList.TrackAppendRequested) -> None:
        self.app.append_to_queue([event.track])

    def action_append_all(self) -> None:
        tracks = self.query_one(TrackList).tracks
        if tracks:
            self.app.append_to_queue(tracks)
