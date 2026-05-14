"""Garmin Connect OAuth — one-time Playwright login, silent refresh forever.

Background: as of 2026, Garmin's `/mobile/api/login` SSO path is permanently
rate-limited per-IP (causes `garth.login()` to 429 immediately on fresh boxes).
The supported workaround in the community: drive a real Chromium login ONCE
to capture an SSO service ticket, exchange that ticket for the OAuth1 token
that the mobile app uses, then exchange OAuth1 → OAuth2 for the bearer token
the actual API endpoints accept. From that point on, the OAuth2 access token
auto-refreshes silently via OAuth1-signed requests to `connectapi.garmin.com`.

Token lifetimes (as observed in 2025-2026):
  OAuth1 token            ~1 year
  OAuth2 access token     ~24 hours
  OAuth2 refresh token    ~3 months

Re-login is needed roughly once a year, or whenever the OAuth1 token expires.

Storage layout (garth-compatible — survives upgrades to any community lib):
  .tokens/oauth1_token.json
  .tokens/oauth2_token.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
TOKENS = ROOT / ".tokens"
OAUTH1_PATH = TOKENS / "oauth1_token.json"
OAUTH2_PATH = TOKENS / "oauth2_token.json"

CONSUMER_KEY = "fc3e99d2-118c-44b8-8ae3-03370dde24c0"  # public Garmin Connect consumer
CONSUMER_SECRET = "E08WAR897WEz2FBfeMvRQTUyFp5e9wYdyJ7gw0HUjkj"  # public — used by all clients


def _has_valid_oauth1() -> bool:
    if not OAUTH1_PATH.exists():
        return False
    try:
        t = json.loads(OAUTH1_PATH.read_text())
    except Exception:
        return False
    # The Garmin OAuth1 token doesn't have a hard expiry header — treat it as
    # valid if the file exists and has the expected fields. Refresh failures
    # downstream will trigger re-login.
    return bool(t.get("oauth_token") and t.get("oauth_token_secret"))


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _save(path: Path, data: dict) -> None:
    TOKENS.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ─── Step 1: Playwright login (one-time) ─────────────────────────────────

def login_via_browser(email: str | None = None, headless: bool = False) -> dict:
    """Open a real Chromium, let the user log in, and harvest the SSO ticket
    that's redirected to after auth. Returns the OAuth1 token dict after
    exchanging the ticket. Saves to .tokens/oauth1_token.json."""
    from playwright.sync_api import sync_playwright

    # The SSO embed URL the modern app redirects to once authenticated. It
    # ends with a service ticket we can intercept.
    SIGNIN_URL = (
        "https://sso.garmin.com/sso/signin"
        "?service=https://connectapi.garmin.com/oauth-service/oauth/preauthorized"
        "&webhost=https://connectapi.garmin.com/oauth-service/oauth/"
        "&source=https://sso.garmin.com/sso/signin"
        "&redirectAfterAccountLoginUrl=https://connectapi.garmin.com/oauth-service/oauth/preauthorized"
        "&redirectAfterAccountCreationUrl=https://connectapi.garmin.com/oauth-service/oauth/preauthorized"
        "&gauthHost=https://sso.garmin.com/sso"
        "&locale=en_US&id=gauth-widget&cssUrl=https://connect.garmin.com/gauth-custom-v3.2-min.css"
        "&privacyStatementUrl=//www.garmin.com/en-US/privacy/connect/"
        "&clientId=GarminConnect&rememberMeShown=true&rememberMeChecked=false"
        "&createAccountShown=true&openCreateAccount=false&displayNameShown=false"
        "&consumeServiceTicket=false&initialFocus=true&embedWidget=false&generateExtraServiceTicket=true"
        "&generateTwoExtraServiceTickets=false&generateNoServiceTicket=false"
        "&globalOptInShown=true&globalOptInChecked=false&mobile=false"
        "&connectLegalTerms=true&showTermsOfUse=false&showPrivacyPolicy=false&showConnectLegalAge=false"
        "&locationPromptShown=true&showPassword=true&useCustomHeader=false&mfaRequired=false"
        "&performMFACheck=false&rememberMyBrowserShown=false&rememberMyBrowserChecked=false"
    )

    captured_ticket: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()

        # Intercept any redirect to .../preauthorized?ticket=... — that's our SSO ticket.
        def handle_response(resp):
            url = resp.url
            if "ticket=ST-" in url and not captured_ticket:
                # Extract ticket from query string
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(url).query)
                t = q.get("ticket", [""])[0]
                if t.startswith("ST-"):
                    captured_ticket.append(t)

        page.on("response", handle_response)

        print(f"Opening Garmin login page. After you log in, the script will close automatically.")
        page.goto(SIGNIN_URL)
        if email:
            try:
                page.fill('input[name="username"]', email)
            except Exception:
                pass

        # Wait up to 5 minutes for the user to authenticate. We're done once we
        # see the ST- ticket in a response URL.
        deadline = time.time() + 300
        while time.time() < deadline and not captured_ticket:
            page.wait_for_timeout(500)

        browser.close()

    if not captured_ticket:
        raise SystemExit("Did not capture an SSO ticket within 5 minutes. Try again.")

    ticket = captured_ticket[0]
    print(f"Captured SSO ticket {ticket[:24]}…")
    return exchange_ticket_for_oauth1(ticket)


# ─── Step 2: SSO ticket → OAuth1 ─────────────────────────────────────────

def exchange_ticket_for_oauth1(ticket: str) -> dict:
    """Exchange the ST- ticket for an OAuth1 request token. Saves and returns."""
    import httpx
    from requests_oauthlib import OAuth1

    auth = OAuth1(CONSUMER_KEY, CONSUMER_SECRET)

    # garth's tested params for this exchange
    params = {
        "ticket": ticket,
        "login-url": "https://sso.garmin.com/sso/embed",
        "accepts-mfa-tokens": "true",
    }

    # Use httpx but sign with OAuth1 (the oauthlib hook for requests works on
    # PreparedRequest objects — we call into it indirectly via a manual sign).
    import oauthlib.oauth1
    client = oauthlib.oauth1.Client(CONSUMER_KEY, client_secret=CONSUMER_SECRET)
    url = "https://connectapi.garmin.com/oauth-service/oauth/preauthorized"
    full_url = url + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    signed_url, headers, _ = client.sign(full_url, http_method="GET")

    with httpx.Client(timeout=30, http2=False) as c:
        r = c.get(signed_url, headers={
            "User-Agent": "com.garmin.android.apps.connectmobile",
            **headers,
        })
        if r.status_code != 200:
            raise SystemExit(f"OAuth1 exchange failed ({r.status_code}): {r.text[:300]}")
        # Response is form-encoded: oauth_token=...&oauth_token_secret=...
        from urllib.parse import parse_qs
        parsed = parse_qs(r.text)
        token = {
            "oauth_token": parsed["oauth_token"][0],
            "oauth_token_secret": parsed["oauth_token_secret"][0],
            "mfa_token": parsed.get("mfa_token", [""])[0] or None,
            "mfa_expiration_timestamp": parsed.get("mfa_expiration_timestamp", [""])[0] or None,
            "domain": "garmin.com",
        }

    _save(OAUTH1_PATH, token)
    print(f"Saved OAuth1 token to {OAUTH1_PATH}")
    return token


# ─── Step 3: OAuth1 → OAuth2 (silent refresh) ─────────────────────────────

def fetch_oauth2(oauth1: dict | None = None) -> dict:
    """Exchange OAuth1 token for a fresh OAuth2 access/refresh token pair."""
    import httpx
    import oauthlib.oauth1

    if oauth1 is None:
        oauth1 = _load(OAUTH1_PATH)
        if not oauth1:
            raise SystemExit("No OAuth1 token. Run login_via_browser() first.")

    client = oauthlib.oauth1.Client(
        CONSUMER_KEY, client_secret=CONSUMER_SECRET,
        resource_owner_key=oauth1["oauth_token"],
        resource_owner_secret=oauth1["oauth_token_secret"],
    )
    url = "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0"
    body = "mfa_token=" + (oauth1.get("mfa_token") or "")
    signed_url, headers, body = client.sign(
        url, http_method="POST", body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    with httpx.Client(timeout=30, http2=False) as c:
        r = c.post(signed_url, headers={
            "User-Agent": "com.garmin.android.apps.connectmobile",
            **headers,
        }, content=body)
        if r.status_code != 200:
            raise SystemExit(f"OAuth2 exchange failed ({r.status_code}): {r.text[:300]}")
        token = r.json()

    # Add absolute expiry timestamps for easy checking
    now = int(time.time())
    token["expires_at"] = now + token.get("expires_in", 86400) - 60
    token["refresh_token_expires_at"] = now + token.get("refresh_token_expires_in", 7776000) - 60
    _save(OAUTH2_PATH, token)
    return token


def current_access_token() -> str:
    """Return a valid OAuth2 access token, refreshing if necessary."""
    oauth2 = _load(OAUTH2_PATH)
    now = int(time.time())
    if not oauth2 or now >= oauth2.get("expires_at", 0):
        oauth2 = fetch_oauth2()
    return oauth2["access_token"]


# ─── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        email = sys.argv[2] if len(sys.argv) > 2 else None
        login_via_browser(email=email)
        fetch_oauth2()
        print("All set. Tokens saved to .tokens/. Try: uv run python -c 'from garmin_oauth import current_access_token; print(current_access_token()[:40])'")
    elif len(sys.argv) > 1 and sys.argv[1] == "refresh":
        token = fetch_oauth2()
        print(f"Refreshed OAuth2. Access token expires in {(token['expires_at'] - int(time.time()))//60} min.")
    else:
        print("Usage:")
        print("  uv run python garmin_oauth.py login [email]   # first time only")
        print("  uv run python garmin_oauth.py refresh         # force OAuth2 refresh")
        if _has_valid_oauth1():
            t = current_access_token()
            print(f"\nCurrent access token (first 40 chars): {t[:40]}…")
