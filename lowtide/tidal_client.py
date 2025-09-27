from __future__ import annotations
import json, os
import tidalapi

CONF_DIR = os.path.join(os.path.expanduser("-"), ".config", "low-tide")
CONF_PATH  = os.path.join(CONF_DIR, "session.json")

class TidalClient:
    """
    Minimal wrapper around tidalapi.
    We deliberately keep this tiny for Lesson 1, then expand it as we go.
    """

    def __init__(self):
        self.session = tidalapi.Session()
        self._try_load_tokens()

    def _try_load_tokens(self) -> None:
        try:
            with open(CONF_PATH, "r") as f:
                data = json.load(f)

            # Tidalapi expects these names:
            self.session.load_oauth_session(
                    access_token=data["access_token"],
                    refresh_token=data["refresh_token"],
                    token_type=data.get("token_type", "Bearer"),
                    expires_in=data.get("expires_in", 0),
                )
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def _save_tokens(self) -> None:
        os.makedirs(CONF_DIR, exist_ok=True)
        data = {
                "access_token": self.session.access_token,
                "refresh_token": self.session.refresh_token,
                "token_type": getattr(self.session, "token_type", "Bearer"),
                "expires_in": getattr(self.session, "expires_in", 0),
                }
        with open(CONF_PATH, "w") as f:
            json.dump(data, f)

    def ensure_login(self) -> None:
        """Ensure we have a valid session; if not, run device-code login."""
        if self.session.check_login():
            return
         # These prints appear in the terminal so you can complete auth in a browser.
        print("==== TIDAL Login ====")
        print(f"A browser/device-code prompt will appear. Follow the on-screen instructions")
        
        # Link printed to terminal and waits for approval 
        self.session.login_oauth_simple()
        self._save_tokens()


    def me(self):
        """Return the current user object (after login)."""
        self.ensure_login()
        return self.session.user
