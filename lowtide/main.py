from __future__ import annotations

from lowtide.app import LowTideApp
from lowtide.tidal_client import TidalClient


def main() -> None:
    client = TidalClient()
    client.ensure_login()
    LowTideApp(client=client).run()


if __name__ == "__main__":
    main()
