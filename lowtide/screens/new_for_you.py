from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, TabPane, TabbedContent

from lowtide.widgets.track_list import TrackList

_SECTION_TRACKS = "Recommended new tracks"
_SECTION_ALBUMS = "Suggested new albums for you"


class NewForYouScreen(Widget):
    BINDINGS = [Binding("A", "append_all", "Add all to queue", show=False)]

    DEFAULT_CSS = """
    NewForYouScreen {
        height: 1fr;
        padding: 1 2;
    }
    NewForYouScreen #nfy-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    NewForYouScreen TabbedContent {
        height: 1fr;
    }
    NewForYouScreen ListView {
        height: 1fr;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("New For You", id="nfy-heading")
        with TabbedContent():
            with TabPane("Tracks", id="tab-nfy-tracks"):
                yield Label("Loading…", id="status-nfy-tracks")
                yield TrackList(id="nfy-tracks")
            with TabPane("Albums", id="tab-nfy-albums"):
                yield Label("Loading…", id="status-nfy-albums")
                yield ListView(id="nfy-albums")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        try:
            home = self.app.client.session.home()
            tracks = []
            albums = []
            for cat in getattr(home, "categories", []):
                title = getattr(cat, "title", "") or ""
                if title == _SECTION_TRACKS:
                    tracks = list(getattr(cat, "items", []))
                elif title == _SECTION_ALBUMS:
                    albums = list(getattr(cat, "items", []))
            self.app.call_from_thread(self._populate, tracks, albums)
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.query_one("#status-nfy-tracks", Label).update(f"[red]Error: {e}[/red]")
            )

    def _populate(self, tracks: list, albums: list) -> None:
        self.query_one("#status-nfy-tracks", Label).update(
            f"[dim]{len(tracks)} tracks[/dim]" if tracks else "[dim]No results[/dim]"
        )
        self.query_one(TrackList).load(tracks)

        self.query_one("#status-nfy-albums", Label).update(
            f"[dim]{len(albums)} albums[/dim]" if albums else "[dim]No results[/dim]"
        )
        lv = self.query_one("#nfy-albums", ListView)
        lv.clear()
        for album in albums:
            name = getattr(album, "name", "?")
            artist = getattr(getattr(album, "artist", None), "name", "")
            year = getattr(album, "year", "") or ""
            label = f"{name}  [dim]{artist}" + (f"  {year}" if year else "") + "[/dim]"
            item = ListItem(Label(label))
            item._album = album
            lv.append(item)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        album = getattr(event.item, "_album", None)
        if album:
            from lowtide.screens.album import AlbumScreen
            await self.app.push_view(AlbumScreen(album))

    def on_track_list_track_selected(self, event: TrackList.TrackSelected) -> None:
        all_tracks = self.query_one(TrackList).tracks
        self.app.enqueue_and_play(all_tracks, start_index=event.index)

    def on_track_list_track_append_requested(self, event: TrackList.TrackAppendRequested) -> None:
        self.app.append_to_queue([event.track])

    def action_append_all(self) -> None:
        tracks = self.query_one(TrackList).tracks
        if tracks:
            self.app.append_to_queue(tracks)
