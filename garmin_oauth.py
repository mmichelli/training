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

_OAUTH_CONSUMER_URL = "https://thegarth.s3.amazonaws.com/oauth_consumer.json"
_OAUTH_CONSUMER: dict[str, str] = {}


def _consumer() -> tuple[str, str]:
    """Fetch (and cache) the current Garmin OAuth consumer key/secret.

    Garmin rotates these — garth keeps them in S3 and any client that wants
    to stay working in 2026+ has to fetch them at runtime.
    """
    global _OAUTH_CONSUMER
    if not _OAUTH_CONSUMER:
        import httpx
        r = httpx.get(_OAUTH_CONSUMER_URL, timeout=15)
        r.raise_for_status()
        _OAUTH_CONSUMER = r.json()
    return _OAUTH_CONSUMER["consumer_key"], _OAUTH_CONSUMER["consumer_secret"]


def _read_zen_garmin_cookies() -> list[tuple[str, str, str, str]] | None:
    """Pull Garmin cookies from a local Firefox-family (Zen / Firefox) profile."""
    import glob, os, shutil, sqlite3, tempfile
    patterns = [
        os.path.expanduser("~/.zen/*/cookies.sqlite"),
        os.path.expanduser("~/.mozilla/firefox/*/cookies.sqlite"),
    ]
    db = None
    for pat in patterns:
        matches = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
        if matches:
            db = matches[0]
            break
    if not db:
        return None
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        shutil.copy(db, tmp.name)
        tmp_path = tmp.name
    try:
        con = sqlite3.connect(tmp_path)
        rows = con.execute(
            "SELECT name, value, host, path FROM moz_cookies "
            "WHERE host LIKE '%garmin.com' OR host LIKE '%.garmin.com'"
        ).fetchall()
        con.close()
    finally:
        os.unlink(tmp_path)
    return rows


def bootstrap_via_zen_session() -> dict:
    """Mint OAuth1 token using cookies from the local Zen/Firefox browser.

    Strategy: we hit Garmin's SSO 'generate-extra-service-ticket' endpoint
    with curl_cffi (Chrome TLS fingerprint to bypass Cloudflare) and the
    Zen browser cookies. The endpoint, if our session is authenticated,
    redirects to a URL containing `?ticket=ST-...`. We capture that ticket
    and exchange it for OAuth1/OAuth2.

    Zero login UI: the user is already logged into Garmin in Zen, we
    piggyback on that. Works once, OAuth1 then lasts ~1 year.
    """
    from curl_cffi import requests as cc
    from urllib.parse import urlparse, parse_qs

    rows = _read_zen_garmin_cookies()
    if not rows:
        raise SystemExit(
            "No Zen/Firefox cookies for garmin.com found. Log into "
            "https://connect.garmin.com in your browser, then retry."
        )
    cookies = {n: v for (n, v, _h, _p) in rows}
    if "JWT_WEB" not in cookies or "session" not in cookies:
        raise SystemExit(
            "Found Garmin cookies but no authenticated session. Log into "
            "https://connect.garmin.com in your browser, then retry."
        )

    # SSO embed URL that, for an authenticated session, redirects to
    # connectapi.../preauthorized?ticket=ST-...
    embed_url = (
        "https://sso.garmin.com/sso/embed"
        "?id=gauth-widget&embedWidget=true"
        "&gauthHost=https%3A%2F%2Fsso.garmin.com%2Fsso"
        "&service=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
        "&source=https%3A%2F%2Fsso.garmin.com%2Fsso%2Fembed"
        "&redirectAfterAccountLoginUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
        "&redirectAfterAccountCreationUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
        "&consumeServiceTicket=false"
        "&generateExtraServiceTicket=true"
        "&generateNoServiceTicket=false"
    )

    session = cc.Session(impersonate="chrome131")
    # Apply Zen cookies on the relevant domains
    for n, v, host, path in rows:
        session.cookies.set(n, v, domain=host.lstrip("."), path=path or "/")

    r = session.get(embed_url, allow_redirects=True, timeout=30)
    # The final URL or any URL in the chain should contain ticket=ST-...
    candidate_urls = [r.url] + [h.url for h in r.history]
    ticket = None
    for u in candidate_urls:
        q = parse_qs(urlparse(u).query)
        t = q.get("ticket", [""])[0]
        if t.startswith("ST-"):
            ticket = t
            break
    if not ticket:
        # Some Garmin flows render the ticket as JSON
        if "ticket" in r.text:
            import re as _re
            m = _re.search(r'"ticket"\s*:\s*"(ST-[^"]+)"', r.text)
            if m:
                ticket = m.group(1)
    if not ticket:
        raise SystemExit(
            "Could not mint an SSO ticket from your Zen session. "
            f"Final URL: {r.url}\nResponse start: {r.text[:300]}"
        )
    print(f"Captured SSO ticket {ticket[:24]}…")
    return exchange_ticket_for_oauth1(ticket)


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

