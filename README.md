# low-tide

A terminal UI client for [TIDAL](https://tidal.com), built with Python and [Textual](https://textual.textualize.io/). Plays music directly in the terminal via `mpv`, with album art rendered natively in GPU-accelerated terminals like [kitty](https://sw.kovidgoyal.net/kitty/).

![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- Browse your playlists, favourites, and TIDAL's "For You" and Mixes
- Search tracks, albums, and artists
- Full playback via `mpv` - no browser, no Electron
- Album art rendered inline using the kitty graphics protocol
- Queue management with click-to-skip
- Album and artist drill-down views
- Transparent UI designed for GPU terminals

## Requirements

- **Python 3.11+**
- **A TIDAL subscription** (HiFi or higher recommended for lossless streams)
- **mpv** - for audio playback
- **kitty** (recommended) - for inline album art; other terminals will work without art

### Installing mpv

```bash
# Arch / CachyOS
sudo pacman -S mpv

# Ubuntu / Debian
sudo apt install mpv

# macOS
brew install mpv
```

## Installation

```bash
git clone https://github.com/pauljhdrake/low-tide.git
cd low-tide
python -m venv .venv
source .venv/bin/activate      # bash/zsh
# source .venv/bin/activate.fish  # fish
pip install -r requirements.txt
```

## Running

```bash
python -m lowtide.main
```

On first launch, you will be prompted to authenticate with TIDAL via a device-code login (a URL is printed - open it in your browser). Tokens are saved to `~/.config/low-tide/session.json` and reused on future launches.

## Keybindings

| Key | Action |
|-----|--------|
| `space` | Play / Pause |
| `n` | Next track |
| `p` | Previous track |
| `]` / `[` | Volume up / down |
| `q` | Toggle queue panel |
| `ctrl+s` | Go to Search |
| `ctrl+l` | Go to Library |
| `escape` | Navigate back |

## Transparency

low-tide uses transparent backgrounds throughout. For the full effect (panels showing your desktop wallpaper), enable background opacity in kitty:

```
# ~/.config/kitty/kitty.conf
background_opacity 0.85
```

## Project structure

```
lowtide/
  main.py            # entry point
  tidal_client.py    # tidalapi wrapper
  player.py          # mpv IPC control
  app.py             # app layout: sidebar, content area, queue panel
  screens/           # library, search, playlist, album, artist, favorites
  widgets/           # now_playing bar, track list table, album art
```

## Disclaimer

low-tide uses the unofficial TIDAL API via [tidalapi](https://github.com/tamland/python-tidal). This may break if TIDAL changes their API. This project is not affiliated with or endorsed by TIDAL.

## License

MIT
