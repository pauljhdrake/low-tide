from __future__ import annotations

import random

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

_BARS = 10
_MAX_HEIGHT = 6
_FPS = 30
_FREQ_LABELS = ["32", "64", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]

# Sub-character block elements: index 1–8 map to ▁▂▃▄▅▆▇█
_BLOCKS = " ▁▂▃▄▅▆▇█"

# Per-band simulation parameters
_BAND_CEILING     = [1.00, 0.97, 0.93, 0.88, 0.82, 0.76, 0.70, 0.64, 0.58, 0.52]
_BAND_RISE        = [0.30, 0.32, 0.35, 0.38, 0.40, 0.43, 0.46, 0.48, 0.50, 0.50]
_BAND_CHANGE_PROB = [0.02, 0.02, 0.03, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12]
_TARGET_DECAY     = 0.97  # slow decay so bars hold height before falling

# Gravity model (cava-style): velocity accumulates each frame
_GRAVITY_ACCEL = 0.038   # fall velocity increment per frame
_GRAVITY_MOD   = 2.0     # scales parabolic decay; higher = faster fall
_PEAK_HOLD     = 14      # frames the peak indicator holds before falling
_PEAK_GRAVITY  = 0.025   # peak indicator fall acceleration
_MONSTERCAT    = 1.5     # horizontal smoothing; higher = more spread

# Themes: four RGB stops, bottom → top (used as a continuous gradient)
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


def _gradient_color(stops: list[_RGB], t: float, alpha: float = 1.0) -> str:
    """Continuous gradient colour at position t (0.0=bottom, 1.0=top), optionally dimmed."""
    t = max(0.0, min(1.0, t))
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
        self._heights = [0.0] * _BARS   # current display height
        self._targets = [0.0] * _BARS   # simulation target
        self._bar_peak = [0.0] * _BARS  # gravity reference peak (reset on rise)
        self._fall_vel = [0.0] * _BARS  # accumulated fall velocity
        self._pk_height = [0.0] * _BARS # peak indicator height
        self._pk_hold = [0] * _BARS     # frames remaining before peak falls
        self._pk_vel = [0.0] * _BARS    # peak indicator fall velocity
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
            # Decay targets toward 0 so bars fall between beat/random events
            for i in range(_BARS):
                self._targets[i] *= _TARGET_DECAY

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
            if self._targets[i] > self._heights[i]:
                # Rise: fast exponential snap (cava rises immediately; we ease slightly)
                self._heights[i] += (self._targets[i] - self._heights[i]) * _BAND_RISE[i]
                self._bar_peak[i] = self._heights[i]
                self._fall_vel[i] = 0.0

                # Raise peak indicator
                if self._heights[i] > self._pk_height[i]:
                    self._pk_height[i] = self._heights[i]
                    self._pk_hold[i] = _PEAK_HOLD
                    self._pk_vel[i] = 0.0
            else:
                # Fall: cava gravity model — velocity accumulates, bar falls parabolically
                self._fall_vel[i] += _GRAVITY_ACCEL
                norm = self._bar_peak[i] / _MAX_HEIGHT if _MAX_HEIGHT else 0.0
                new_norm = norm * (1.0 - self._fall_vel[i] ** 2 * _GRAVITY_MOD)
                self._heights[i] = max(0.0, new_norm * _MAX_HEIGHT)

            # Peak indicator physics
            if self._pk_hold[i] > 0:
                self._pk_hold[i] -= 1
            else:
                self._pk_vel[i] += _PEAK_GRAVITY
                self._pk_height[i] -= self._pk_vel[i] ** 2 * _GRAVITY_MOD * _MAX_HEIGHT
                if self._pk_height[i] < self._heights[i]:
                    self._pk_height[i] = self._heights[i]

    def _apply_monstercat(self, heights: list[float]) -> list[float]:
        """Horizontal smoothing: each bar is lifted toward its neighbours' heights
        divided by MONSTERCAT^distance. Creates a smooth mountain profile."""
        smoothed = list(heights)
        for i in range(_BARS):
            for j in range(_BARS):
                dist = abs(i - j)
                if dist > 0:
                    contribution = heights[j] / (_MONSTERCAT ** dist)
                    if contribution > smoothed[i]:
                        smoothed[i] = contribution
        return smoothed

    def _render_bars(self) -> None:
        stops = THEMES[self._theme]
        display = self._apply_monstercat(self._heights)
        rows = []

        for row in range(_MAX_HEIGHT - 1, -1, -1):
            parts = []
            for b in range(_BARS):
                h = display[b]
                pk = self._pk_height[b]
                pk_row = int(pk)

                if row == pk_row and pk > h + 0.4:
                    # Peak indicator: ▄ dimmed, colored at the peak's gradient position
                    color = _gradient_color(stops, pk / _MAX_HEIGHT, 0.60)
                    parts.append(f"[{color}]▄▄[/{color}] ")
                elif h >= row + 1:
                    # Full block: blend the lower and upper halves of the cell using ▄.
                    # ▄ fills the bottom half in fg; background shows the top half.
                    # This gives two gradient samples per character row, hiding seams.
                    c_lo = _gradient_color(stops, row / _MAX_HEIGHT)
                    c_hi = _gradient_color(stops, (row + 0.5) / _MAX_HEIGHT)
                    parts.append(f"[{c_lo} on {c_hi}]▄▄[/] ")
                elif h > row:
                    # Tip of bar: sub-block character at exact fractional height,
                    # color at the precise bar height for a seamless gradient continuation.
                    frac = h - row
                    ch = _BLOCKS[max(1, round(frac * 8))]
                    color = _gradient_color(stops, h / _MAX_HEIGHT)
                    parts.append(f"[{color}]{ch}{ch}[/] ")
                else:
                    parts.append("   ")
            rows.append("".join(parts))

        if self._show_labels:
            rows.append("".join(f"[dim]{lbl:<3}[/dim]" for lbl in _FREQ_LABELS))

        self.query_one("#eq-bars", Static).update("\n".join(rows))

    def set_theme(self, theme: str) -> None:
        if theme in THEMES:
            self._theme = theme
