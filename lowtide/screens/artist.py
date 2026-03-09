from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

from lowtide.widgets.album_art import AlbumArt


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
    ArtistScreen #albums-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    ArtistScreen ListView {
        height: 1fr;
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
        yield Label("Loading albums…", id="artist-status")
        yield Label("Albums", id="albums-heading")
        yield ListView(id="album-list")

    def on_mount(self) -> None:
        try:
            art_url = self._artist.image(320)
        except Exception:
            art_url = None
        self.query_one(AlbumArt).load(art_url)
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        try:
            albums = self.app.client.get_artist_albums(self._artist)
            self.app.call_from_thread(self._populate, albums)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#artist-status", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate(self, albums: list) -> None:
        self.query_one("#artist-status", Label).update("")
        lv = self.query_one("#album-list", ListView)
        lv.clear()
        for album in albums:
            name = getattr(album, "name", "?")
            year = getattr(album, "year", "")
            n = getattr(album, "num_tracks", getattr(album, "numberOfTracks", ""))
            suffix = f"[dim]{year}  •  {n} tracks[/dim]" if year else f"[dim]{n} tracks[/dim]"
            item = ListItem(Label(f"{name}  {suffix}"))
            item._album = album
            lv.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        album = getattr(event.item, "_album", None)
        if album:
            from lowtide.screens.album import AlbumScreen
            asyncio.ensure_future(self.app.push_view(AlbumScreen(album)))
