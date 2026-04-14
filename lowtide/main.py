from __future__ import annotations

import os
import sys


def _cmd_import_spotify(path: str) -> None:
    from lowtide.play_count_store import PlayCountStore
    from lowtide.tidal_client import CONF_DIR

    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"error: path not found: {path}", file=sys.stderr)
        sys.exit(1)

    store_path = os.path.join(CONF_DIR, "playcounts.json")
    store = PlayCountStore(store_path)

    counts_before = len(store._counts)
    print(f"Importing Spotify listening history from: {path}")
    print("This may take a moment for large libraries…")

    n = store.import_spotify(path)

    if n == 0:
        print(
            "\nNo streaming history files found. Make sure you're pointing at the "
            "folder extracted from your Spotify data export, which should contain "
            "files named StreamingHistory_music_0.json or similar.",
            file=sys.stderr,
        )
        sys.exit(1)

    new = len(store._counts) - counts_before
    print(f"\nDone.")
    print(f"  {n} distinct tracks imported")
    print(f"  {new} new tracks added to your play count history")
    print(f"  Play counts saved to: {store_path}")
    print(
        "\nYour favourites and discovery shuffle modes will now use this history. "
        "Run low-tide and press s to cycle to favourites (★) or discovery (⊕)."
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="low-tide",
        description="Terminal UI client for TIDAL.",
    )
    sub = parser.add_subparsers(dest="command")

    sp = sub.add_parser(
        "import-spotify",
        help="Import listening history from a Spotify data export to seed shuffle modes.",
        description=(
            "Import your Spotify listening history into low-tide's play count store. "
            "This seeds the favourites (★) and discovery (⊕) shuffle modes with your "
            "existing history so you don't have to start from scratch.\n\n"
            "To get your Spotify data: go to spotify.com → Account → Privacy settings → "
            "Request data download. You'll receive an email with a zip file. Extract it "
            "and pass the extracted folder path to this command."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp.add_argument(
        "path",
        metavar="SPOTIFY_DATA_DIR",
        help="Path to the extracted Spotify data export folder.",
    )

    args = parser.parse_args()

    if args.command == "import-spotify":
        _cmd_import_spotify(args.path)
        return

    # Default: launch the TUI
    from lowtide.app import LowTideApp
    from lowtide.tidal_client import TidalClient

    client = TidalClient()
    client.ensure_login()
    LowTideApp(client=client).run()


if __name__ == "__main__":
    main()
