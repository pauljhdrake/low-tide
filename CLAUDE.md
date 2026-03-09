# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**low-tide** is a terminal UI client for [TIDAL](https://tidal.com) built with Python. It uses `tidalapi` for API access and `textual` for the TUI.

## Running the App

```bash
# Activate the venv first
source .venv/bin/activate

# Run the app
python -m lowtide.main
```

There is no build step, test suite, or linter configured yet.

## Architecture

The app has three layers:

1. **`lowtide/tidal_client.py` — `TidalClient`**: Wraps `tidalapi.Session`. Handles OAuth device-code login, persists tokens to `~/.config/low-tide/session.json`, and exposes a minimal API. Login runs in the plain terminal (blocking) before the TUI starts.

   > **Note**: `CONF_DIR` has a bug — `os.path.expanduser("-")` should be `os.path.expanduser("~")`. The session file is currently saved to a literal `-/.config/low-tide/` directory inside the repo root.

2. **`lowtide/app.py` — `LowTideApp`**: A `textual.App` subclass. Composes the full TUI (search input, results list, status labels). Search hits `tidalapi.Session.search()` directly and opens selected tracks/albums in the TIDAL web player via `webbrowser` / `xdg-open`.

3. **`lowtide/main.py`**: Entry point. Runs `TidalClient.ensure_login()` synchronously, then hands off to `LowTideApp.run()`.

## Key Dependencies

- `tidalapi==0.8.6` — TIDAL API wrapper
- `textual==6.0.0` — terminal UI framework

Both are installed in `.venv/`.

## TUI Keybindings

| Key | Action |
|-----|--------|
| `/` | Focus search input |
| `Enter` (in list) | Open selected item in TIDAL web player |
| `q` | Quit |
