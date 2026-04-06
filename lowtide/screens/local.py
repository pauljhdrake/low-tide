from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from lowtide.local_library import LocalLibrary
from lowtide.local_track import LocalAlbum
from lowtide.widgets.track_list import TrackList


class LocalLibraryScreen(Widget):
    DEFAULT_CSS = """
    LocalLibraryScreen {
        height: 1fr;
        padding: 1 2;
    }
    LocalLibraryScreen #local-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    LocalLibraryScreen #local-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    LocalLibraryScreen ListView {
        height: 1fr;
        background: transparent;
    }
    LocalLibraryScreen ListItem {
        background: transparent;
    }
    """

    def __init__(self, library: LocalLibrary, **kwargs):
        super().__init__(**kwargs)
        self._library = library

    def compose(self) -> ComposeResult:
        yield Label("Local Library", id="local-heading")
        yield Label("Loading…", id="local-status")
        yield ListView(id="local-artists")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        def on_progress(fname: str) -> None:
            self.app.call_from_thread(
                self.query_one("#local-status", Label).update,
                f"[dim]Scanning – {fname}[/dim]",
            )
        self._library.load(progress_cb=on_progress)
        self.app.call_from_thread(self._populate)

    def _populate(self) -> None:
        artists = self._library.artists()
        count = len(self._library.tracks)
        self.query_one("#local-status", Label).update(
            f"[dim]{len(artists)} artists  ·  {count} tracks[/dim]"
        )
        lv = self.query_one("#local-artists", ListView)
        lv.clear()
        for name in artists:
            item = ListItem(Label(name))
            item._artist_name = name
            lv.append(item)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        artist_name = getattr(event.item, "_artist_name", None)
        if artist_name:
            albums = self._library.albums_for_artist(artist_name)
            await self.app.push_view(LocalArtistScreen(self._library, artist_name, albums))


class LocalArtistScreen(Widget):
    DEFAULT_CSS = """
    LocalArtistScreen {
        height: 1fr;
        padding: 1 2;
    }
    LocalArtistScreen #local-artist-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    LocalArtistScreen ListView {
        height: 1fr;
        background: transparent;
    }
    LocalArtistScreen ListItem {
        background: transparent;
    }
    """

    def __init__(self, library: LocalLibrary, artist_name: str, albums: list[LocalAlbum], **kwargs):
        super().__init__(**kwargs)
        self._library = library
        self._artist_name = artist_name
        self._albums = albums

    def compose(self) -> ComposeResult:
        yield Label(self._artist_name, id="local-artist-heading")
        with ListView(id="local-albums"):
            for album in self._albums:
                year = f"  [dim]{album.year}[/dim]" if album.year else ""
                item = ListItem(Label(f"{album.name}{year}"))
                item._album = album
                yield item

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        album = getattr(event.item, "_album", None)
        if album:
            tracks = self._library.tracks_for_album(self._artist_name, album.name)
            await self.app.push_view(LocalAlbumScreen(album, tracks))


class LocalAlbumScreen(Widget):
    BINDINGS = [Binding("A", "append_all", "Add all to queue", show=False)]

    DEFAULT_CSS = """
    LocalAlbumScreen {
        height: 1fr;
        padding: 1 2;
    }
    LocalAlbumScreen #local-album-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    LocalAlbumScreen #local-album-sub {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def __init__(self, album: LocalAlbum, tracks: list, **kwargs):
        super().__init__(**kwargs)
        self._album = album
        self._tracks = tracks

    def compose(self) -> ComposeResult:
        meta = self._album.artist.name
        if self._album.year:
            meta += f"  ·  {self._album.year}"
        meta += f"  ·  {len(self._tracks)} tracks"
        yield Label(self._album.name, id="local-album-heading")
        yield Label(meta, id="local-album-sub")
        yield TrackList(id="local-tracks")

    def on_mount(self) -> None:
        self.query_one(TrackList).load(self._tracks)

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        self.app.enqueue_and_play(self._tracks, start_index=event.index)

    def on_track_list_track_append_requested(self, event: TrackList.TrackAppendRequested) -> None:
        self.app.append_to_queue([event.track])

    def action_append_all(self) -> None:
        if self._tracks:
            self.app.append_to_queue(self._tracks)
