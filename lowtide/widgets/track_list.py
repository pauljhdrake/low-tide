from __future__ import annotations

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable


class TrackList(Widget):
    """Reusable scrollable track table. Fires TrackList.TrackSelected on activation."""

    DEFAULT_CSS = """
    TrackList {
        height: 1fr;
    }
    TrackList DataTable {
        height: 1fr;
    }
    """

    class TrackSelected(Message):
        def __init__(self, track, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    class TrackAppendRequested(Message):
        def __init__(self, track, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    class TrackRadioRequested(Message):
        def __init__(self, track) -> None:
            super().__init__()
            self.track = track

    def __init__(self, tracks: Optional[list] = None, **kwargs):
        super().__init__(**kwargs)
        self._tracks: list = tracks or []

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("#", "Title", "Artist", "Album", "Time")
        self._fill(table)

    def load(self, tracks: list) -> None:
        self._tracks = tracks
        table = self.query_one(DataTable)
        table.clear()
        self._fill(table)
        table.focus()

    def _fill(self, table: DataTable) -> None:
        for i, t in enumerate(self._tracks):
            name = getattr(t, "name", "?")
            artist = getattr(getattr(t, "artist", None), "name", "–")
            album = getattr(getattr(t, "album", None), "name", "–")
            dur = int(getattr(t, "duration", 0))
            m, s = divmod(dur, 60)
            table.add_row(str(i + 1), name, artist, album, f"{m}:{s:02d}", key=str(i))

    def _row_key_to_index(self, row_key) -> int | None:
        if row_key and row_key.value is not None:
            idx = int(str(row_key.value))
            if 0 <= idx < len(self._tracks):
                return idx
        return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = self._row_key_to_index(event.row_key)
        if idx is not None:
            self.post_message(self.TrackSelected(self._tracks[idx], idx))

    def on_data_table_row_clicked(self, event: DataTable.RowClicked) -> None:
        idx = self._row_key_to_index(event.row_key)
        if idx is not None:
            self.post_message(self.TrackSelected(self._tracks[idx], idx))

    def on_key(self, event) -> None:
        idx = self.query_one(DataTable).cursor_row
        if event.key == "a":
            if 0 <= idx < len(self._tracks):
                event.stop()
                self.post_message(self.TrackAppendRequested(self._tracks[idx], idx))
        elif event.key == "R":
            if 0 <= idx < len(self._tracks):
                event.stop()
                self.post_message(self.TrackRadioRequested(self._tracks[idx]))

    @property
    def tracks(self) -> list:
        return self._tracks
