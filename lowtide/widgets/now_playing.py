from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label

from lowtide.lyrics import LyricLine, current_line_index
from lowtide.widgets.eq_visualizer import EQVisualizer

_LYRICS_CONTEXT = 2  # lines shown above and below the current line
_LYRICS_TOTAL = _LYRICS_CONTEXT * 2 + 1  # 5 lines total
_HEIGHT_COMPACT = 5
_HEIGHT_EQ = 6 + 1   # 6 bar rows + margin-bottom
_HEIGHT_LYRICS_EXTRA = _LYRICS_TOTAL + 1


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _bar(pos: float, dur: float, width: int = 26) -> str:
    if dur <= 0:
        return "─" * width
    filled = max(0, min(width - 1, int((pos / dur) * width)))
    return "─" * filled + "●" + "─" * (width - filled - 1)


def _quality_label(audio_quality: str | None, bpm: float | None, explicit: bool) -> str:
    parts = []
    if audio_quality:
        label = {
            "LOW": "AAC 96k",
            "HIGH": "AAC 320k",
            "LOSSLESS": "FLAC",
            "HI_RES_LOSSLESS": "FLAC MAX",
        }.get(audio_quality, audio_quality)
        parts.append(label)
    if bpm:
        parts.append(f"{int(bpm)} BPM")
    if explicit:
        parts.append("E")
    return "  ·  ".join(parts)


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
    NowPlayingBar #np-lyrics {
        height: auto;
        display: none;
        margin-top: 1;
    }
    NowPlayingBar .lyric-line {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    NowPlayingBar .lyric-current {
        width: 1fr;
        height: 1;
        text-style: bold;
        text-align: center;
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
        margin-left: 2;
    }
    """

    track_name: reactive[str] = reactive("")
    artist_name: reactive[str] = reactive("")
    track_info: reactive[str] = reactive("")
    paused: reactive[bool] = reactive(True)
    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)
    volume: reactive[int] = reactive(80)
    shuffle: reactive[bool] = reactive(False)
    repeat: reactive[bool] = reactive(False)
    favourited: reactive[bool] = reactive(False)
    crossfade: reactive[bool] = reactive(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._lyrics: list[LyricLine] = []
        self._lyrics_idx: int = 0
        self._eq_visible: bool = False

    def compose(self) -> ComposeResult:
        yield Label("", id="np-track")
        yield Label("", id="np-artist")
        with Vertical(id="np-lyrics"):
            for i in range(_LYRICS_TOTAL):
                cls = "lyric-current" if i == _LYRICS_CONTEXT else "lyric-line"
                yield Label("", id=f"np-lyric-{i}", classes=cls)
        yield EQVisualizer()
        with Horizontal(id="np-bottom"):
            yield Label("", id="np-controls")
            yield Label("", id="np-progress")
            yield Label("", id="np-vol")
            yield Label("", id="np-modes")

    def on_mount(self) -> None:
        self.watch_paused(self.paused)
        self.watch_volume(self.volume)
        self._refresh_progress()
        self._refresh_modes()

    def watch_track_name(self, v: str) -> None:
        self.query_one("#np-track", Label).update(
            f"[b]{v}[/b]" if v else "[dim]Nothing playing[/dim]"
        )

    def watch_artist_name(self, _: str) -> None:
        self._refresh_artist()

    def watch_track_info(self, _: str) -> None:
        self._refresh_artist()

    def _refresh_artist(self) -> None:
        parts = [self.artist_name] if self.artist_name else []
        if self.track_info:
            parts.append(f"[dim]{self.track_info}[/dim]")
        self.query_one("#np-artist", Label).update("  ·  ".join(parts) if parts else "")

    def watch_paused(self, paused: bool) -> None:
        icon = "▶" if paused else "⏸"
        self.query_one("#np-controls", Label).update(f"[b]⏮  {icon}  ⏭[/b]")

    def watch_shuffle(self, _: bool) -> None:
        self._refresh_modes()

    def watch_repeat(self, _: bool) -> None:
        self._refresh_modes()

    def watch_favourited(self, _: bool) -> None:
        self._refresh_modes()

    def watch_crossfade(self, _: bool) -> None:
        self._refresh_modes()

    def _refresh_modes(self) -> None:
        parts = [
            "[b]♥[/b]" if self.favourited else "[dim]♡[/dim]",
            "[b]⇄[/b]" if self.shuffle else "[dim]⇄[/dim]",
            "[b]↺[/b]" if self.repeat else "[dim]↺[/dim]",
            "[b]≋[/b]" if self.crossfade else "[dim]≋[/dim]",
        ]
        self.query_one("#np-modes", Label).update("  ".join(parts))

    def watch_position(self, pos: float) -> None:
        self._refresh_progress()
        if self._lyrics:
            new_idx = current_line_index(self._lyrics, pos)
            if new_idx != self._lyrics_idx:
                self._lyrics_idx = new_idx
                self._refresh_lyrics_display()

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

    def set_track_info(self, info: dict) -> None:
        self.track_info = _quality_label(
            info.get("audio_quality"),
            info.get("bpm"),
            info.get("explicit", False),
        )

    def set_lyrics(self, lines: list[LyricLine]) -> None:
        self._lyrics = lines
        self._lyrics_idx = 0
        self.query_one("#np-lyrics").display = bool(lines)
        self._update_height()
        if lines:
            self._refresh_lyrics_display()

    def toggle_eq(self) -> None:
        self._eq_visible = not self._eq_visible
        self.query_one(EQVisualizer).display = self._eq_visible
        self._update_height()

    def _update_height(self) -> None:
        h = _HEIGHT_COMPACT
        if self._lyrics:
            h += _HEIGHT_LYRICS_EXTRA
        if self._eq_visible:
            h += _HEIGHT_EQ
        self.styles.height = h

    def _refresh_lyrics_display(self) -> None:
        lines = self._lyrics
        idx = self._lyrics_idx
        for offset in range(-_LYRICS_CONTEXT, _LYRICS_CONTEXT + 1):
            slot = offset + _LYRICS_CONTEXT  # 0..4
            line_idx = idx + offset
            label = self.query_one(f"#np-lyric-{slot}", Label)
            if 0 <= line_idx < len(lines):
                text = lines[line_idx].text
                if offset == 0:
                    label.update(f"[b]{text}[/b]")
                else:
                    label.update(f"[dim]{text}[/dim]")
            else:
                label.update("")
