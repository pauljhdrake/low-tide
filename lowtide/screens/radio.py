from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label

from lowtide.widgets.track_list import TrackList


class RadioScreen(Widget):
    """
    Shared screen for both track radio (seeded from a track) and
    Ride the Tide (seeded from listening history).
    """

    BINDINGS = [Binding("A", "append_all", "Add all to queue", show=False)]

    DEFAULT_CSS = """
    RadioScreen {
        height: 1fr;
        padding: 1 2;
    }
    RadioScreen #radio-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    RadioScreen #radio-status {
        color: $text-muted;
        margin-bottom: 1;
    }
    RadioScreen #radio-nudge {
        color: $warning;
        margin-bottom: 1;
        display: none;
    }
    """

    def __init__(self, seed_track=None, **kwargs) -> None:
        super().__init__(**kwargs)
        # seed_track=None means Ride the Tide mode
        self._seed_track = seed_track

    def compose(self) -> ComposeResult:
        if self._seed_track:
            title = getattr(self._seed_track, "name", "Radio")
            artist = getattr(getattr(self._seed_track, "artist", None), "name", "")
            heading = f"Radio: {title}" + (f" – {artist}" if artist else "")
        else:
            heading = "Ride the Tide"
        yield Label(heading, id="radio-heading")
        yield Label("Building…", id="radio-status")
        yield Label("", id="radio-nudge")
        yield TrackList(id="radio-tracks")

    def on_mount(self) -> None:
        self._build()

    @work(thread=True)
    def _build(self) -> None:
        try:
            recommender = self.app.recommender
            if self._seed_track:
                tracks, nudge = recommender.build_track_radio(self._seed_track)
            else:
                tracks, nudge = recommender.build_ride_the_tide()
            self.app.call_from_thread(self._populate, tracks, nudge)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#radio-status", Label).update,
                f"[red]Error: {e}[/red]",
            )

    def _populate(self, tracks: list, nudge: str | None) -> None:
        self.query_one("#radio-status", Label).update(
            f"[dim]{len(tracks)} tracks[/dim]" if tracks else "[dim]No results found[/dim]"
        )
        if nudge:
            nudge_label = self.query_one("#radio-nudge", Label)
            nudge_label.update(f"[yellow]⚡ {nudge}[/yellow]")
            nudge_label.display = True
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
