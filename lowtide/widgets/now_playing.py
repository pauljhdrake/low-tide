from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _bar(pos: float, dur: float, width: int = 26) -> str:
    if dur <= 0:
        return "─" * width
    filled = max(0, min(width - 1, int((pos / dur) * width)))
    return "─" * filled + "●" + "─" * (width - filled - 1)


class NowPlayingBar(Widget):
    DEFAULT_CSS = """
    NowPlayingBar {
        dock: bottom;
        height: 5;
        background: transparent;
        border-top: tall $primary-darken-2;
        padding: 0 2;
    }
    NowPlayingBar #np-track {
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    NowPlayingBar #np-artist {
        color: $text-muted;
        height: 1;
    }
    NowPlayingBar #np-bottom {
        height: 1;
    }
    NowPlayingBar #np-controls {
        width: 12;
        text-style: bold;
    }
    NowPlayingBar #np-progress {
        width: 1fr;
        color: $text-muted;
    }
    NowPlayingBar #np-vol {
        width: 10;
        color: $text-muted;
        content-align: right middle;
    }
    NowPlayingBar #np-modes {
        width: 10;
        content-align: right middle;
    }
    """

    track_name: reactive[str] = reactive("")
    artist_name: reactive[str] = reactive("")
    paused: reactive[bool] = reactive(True)
    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)
    volume: reactive[int] = reactive(80)
    shuffle: reactive[bool] = reactive(False)
    repeat: reactive[bool] = reactive(False)
    favourited: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        yield Label("", id="np-track")
        yield Label("", id="np-artist")
        with Horizontal(id="np-bottom"):
            yield Label("", id="np-controls")
            yield Label("", id="np-progress")
            yield Label("", id="np-vol")
            yield Label("", id="np-modes")

    def on_mount(self) -> None:
        # Trigger initial render of all reactive values
        self.watch_paused(self.paused)
        self.watch_volume(self.volume)
        self._refresh_progress()
        self._refresh_modes()

    def watch_track_name(self, v: str) -> None:
        self.query_one("#np-track", Label).update(
            f"[b]{v}[/b]" if v else "[dim]Nothing playing[/dim]"
        )

    def watch_artist_name(self, v: str) -> None:
        self.query_one("#np-artist", Label).update(f"[dim]{v}[/dim]")

    def watch_paused(self, paused: bool) -> None:
        icon = "▶" if paused else "⏸"
        self.query_one("#np-controls", Label).update(f"[b]⏮  {icon}  ⏭[/b]")

    def watch_shuffle(self, _: bool) -> None:
        self._refresh_modes()

    def watch_repeat(self, _: bool) -> None:
        self._refresh_modes()

    def watch_favourited(self, _: bool) -> None:
        self._refresh_modes()

    def _refresh_modes(self) -> None:
        parts = []
        parts.append("[b]♥[/b]" if self.favourited else "[dim]♡[/dim]")
        parts.append("[b]⇄[/b]" if self.shuffle else "[dim]⇄[/dim]")
        parts.append("[b]↺[/b]" if self.repeat else "[dim]↺[/dim]")
        self.query_one("#np-modes", Label).update("  ".join(parts))

    def watch_position(self, _: float) -> None:
        self._refresh_progress()

    def watch_duration(self, _: float) -> None:
        self._refresh_progress()

    def watch_volume(self, vol: int) -> None:
        self.query_one("#np-vol", Label).update(f"[dim]🔊 {vol}%[/dim]")

    def _refresh_progress(self) -> None:
        self.query_one("#np-progress", Label).update(
            f"[dim]{_fmt(self.position)} {_bar(self.position, self.duration)} {_fmt(self.duration)}[/dim]"
        )

    def set_track(self, track) -> None:
        self.track_name = getattr(track, "name", "")
        self.artist_name = getattr(getattr(track, "artist", None), "name", "")
