from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label

from lowtide.widgets.album_art import AlbumArt
from lowtide.widgets.track_list import TrackList


class AlbumScreen(Widget):
    BINDINGS = [Binding("A", "append_all", "Add all to queue", show=False)]

    DEFAULT_CSS = """
    AlbumScreen {
        height: 1fr;
        padding: 1 2;
    }
    AlbumScreen #album-header {
        layout: horizontal;
        height: auto;
        margin-bottom: 1;
    }
    AlbumScreen AlbumArt {
        width: 18;
        height: 9;
        margin-right: 2;
    }
    AlbumScreen #album-meta {
        height: auto;
        padding-top: 1;
    }
    AlbumScreen #album-title {
        text-style: bold;
    }
    AlbumScreen #album-artist {
        color: $text-muted;
    }
    AlbumScreen #album-info {
        color: $text-muted;
    }
    AlbumScreen #album-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def __init__(self, album, **kwargs):
        super().__init__(**kwargs)
        self._album = album

    def compose(self) -> ComposeResult:
        title = getattr(self._album, "name", "Album")
        artist = getattr(getattr(self._album, "artist", None), "name", "")
        year = getattr(self._album, "year", "")
        n = getattr(self._album, "num_tracks", getattr(self._album, "numberOfTracks", ""))

        with Horizontal(id="album-header"):
            yield AlbumArt(id="album-art")
            with Vertical(id="album-meta"):
                yield Label(title, id="album-title")
                yield Label(artist, id="album-artist")
                yield Label(f"{year}  •  {n} tracks" if year else f"{n} tracks", id="album-info")
        yield Label("Loading…", id="album-status")
        yield TrackList(id="album-tracks")

    def on_mount(self) -> None:
        try:
            art_url = self._album.image(320)
        except Exception:
            art_url = None
        self.query_one(AlbumArt).load(art_url)
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        try:
            tracks = self.app.client.get_album_tracks(self._album)
            self.app.call_from_thread(self._populate, tracks)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#album-status", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate(self, tracks: list) -> None:
        self.query_one("#album-status", Label).update("")
        self.query_one(TrackList).load(tracks)

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        self.app.enqueue_and_play(self.query_one(TrackList).tracks, start_index=event.index)

    def on_track_list_track_append_requested(self, event: TrackList.TrackAppendRequested) -> None:
        self.app.append_to_queue([event.track])

    def action_append_all(self) -> None:
        tracks = self.query_one(TrackList).tracks
        if tracks:
            self.app.append_to_queue(tracks)
