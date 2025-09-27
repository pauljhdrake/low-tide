from __future__ import annotations
import webbrowser
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Label, Button, Input, ListView, ListItem
from textual.containers import Vertical
from lowtide.tidal_client import TidalClient

def display_user(user) -> str:
    for attr in ("name", "username", "userName", "full_name", "email"):
        val = getattr(user, attr, None)
        if val:
            return str(val)
    return f"id {getattr(user, 'id', '?')}"

class LowTideApp(App):
    CSS = """
    Screen { align: center middle; }
    #box { width: 80; border: round; padding: 1 2; }
    #results { height: 16; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("/", "focus_search", "Search"),
        ("enter", "open_selected", "Open"),
    ]

    def __init__(self, client: TidalClient):
        super().__init__()
        self.client = client
        self.status = Label("Ready.")
        self.user_label = Label("")
        self.search_input = Input(placeholder="Type to search… (press Enter)")
        self.results = ListView()
        self._search_hits = []  # list of tuples: (kind, obj)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="box"):
            yield Label("low-tide — TIDAL in your terminal", id="title")
            yield self.status
            yield self.user_label
            yield self.search_input
            yield Label("Results:")
            yield self.results
            yield Button("Refresh user", id="refresh-user")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            user = self.client.me()
            self.status.update("Logged in ✔")
            self.user_label.update(f"Hello, {display_user(user)} (user id: {getattr(user, 'id', '?')})")
        except Exception as e:
            self.status.update(f"Not logged in: {e!r}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-user":
            try:
                user = self.client.me()
                self.status.update("Logged in ✔")
                self.user_label.update(f"Hello, {display_user(user)} (user id: {getattr(user, 'id', '?')})")
            except Exception as e:
                self.status.update(f"Not logged in: {e!r}")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self.status.update(f"Searching “{query}”…")
        # Use tidalapi search via underlying session
        s = self.client.session
        # Returns dict-like: tracks, albums, artists, playlists (lists of objects)
        hits = s.search(query, limit=25)
        self._populate_results(hits)
        self.status.update(f"Found {len(hits.get('tracks', []))} tracks, {len(hits.get('albums', []))} albums.")

    def _populate_results(self, hits: dict) -> None:
        self.results.clear()
        self._search_hits.clear()

        # Show tracks first
        for t in hits.get("tracks", []):
            primary = f"{getattr(t, 'name', '(unknown)')}"
            artist = getattr(getattr(t, 'artist', None), 'name', '—')
            album = getattr(getattr(t, 'album', None), 'name', '—')
            dur = getattr(t, 'duration', 0)
            m, s = divmod(int(dur), 60)
            secondary = f"{artist} • {album} • {m}:{s:02d}"
            item = ListItem(Label(primary), Label(secondary))
            item.data = ("track", t)
            self.results.append(item)
            self._search_hits.append(("track", t))

        # Then albums
        for a in hits.get("albums", []):
            primary = f"{getattr(a, 'name', '(unknown)')}"
            artist = getattr(getattr(a, 'artist', None), 'name', '—')
            tracks = getattr(a, 'number_of_tracks', 0)
            secondary = f"{artist} • {tracks} tracks"
            item = ListItem(Label(primary), Label(secondary))
            item.data = ("album", a)
            self.results.append(item)
            self._search_hits.append(("album", a))

    async def action_focus_search(self) -> None:
        self.set_focus(self.search_input)

    async def action_open_selected(self) -> None:
        if self.results.index is None:
            return
        item = self.results.get_child_at_index(self.results.index)
        kind, obj = item.data  # ("track"|"album", tidalapi object)
        if kind == "track":
            webbrowser.open(f"https://tidal.com/browse/track/{obj.id}")
            self.status.update("Opening track in TIDAL web player…")
        elif kind == "album":
            webbrowser.open(f"https://tidal.com/browse/album/{obj.id}")
            self.status.update("Opening album in TIDAL web player…")
