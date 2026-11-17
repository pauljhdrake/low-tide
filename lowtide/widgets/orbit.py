"""Orbit view: one artist in the centre, similar artists in a ring around it.

More similar artists sit closer to the centre. Arrow keys move the selection
spatially, Enter re-centres on the selected artist. Lines are drawn with
braille dots so they stay smooth at terminal resolution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

# Terminal cells are roughly twice as tall as they are wide.
_ASPECT = 2.1
_MAX_LABEL = 22

# Braille dot bits, indexed [y][x] within a 2x4 cell.
_BRAILLE_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

_DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}


@dataclass
class OrbitNode:
    name: str
    match: float  # 0..1 similarity to the centre artist
    plays: int | None = None  # None = not in your library
    data: object = None  # whatever the caller wants back (artist object etc.)

    @property
    def in_library(self) -> bool:
        return self.plays is not None


def _truncate(name: str) -> str:
    return name if len(name) <= _MAX_LABEL else name[: _MAX_LABEL - 1] + "…"


class Orbit(Widget, can_focus=True):
    """Radial similarity map around a single artist."""

    BINDINGS = [
        Binding("up", "move('up')", "Up", show=False),
        Binding("down", "move('down')", "Down", show=False),
        Binding("left", "move('left')", "Left", show=False),
        Binding("right", "move('right')", "Right", show=False),
        Binding("enter", "activate", "Centre", show=False),
    ]

    COMPONENT_CLASSES = {
        "orbit--spoke",
        "orbit--spoke-selected",
        "orbit--guide",
        "orbit--centre",
        "orbit--node",
        "orbit--node-library",
        "orbit--node-selected",
    }

    DEFAULT_CSS = """
    Orbit {
        height: 1fr;
        background: transparent;
    }
    Orbit > .orbit--spoke { color: $text-disabled; }
    Orbit > .orbit--spoke-selected { color: $accent; }
    Orbit > .orbit--guide { color: $text-disabled; }
    Orbit > .orbit--centre { color: $text; text-style: bold; }
    Orbit > .orbit--node { color: $text-muted; }
    Orbit > .orbit--node-library { color: $text; }
    Orbit > .orbit--node-selected { color: $background; background: $accent; text-style: bold; }
    """

    class Selected(Message):
        def __init__(self, orbit: Orbit, node: OrbitNode | None) -> None:
            super().__init__()
            self.orbit = orbit
            self.node = node

    class Activated(Message):
        def __init__(self, orbit: Orbit, node: OrbitNode) -> None:
            super().__init__()
            self.orbit = orbit
            self.node = node

    bloom: reactive[float] = reactive(1.0)
    selected: reactive[int] = reactive(-1)  # -1 = nothing selected

    def __init__(self, *, name: str | None = None, id: str | None = None, classes: str | None = None):
        super().__init__(name=name, id=id, classes=classes)
        self.centre: str = ""
        self.nodes: list[OrbitNode] = []
        self._positions: list[tuple[float, float]] = []

    # --- public API ---

    def set_orbit(self, centre: str, nodes: list[OrbitNode], animate: bool = True) -> None:
        """Show a new centre artist and its ring. Nodes are laid out around the
        circle in the order given, so pass them sorted however reads best."""
        self.centre = centre
        self.nodes = list(nodes)
        self.selected = 0 if self.nodes else -1
        if animate:
            self.bloom = 0.0
            self.animate("bloom", 1.0, duration=0.35, easing="out_cubic")
        else:
            self.bloom = 1.0
        self.refresh()
        self.post_message(self.Selected(self, self.selected_node))

    @property
    def selected_node(self) -> OrbitNode | None:
        if 0 <= self.selected < len(self.nodes):
            return self.nodes[self.selected]
        return None

    # --- actions ---

    def action_move(self, direction: str) -> None:
        if not self.nodes:
            return
        dx, dy = _DIRECTIONS[direction]
        positions = self._visual_positions()
        origin = positions[self.selected] if 0 <= self.selected < len(positions) else (0.0, 0.0)
        best, best_score = None, math.inf
        for i, (x, y) in enumerate(positions):
            if i == self.selected:
                continue
            vx, vy = x - origin[0], y - origin[1]
            dist = math.hypot(vx, vy)
            if dist == 0:
                continue
            cos = (vx * dx + vy * dy) / dist
            if cos < 0.35:
                continue
            score = dist * (2.0 - cos)
            if score < best_score:
                best, best_score = i, score
        if best is None:
            # Nothing further that way: jump to the node furthest in that
            # direction around the ring, so the keys never feel stuck.
            best = max(range(len(positions)), key=lambda i: positions[i][0] * dx + positions[i][1] * dy)
        self._select(best)

    def action_activate(self) -> None:
        node = self.selected_node
        if node is not None:
            self.post_message(self.Activated(self, node))

    def _select(self, index: int) -> None:
        if index != self.selected:
            self.selected = index
            self.post_message(self.Selected(self, self.selected_node))

    # --- layout ---

    def _angles(self) -> list[float]:
        n = len(self.nodes)
        # Start at the top and go clockwise.
        return [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

    def _radii(self) -> tuple[float, float]:
        """Maximum x and y radius, in cells."""
        w, h = self.size.width, self.size.height
        ry = max(2.0, h / 2 - 1.5)
        rx = min(ry * _ASPECT, w / 2 - _MAX_LABEL / 2 - 2)
        ry = min(ry, rx / _ASPECT)
        return max(4.0, rx), max(2.0, ry)

    def _ring_fraction(self, match: float) -> float:
        # Most similar sits at 45% of the radius, least similar at the edge.
        return 0.45 + 0.55 * (1.0 - max(0.0, min(1.0, match)))

    def _visual_positions(self) -> list[tuple[float, float]]:
        """Node positions relative to the centre, with x corrected for the cell
        aspect ratio so distances look the way they're drawn."""
        rx, ry = self._radii()
        out = []
        for angle, node in zip(self._angles(), self.nodes):
            f = self._ring_fraction(node.match)
            out.append((math.cos(angle) * rx * f / _ASPECT, math.sin(angle) * ry * f))
        return out

    # --- rendering ---

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        if w < 20 or h < 7:
            return Text("Window too small for the orbit view", style="dim")

        style_spoke = self.get_component_rich_style("orbit--spoke")
        style_spoke_sel = self.get_component_rich_style("orbit--spoke-selected")
        style_guide = self.get_component_rich_style("orbit--guide")
        style_centre = self.get_component_rich_style("orbit--centre")
        style_node = self.get_component_rich_style("orbit--node")
        style_lib = self.get_component_rich_style("orbit--node-library")
        style_sel = self.get_component_rich_style("orbit--node-selected")

        cx, cy = float((w - 1) // 2), float((h - 1) // 2)  # whole cells, so lines meet the label
        rx, ry = self._radii()
        bloom = max(0.05, self.bloom)

        # Braille layer: dot bits per cell, plus whether the selected spoke
        # touches the cell (it then wins the colour).
        bits = [[0] * w for _ in range(h)]
        cell_style: list[list[Style | None]] = [[None] * w for _ in range(h)]

        def plot(px: float, py: float, style: Style, strong: bool = False) -> None:
            ix, iy = int(px), int(py)
            if 0 <= ix < w * 2 and 0 <= iy < h * 4:
                c, r = ix // 2, iy // 4
                bits[r][c] |= _BRAILLE_BITS[iy % 4][ix % 2]
                if strong or cell_style[r][c] is None:
                    cell_style[r][c] = style

        # Faint outer guide ring marking the edge of "similar".
        steps = int(2 * math.pi * rx / 2) or 1
        for s in range(steps):
            a = 2 * math.pi * s / steps
            plot((cx + math.cos(a) * rx * bloom) * 2 + 1,
                 (cy + math.sin(a) * ry * bloom) * 4 + 2, style_guide)

        # Node positions in cells.
        nodes_xy = []
        for angle, node in zip(self._angles(), self.nodes):
            f = self._ring_fraction(node.match) * bloom
            nodes_xy.append((cx + math.cos(angle) * rx * f, cy + math.sin(angle) * ry * f))

        # Spokes, drawn in braille-dot space. Selected last so it sits on top.
        order = [i for i in range(len(self.nodes)) if i != self.selected]
        if 0 <= self.selected < len(self.nodes):
            order.append(self.selected)
        for i in order:
            nx, ny = nodes_xy[i]
            x0, y0 = cx * 2 + 1, cy * 4 + 2
            x1, y1 = nx * 2 + 1, ny * 4 + 2
            steps = int(max(abs(x1 - x0), abs(y1 - y0))) or 1
            sel = i == self.selected
            for s in range(steps + 1):
                t = s / steps
                plot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t,
                     style_spoke_sel if sel else style_spoke, strong=sel)

        # Character grid; labels are written over the braille layer.
        chars = [[chr(0x2800 + bits[r][c]) if bits[r][c] else " " for c in range(w)] for r in range(h)]
        styles: list[list[Style | None]] = [row[:] for row in cell_style]
        occupied: list[tuple[int, int, int]] = []  # (row, start, end)

        def fits(row: int, start: int, end: int) -> bool:
            if row < 0 or row >= h or start < 0 or end > w:
                return False
            return all(not (r == row and start < e + 1 and s - 1 < end) for r, s, e in occupied)

        def write(row: int, start: int, text: str, style: Style, pad: bool = True) -> None:
            if pad:  # clear a cell either side so lines don't touch the text
                for c in (start - 1, start + len(text)):
                    if 0 <= c < w:
                        chars[row][c], styles[row][c] = " ", None
            for k, ch in enumerate(text):
                if 0 <= start + k < w:
                    chars[row][start + k], styles[row][start + k] = ch, style
            occupied.append((row, start, start + len(text)))

        # Centre label.
        centre_text = f"★ {_truncate(self.centre)}"
        cstart = int(round(cx - len(centre_text) / 2))
        write(int(round(cy)), max(0, cstart), centre_text, style_centre)

        # Node labels: marker at the node, name on the side facing away from
        # the centre. Nudge outward a row at a time if a label would collide.
        draw_order = order[::-1]  # selected first so it never gets displaced
        for i in draw_order:
            node = self.nodes[i]
            nx, ny = nodes_xy[i]
            marker = "●" if node.in_library else "○"
            name = _truncate(node.name)
            if nx > cx + 2:
                label, start = f"{marker} {name}", int(round(nx))
            elif nx < cx - 2:
                label = f"{name} {marker}"
                start = int(round(nx)) - len(label) + 1
            else:
                label = f"{marker} {name}"
                start = int(round(nx - len(label) / 2))
            start = max(0, min(w - len(label), start))
            row = int(round(ny))
            outward = -1 if ny < cy else 1
            for shift in (0, outward, 2 * outward, -outward):
                if fits(row + shift, start, start + len(label)):
                    row += shift
                    break
            else:
                row = max(0, min(h - 1, row))
            if not 0 <= row < h:
                continue
            if i == self.selected:
                style = style_sel
                label = f" {label} "
                start = max(0, min(w - len(label), start - 1))
            else:
                style = style_lib if node.in_library else style_node
            write(row, start, label, style)

        text = Text()
        for r in range(h):
            if r:
                text.append("\n")
            row_chars, row_styles = chars[r], styles[r]
            c = 0
            while c < w:
                st = row_styles[c]
                end = c
                while end < w and row_styles[end] is st:
                    end += 1
                text.append("".join(row_chars[c:end]), style=st or "")
                c = end
        return text

    def watch_bloom(self) -> None:
        self.refresh()

    def watch_selected(self) -> None:
        self.refresh()
