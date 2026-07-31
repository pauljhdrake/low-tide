"""Bounded and validated loading of remote and local album artwork."""

from __future__ import annotations

import ipaddress
import os
import socket
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from lowtide.secure_io import get_config_dir, read_json

MAX_ART_BYTES = 5 * 1024 * 1024
MAX_ART_PIXELS = 25_000_000
MAX_REDIRECTS = 3
_CONTENT_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_FORMATS = {"GIF", "JPEG", "PNG", "WEBP"}


class ArtworkError(ValueError):
    """An artwork source failed validation."""


def _configured_music_roots() -> list[Path]:
    config_path = os.path.join(get_config_dir(), "config.json")
    try:
        config = read_json(config_path, default={})
    except (OSError, ValueError):
        return []
    if not isinstance(config, dict):
        return []
    raw = config.get("music_dir")
    values = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
    roots: list[Path] = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            root = Path(os.path.expanduser(value)).resolve(strict=True)
        except OSError:
            continue
        if root.is_dir():
            roots.append(root)
    return roots


def _validate_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ArtworkError("remote artwork must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ArtworkError("remote artwork URL has invalid authority")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ArtworkError("remote artwork hostname could not be resolved") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ArtworkError("remote artwork resolved to a non-public address")


def _read_remote(url: str) -> bytes:
    current = url
    with requests.Session() as session:
        session.headers["User-Agent"] = "low-tide/0.1"
        for redirect in range(MAX_REDIRECTS + 1):
            _validate_https_url(current)
            with session.get(
                current,
                timeout=(3.05, 10),
                stream=True,
                allow_redirects=False,
            ) as response:
                if response.is_redirect or response.is_permanent_redirect:
                    if redirect >= MAX_REDIRECTS:
                        raise ArtworkError("remote artwork exceeded redirect limit")
                    location = response.headers.get("Location")
                    if not location:
                        raise ArtworkError("remote artwork redirect had no location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type not in _CONTENT_TYPES:
                    raise ArtworkError("remote artwork has an unsupported content type")
                raw_length = response.headers.get("Content-Length")
                if raw_length:
                    try:
                        if int(raw_length) > MAX_ART_BYTES:
                            raise ArtworkError("remote artwork exceeds size limit")
                    except ValueError as error:
                        raise ArtworkError("remote artwork has an invalid content length") from error
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_ART_BYTES:
                        raise ArtworkError("remote artwork exceeds size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ArtworkError("remote artwork redirect handling failed")


def _read_local(url: str, allowed_roots: list[Path] | None) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "file" or parsed.netloc:
        raise ArtworkError("invalid local artwork URL")
    roots = allowed_roots if allowed_roots is not None else _configured_music_roots()
    try:
        candidate = Path(unquote(parsed.path)).resolve(strict=True)
    except OSError as error:
        raise ArtworkError("local artwork is unavailable") from error
    if not candidate.is_file() or not any(
        candidate == root or candidate.is_relative_to(root) for root in roots
    ):
        raise ArtworkError("local artwork is outside the configured library")
    try:
        size = candidate.stat().st_size
    except OSError as error:
        raise ArtworkError("local artwork is unavailable") from error
    if size > MAX_ART_BYTES:
        raise ArtworkError("local artwork exceeds size limit")
    with candidate.open("rb") as handle:
        return handle.read(MAX_ART_BYTES + 1)


def decode_image(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_ART_BYTES:
        raise ArtworkError("artwork data has an invalid size")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                if probe.format not in _FORMATS:
                    raise ArtworkError("artwork has an unsupported image format")
                width, height = probe.size
                if width <= 0 or height <= 0 or width * height > MAX_ART_PIXELS:
                    raise ArtworkError("artwork exceeds decoded pixel limit")
                probe.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
                return image.convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError) as error:
        raise ArtworkError("artwork image validation failed") from error


def load_album_art(url: str, *, allowed_roots: list[Path] | None = None) -> Image.Image:
    parsed = urlsplit(url)
    if parsed.scheme.lower() == "file":
        data = _read_local(url, allowed_roots)
    elif parsed.scheme.lower() == "https":
        data = _read_remote(url)
    else:
        raise ArtworkError("unsupported artwork URL scheme")
    return decode_image(data)
