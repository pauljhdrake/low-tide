from __future__ import annotations
from lowtide.tidal_client import TidalClient
from lowtide.app import LowTideApp

def main():
    client = TidalClient()
    # Do the blocking login in the plain console (no TUI yet)
    client.ensure_login()
    # Now launch the TUI with an already-authenticated session
    LowTideApp(client=client).run()
    
    

if __name__ == "__main__":
    main()
