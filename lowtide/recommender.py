from __future__ import annotations

import logging
import math
import time
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lowtide.play_count_store import PlayCountStore
    from lowtide.tidal_client import TidalClient

log = logging.getLogger(__name__)

_LASTFM_NUDGE = (
    "Tip: configure Last.fm in config.json for similarity-based recommendations. "
    "See github.com/pauljhdrake/low-tide for setup instructions."
)

# How much a matching mood tag boosts a candidate's score
_TAG_BOOST = 0.3


class DiscoveryMode(IntEnum):
    ESSENTIAL = 0
    BALANCED = 1
    ADVENTUROUS = 2


# Per-mode tuning knobs
_MODE_PARAMS: dict[DiscoveryMode, dict] = {
    DiscoveryMode.ESSENTIAL:   dict(sim_threshold=0.25, artist_limit=8,  novelty_mul=0.4, top_tracks=3),
    DiscoveryMode.BALANCED:    dict(sim_threshold=0.0,  artist_limit=15, novelty_mul=1.0, top_tracks=3),
    DiscoveryMode.ADVENTUROUS: dict(sim_threshold=0.0,  artist_limit=25, novelty_mul=2.0, top_tracks=3),
}


def _novelty(play_count: int) -> float:
    return 1.0 / (1.0 + math.log1p(play_count))


def _artist_tags(lastfm_artist, limit: int = 5) -> set[str]:
    try:
        return {t.item.name.lower() for t in lastfm_artist.get_top_tags(limit=limit)}
    except Exception:
        return set()


