#!/usr/bin/env python3
"""
Import a podcast episode's playlist into TIDAL.

Usage:
    .venv/bin/python scripts/import-podcast.py <feed-url> [--episode N]

Fetches an RSS feed, lets you pick an episode, extracts the track list from
the show notes ('Title' by Artist lines in a <ul>), resolves each track on
TIDAL, and creates a playlist. Requires an active low-tide session
(~/.config/low-tide/session.json).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

# Allow running from the repo root without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

from lowtide.tidal_client import TidalClient


# ── HTML parsing ──────────────────────────────────────────────────────────────

class _LiExtractor(HTMLParser):
    """Pull plain text out of every <li> element."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._buf = ""
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "li":
            self._depth += 1
            if self._depth == 1:
                self._buf = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._depth > 0:
            self._depth -= 1
            if self._depth == 0:
                text = self._buf.strip().rstrip(",\xa0").strip()
                if text:
                    self.items.append(text)

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._buf += data


# ── Track line parsing ─────────────────────────────────────────

# Separator alternatives (tried in order -- "- by" must precede bare "-"):
#   " - by "   ->  "Runaway - by Nuyorican Soul"
#   " by "     ->  standard
#   " - " / en-dash  ->  dash-separated episodes
_SEP = r"(?:\s*[-–]\s+by\s+|\s+by\s+|\s*[-–]\s+)"

# 'Title' by/- Artist  or  “Title” by/- Artist  (handles smart/curly quotes too)
_QUOTED = re.compile(
    r"^[‘’“”'\"](.+?)[‘’“”'\"]" + _SEP + r"(.+)$"
)
_UNQUOTED = re.compile(r"^(.+?)" + _SEP + r"(.+)$")

# Best-effort: playlist name in a <strong> tag after "playlist is called"
_PLAYLIST_NAME = re.compile(
    r"playlist is called\s*<[^>]+>\s*[‘“'\"](.+?)[’”'\"]",
    re.IGNORECASE,
)


