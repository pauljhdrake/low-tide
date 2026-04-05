from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, TabPane, TabbedContent

from lowtide.widgets.track_list import TrackList


class SearchScreen(Widget):
    DEFAULT_CSS = """
    SearchScreen {
        height: 1fr;
        padding: 1 2;
    }
    SearchScreen #search-status {
        color: $text-muted;
        margin: 1 0;
    }
    SearchScreen TabbedContent {
        height: 1fr;
    }
    SearchScreen ListView {
        height: 1fr;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search tracks, albums, artists…", id="search-input")
        yield Label("", id="search-status")
        with TabbedContent(id="search-tabs"):
            with TabPane("Tracks", id="tab-tracks"):
                yield TrackList(id="search-tracks")
            with TabPane("Albums", id="tab-albums"):
                yield ListView(id="search-albums")
            with TabPane("Artists", id="tab-artists"):
                yield ListView(id="search-artists")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self._search(query)

    @work(thread=True)
    def _search(self, query: str) -> None:
        self.app.call_from_thread(
            self.query_one("#search-status", Label).update,
            f"[dim]Searching…[/dim]",
        )
        try:
            hits = self.app.client.search(query, limit=50)
            self.app.call_from_thread(self._show_results, hits, query)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#search-status", Label).update,
                f"[red]Search failed: {e}[/red]",
            )

    def _show_results(self, hits: dict, query: str) -> None:
        tracks = hits.get("tracks", [])
        albums = hits.get("albums", [])
        artists = hits.get("artists", [])
        self.query_one("#search-status", Label).update(
            f"[dim]{len(tracks)} tracks  •  {len(albums)} albums  •  {len(artists)} artists[/dim]"
        )
        # Tracks
        self.query_one(TrackList).load(tracks)

        # Albums
        lv_albums = self.query_one("#search-albums", ListView)
        lv_albums.clear()
        for album in albums:
            name = getattr(album, "name", "?")
            artist = getattr(getattr(album, "artist", None), "name", "—")
            year = getattr(album, "year", "")
            n = getattr(album, "num_tracks", getattr(album, "numberOfTracks", ""))
            label = f"{name}  [dim]{artist}  •  {year}  •  {n} tracks[/dim]"
            item = ListItem(Label(label))
            item._album = album
            lv_albums.append(item)

        # Artists
        lv_artists = self.query_one("#search-artists", ListView)
        lv_artists.clear()
        for artist in artists:
            name = getattr(artist, "name", "?")
            item = ListItem(Label(name))
            item._artist = artist
            lv_artists.append(item)

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        self.app.enqueue_and_play(self.query_one(TrackList).tracks, start_index=event.index)

    def on_track_list_track_append_requested(self, event: TrackList.TrackAppendRequested) -> None:
        self.app.append_to_queue([event.track])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        album = getattr(event.item, "_album", None)
        if album:
            self.app.open_object(album)
            return
        artist = getattr(event.item, "_artist", None)
        if artist:
            self.app.open_object(artist)
