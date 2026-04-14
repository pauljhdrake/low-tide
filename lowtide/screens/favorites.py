from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, TabPane, TabbedContent

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
    FavoritesScreen TabbedContent {
        height: 1fr;
    }
    FavoritesScreen ListView {
        height: 1fr;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Favorites", id="fav-heading")
        with TabbedContent():
            with TabPane("Tracks", id="tab-fav-tracks"):
                yield Label("Loading…", id="status-fav-tracks")
                yield TrackList(id="fav-tracks")
            with TabPane("Albums", id="tab-fav-albums"):
                yield Label("Loading…", id="status-fav-albums")
                yield ListView(id="fav-albums")
            with TabPane("Artists", id="tab-fav-artists"):
                yield Label("Loading…", id="status-fav-artists")
                yield ListView(id="fav-artists")

    def on_mount(self) -> None:
        self._load_tracks()
        self._load_albums()
        self._load_artists()

    # ── Tracks ────────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_tracks(self) -> None:
        try:
            tracks = self.app.client.get_favorite_tracks()
            self.app.call_from_thread(self._populate_tracks, tracks)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-fav-tracks", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate_tracks(self, tracks: list) -> None:
        self.query_one("#status-fav-tracks", Label).update(
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

    # ── Albums ────────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_albums(self) -> None:
        try:
            albums = self.app.client.get_favorite_albums()
            self.app.call_from_thread(self._populate_albums, albums)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-fav-albums", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate_albums(self, albums: list) -> None:
        self.query_one("#status-fav-albums", Label).update(
            f"[dim]{len(albums)} saved albums[/dim]"
        )
        lv = self.query_one("#fav-albums", ListView)
        lv.clear()
        for album in albums:
            name = getattr(album, "name", "?")
            artist = getattr(getattr(album, "artist", None), "name", "–")
            year = getattr(album, "year", "")
            n = getattr(album, "num_tracks", getattr(album, "numberOfTracks", ""))
            label = f"{name}  [dim]{artist}  •  {year}  •  {n} tracks[/dim]"
            item = ListItem(Label(label))
            item._album = album
            lv.append(item)

    # ── Artists ───────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_artists(self) -> None:
        try:
            artists = self.app.client.get_favorite_artists()
            self.app.call_from_thread(self._populate_artists, artists)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-fav-artists", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate_artists(self, artists: list) -> None:
        self.query_one("#status-fav-artists", Label).update(
            f"[dim]{len(artists)} saved artists[/dim]"
        )
        lv = self.query_one("#fav-artists", ListView)
        lv.clear()
        for artist in artists:
            name = getattr(artist, "name", "?")
            item = ListItem(Label(name))
            item._artist = artist
            lv.append(item)

    # ── Navigation ────────────────────────────────────────────────────────

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        from lowtide.screens.album import AlbumScreen
        from lowtide.screens.artist import ArtistScreen

        album = getattr(event.item, "_album", None)
        if album:
            await self.app.push_view(AlbumScreen(album))
            return
        artist = getattr(event.item, "_artist", None)
        if artist:
            await self.app.push_view(ArtistScreen(artist))
