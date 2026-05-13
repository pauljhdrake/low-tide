from __future__ import annotations

import calendar
import datetime
import logging
from collections import defaultdict

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label

log = logging.getLogger(__name__)

_MONTH_ABBR = "JFMAMJJASOND"


def _cell(count: int, peak: int) -> tuple[str, str]:
    """Map a scrobble count to a block character and Rich style."""
    if count == 0 or peak == 0:
        return " ", ""
    ratio = count / peak
    if ratio < 0.15:
        return "░", "dim"
    if ratio < 0.4:
        return "▒", ""
    if ratio < 0.75:
        return "▓", "cyan"
    return "█", "bold cyan"


class HeatmapCanvas(Widget):
    """Scrollable heatmap: rows = artists, columns = months."""

    can_focus = True

    BINDINGS = [
        Binding("up",    "move_up",      show=False),
        Binding("down",  "move_down",    show=False),
        Binding("left",  "scroll_left",  show=False),
        Binding("right", "scroll_right", show=False),
        Binding("enter", "open_artist",  show=False),
        Binding("r",     "radio",        show=False),
    ]

    DEFAULT_CSS = """
    HeatmapCanvas {
        height: 1fr;
        background: transparent;
    }
    """

    class ArtistSelected(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    class RadioRequested(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    class RangeChanged(Message):
        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

    _NAME_W = 22
    _COL_W = 3

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._artists: list[str] = []
        self._monthly: dict[str, dict[tuple[int, int], int]] = {}
        self._months: list[tuple[int, int]] = []
        self._col_offset = 0
        self._selected = 0
        self._row_offset = 0

    def load(
        self,
        artists: list[str],
        monthly: dict[str, dict[tuple[int, int], int]],
        months: list[tuple[int, int]],
    ) -> None:
        self._artists = artists
        self._monthly = monthly
        self._months = months
        self._col_offset = max(0, len(months) - self._n_cols())
        self._selected = 0
        self._row_offset = 0
        self.refresh()
        self._emit_range()

    # ── Layout ────────────────────────────────────────────────────────────

    def _n_cols(self) -> int:
        w = self.size.width or 80
        return max(1, (w - self._NAME_W - 3) // self._COL_W)

    def _n_rows(self) -> int:
        h = self.size.height or 20
        return max(1, h - 1)  # -1 for header row

    # ── Render ────────────────────────────────────────────────────────────

    def render(self) -> Text:
        if not self._artists:
            return Text("No data.", style="dim")

        n_cols = self._n_cols()
        n_rows = self._n_rows()
        months = self._months[self._col_offset : self._col_offset + n_cols]
        artists = self._artists[self._row_offset : self._row_offset + n_rows]

        out = Text(no_wrap=True, overflow="crop")

        # Month header
        out.append(" " * self._NAME_W + " │ ", style="dim")
        for _, m in months:
            out.append(_MONTH_ABBR[m - 1], style="dim")
            out.append("  ")
        out.append("\n")

        # Artist rows
        for rel_i, artist in enumerate(artists):
            selected = (self._row_offset + rel_i) == self._selected
            sfx = " reverse" if selected else ""

            name = artist[: self._NAME_W].ljust(self._NAME_W)
            out.append(name, style=f"bold{sfx}")
            out.append(" │ ", style=f"dim{sfx}")

            data = self._monthly.get(artist, {})
            peak = max(data.values(), default=1)

            for y, m in months:
                char, style = _cell(data.get((y, m), 0), peak)
                out.append(char, style=f"{style}{sfx}".strip())
                out.append("  ", style=sfx.strip() or "")

            out.append("\n")

        return out

    # ── Navigation ────────────────────────────────────────────────────────

    def action_move_up(self) -> None:
        if self._selected > 0:
            self._selected -= 1
            if self._selected < self._row_offset:
                self._row_offset = self._selected
            self.refresh()

    def action_move_down(self) -> None:
        if self._selected < len(self._artists) - 1:
            self._selected += 1
            bottom = self._row_offset + self._n_rows() - 1
            if self._selected >= bottom:
                self._row_offset = self._selected - self._n_rows() + 2
            self._row_offset = max(0, min(self._row_offset, len(self._artists) - 1))
            self.refresh()

    def action_scroll_left(self) -> None:
        if self._col_offset > 0:
            self._col_offset -= 1
            self.refresh()
            self._emit_range()

    def action_scroll_right(self) -> None:
        if self._col_offset + self._n_cols() < len(self._months):
            self._col_offset += 1
            self.refresh()
            self._emit_range()

    def action_open_artist(self) -> None:
        if self._artists:
            self.post_message(HeatmapCanvas.ArtistSelected(self._artists[self._selected]))

    def action_radio(self) -> None:
        if self._artists:
            self.post_message(HeatmapCanvas.RadioRequested(self._artists[self._selected]))

    def _emit_range(self) -> None:
        if not self._months:
            return
        visible = self._months[self._col_offset : self._col_offset + self._n_cols()]
        if visible:
            s, e = visible[0], visible[-1]
            label = (
                f"{calendar.month_abbr[s[1]]} {s[0]} – "
                f"{calendar.month_abbr[e[1]]} {e[0]}"
            )
            self.post_message(HeatmapCanvas.RangeChanged(label))


class JourneyScreen(Widget):
    """
    Listening Journey: heatmap of Last.fm scrobble activity by artist and month.
    """

    class OpenArtist(Message):
        def __init__(self, artist) -> None:
            super().__init__()
            self.artist = artist

    class OpenRadio(Message):
        def __init__(self, seed_track) -> None:
            super().__init__()
            self.seed_track = seed_track

    DEFAULT_CSS = """
    JourneyScreen {
        height: 1fr;
        padding: 1 2;
    }
    JourneyScreen #journey-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    JourneyScreen #journey-status {
        height: 1;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Listening Journey", id="journey-heading")
        yield HeatmapCanvas(id="journey-canvas")
        yield Label("", id="journey-status")

    def on_mount(self) -> None:
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        store = self.app._scrobble_store

        if not self.app.recommender.has_lastfm:
            self.app.call_from_thread(
                lambda: self.query_one("#journey-status", Label).update(
                    "[dim]Last.fm not configured — configure in config.json to use this screen.[/dim]"
                )
            )
            return

        username = self.app.client.config.get("lastfm", {}).get("username", "")
        if not username:
            self.app.call_from_thread(
                lambda: self.query_one("#journey-status", Label).update(
                    "[dim]Last.fm username not found in config.json.[/dim]"
                )
            )
            return

        if store.is_empty:
            self.app.call_from_thread(
                lambda: self.query_one("#journey-status", Label).update(
                    "[dim]Fetching scrobble history… (first run, may take a moment)[/dim]"
                )
            )

            def on_progress(n: int) -> None:
                self.app.call_from_thread(
                    lambda: self.query_one("#journey-status", Label).update(
                        f"[dim]Fetching… {n:,} scrobbles loaded[/dim]"
                    )
                )

            n = store.sync(
                self.app.recommender._network,
                username,
                on_progress=on_progress,
            )
            self.app.call_from_thread(
                lambda: self.query_one("#journey-status", Label).update(
                    f"[dim]{n:,} scrobbles fetched[/dim]"
                )
            )
        else:
            # Render immediately from cache, then sync new entries in background
            self.app.call_from_thread(self._build_heatmap)
            self._sync_new(username)
            return

        self.app.call_from_thread(self._build_heatmap)

    @work(thread=True)
    def _sync_new(self, username: str) -> None:
        n = self.app._scrobble_store.sync(self.app.recommender._network, username)
        if n:
            log.debug("journey: incremental sync added %d scrobbles", n)
            self.app.call_from_thread(self._build_heatmap)

    def _build_heatmap(self) -> None:
        scrobbles = self.app._scrobble_store.scrobbles
        if not scrobbles:
            return

        # Aggregate: artist → {(year, month): count}
        monthly: dict[str, dict[tuple[int, int], int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for artist, ts in scrobbles:
            dt = datetime.datetime.fromtimestamp(ts)
            monthly[artist][(dt.year, dt.month)] += 1

        # Top 25 artists by total scrobbles
        totals = {a: sum(v.values()) for a, v in monthly.items()}
        artists = sorted(totals, key=lambda a: -totals[a])[:25]

        # All months spanned by those artists
        all_months: set[tuple[int, int]] = set()
        for a in artists:
            all_months.update(monthly[a].keys())
        months = sorted(all_months)

        monthly_plain = {a: dict(monthly[a]) for a in artists}

        canvas = self.query_one("#journey-canvas", HeatmapCanvas)
        canvas.load(artists, monthly_plain, months)
        canvas.focus()

        if months:
            s, e = months[0], months[-1]
            self.query_one("#journey-status", Label).update(
                f"[dim]{calendar.month_abbr[s[1]]} {s[0]} – "
                f"{calendar.month_abbr[e[1]]} {e[0]}  ·  "
                f"{len(scrobbles):,} scrobbles  ·  "
                f"↑↓ artist  ·  ←→ scroll  ·  Enter open  ·  R radio[/dim]"
            )

    # ── Canvas events ─────────────────────────────────────────────────────

    def on_heatmap_canvas_range_changed(self, event: HeatmapCanvas.RangeChanged) -> None:
        self.query_one("#journey-heading", Label).update(
            f"[bold]Listening Journey[/bold]  [dim]{event.label}[/dim]"
        )

    def on_heatmap_canvas_artist_selected(
        self, event: HeatmapCanvas.ArtistSelected
    ) -> None:
        self._resolve_and_open(event.name)

    def on_heatmap_canvas_radio_requested(
        self, event: HeatmapCanvas.RadioRequested
    ) -> None:
        self._resolve_and_radio(event.name)

    # ── Navigation ────────────────────────────────────────────────────────

    @work(thread=True)
    def _resolve_and_open(self, name: str) -> None:
        self.app.call_from_thread(
            lambda: self.query_one("#journey-status", Label).update(
                f"[dim]Searching TIDAL for {name}…[/dim]"
            )
        )
        try:
            results = self.app.client.session.search(name, limit=5)
            artists = results.get("artists") or []
            match = next(
                (a for a in artists if getattr(a, "name", "").lower() == name.lower()),
                artists[0] if artists else None,
            )
            if match:
                self.app.call_from_thread(
                    lambda: self.post_message(JourneyScreen.OpenArtist(match))
                )
            else:
                self.app.call_from_thread(
                    lambda: self.query_one("#journey-status", Label).update(
                        f"[dim red]Artist not found on TIDAL: {name}[/dim red]"
                    )
                )
        except Exception as e:
            log.debug("journey: resolve open failed for %s: %s", name, e)

    @work(thread=True)
    def _resolve_and_radio(self, name: str) -> None:
        self.app.call_from_thread(
            lambda: self.query_one("#journey-status", Label).update(
                f"[dim]Building radio for {name}…[/dim]"
            )
        )
        try:
            results = self.app.client.session.search(name, limit=5)
            artists = results.get("artists") or []
            artist = next(
                (a for a in artists if getattr(a, "name", "").lower() == name.lower()),
                artists[0] if artists else None,
            )
            if artist:
                top = self.app.client.get_artist_top_tracks(artist)
                if top:
                    self.app.call_from_thread(
                        lambda: self.post_message(JourneyScreen.OpenRadio(top[0]))
                    )
        except Exception as e:
            log.debug("journey: resolve radio failed for %s: %s", name, e)

    async def on_open_artist(self, event: OpenArtist) -> None:
        from lowtide.screens.artist import ArtistScreen
        await self.app.push_view(ArtistScreen(event.artist))

    async def on_open_radio(self, event: OpenRadio) -> None:
        from lowtide.screens.radio import RadioScreen
        await self.app.push_view(RadioScreen(seed_track=event.seed_track))
