from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label

from lowtide.widgets.track_list import TrackList


class PlaylistScreen(Widget):
    DEFAULT_CSS = """
    PlaylistScreen {
        height: 1fr;
        padding: 1 2;
    }
    PlaylistScreen #pl-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    PlaylistScreen #pl-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def __init__(self, playlist, **kwargs):
        super().__init__(**kwargs)
        self._playlist = playlist

    def compose(self) -> ComposeResult:
        name = getattr(self._playlist, "name", "Playlist")
        yield Label(name, id="pl-heading")
        yield Label("Loading…", id="pl-status")
        yield TrackList(id="pl-tracks")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        try:
            tracks = self.app.client.get_playlist_tracks(self._playlist)
            self.app.call_from_thread(self._populate, tracks)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#pl-status", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate(self, tracks: list) -> None:
        self.query_one("#pl-status", Label).update(
            f"[dim]{len(tracks)} tracks[/dim]"
        )
        self.query_one(TrackList).load(tracks)

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        all_tracks = self.query_one(TrackList).tracks
        self.app.enqueue_and_play(all_tracks, start_index=event.index)
