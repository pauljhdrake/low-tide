from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, TabPane, TabbedContent
from tidalapi.playlist import UserPlaylist


def _added_label(playlist) -> str:
    """Label for a playlist the user added rather than created.

    TIDAL's own playlists have creator id 0. For other users TIDAL sends only
    an id, and tidalapi fills in the placeholder name "user", so say "added".
    """
    creator = getattr(playlist, "creator", None)
    if creator is None or getattr(creator, "id", None) == 0:
        return "by TIDAL"
    name = getattr(creator, "name", None)
    if name and name != "user":
        return f"by {name}"
    return "added"


class LibraryScreen(Widget):
    DEFAULT_CSS = """
    LibraryScreen {
        height: 1fr;
        padding: 1 2;
    }
    LibraryScreen #lib-heading {
        text-style: bold;
        margin-bottom: 1;
    }
    LibraryScreen ListView {
        height: 1fr;
        background: transparent;
    }
    LibraryScreen TabbedContent {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Library", id="lib-heading")
        with TabbedContent():
            with TabPane("My Playlists", id="tab-playlists"):
                yield Label("Loading…", id="status-playlists")
                yield ListView(id="list-playlists")
            with TabPane("For You", id="tab-foryou"):
                yield Label("Loading…", id="status-foryou")
                yield ListView(id="list-foryou")
            with TabPane("Mixes", id="tab-mixes"):
                yield Label("Loading…", id="status-mixes")
                yield ListView(id="list-mixes")

    def on_mount(self) -> None:
        self._load_playlists()
        self._load_foryou()
        self._load_mixes()

    @work(thread=True)
    def _load_playlists(self) -> None:
        try:
            items = self.app.client.get_user_playlists()
            self.app.call_from_thread(self._populate_playlists, items)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-playlists", Label).update,
                f"[red]{e}[/red]",
            )

    def _populate_playlists(self, playlists: list) -> None:
        self.query_one("#status-playlists", Label).update(
            f"[dim]{len(playlists)} playlists[/dim]"
        )
        lv = self.query_one("#list-playlists", ListView)
        lv.clear()
        for pl in playlists:
            name = getattr(pl, "name", "?")
            n = getattr(pl, "num_tracks", getattr(pl, "numberOfTracks", ""))
            info = f"{n} tracks"
            if not isinstance(pl, UserPlaylist):
                # Not created by the user: added from someone else.
                info += f" · {_added_label(pl)}"
            item = ListItem(Label(f"{name}  [dim]{info}[/dim]"))
            item._playlist = pl
            lv.append(item)

    @work(thread=True)
    def _load_foryou(self) -> None:
        try:
            page = self.app.client.get_for_you()
            items = []
            for cat in getattr(page, "categories", []):
                for obj in getattr(cat, "items", []):
                    items.append((getattr(cat, "title", ""), obj))
            self.app.call_from_thread(self._populate_foryou, items)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-foryou", Label).update,
                f"[red]{e}[/red]",
            )

    def _populate_foryou(self, items: list) -> None:
        self.query_one("#status-foryou", Label).update(
            f"[dim]{len(items)} items[/dim]"
        )
        lv = self.query_one("#list-foryou", ListView)
        lv.clear()
        for cat_title, obj in items:
            name = getattr(obj, "name", getattr(obj, "title", "?"))
            kind = type(obj).__name__
            item = ListItem(Label(f"{name}  [dim]{cat_title} · {kind}[/dim]"))
            item._obj = obj
            lv.append(item)

    @work(thread=True)
    def _load_mixes(self) -> None:
        try:
            mixes = self.app.client.get_mixes().categories
            items = []
            for cat in mixes:
                for obj in getattr(cat, "items", []):
                    items.append(obj)
            self.app.call_from_thread(self._populate_mixes, items)
        except Exception as e:
            self.app.call_from_thread(
                self.query_one("#status-mixes", Label).update,
                f"[red]{e}[/red]",
            )

    def _populate_mixes(self, mixes: list) -> None:
        self.query_one("#status-mixes", Label).update(
            f"[dim]{len(mixes)} mixes[/dim]"
        )
        lv = self.query_one("#list-mixes", ListView)
        lv.clear()
        for mix in mixes:
            name = getattr(mix, "title", getattr(mix, "name", "?"))
            sub = getattr(mix, "subTitle", getattr(mix, "sub_title", ""))
            item = ListItem(Label(f"{name}  [dim]{sub}[/dim]" if sub else name))
            item._mix = mix
            lv.append(item)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        from lowtide.screens.playlist import PlaylistScreen

        # Playlist
        pl = getattr(event.item, "_playlist", None)
        if pl:
            await self.app.push_view(PlaylistScreen(pl))
            return
        # For You item
        obj = getattr(event.item, "_obj", None)
        if obj:
            await self.app.open_object(obj)
            return
        # Mix
        mix = getattr(event.item, "_mix", None)
        if mix:
            await self.app.open_object(mix)
