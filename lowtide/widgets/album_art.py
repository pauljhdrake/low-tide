from __future__ import annotations

from io import BytesIO

import requests
from PIL import Image as PILImage
from textual import work
from textual.app import ComposeResult
from textual.widget import Widget
from textual_image.widget import Image as TImage


class AlbumArt(Widget):
    """Downloads a TIDAL album art URL and renders it using the best available protocol."""

    DEFAULT_CSS = """
    AlbumArt {
        width: 14;
        height: 7;
        background: transparent;
    }
    AlbumArt #art-img {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_url: str | None = None

    def compose(self) -> ComposeResult:
        yield TImage(id="art-img")

    def load(self, url: str | None) -> None:
        if not url or url == self._current_url:
            return
        self._current_url = url
        self._fetch(url)

    def clear(self) -> None:
        self._current_url = None
        try:
            self.query_one(TImage).image = None
        except Exception:
            pass

    @work(thread=True)
    def _fetch(self, url: str) -> None:
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            img = PILImage.open(BytesIO(resp.content)).convert("RGB")
            self.app.call_from_thread(self._apply, img)
        except Exception:
            pass

    def _apply(self, img: PILImage.Image) -> None:
        widget = self.query_one(TImage)
        widget.image = img
        widget.refresh(layout=True)
