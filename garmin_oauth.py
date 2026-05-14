"""Garmin Connect OAuth — one-time browser-side auth, silent refresh.

Auth model
----------
Garmin's `/mobile/api/login` SSO endpoint is permanently rate-limited (March
2026 onwards). We don't try to drive it. Instead:

1. ONCE: the user opens an SSO URL in their already-authenticated Chrome.
   Garmin returns a service ticket (`ST-...`). They paste it into the
   dashboard. We exchange it for an OAuth1 token (~12 month lifetime).
2. EVERY API CALL: we exchange the OAuth1 token for a fresh OAuth2 access
   token (~24 hour lifetime) silently against connectapi.garmin.com.

Tokens are stored under `.tokens/` (gitignored). Re-bootstrap once a year
when OAuth1 expires; the dashboard re-prompts automatically on 401.

Consumer credentials
--------------------
Garmin rotates the OAuth consumer key/secret. We fetch them live from the
garth project's S3 mirror so we stay current without code changes.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from requests_oauthlib import OAuth1Session

ROOT = Path(__file__).parent
TOKENS = ROOT / ".tokens"
OAUTH1_PATH = TOKENS / "oauth1_token.json"
OAUTH2_PATH = TOKENS / "oauth2_token.json"

API_BASE = "https://connectapi.garmin.com"
USER_AGENT = {"User-Agent": "com.garmin.android.apps.connectmobile"}
CONSUMER_URL = "https://thegarth.s3.amazonaws.com/oauth_consumer.json"

_consumer_cache: dict[str, str] = {}


def _consumer() -> tuple[str, str]:
    """Return (consumer_key, consumer_secret), fetched once and cached."""
    if not _consumer_cache:
        r = httpx.get(CONSUMER_URL, timeout=15)
        r.raise_for_status()
        _consumer_cache.update(r.json())
    return _consumer_cache["consumer_key"], _consumer_cache["consumer_secret"]


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _save(path: Path, data: dict) -> None:
    TOKENS.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def exchange_ticket_for_oauth1(ticket: str) -> dict:
    """Exchange an SSO service ticket (ST-...) for a long-lived OAuth1 token."""
    ck, cs = _consumer()
    sess = OAuth1Session(client_key=ck, client_secret=cs)
    r = sess.get(
        f"{API_BASE}/oauth-service/oauth/preauthorized",
        params={
            "ticket": ticket,
            "login-url": "https://sso.garmin.com/sso/embed",
            "accepts-mfa-tokens": "true",
        },
        headers=USER_AGENT,
        timeout=30,
    )
    if r.status_code != 200:
        raise SystemExit(f"OAuth1 exchange failed ({r.status_code}): {r.text[:500]}")
    parsed = parse_qs(r.text)
    token = {
        "oauth_token": parsed["oauth_token"][0],
        "oauth_token_secret": parsed["oauth_token_secret"][0],
        "mfa_token": parsed.get("mfa_token", [""])[0] or None,
        "mfa_expiration_timestamp": parsed.get("mfa_expiration_timestamp", [""])[0] or None,
        "domain": "garmin.com",
    }
    _save(OAUTH1_PATH, token)
    return token


def fetch_oauth2() -> dict:
    """Exchange the stored OAuth1 token for a fresh OAuth2 access/refresh pair."""
    oauth1 = _load(OAUTH1_PATH)
    if not oauth1:
        raise SystemExit("No OAuth1 token. Authorize once via the dashboard's ⚿ button.")

    ck, cs = _consumer()
    sess = OAuth1Session(
        client_key=ck, client_secret=cs,
        resource_owner_key=oauth1["oauth_token"],
        resource_owner_secret=oauth1["oauth_token_secret"],
    )
    r = sess.post(
        f"{API_BASE}/oauth-service/oauth/exchange/user/2.0",
        data={"mfa_token": oauth1.get("mfa_token") or ""},
        headers=USER_AGENT,
        timeout=30,
    )
    if r.status_code != 200:
        raise SystemExit(f"OAuth2 exchange failed ({r.status_code}): {r.text[:500]}")
    token = r.json()
    now = int(time.time())
    token["expires_at"] = now + token.get("expires_in", 86400) - 60
    token["refresh_token_expires_at"] = now + token.get("refresh_token_expires_in", 7776000) - 60
    _save(OAUTH2_PATH, token)
    return token


def current_access_token() -> str:
    """Return a valid OAuth2 access token, refreshing if expired."""
    oauth2 = _load(OAUTH2_PATH)
    if not oauth2 or int(time.time()) >= oauth2.get("expires_at", 0):
        oauth2 = fetch_oauth2()
    return oauth2["access_token"]


# ─── CLI ──────────────────────────────────────────────────────────────────

def _print_usage() -> None:
    print("Usage:")
    print("  uv run python garmin_oauth.py ticket ST-XXX     # bootstrap (annual)")
    print("  uv run python garmin_oauth.py refresh           # force OAuth2 refresh")
    print()
    print("How to get a ticket (once a year):")
    print("  1. Open https://connect.garmin.com in Chrome (logged in).")
    print("  2. In a new tab, visit:")
    print("     https://sso.garmin.com/sso/embed?clientId=GCM_ANDROID_DARK&locale=en"
          "&service=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
          "&consumeServiceTicket=false&generateExtraServiceTicket=true")
    print("  3. Copy the ST-... value shown in the response.")
    print("  4. Run:  uv run python garmin_oauth.py ticket ST-...")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "ticket" and len(sys.argv) > 2:
        raw = sys.argv[2].strip()
        if raw.startswith("http"):
            from urllib.parse import urlparse
            raw = parse_qs(urlparse(raw).query).get("ticket", [""])[0]
        if not raw.startswith("ST-"):
            sys.exit(f"Not a Garmin SSO ticket (should start with ST-): {raw[:60]}")
        exchange_ticket_for_oauth1(raw)
        fetch_oauth2()
        print(f"All set. Tokens saved to {TOKENS}/ — good for ~12 months.")

    elif cmd == "refresh":
        t = fetch_oauth2()
        print(f"Refreshed. Access token good for {(t['expires_at'] - int(time.time()))//60} min.")

    else:
        _print_usage()
        if OAUTH2_PATH.exists():
            print(f"\nCurrent access token: {current_access_token()[:40]}…")