class Recommender:
    """
    Builds recommendation queues using Last.fm similarity data and the local
    play count store. Falls back to TIDAL-only logic when Last.fm is not configured,
    and includes a nudge message prompting the user to set it up.
    """

    def __init__(self, network, store: PlayCountStore, client: TidalClient) -> None:
        self._network = network  # pylast.LastFMNetwork or None
        self._store = store
        self._client = client

    @property
    def has_lastfm(self) -> bool:
        return self._network is not None

    # ── Public API ────────────────────────────────────────────────────────

    def build_track_radio(
        self, track, n: int = 25, mode: DiscoveryMode = DiscoveryMode.BALANCED
    ) -> tuple[list, str | None]:
        """
        Contextual radio seeded from a specific track.
        Returns (tidal_tracks, nudge_message_or_None).
        """
        if self.has_lastfm:
            return self._lastfm_track_radio(track, n, mode), None
        return self._fallback_track_radio(track, n), _LASTFM_NUDGE

    def build_ride_the_tide(
        self, n: int = 25, mode: DiscoveryMode = DiscoveryMode.BALANCED
    ) -> tuple[list, str | None]:
        """
        General recommendations based on overall listening history.
        Returns (tidal_tracks, nudge_message_or_None).
        """
        if self.has_lastfm:
            return self._lastfm_ride_the_tide(n, mode), None
        return self._fallback_ride_the_tide(n), _LASTFM_NUDGE

    def build_genre_playlist(
        self, genre: str, n: int = 20, mode: DiscoveryMode = DiscoveryMode.BALANCED
    ) -> tuple[list, str | None]:
        """
        Build a playlist for a given Last.fm tag/genre.
        Returns (tidal_tracks, nudge_message_or_None).
        """
        if self.has_lastfm:
            return self._lastfm_genre_playlist(genre, n, mode), None
        return self._fallback_genre_playlist(genre, n), _LASTFM_NUDGE

    # ── Last.fm: genre playlist ───────────────────────────────────────────

    def _lastfm_genre_playlist(self, genre: str, n: int, mode: DiscoveryMode) -> list:
        p = _MODE_PARAMS[mode]
        tag = self._network.get_tag(genre)
        candidates: dict[tuple[str, str], float] = {}

        # Direct: top tracks for this tag (highest signal)
        try:
            for item in tag.get_top_tracks(limit=50):
                a = getattr(item.item, "artist", None)
                a_name = getattr(a, "name", "") if a else ""
                t_name = getattr(item.item, "title", "") or ""
                if a_name and t_name:
                    candidates[(a_name, t_name)] = float(item.weight or 1)
        except Exception as e:
            log.debug("genre playlist: top tracks failed for %s: %s", genre, e)

        # Broader: top artists for this tag → their top tracks
        try:
            for item in tag.get_top_artists(limit=p["artist_limit"]):
                a_name = getattr(item.item, "name", "") or ""
                artist_weight = float(item.weight or 1) * 0.6
                try:
                    for tt in item.item.get_top_tracks(limit=p["top_tracks"]):
                        t_name = getattr(tt.item, "title", "") or ""
                        if not t_name:
                            continue
                        key = (a_name, t_name)
                        if key not in candidates or candidates[key] < artist_weight:
                            candidates[key] = artist_weight
                except Exception:
                    pass
        except Exception as e:
            log.debug("genre playlist: top artists failed for %s: %s", genre, e)

        return self._score_and_resolve(candidates, {genre.lower()}, n, mode)

    # ── Last.fm: track radio ──────────────────────────────────────────────

    def _lastfm_track_radio(self, track, n: int, mode: DiscoveryMode) -> list:
        p = _MODE_PARAMS[mode]
        artist_name = getattr(getattr(track, "artist", None), "name", "") or ""
        title = getattr(track, "name", "") or ""

        # 1. Seed track tags — used for soft mood boosting
        seed_tags: set[str] = set()
        try:
            lastfm_track = self._network.get_track(artist_name, title)
            seed_tags = {t.item.name.lower() for t in lastfm_track.get_top_tags(limit=5)}
        except Exception as e:
            log.debug("track radio: seed tag lookup failed: %s", e)

        # 2. Similar tracks (direct signal)
        candidates: dict[tuple[str, str], float] = {}
        try:
            for item in lastfm_track.get_similar(limit=50):
                a = getattr(item.item, "artist", None)
                a_name = getattr(a, "name", "") if a else ""
                t_name = getattr(item.item, "title", "") or ""
                if a_name and t_name:
                    candidates[(a_name, t_name)] = float(item.match)
        except Exception as e:
            log.debug("track radio: get_similar failed: %s", e)

        # 3. Similar artists → their top tracks (broader pool)
        try:
            lastfm_artist = self._network.get_artist(artist_name)
            for sim in lastfm_artist.get_similar(limit=p["artist_limit"]):
                artist_sim = float(sim.match)
                sim_name = getattr(sim.item, "name", "") or ""
                sim_tags = _artist_tags(sim.item)
                tag_overlap = len(seed_tags & sim_tags)
                boost = 1.0 + _TAG_BOOST * tag_overlap
                try:
                    for tt in sim.item.get_top_tracks(limit=p["top_tracks"]):
                        t_name = getattr(tt.item, "title", "") or ""
                        if not t_name:
                            continue
                        key = (sim_name, t_name)
                        score = artist_sim * boost * 0.7  # discount vs direct similar tracks
                        if key not in candidates or candidates[key] < score:
                            candidates[key] = score
                except Exception:
                    pass
        except Exception as e:
            log.debug("track radio: similar artists failed: %s", e)

        return self._score_and_resolve(candidates, seed_tags, n, mode)

    # ── Last.fm: ride the tide ────────────────────────────────────────────

    def _lastfm_ride_the_tide(self, n: int, mode: DiscoveryMode) -> list:
        top_artists = self._store.top_artists(limit=10)
        if not top_artists:
            # Cold start: no local play counts yet – query Last.fm directly
            try:
                import pylast
                user = self._network.get_authenticated_user()
                items = user.get_top_artists(period=pylast.PERIOD_1MONTH, limit=10)
                top_artists = [
                    (getattr(item.item, "name", ""), int(item.weight or 1))
                    for item in items
                    if getattr(item.item, "name", "")
                ]
                log.debug("ride the tide: seeding from Last.fm top artists (%d)", len(top_artists))
            except Exception as e:
                log.debug("ride the tide: Last.fm top artists failed: %s", e)
        if not top_artists:
            return self._fallback_ride_the_tide(n)

        # Build a mood tag profile from the user's top 3 artists
        seed_tags: set[str] = set()
        for artist_name, _ in top_artists[:3]:
            try:
                a = self._network.get_artist(artist_name)
                seed_tags.update(_artist_tags(a))
            except Exception:
                pass

        p = _MODE_PARAMS[mode]

        # Expand: for each top artist, get similar artists and their top tracks
        candidates: dict[tuple[str, str], float] = {}
        for artist_name, artist_plays in top_artists:
            # Penalise artists the user already listens to heavily
            familiarity_penalty = 1.0 / (1.0 + math.log1p(artist_plays / 10))
            try:
                a = self._network.get_artist(artist_name)
                for sim in a.get_similar(limit=p["artist_limit"]):
                    sim_name = getattr(sim.item, "name", "") or ""
                    artist_sim = float(sim.match) * familiarity_penalty
                    sim_tags = _artist_tags(sim.item)
                    tag_overlap = len(seed_tags & sim_tags)
                    boost = 1.0 + _TAG_BOOST * tag_overlap
                    try:
                        for tt in sim.item.get_top_tracks(limit=p["top_tracks"]):
                            t_name = getattr(tt.item, "title", "") or ""
                            if not t_name:
                                continue
                            key = (sim_name, t_name)
                            score = artist_sim * boost
                            if key not in candidates or candidates[key] < score:
                                candidates[key] = score
                    except Exception:
                        pass
            except Exception as e:
                log.debug("ride the tide: failed for artist %s: %s", artist_name, e)

        return self._score_and_resolve(candidates, seed_tags, n, mode)

    # ── Shared scoring + TIDAL resolution ────────────────────────────────

    def _score_and_resolve(
        self,
        candidates: dict[tuple[str, str], float],
        seed_tags: set[str],
        n: int,
        mode: DiscoveryMode = DiscoveryMode.BALANCED,
    ) -> list:
        """Apply novelty weighting, sort, and resolve to TIDAL tracks."""
        p = _MODE_PARAMS[mode]
        threshold = p["sim_threshold"]
        novelty_exp = p["novelty_mul"]
        scored = []
        for (a_name, t_name), sim_score in candidates.items():
            if sim_score < threshold:
                continue
            play_count = self._store.get_by_names(a_name, t_name)
            # novelty_exp > 1 penalises familiar tracks harder (Adventurous);
            # novelty_exp < 1 softens the penalty (Essential)
            score = sim_score * (_novelty(play_count) ** novelty_exp)
            scored.append((a_name, t_name, score))

        scored.sort(key=lambda x: -x[2])

        results = []
        max_attempts = n + 20
        for a_name, t_name, _ in scored[:max_attempts]:
            if len(results) >= n:
                break
            track = self._client.resolve_track(a_name, t_name)
            if track:
                results.append(track)
            time.sleep(0.2)

        return results

    # ── Fallbacks (no Last.fm) ────────────────────────────────────────────

    def _fallback_track_radio(self, track, n: int) -> list:
        """Without Last.fm: return other tracks by the same artist, novelty-weighted."""
        artist = getattr(track, "artist", None)
        if not artist:
            return []
        try:
            albums = self._client.get_artist_albums(artist)
            all_tracks = []
            for album in albums[:5]:
                try:
                    all_tracks.extend(self._client.get_album_tracks(album))
                except Exception:
                    pass
            seed_title = getattr(track, "name", "").lower()
            candidates = [t for t in all_tracks if getattr(t, "name", "").lower() != seed_title]
            candidates.sort(
                key=lambda t: _novelty(self._store.get(t)),
                reverse=True,
            )
            return candidates[:n]
        except Exception as e:
            log.debug("fallback track radio failed: %s", e)
            return []

    def _fallback_ride_the_tide(self, n: int) -> list:
        """Without Last.fm: surface less-heard tracks from your most-played artists.
        Falls back to a shuffled sample of TIDAL favourites on a cold start."""
        top_artists = self._store.top_artists(limit=5)
        if not top_artists:
            return self._fallback_from_favourites(n)

        results = []
        for artist_name, _ in top_artists:
            if len(results) >= n:
                break
            try:
                results_search = self._client.session.search(artist_name, limit=10)
                artists = results_search.get("artists") or []
                if not artists:
                    continue
                tidal_artist = artists[0]
                albums = self._client.get_artist_albums(tidal_artist)
                for album in albums[:3]:
                    try:
                        for t in self._client.get_album_tracks(album):
                            play_count = self._store.get(t)
                            results.append((t, _novelty(play_count)))
                    except Exception:
                        pass
            except Exception as e:
                log.debug("fallback ride the tide failed for %s: %s", artist_name, e)

        results.sort(key=lambda x: -x[1])
        return [t for t, _ in results[:n]]

    def _fallback_genre_playlist(self, genre: str, n: int) -> list:
        """Without Last.fm: TIDAL keyword search for the genre string."""
        try:
            results = self._client.search(genre, limit=50)
            return (results.get("tracks") or [])[:n]
        except Exception as e:
            log.debug("fallback genre playlist failed: %s", e)
            return []

    def _fallback_from_favourites(self, n: int) -> list:
        """Cold-start fallback: return a random sample of TIDAL favourites."""
        import random
        try:
            tracks = self._client.get_favorite_tracks()
            random.shuffle(tracks)
            return tracks[:n]
        except Exception as e:
            log.debug("fallback from favourites failed: %s", e)
            return []