def _clean_artist(artist: str) -> str:
    """Strip featuring clauses that aren't the main artist name."""
    # "featuring X - RealArtist" (main artist at end after last dash)
    m = re.match(r"^featuring\s+.+[-–]\s*(.+)$", artist, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # "RealArtist - featuring X"
    cleaned = re.sub(r"\s*[-–]\s*featuring\s+.+$", "", artist, flags=re.IGNORECASE)
    return cleaned.strip()


_QUOTE_CHARS = "‘’“”'\""


def _parse_track_line(text: str) -> tuple[str, str] | None:
    """Return (title, artist) or None."""
    text = text.replace("\xa0", " ").strip()
    for pattern in (_QUOTED, _UNQUOTED):
        m = pattern.match(text)
        if m:
            # Strip outer quote chars from title regardless of which pattern matched --
            # handles cases like "'I've Got News for You'" where the apostrophe in the
            # title fools the quoted regex into falling through to the unquoted one
            title = m.group(1).strip().strip(_QUOTE_CHARS)
            return title, _clean_artist(m.group(2).strip())
    return None

# ── RSS feed ──────────────────────────────────────────────────────────────────

def _fetch_feed(url: str) -> tuple[str, list[dict]]:
    """Return (show_title, episodes) where episodes are newest-first dicts."""
    print("Fetching feed…")
    req = urllib.request.Request(url, headers={"User-Agent": "low-tide/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()

    root = ET.fromstring(raw)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    channel = root.find("channel")
    if channel is None:
        raise ValueError("No <channel> element in feed")

    title_el = channel.find("title")
    show_title = (title_el.text or "").strip() if title_el is not None else "Unknown show"

    episodes = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        encoded_el = item.find("content:encoded", ns)

        desc_html = ""
        if encoded_el is not None and encoded_el.text:
            desc_html = encoded_el.text
        elif desc_el is not None and desc_el.text:
            desc_html = desc_el.text

        episodes.append({
            "title": (title_el.text or "").strip() if title_el is not None else "",
            "description_html": desc_html,
        })

    return show_title, episodes


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("feed_url", metavar="FEED_URL", help="Podcast RSS feed URL")
    parser.add_argument("--episode", type=int, metavar="N", default=None,
                        help="Episode to import (1 = most recent). Prompts if omitted.")
    parser.add_argument("--debug", action="store_true",
                        help="Print raw TIDAL results for tracks that aren't matched.")
    args = parser.parse_args()

    show_title, episodes = _fetch_feed(args.feed_url)
    print(f"Show: {show_title}  ({len(episodes)} episodes)")

    if not episodes:
        print("error: no episodes found", file=sys.stderr)
        sys.exit(1)

    # Episode selection
    if args.episode is not None:
        n = args.episode
        if not 1 <= n <= len(episodes):
            print(f"error: --episode must be between 1 and {len(episodes)}", file=sys.stderr)
            sys.exit(1)
        episode = episodes[n - 1]
    else:
        print()
        for i, ep in enumerate(episodes, 1):
            print(f"  {i:3}.  {ep['title']}")
        print()
        while True:
            try:
                raw = input(f"Select episode [1–{len(episodes)}]: ").strip()
                n = int(raw)
                if 1 <= n <= len(episodes):
                    episode = episodes[n - 1]
                    break
            except (ValueError, EOFError):
                pass
            print("Please enter a number in range.")

    desc_html = episode["description_html"]

    # Extract playlist name (show-specific best-effort, falls back to episode title)
    m = _PLAYLIST_NAME.search(desc_html)
    playlist_title = m.group(1).strip() if m else episode["title"]

    # Parse tracks from <ul> list
    extractor = _LiExtractor()
    extractor.feed(desc_html)
    tracks_to_find = []
    unparseable = []
    for item in extractor.items:
        parsed = _parse_track_line(item)
        if parsed:
            tracks_to_find.append(parsed)
        else:
            unparseable.append(item)

    if not tracks_to_find:
        print("\nNo tracks found in show notes for this episode.")
        if unparseable:
            print("Lines that couldn't be parsed:")
            for line in unparseable:
                print(f"  {line!r}")
        sys.exit(1)

    print(f"\nEpisode:  {episode['title']}")
    print(f"Playlist: {playlist_title!r}")
    print(f"\nResolving {len(tracks_to_find)} tracks on TIDAL…\n")

    client = TidalClient()
    client.ensure_login()

    resolved = []
    not_found = []
    for title, artist in tracks_to_find:
        track = client.resolve_track(artist, title)
        if track is None:
            # Retry with just the first artist if comma-separated
            first = artist.split(",")[0].strip()
            if first != artist:
                track = client.resolve_track(first, title)
        if track:
            resolved.append(track)
            print(f"  ✓  {title!r} – {artist}")
        else:
            not_found.append((title, artist))
            print(f"  ✗  {title!r} – {artist}  [not found on TIDAL]")
            if args.debug:
                raw = client.session.search(f"{artist} {title}", limit=5).get("tracks") or []
                if raw:
                    for t in raw:
                        ta = getattr(getattr(t, "artist", None), "name", "?")
                        print(f"       TIDAL: {t.name!r} – {ta!r}")
                else:
                    print(f"       TIDAL: no results for {artist!r} {title!r}")
        time.sleep(0.2)

    print(f"\n{len(resolved)} found, {len(not_found)} not matched.")

    if not resolved:
        print("Nothing to import.")
        sys.exit(0)

    # Check for an existing playlist with the same name
    existing = next(
        (pl for pl in client.session.user.playlists() if pl.name == playlist_title),
        None,
    )

    if existing:
        print(f'\nPlaylist "{playlist_title}" already exists ({existing.num_tracks} tracks).')
        try:
            answer = input("Update it (clear and replace tracks)? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("Skipped.")
            sys.exit(0)
        print("Clearing existing playlist…")
        existing.clear()
        playlist = existing
    else:
        print(f'\nCreating TIDAL playlist "{playlist_title}"…')
        playlist = client.session.user.create_playlist(
            title=playlist_title,
            description=f"From: {show_title} – {episode['title']}",
        )

    playlist.add([str(t.id) for t in resolved])
    print(f"Done. {len(resolved)} tracks added to your TIDAL library.")

    if not_found:
        print("\nNot matched on TIDAL:")
        for title, artist in not_found:
            print(f"  {title!r} – {artist}")


if __name__ == "__main__":
    main()
