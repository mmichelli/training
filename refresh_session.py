"""Silent OAuth2 token refresh.

Kept as a thin wrapper around `garmin_oauth.fetch_oauth2()` so the existing
`make refresh` / dashboard sync flow keeps working. The old cookie-scraping
implementation is gone — first-time auth now happens once via
`uv run python garmin_oauth.py login`.
"""
from __future__ import annotations

import time

from garmin_oauth import OAUTH1_PATH, fetch_oauth2


def main() -> int:
    if not OAUTH1_PATH.exists():
        print("No OAuth1 token yet. First-time setup:")
        print("    uv run python garmin_oauth.py login")
        return 1
    token = fetch_oauth2()
    expires_in_min = max(0, (token["expires_at"] - int(time.time())) // 60)
    print(f"Refreshed Garmin OAuth2. Access token good for {expires_in_min} min.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
