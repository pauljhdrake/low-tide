from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label

from lowtide.recommender import DiscoveryMode
from lowtide.widgets.orbit import Orbit, OrbitNode

log = logging.getLogger(__name__)

_MODE_LABELS = {
    DiscoveryMode.ESSENTIAL:   "Essential",
    DiscoveryMode.BALANCED:    "Balanced",
    DiscoveryMode.ADVENTUROUS: "Adventurous",
}

_NO_LASTFM = (
    "Orbit needs Last.fm for similar-artist data. "
    "Add a lastfm block to config.json (see the README) and restart."
)

_TRAIL_LEN = 5


def _arrange(nodes: list[OrbitNode]) -> list[OrbitNode]:
    """Alternate strong and weak matches around the ring, so near and far
    nodes interleave and labels don't bunch up on one side."""
    nodes = sorted(nodes, key=lambda n: -n.match)
    return nodes[0::2] + nodes[1::2][::-1]


class OrbitScreen(Widget):
    """
    Artist similarity map: one artist in the centre, Last.fm similar artists
    around it. Enter re-centres on the selected artist, so you can walk
    outward from somewhere familiar.
    """

    class OpenArtist(Message):
        def __init__(self, artist) -> None:
            super().__init__()
            self.artist = artist

    class OpenRadio(Message):
        def __init__(self, seed_track) -> None:
            super().__init__()
            self.seed_track = seed_track

    BINDINGS = [
        Binding("backspace", "back", "Back", show=False),
        Binding("o", "open_artist", "Open artist", show=False),
        Binding("R", "radio", "Artist radio", show=False),
        Binding("c", "centre_now_playing", "Now playing", show=False),
        Binding("D", "cycle_dial", "Discovery dial", show=True),
    ]

    DEFAULT_CSS = """
    OrbitScreen {
        height: 1fr;
        padding: 1 2;
    }
    OrbitScreen #orbit-heading { text-style: bold; }
    OrbitScreen #orbit-dial { margin-top: 1; }
    OrbitScreen #orbit-trail { height: 1; color: $text-muted; margin-top: 1; }
    OrbitScreen #orbit-info { height: 1; color: $text-muted; }
    OrbitScreen #orbit-status { height: 1; color: $text-muted; }
    OrbitScreen #orbit-keys { height: 1; color: $text-disabled; }
    """

    def __init__(self, artist_name: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._start = artist_name
        self._mode = DiscoveryMode.BALANCED
        self._trail: list[str] = []
        self._gen = 0

    def compose(self) -> ComposeResult:
        yield Label("Orbit", id="orbit-heading")
        yield Label(self._dial_label(), id="orbit-dial")
        yield Label("", id="orbit-trail")
        yield Orbit(id="orbit")
        yield Label("", id="orbit-info")
        yield Label("", id="orbit-status")
        yield Label(
            "arrows move · enter centre · backspace back · o open · R radio · c now playing",
            id="orbit-keys",
        )

    def on_mount(self) -> None:
        self.query_one(Orbit).focus()
        if not self.app.recommender.has_lastfm:
            self._status(f"[yellow]⚡ {_NO_LASTFM}[/yellow]")
            return
        self._pick_start(self._start)

    # ── Loading ───────────────────────────────────────────────────────────

    @work(thread=True)
    def _pick_start(self, name: str | None) -> None:
        """Start from the given artist, else whoever is playing, else your
        most-played artist."""
        if not name:
            track = getattr(self.app, "_current_track", None)
            name = getattr(getattr(track, "artist", None), "name", None)
        if not name:
            top = self.app._play_count_store.top_artists(limit=1)
            if top:
                # Play counts are stored lower-cased; ask Last.fm for the
                # proper spelling so the centre label looks right.
                name = self.app.recommender.artist_display_name(top[0][0])
        if not name:
            self.app.call_from_thread(
                self._status,
                "[dim]Nothing to start from yet. Play something, or press O on an artist page.[/dim]",
            )
            return
        self.app.call_from_thread(self._go, name)

    def _go(self, name: str, push: bool = True) -> None:
        if push:
            self._trail.append(name)
        self._gen += 1
        self.query_one(Orbit).set_orbit(name, [], animate=False)
        self._update_trail()
        self._status(f"[dim]Finding artists similar to {name}…[/dim]")
        self._load(name, self._mode, self._gen)

    @work(thread=True, group="orbit-load")
    def _load(self, name: str, mode: DiscoveryMode, gen: int) -> None:
        similar = self.app.recommender.similar_artists(name, mode)
        plays = self.app._play_count_store.artist_plays()
        nodes = [
            OrbitNode(name=n, match=m, plays=plays.get(n.lower()) or None)
            for n, m in similar
        ]
        self.app.call_from_thread(self._show, name, nodes, gen)

    def _show(self, name: str, nodes: list[OrbitNode], gen: int) -> None:
        if gen != self._gen:
            return  # the user has moved on since this was requested
        self.query_one(Orbit).set_orbit(name, _arrange(nodes))
        self._status("" if nodes else f"[dim]Last.fm has no similar artists for {name}[/dim]")

    # ── UI helpers ────────────────────────────────────────────────────────

    def _status(self, text: str) -> None:
        self.query_one("#orbit-status", Label).update(text)

    def _dial_label(self) -> str:
        parts = []
        for mode, name in _MODE_LABELS.items():
            parts.append(f"[bold]{name}[/bold]" if mode == self._mode else f"[dim]{name}[/dim]")
        return "  ".join(parts) + "  [dim](D)[/dim]"

    def _update_trail(self) -> None:
        trail = self._trail[-_TRAIL_LEN:]
        text = " › ".join(trail)
        if len(self._trail) > _TRAIL_LEN:
            text = "… › " + text
        self.query_one("#orbit-trail", Label).update(text)

    def on_orbit_selected(self, event: Orbit.Selected) -> None:
        node = event.node
        info = self.query_one("#orbit-info", Label)
        if node is None:
            info.update("")
            return
        plays = f"{node.plays:,} plays" if node.plays else "no plays yet"
        info.update(f"[b]{node.name}[/b]  ·  {round(node.match * 100)}% similar  ·  {plays}")

    def on_orbit_activated(self, event: Orbit.Activated) -> None:
        self._go(event.node.name)

    # ── Actions ───────────────────────────────────────────────────────────

    def action_back(self) -> None:
        if len(self._trail) > 1:
            self._trail.pop()
            self._go(self._trail[-1], push=False)

    def action_cycle_dial(self) -> None:
        self._mode = DiscoveryMode((self._mode + 1) % len(DiscoveryMode))
        self.query_one("#orbit-dial", Label).update(self._dial_label())
        if self._trail:
            self._go(self._trail[-1], push=False)

    def action_centre_now_playing(self) -> None:
        track = getattr(self.app, "_current_track", None)
        name = getattr(getattr(track, "artist", None), "name", None)
        if not name:
            self.app.notify("Nothing playing")
        elif not self._trail or self._trail[-1].lower() != name.lower():
            self._go(name)

    def _target(self) -> str | None:
        """The selected artist, or the centre one if nothing is selected."""
        node = self.query_one(Orbit).selected_node
        if node is not None:
            return node.name
        return self._trail[-1] if self._trail else None

    def action_open_artist(self) -> None:
        name = self._target()
        if name:
            self._resolve(name, radio=False)

    def action_radio(self) -> None:
        name = self._target()
        if name:
            self._resolve(name, radio=True)

    @work(thread=True)
    def _resolve(self, name: str, radio: bool) -> None:
        """Find the artist on TIDAL, then open their page or start a radio
        seeded from their top track."""
        doing = "Building radio for" if radio else "Searching TIDAL for"
        self.app.call_from_thread(self._status, f"[dim]{doing} {name}…[/dim]")
        try:
            artists = self.app.client.search(name, limit=5).get("artists") or []
            artist = next(
                (a for a in artists if getattr(a, "name", "").lower() == name.lower()),
                artists[0] if artists else None,
            )
            if artist is None:
                self.app.call_from_thread(
                    self._status, f"[dim red]Artist not found on TIDAL: {name}[/dim red]"
                )
                return
            if radio:
                top = self.app.client.get_artist_top_tracks(artist)
                if not top:
                    self.app.call_from_thread(
                        self._status, f"[dim red]No tracks on TIDAL for {name}[/dim red]"
                    )
                    return
                message = OrbitScreen.OpenRadio(top[0])
            else:
                message = OrbitScreen.OpenArtist(artist)
            self.app.call_from_thread(self._status, "")
            self.app.call_from_thread(self.post_message, message)
        except Exception as e:
            log.debug("orbit: resolve failed for %s: %s", name, e)
            self.app.call_from_thread(self._status, f"[dim red]Couldn't reach TIDAL: {e}[/dim red]")

    async def on_orbit_screen_open_artist(self, event: OpenArtist) -> None:
        from lowtide.screens.artist import ArtistScreen
        await self.app.push_view(ArtistScreen(event.artist))

    async def on_orbit_screen_open_radio(self, event: OpenRadio) -> None:
        from lowtide.screens.radio import RadioScreen
        await self.app.push_view(RadioScreen(seed_track=event.seed_track))
