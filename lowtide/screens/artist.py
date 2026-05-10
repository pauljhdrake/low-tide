from __future__ import annotations

import re

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static, TabPane, TabbedContent

from lowtide.widgets.album_art import AlbumArt
from lowtide.widgets.track_list import TrackList


def _clean_bio(text: str) -> str:
    """Strip TIDAL's wimpLink markup and HTML tags from bio text."""
    # Remove [wimpLink ...] references TIDAL embeds in bio text
    text = re.sub(r"\[wimpLink[^\]]*\]", "", text)
    text = re.sub(r"\[/wimpLink\]", "", text)
    # Strip any remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse multiple blank lines to one
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


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
    ArtistScreen #artist-meta {
        height: auto;
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
    ArtistScreen #bio-scroll {
        height: 1fr;
        padding: 0 1;
    }
    ArtistScreen #bio-text {
        color: $text;
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
            with TabPane("Bio", id="tab-bio"):
                with VerticalScroll(id="bio-scroll"):
                    yield Static("", id="bio-text", markup=False)

    def on_mount(self) -> None:
        try:
            art_url = self._artist.image(320)
        except Exception:
            art_url = None
        self.query_one(AlbumArt).load(art_url)
        self._load_top_tracks()
        self._load_albums()
        self._load_eps()
        self._load_bio()

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

    @work(thread=True)
    def _load_bio(self) -> None:
        try:
            raw = self._artist.get_bio()
            text = _clean_bio(raw) if raw else ""
        except Exception:
            text = ""
        self.app.call_from_thread(
            self.query_one("#bio-text", Static).update,
            text or "No biography available.",
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
