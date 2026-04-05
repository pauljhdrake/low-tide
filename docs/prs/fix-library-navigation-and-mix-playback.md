# Fix library navigation and mix playback

## Summary

- Library list selections were silently failing because sync event handlers used `asyncio.ensure_future` unreliably in Textual 8.x — fixed by making handlers `async` with direct `await`
- Mixes (e.g. My Daily Discovery) now push a `PlaylistScreen` showing the track listing and autoplay from track 1, instead of playing blindly without any view change
- `TrackList.load()` auto-focuses the `DataTable` so keyboard navigation works immediately after a playlist/album opens
- `get_playlist_tracks` falls back to `items()` for mix objects that don't have `tracks()`
- Removed dead `_open_mix` method and the now-unnecessary `PlaylistSelected` message indirection

## Test plan

- [ ] Navigate to Library → Playlists tab, select a playlist → track listing appears, select a track to play
- [ ] Navigate to Library → Mixes tab, select a mix → track listing appears and playback starts automatically from track 1
- [ ] After opening a playlist/album, arrow keys and Enter work on the track list without needing to click first
- [ ] Sidebar nav (Library / Search / Favorites) still works via keyboard and mouse
