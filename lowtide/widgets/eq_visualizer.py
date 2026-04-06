from __future__ import annotations

import math
import random

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

_BARS = 10
_MAX_HEIGHT = 4
_FPS = 30
_FREQ_LABELS = ["32", "64", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]

_BAND_CEILING   = [1.00, 0.97, 0.93, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58, 0.52]
_BAND_RISE      = [0.10, 0.11, 0.13, 0.15, 0.17, 0.20, 0.23, 0.26, 0.30, 0.34]
_BAND_DECAY     = [0.03, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.14]
_BAND_CHANGE_PROB = [0.03, 0.03, 0.04, 0.05, 0.06, 0.07, 0.09, 0.11, 0.14, 0.17]

# Themes: four RGB stops, bottom row → top row
_RGB = tuple[int, int, int]
THEMES: dict[str, list[_RGB]] = {
    "classic": [(0, 130, 0),   (0, 210, 0),   (210, 210, 0), (210, 55, 55) ],
    "fire":    [(170, 35, 0),  (215, 95, 0),   (230, 175, 0), (250, 235, 90)],
    "ice":     [(0, 55, 150),  (0, 135, 215),  (55, 205, 225),(195, 235, 255)],
    "mono":    [(55, 55, 55),  (105, 105, 105),(160, 160, 160),(215, 215, 215)],
    "neon":    [(150, 0, 195), (215, 0, 175),  (250, 75, 195),(250, 195, 235)],
}

DEFAULT_THEME = "mono"
DEFAULT_BPM = 120.0


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _row_color(stops: list[_RGB], row: int) -> str:
    """Interpolate across the gradient stops for the given row (0 = bottom)."""
    t = row / (_MAX_HEIGHT - 1) if _MAX_HEIGHT > 1 else 0.0
    seg = t * (len(stops) - 1)
    lo = min(int(seg), len(stops) - 2)
    frac = seg - lo
    r = _lerp(stops[lo][0], stops[lo + 1][0], frac)
    g = _lerp(stops[lo][1], stops[lo + 1][1], frac)
    b = _lerp(stops[lo][2], stops[lo + 1][2], frac)
    return f"#{r:02x}{g:02x}{b:02x}"


def _row_color_faded(stops: list[_RGB], row: int, alpha: float) -> str:
    """Same as _row_color but faded toward black by alpha (0=black, 1=full)."""
    t = row / (_MAX_HEIGHT - 1) if _MAX_HEIGHT > 1 else 0.0
    seg = t * (len(stops) - 1)
    lo = min(int(seg), len(stops) - 2)
    frac = seg - lo
    r = int(_lerp(stops[lo][0], stops[lo + 1][0], frac) * alpha)
    g = int(_lerp(stops[lo][1], stops[lo + 1][1], frac) * alpha)
    b = int(_lerp(stops[lo][2], stops[lo + 1][2], frac) * alpha)
    return f"#{r:02x}{g:02x}{b:02x}"


class EQVisualizer(Widget):
    DEFAULT_CSS = """
    EQVisualizer {
        width: 1fr;
        height: auto;
        background: transparent;
        opacity: 0.8;
        display: none;
        margin-bottom: 1;
    }
    EQVisualizer #eq-bars {
        text-align: right;
    }
    """

    paused: reactive[bool] = reactive(True)

    def __init__(self, theme: str = DEFAULT_THEME, show_labels: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._theme = theme if theme in THEMES else DEFAULT_THEME
        self._show_labels = show_labels
        self._heights = [0.0] * _BARS
        self._targets = [0.0] * _BARS
        self._energy = 0.8
        self._energy_target = 0.8
        self._bpm = DEFAULT_BPM
        self._beat_phase = 0.0
        self._beat_count = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="eq-bars")

    def on_mount(self) -> None:
        self.set_interval(1 / _FPS, self._tick)

    def set_bpm(self, bpm: float | None) -> None:
        self._bpm = float(bpm) if bpm and 40 <= bpm <= 240 else DEFAULT_BPM

    def _tick(self) -> None:
        dt = 1.0 / _FPS
        if self.paused:
            for i in range(_BARS):
                self._targets[i] = 0.0
            self._energy_target = 0.0
        else:
            self._advance_beat(dt)
            self._update_energy()
            self._update_targets()

        self._step_heights()
        self._render_bars()

    def _advance_beat(self, dt: float) -> None:
        beat_interval = 60.0 / self._bpm
        self._beat_phase += dt
        if self._beat_phase >= beat_interval:
            self._beat_phase -= beat_interval
            self._beat_count += 1
            self._on_beat()

    def _on_beat(self) -> None:
        beat_in_bar = self._beat_count % 4
        for i in range(3):
            self._targets[i] = _MAX_HEIGHT * _BAND_CEILING[i] * random.uniform(0.75, 1.0)
        if beat_in_bar in (1, 3):
            for i in range(3, 7):
                self._targets[i] = _MAX_HEIGHT * _BAND_CEILING[i] * random.uniform(0.55, 0.85)
        self._energy = min(1.2, self._energy + random.uniform(0.05, 0.15))

    def _update_energy(self) -> None:
        if random.random() < 0.015:
            self._energy_target = random.uniform(0.5, 1.1)
        self._energy += (self._energy_target - self._energy) * 0.015

    def _update_targets(self) -> None:
        for i in range(_BARS):
            if random.random() < _BAND_CHANGE_PROB[i]:
                ceiling = _MAX_HEIGHT * _BAND_CEILING[i] * self._energy
                self._targets[i] = random.uniform(ceiling * 0.15, ceiling)

    def _step_heights(self) -> None:
        for i in range(_BARS):
            diff = self._targets[i] - self._heights[i]
            rate = _BAND_RISE[i] if diff > 0 else _BAND_DECAY[i]
            self._heights[i] += diff * rate
            self._heights[i] = max(0.0, min(float(_MAX_HEIGHT), self._heights[i]))

    def _render_bars(self) -> None:
        stops = THEMES[self._theme]
        rows = []

        for row in range(_MAX_HEIGHT - 1, -1, -1):
            parts = []
            for b in range(_BARS):
                h = self._heights[b]
                if h >= row + 1:
                    # Fully lit cell — colour based on row position in gradient
                    color = _row_color(stops, row)
                    parts.append(f"[{color}]██[/{color}] ")
                elif h > row:
                    # Topmost partial cell — fade by fractional height
                    color = _row_color_faded(stops, row, h - row)
                    parts.append(f"[{color}]██[/{color}] ")
                else:
                    parts.append("   ")
            rows.append("".join(parts))

        if self._show_labels:
            rows.append("".join(f"[dim]{lbl:<3}[/dim]" for lbl in _FREQ_LABELS))

        self.query_one("#eq-bars", Static).update("\n".join(rows))

    def set_theme(self, theme: str) -> None:
        if theme in THEMES:
            self._theme = theme
