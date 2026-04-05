from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label

from lowtide.widgets.track_list import TrackList


class FavoritesScreen(Widget):
    BINDINGS = [Binding("A", "append_all", "Add all to queue", show=False)]

    DEFAULT_CSS = """
    FavoritesScreen {
        height: 1fr;
        padding: 1 2;
    }
    FavoritesScreen #fav-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    FavoritesScreen #fav-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Favorites", id="fav-heading")
        yield Label("Loading…", id="fav-status")
        yield TrackList(id="fav-tracks")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        try:
            tracks = self.app.client.get_favorite_tracks()
            self.app.call_from_thread(self._populate, tracks)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#fav-status", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate(self, tracks: list) -> None:
        self.query_one("#fav-status", Label).update(
            f"[dim]{len(tracks)} saved tracks[/dim]"
        )
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
