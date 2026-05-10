from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, TabPane, TabbedContent

from lowtide.widgets.album_art import AlbumArt
from lowtide.widgets.track_list import TrackList


class ArtistScreen(Widget):
    DEFAULT_CSS = """
    ArtistScreen {
        height: 1fr;
        padding: 1 2;
    }
    ArtistScreen #artist-header {
        layout: horizontal;
        height: auto;
        margin-bottom: 1;
    }
    ArtistScreen AlbumArt {
        width: 18;
        height: 9;
        margin-right: 2;
    }
    ArtistScreen #artist-name {
        text-style: bold;
        padding-top: 1;
    }
    ArtistScreen #artist-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    ArtistScreen TabbedContent {
        height: 1fr;
    }
    ArtistScreen ListView {
        height: 1fr;
        background: transparent;
    }
    """

    def __init__(self, artist, **kwargs):
        super().__init__(**kwargs)
        self._artist = artist

    def compose(self) -> ComposeResult:
        name = getattr(self._artist, "name", "Artist")
        with Horizontal(id="artist-header"):
            yield AlbumArt(id="artist-art")
            with Vertical(id="artist-meta"):
                yield Label(name, id="artist-name")
        yield Label("", id="artist-status")
        with TabbedContent(id="artist-tabs"):
            with TabPane("Top Tracks", id="tab-top-tracks"):
                yield TrackList(id="top-tracks")
            with TabPane("Albums", id="tab-albums"):
                yield ListView(id="album-list")
            with TabPane("EPs & Singles", id="tab-eps"):
                yield ListView(id="ep-list")

    def on_mount(self) -> None:
        try:
            art_url = self._artist.image(320)
        except Exception:
            art_url = None
        self.query_one(AlbumArt).load(art_url)
        self._load_top_tracks()
        self._load_albums()
        self._load_eps()

    @work(thread=True)
    def _load_top_tracks(self) -> None:
        try:
            tracks = self.app.client.get_artist_top_tracks(self._artist)
            self.app.call_from_thread(self.query_one("#top-tracks", TrackList).load, tracks)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#artist-status", Label).update,
                f"[red]Error loading tracks: {e}[/red]",
            )

    @work(thread=True)
    def _load_albums(self) -> None:
        try:
            albums = self.app.client.get_artist_albums(self._artist)
            self.app.call_from_thread(self._populate_albums, "#album-list", albums)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#artist-status", Label).update,
                f"[red]Error loading albums: {e}[/red]",
            )

    @work(thread=True)
    def _load_eps(self) -> None:
        try:
            eps = self.app.client.get_artist_ep_singles(self._artist)
            self.app.call_from_thread(self._populate_albums, "#ep-list", eps)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#artist-status", Label).update,
                f"[red]Error loading EPs: {e}[/red]",
            )

    def _populate_albums(self, list_id: str, albums: list) -> None:
        lv = self.query_one(list_id, ListView)
        lv.clear()
        for album in albums:
            name = getattr(album, "name", "?")
            year = getattr(album, "year", "")
            n = getattr(album, "num_tracks", getattr(album, "numberOfTracks", ""))
            suffix = f"[dim]{year}  •  {n} tracks[/dim]" if year else f"[dim]{n} tracks[/dim]"
            item = ListItem(Label(f"{name}  {suffix}"))
            item._album = album
            lv.append(item)

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        self.app.enqueue_and_play(self.query_one(TrackList).tracks, start_index=event.index)

    def on_track_list_track_append_requested(self, event: TrackList.TrackAppendRequested) -> None:
        self.app.append_to_queue([event.track])

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        album = getattr(event.item, "_album", None)
        if album:
            from lowtide.screens.album import AlbumScreen
            await self.app.push_view(AlbumScreen(album))
