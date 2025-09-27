from __future__ import annotations
import subprocess, shutil, webbrowser
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Label, Button, Input, ListView, ListItem
from textual.containers import Vertical
from textual.events import Key
from lowtide.tidal_client import TidalClient

def open_url(url: str) -> bool:
    try:
        if webbrowser.open_new_tab(url):
            return True
    except Exception:
        pass
    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(["xdg-open", url])
            return True
        except Exception:
            pass
    return False

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
        self.results = ListView(id="results")
        self.results.can_focus = True

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

    async def action_focus_search(self) -> None:
        self.set_focus(self.search_input)

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
        hits = self.client.session.search(query, limit=25)
        self._populate_results(hits)
        # focus list + select first
        self.set_focus(self.results)
        if self.results.children:
            try:
                self.results.index = 0
            except Exception:
                try:
                    self.results.action_cursor_home()
                except Exception:
                    pass
        t, a = len(hits.get("tracks", [])), len(hits.get("albums", []))
        self.status.update(f"Found {t} tracks, {a} albums.")

    def _populate_results(self, hits: dict) -> None:
        self.results.clear()

        # tracks
        for t in hits.get("tracks", []):
            name = getattr(t, "name", "(unknown)")
            artist = getattr(getattr(t, "artist", None), "name", "—")
            album = getattr(getattr(t, "album", None), "name", "—")
            dur = int(getattr(t, "duration", 0))
            m, s = divmod(dur, 60)
            item = ListItem(Label(name), Label(f"{artist} • {album} • {m}:{s:02d}"))
            item.data = ("track", t)
            self.results.append(item)

        # albums
        for a in hits.get("albums", []):
            name = getattr(a, "name", "(unknown)")
            artist = getattr(getattr(a, "artist", None), "name", "—")
            ntracks = getattr(a, "number_of_tracks", 0)
            item = ListItem(Label(name), Label(f"{artist} • {ntracks} tracks"))
            item.data = ("album", a)
            self.results.append(item)

    async def _open_item(self, kind, obj):
        url = f"https://listen.tidal.com/{'track' if kind=='track' else 'album'}/{obj.id}"
        print("Opening:", url)
        ok = open_url(url)
        self.status.update("Opening in TIDAL web player…" if ok else "Failed to open URL.")

    async def on_list_view_submitted(self, event: ListView.Submitted) -> None:
        kind, obj = event.item.data
        await self._open_item(kind, obj)

    async def action_open_selected(self) -> None:
        if not self.results.has_focus:
            self.status.update("Open: list not focused")
            return
        items = [c for c in self.results.children if isinstance(c, ListItem)]
        if not items:
            self.status.update("Open: no results")
            return
        idx = getattr(self.results, "index", 0) or 0
        idx = max(0, min(idx, len(items) - 1))
        kind, obj = items[idx].data
        await self._open_item(kind, obj)

    async def on_key(self, event: Key) -> None:
        if event.key in ("enter", "return") and self.results.has_focus:
            await self.action_open_selected()
            event.stop()
