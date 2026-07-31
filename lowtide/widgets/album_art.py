from __future__ import annotations

import logging

from PIL import Image as PILImage
from textual import work
from textual.app import ComposeResult
from textual.widget import Widget
from textual_image.widget import Image as TImage

from lowtide.artwork import load_album_art

log = logging.getLogger(__name__)


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
        except Exception as error:
            log.debug("album artwork clear failed (%s)", type(error).__name__)

    @work(thread=True)
    def _fetch(self, url: str) -> None:
        try:
            img = load_album_art(url)
            self.app.call_from_thread(self._apply, img, url)
        except (OSError, RuntimeError, ValueError) as error:
            log.debug("album artwork unavailable (%s)", type(error).__name__)

    def _apply(self, img: PILImage.Image, url: str) -> None:
        if url != self._current_url:
            return  # a newer track started before this fetch completed
        widget = self.query_one(TImage)
        widget.image = img
        widget.refresh(layout=True)
