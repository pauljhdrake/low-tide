from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, Tree

log = logging.getLogger(__name__)


def _tier_char(rank: int) -> str:
    return "★" if rank < 5 else "◆"


def _sim_bar(match: float, width: int = 10) -> str:
    filled = round(max(0.0, min(1.0, match)) * width)
    return "█" * filled + "░" * (width - filled)


class _InfoPanel(Widget):
    DEFAULT_CSS = """
    _InfoPanel {
        width: 30;
        height: 100%;
        border-left: tall $primary-darken-3;
        padding: 1 2;
        background: transparent;
    }
    _InfoPanel #info-name { text-style: bold; margin-bottom: 0; }
    _InfoPanel #info-meta { color: $text-muted; margin-bottom: 1; }
    _InfoPanel #info-sep  { color: $text-disabled; margin-bottom: 1; }
    _InfoPanel #info-heading { color: $text-muted; margin-bottom: 0; }
    _InfoPanel #info-similar { color: $text-muted; height: 1fr; }
    _InfoPanel #info-hints { color: $text-disabled; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Label("", id="info-name")
        yield Label("", id="info-meta")
        yield Label("", id="info-sep")
        yield Label("", id="info-heading")
        yield Label("", id="info-similar")
        yield Label(
            "[dim]Enter  open artist[/dim]\n[dim]R      artist radio[/dim]",
            id="info-hints",
        )

    def show(
        self,
        name: str,
        rank: int | None,
        plays: int | None,
        similar_names: list[str],
    ) -> None:
        tier = _tier_char(rank) if rank is not None else "•"
        self.query_one("#info-name", Label).update(f"{tier} [bold]{name}[/bold]")
        if plays is not None and rank is not None:
            meta = f"[dim]#{rank + 1} · {plays:,} plays[/dim]"
        elif plays is not None:
            meta = f"[dim]{plays:,} plays[/dim]"
        else:
            meta = "[dim]not in library[/dim]"
        self.query_one("#info-meta", Label).update(meta)
        self.query_one("#info-sep", Label).update("[dim]──────────────────────────[/dim]")
        if similar_names:
            self.query_one("#info-heading", Label).update("[dim]Similar artists in view:[/dim]")
            lines = "\n".join(f"[dim]• {n}[/dim]" for n in similar_names[:8])
            self.query_one("#info-similar", Label).update(lines)
        else:
            self.query_one("#info-heading", Label).update("")
            self.query_one("#info-similar", Label).update(
                "[dim]Expand to load similar…[/dim]"
            )


class ConstellationScreen(Widget):
    """
    Artist similarity explorer. Top 20 most-played artists as primary nodes;
    expand any to load their Last.fm similar artists as children.
    """

    class OpenArtist(Message):
        def __init__(self, artist) -> None:
            super().__init__()
            self.artist = artist

    class OpenRadio(Message):
        def __init__(self, seed_track) -> None:
            super().__init__()
            self.seed_track = seed_track

    BINDINGS = [Binding("r", "radio", "Artist Radio", show=False)]

    DEFAULT_CSS = """
    ConstellationScreen {
        height: 1fr;
    }
    ConstellationScreen Vertical {
        height: 1fr;
    }
    ConstellationScreen Horizontal {
        height: 1fr;
    }
    ConstellationScreen Tree {
        height: 1fr;
        background: transparent;
    }
    ConstellationScreen #const-status {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal():
                yield Tree("Artists", id="const-tree", show_root=False)
                yield _InfoPanel(id="info-panel")
            yield Label("Loading…", id="const-status")

    def on_mount(self) -> None:
        self._artist_plays: dict[str, int] = {}  # name.lower() → total plays
        self._top_names: list[str] = []           # original-case names in rank order
        self._highlighted = None
        self._load_top_artists()

    # ── Data loading ──────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_top_artists(self) -> None:
        top = self.app._play_count_store.top_artists(limit=200)
        self.app.call_from_thread(self._populate_top, top)

    def _populate_top(self, top: list[tuple[str, int]]) -> None:
        self._artist_plays = {n.lower(): p for n, p in top}
        self._top_names = [n for n, _ in top]

        tree = self.query_one("#const-tree", Tree)
        tree.clear()

        top20 = top[:20]
        if not top20:
            self.query_one("#const-status", Label).update(
                "[dim]No play history yet — listen to some music first[/dim]"
            )
            return

        for i, (name, plays) in enumerate(top20):
            label = (
                f"{_tier_char(i)} [bold]{name}[/bold]  "
                f"[dim]#{i + 1} · {plays:,} plays[/dim]"
            )
            node = tree.root.add(label, expand=False)
            node._artist_name = name
            node._rank = i
            node._plays = plays
            node._similar_loaded = False
            # Placeholder child so the expand toggle appears
            ph = node.add_leaf("[dim]…[/dim]")
            ph._is_placeholder = True

        self.query_one("#const-status", Label).update(
            f"[dim]{len(top20)} artists  ·  → expand  ·  Enter open  ·  R radio[/dim]"
        )
        tree.focus()

    @work(thread=True)
    def _load_similar(self, node, artist_name: str) -> None:
        if not self.app.recommender.has_lastfm:
            self.app.call_from_thread(
                lambda: self.query_one("#const-status", Label).update(
                    "[dim]Last.fm not configured — similar artists unavailable[/dim]"
                )
            )
            return

        similar: list[tuple[str, float]] = []
        try:
            la = self.app.recommender._network.get_artist(artist_name)
            for item in la.get_similar(limit=12):
                sim_name = getattr(item.item, "name", "") or ""
                match = float(item.match)
                if sim_name:
                    similar.append((sim_name, match))
        except Exception as e:
            log.debug("constellation: get_similar failed for %s: %s", artist_name, e)

        self.app.call_from_thread(self._populate_similar, node, similar)

    def _populate_similar(
        self, node: object, similar: list[tuple[str, float]]
    ) -> None:
        node.remove_children()

        for sim_name, match in similar:
            plays = self._artist_plays.get(sim_name.lower())
            bar = _sim_bar(match)
            if plays is not None:
                try:
                    ri = next(
                        i
                        for i, n in enumerate(self._top_names)
                        if n.lower() == sim_name.lower()
                    )
                    tier = _tier_char(ri)
                except StopIteration:
                    tier = "◆"
                label = (
                    f"{tier} [bold]{sim_name}[/bold]  "
                    f"[dim]{plays:,} plays[/dim]  "
                    f"[cyan]{bar}[/cyan]  [dim]{match:.0%}[/dim]"
                )
            else:
                label = (
                    f"• {sim_name}  "
                    f"[dim]not in library[/dim]  "
                    f"[dim]{bar}  {match:.0%}[/dim]"
                )

            leaf = node.add_leaf(label)
            leaf._artist_name = sim_name
            leaf._rank = None
            leaf._plays = plays

        n_lib = sum(
            1 for n, _ in similar if self._artist_plays.get(n.lower()) is not None
        )
        status = f"[dim]{node._artist_name}: {len(similar)} similar"
        if n_lib:
            status += f"  ·  {n_lib} in your library"
        status += "[/dim]"
        self.query_one("#const-status", Label).update(status)

        # Refresh the info panel if this node is currently highlighted
        if self._highlighted is not None and self._highlighted is node:
            self._update_panel(node)

    # ── Tree events ───────────────────────────────────────────────────────────

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        if getattr(node, "_similar_loaded", True):
            return
        node._similar_loaded = True
        node.remove_children()
        ph = node.add_leaf("[dim]Loading…[/dim]")
        ph._is_placeholder = True
        self._load_similar(node, node._artist_name)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._highlighted = event.node
        self._update_panel(event.node)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        name = getattr(event.node, "_artist_name", None)
        if name:
            self._resolve_and_open(name)

    def _update_panel(self, node: object) -> None:
        name = getattr(node, "_artist_name", None)
        if not name:
            return

        rank: int | None = getattr(node, "_rank", None)
        plays: int | None = getattr(node, "_plays", None)
        if plays is None:
            plays = self._artist_plays.get(name.lower())

        # Collect similar names shown in the tree for the right panel
        similar_names: list[str] = []
        tree = self.query_one("#const-tree", Tree)
        if node.parent is tree.root:
            # Top artist node — show its loaded children
            for child in node.children:
                cname = getattr(child, "_artist_name", None)
                if cname and not getattr(child, "_is_placeholder", False):
                    similar_names.append(cname)
        else:
            # Similar artist leaf — show siblings
            for sib in node.parent.children:
                sname = getattr(sib, "_artist_name", None)
                if sname and sname != name and not getattr(sib, "_is_placeholder", False):
                    similar_names.append(sname)

        self.query_one("#info-panel", _InfoPanel).show(name, rank, plays, similar_names)

    # ── Navigation actions ────────────────────────────────────────────────────

    def action_radio(self) -> None:
        node = self._highlighted
        if node is None:
            return
        name = getattr(node, "_artist_name", None)
        if name:
            self._resolve_and_radio(name)

    @work(thread=True)
    def _resolve_and_open(self, name: str) -> None:
        self.app.call_from_thread(
            lambda: self.query_one("#const-status", Label).update(
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
                    lambda: self.post_message(ConstellationScreen.OpenArtist(match))
                )
            else:
                self.app.call_from_thread(
                    lambda: self.query_one("#const-status", Label).update(
                        f"[dim red]Artist not found on TIDAL: {name}[/dim red]"
                    )
                )
        except Exception as e:
            log.debug("constellation: resolve open failed for %s: %s", name, e)

    @work(thread=True)
    def _resolve_and_radio(self, name: str) -> None:
        self.app.call_from_thread(
            lambda: self.query_one("#const-status", Label).update(
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
                        lambda: self.post_message(ConstellationScreen.OpenRadio(top[0]))
                    )
        except Exception as e:
            log.debug("constellation: resolve radio failed for %s: %s", name, e)

    # ── Message handlers (async navigation, runs on main event loop) ──────────

    async def on_open_artist(self, event: OpenArtist) -> None:
        from lowtide.screens.artist import ArtistScreen
        await self.app.push_view(ArtistScreen(event.artist))

    async def on_open_radio(self, event: OpenRadio) -> None:
        from lowtide.screens.radio import RadioScreen
        await self.app.push_view(RadioScreen(seed_track=event.seed_track))