def _read_secrets() -> tuple[str | None, str | None]:
    """Read GARMIN_EMAIL / GARMIN_PASSWORD from .secrets so we can autofill."""
    p = ROOT / ".secrets"
    if not p.exists():
        return None, None
    env = {}
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env.get("GARMIN_EMAIL"), env.get("GARMIN_PASSWORD")


def login_via_browser(email: str | None = None, password: str | None = None,
                      headless: bool = False) -> dict:
    """Drive a Chromium login. Defaults to headless=False because Cloudflare's
    bot gate blocks headless Chromium before the login form even renders.
    With credentials present we still autofill+submit so it's hands-free."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlparse, parse_qs

    if email is None or password is None:
        em, pw = _read_secrets()
        email = email or em
        password = password or pw
    can_autofill = bool(email and password)

    SIGNIN_URL = "https://connect.garmin.com/signin/"
    PREAUTH_TRIGGER = (
        "https://sso.garmin.com/sso/embed"
        "?id=gauth-widget&embedWidget=true"
        "&gauthHost=https%3A%2F%2Fsso.garmin.com%2Fsso"
        "&service=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
        "&source=https%3A%2F%2Fsso.garmin.com%2Fsso%2Fembed"
        "&redirectAfterAccountLoginUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
        "&redirectAfterAccountCreationUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
        "&consumeServiceTicket=false"
        "&generateExtraServiceTicket=true"
        "&generateNoServiceTicket=false"
    )

    captured_ticket: list[str] = []
    seen_urls: list[str] = []

    def extract_ticket(url: str) -> str | None:
        if "ticket=ST-" not in url:
            return None
        q = parse_qs(urlparse(url).query)
        t = q.get("ticket", [""])[0]
        return t if t.startswith("ST-") else None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()

        # Apply stealth patches to dodge Cloudflare bot detection
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
        except Exception as e:
            print(f"(stealth plugin not applied: {e})")

        def handle_response(resp):
            url = resp.url
            seen_urls.append(url)
            t = extract_ticket(url)
            if t and not captured_ticket:
                captured_ticket.append(t)

        page.on("response", handle_response)

        print(f"Opening Garmin Connect signin (headless={headless}, autofill={can_autofill})…")
        page.goto(SIGNIN_URL)
        if can_autofill:
            try:
                page.wait_for_selector('input[name="username"]', timeout=15000)
                page.fill('input[name="username"]', email)
                page.fill('input[name="password"]', password)
                # Submit — the form button text/role varies, try a few selectors.
                for selector in [
                    'button[type="submit"]',
                    'button:has-text("Sign In")',
                    'button:has-text("Log In")',
                    'input[type="submit"]',
                ]:
                    try:
                        page.click(selector, timeout=2000)
                        break
                    except Exception:
                        continue
                print("Submitted credentials. Waiting for authentication…")
            except Exception as e:
                print(f"Auto-fill failed ({e}); falling back to manual login.")
                can_autofill = False

        # Wait until authenticated (URL contains /modern or JWT_WEB cookie exists)
        if not can_autofill:
            print("Waiting for authentication…")
        deadline = time.time() + (60 if can_autofill else 300)
        authenticated = False
        while time.time() < deadline and not authenticated:
            cookies = {c["name"] for c in ctx.cookies()}
            if "JWT_WEB" in cookies or "/modern" in page.url:
                authenticated = True
                break
            page.wait_for_timeout(500)

        if not authenticated:
            print("Login window timed out before authentication.")
            print("Last few URLs seen:")
            for u in seen_urls[-8:]:
                print(f"  {u}")
            browser.close()
            raise SystemExit("Login not completed in 5 minutes.")

        print("Authenticated. Fetching OAuth ticket…")
        # Trigger the SSO embed preauth flow in the same authenticated context.
        # It redirects to connectapi.garmin.com/.../preauthorized?ticket=ST-...
        page.goto(PREAUTH_TRIGGER, wait_until="domcontentloaded", timeout=15000)
        # Give redirects a moment to settle
        page.wait_for_timeout(2000)

        if not captured_ticket:
            # Last-ditch: log what we saw to debug
            print("Did not see a ticket. URLs touched after auth:")
            for u in seen_urls[-20:]:
                print(f"  {u}")
            browser.close()
            raise SystemExit("Could not capture SSO ticket — see URL trace above.")

        browser.close()

    ticket = captured_ticket[0]
    print(f"Captured SSO ticket {ticket[:24]}…")
    return exchange_ticket_for_oauth1(ticket)


# ─── Step 2: SSO ticket → OAuth1 ─────────────────────────────────────────

def exchange_ticket_for_oauth1(ticket: str) -> dict:
    """Exchange the SSO ST- ticket for an OAuth1 request token (garth-style)."""
    from requests_oauthlib import OAuth1Session
    from urllib.parse import parse_qs

    ck, cs = _consumer()
    sess = OAuth1Session(client_key=ck, client_secret=cs)
    url = "https://connectapi.garmin.com/oauth-service/oauth/preauthorized"
    params = {
        "ticket": ticket,
        "login-url": "https://sso.garmin.com/sso/embed",
        "accepts-mfa-tokens": "true",
    }
    r = sess.get(
        url, params=params,
        headers={"User-Agent": "com.garmin.android.apps.connectmobile"},
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
    print(f"Saved OAuth1 token to {OAUTH1_PATH}")
    return token


# ─── Step 3: OAuth1 → OAuth2 (silent refresh) ─────────────────────────────

def fetch_oauth2(oauth1: dict | None = None) -> dict:
    """Exchange OAuth1 token for a fresh OAuth2 access/refresh token pair."""
    from requests_oauthlib import OAuth1Session

    if oauth1 is None:
        oauth1 = _load(OAUTH1_PATH)
        if not oauth1:
            raise SystemExit("No OAuth1 token. Run login_via_browser() first.")

    ck, cs = _consumer()
    sess = OAuth1Session(
        client_key=ck, client_secret=cs,
        resource_owner_key=oauth1["oauth_token"],
        resource_owner_secret=oauth1["oauth_token_secret"],
    )
    url = "https://connectapi.garmin.com/oauth-service/oauth/exchange/user/2.0"
    data = {"mfa_token": oauth1.get("mfa_token") or ""}
    r = sess.post(
        url, data=data,
        headers={"User-Agent": "com.garmin.android.apps.connectmobile"},
        timeout=30,
    )
    if r.status_code != 200:
        raise SystemExit(f"OAuth2 exchange failed ({r.status_code}): {r.text[:500]}")
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
    if len(sys.argv) > 1 and sys.argv[1] == "ticket":
        # Paste a ticket captured manually from your own Chrome's DevTools.
        if len(sys.argv) < 3:
            print("Usage: uv run python garmin_oauth.py ticket ST-XXX")
            print()
            print("How to get ST-XXX (once a year):")
            print("  1. Open Chrome on the laptop, already logged into Garmin Connect.")
            print("  2. Open DevTools → Network tab → make sure 'Preserve log' is on.")
            print("  3. In a new tab, paste this URL and press enter:")
            print()
            print("     https://sso.garmin.com/sso/embed?id=gauth-widget&embedWidget=true"
                  "&gauthHost=https%3A%2F%2Fsso.garmin.com%2Fsso"
                  "&service=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
                  "&source=https%3A%2F%2Fsso.garmin.com%2Fsso%2Fembed"
                  "&redirectAfterAccountLoginUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
                  "&redirectAfterAccountCreationUrl=https%3A%2F%2Fconnectapi.garmin.com%2Foauth-service%2Foauth%2Fpreauthorized"
                  "&consumeServiceTicket=false&generateExtraServiceTicket=true&generateNoServiceTicket=false")
            print()
            print("  4. In Network tab, find a request to .../preauthorized?ticket=ST-...")
            print("  5. Copy the ST-... value (everything after 'ticket=', up to '&' or end).")
            print("  6. Run:  uv run python garmin_oauth.py ticket ST-XXXXXX...")
            sys.exit(1)
        t = sys.argv[2].strip()
        if t.startswith("http"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(t).query)
            t = q.get("ticket", [""])[0]
        if not t.startswith("ST-"):
            sys.exit(f"That doesn't look like a Garmin SSO ticket (should start with ST-): {t[:40]}")
        exchange_ticket_for_oauth1(t)
        fetch_oauth2()
        print("All set. Tokens saved to .tokens/. Good for ~12 months.")
    elif len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
        bootstrap_via_zen_session()
        fetch_oauth2()
        print("All set. Tokens saved to .tokens/.")
    elif len(sys.argv) > 1 and sys.argv[1] == "login":
        email = sys.argv[2] if len(sys.argv) > 2 else None
        login_via_browser(email=email)
        fetch_oauth2()
        print("All set. Tokens saved to .tokens/.")
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
