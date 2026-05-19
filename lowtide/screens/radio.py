from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Label

from lowtide.recommender import DiscoveryMode
from lowtide.widgets.track_list import TrackList

_MODE_LABELS = {
    DiscoveryMode.ESSENTIAL:   "Essential",
    DiscoveryMode.BALANCED:    "Balanced",
    DiscoveryMode.ADVENTUROUS: "Adventurous",
}


class RadioScreen(Widget):
    """
    Shared screen for both track radio (seeded from a track) and
    Ride the Tide (seeded from listening history).
    """

    BINDINGS = [
        Binding("A", "append_all", "Add all to queue", show=False),
        Binding("D", "cycle_dial", "Discovery dial", show=True),
    ]

    DEFAULT_CSS = """
    RadioScreen {
        height: 1fr;
        padding: 1 2;
    }
    RadioScreen #radio-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    RadioScreen #radio-dial {
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

    def __init__(self, seed_track=None, cached: tuple | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._seed_track = seed_track
        self._cached = cached  # pre-built results for Ride the Tide
        self._mode = DiscoveryMode.BALANCED

    def compose(self) -> ComposeResult:
        if self._seed_track:
            title = getattr(self._seed_track, "name", "Radio")
            artist = getattr(getattr(self._seed_track, "artist", None), "name", "")
            heading = f"Radio: {title}" + (f" – {artist}" if artist else "")
        else:
            heading = "Ride the Tide"
        yield Label(heading, id="radio-heading")
        yield Label(self._dial_label(), id="radio-dial")
        yield Label("Building…", id="radio-status")
        yield Label("", id="radio-nudge")
        yield TrackList(id="radio-tracks")

    def on_mount(self) -> None:
        if self._cached is not None:
            self._populate(*self._cached)
        else:
            self._build()

    def _dial_label(self) -> str:
        parts = []
        for mode, name in _MODE_LABELS.items():
            parts.append(f"[bold]{name}[/bold]" if mode == self._mode else f"[dim]{name}[/dim]")
        return "  ".join(parts) + "  [dim](D)[/dim]"

    def action_cycle_dial(self) -> None:
        self._mode = DiscoveryMode((self._mode + 1) % len(DiscoveryMode))
        self.query_one("#radio-dial", Label).update(self._dial_label())
        # Invalidate Ride the Tide cache so re-visit rebuilds with the new mode
        if self._seed_track is None:
            self.app._ride_the_tide_cache = None
        self._build()

    @work(thread=True)
    def _build(self) -> None:
        mode = self._mode
        self.app.call_from_thread(
            lambda: self.query_one("#radio-status", Label).update("[dim]Building…[/dim]")
        )
        try:
            recommender = self.app.recommender
            if self._seed_track:
                tracks, nudge = recommender.build_track_radio(self._seed_track, mode=mode)
            else:
                tracks, nudge = recommender.build_ride_the_tide(mode=mode)
                self.app.call_from_thread(
                    setattr, self.app, "_ride_the_tide_cache", (tracks, nudge)
                )
            self.app.call_from_thread(self._populate, tracks, nudge)
        except Exception as e:
            self.app.call_from_thread(
                lambda: self.query_one("#radio-status", Label).update(f"[red]Error: {e}[/red]")
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
