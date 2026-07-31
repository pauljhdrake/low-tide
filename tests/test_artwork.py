from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lowtide import artwork
from lowtide.artwork import ArtworkError, decode_image, load_album_art


def _png(width=2, height=2) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, format="PNG")
    return output.getvalue()


def test_artwork_byte_and_pixel_limits(monkeypatch):
    with pytest.raises(ArtworkError):
        decode_image(b"x" * (artwork.MAX_ART_BYTES + 1))
    monkeypatch.setattr(artwork, "MAX_ART_PIXELS", 3)
    with pytest.raises(ArtworkError):
        decode_image(_png(2, 2))


def test_local_artwork_must_be_in_configured_library(tmp_path):
    library = tmp_path / "Music"
    library.mkdir()
    cover = library / "cover.png"
    cover.write_bytes(_png())
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())

    image = load_album_art(cover.as_uri(), allowed_roots=[library.resolve()])
    assert image.size == (2, 2)
    with pytest.raises(ArtworkError):
        load_album_art(outside.as_uri(), allowed_roots=[library.resolve()])


@pytest.mark.parametrize("url", ["http://example.invalid/a.jpg", "data:image/png,x"])
def test_remote_artwork_requires_https(url):
    with pytest.raises(ArtworkError):
        load_album_art(url)
