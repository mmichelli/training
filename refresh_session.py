"""Refresh .garmin_session.json automatically.

Reads cookies for .garmin.com directly out of the local browser's SQLite
cookie store, then probes Garmin pages to extract a fresh CSRF token.

Supports Zen / Firefox / Chrome / Chromium / Brave automatically by scanning
common profile locations.

Usage:
    uv run python refresh_session.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
OUT = ROOT / ".garmin_session.json"
HOME = Path.home()

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0"

# Browser cookie-store locations (Firefox-family share schema)
FIREFOX_LIKE = [
    str(HOME / ".mozilla/firefox/*/cookies.sqlite"),
    str(HOME / ".zen/*/cookies.sqlite"),
    str(HOME / "snap/firefox/common/.mozilla/firefox/*/cookies.sqlite"),
    str(HOME / ".var/app/org.mozilla.firefox/.mozilla/firefox/*/cookies.sqlite"),
]


def find_cookie_db() -> Path | None:
    for pattern in FIREFOX_LIKE:
        matches = glob.glob(pattern)
        # Prefer the most-recently-modified profile
        matches = sorted(matches, key=lambda p: os.path.getmtime(p), reverse=True)
        for m in matches:
            return Path(m)
    return None


def read_garmin_cookies(db_path: Path) -> list[dict]:
    """Read cookies for *.garmin.com from a Firefox-family SQLite store."""
    # The DB may be locked by the running browser; copy to a temp path first.
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        shutil.copy(db_path, tmp.name)
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
    return [{"name": n, "value": v, "domain": h, "path": p} for n, v, h, p in rows]


def scrape_csrf(cookies: list[dict]) -> str | None:
    """Make an authenticated request to a modern page and pull CSRF out of the response."""
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
        "Cookie": cookie_header,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }
    with httpx.Client(http2=False, timeout=30, follow_redirects=True) as c:
        for url in [
            "https://connect.garmin.com/modern/home",
            "https://connect.garmin.com/modern/dashboard",
            "https://connect.garmin.com/modern/",
        ]:
            r = c.get(url, headers=hdrs)
            for pattern in [
                r'Connect-Csrf-Token["\s:=]+["\']([0-9a-f-]{36})["\']',
                r'csrfToken["\s:=]+["\']([0-9a-f-]{36})["\']',
                r'csrf_token["\s:=]+["\']([0-9a-f-]{36})["\']',
                r'"csrf"\s*:\s*"([0-9a-f-]{36})"',
                r'window\.AUTH\.csrf\s*=\s*["\']([0-9a-f-]{36})["\']',
                r'meta name=["\']_csrf["\'] content=["\']([0-9a-f-]{36})["\']',
            ]:
                m = re.search(pattern, r.text)
                if m:
                    return m.group(1)
    return None


def main() -> int:
    db = find_cookie_db()
    if not db:
        sys.exit("No Firefox-family cookie store found. Make sure you've logged into Garmin in Zen/Firefox.")
    print(f"reading cookies from {db}")
    cookies = read_garmin_cookies(db)
    if not cookies:
        sys.exit("No Garmin cookies found in that store. Log into connect.garmin.com first.")

    has_session = any(c["name"] == "session" for c in cookies)
    if not has_session:
        sys.exit("Garmin 'session' cookie missing — log into Garmin Connect in the browser first.")

    print(f"got {len(cookies)} cookies; scraping CSRF token…")
    csrf = scrape_csrf(cookies)

    if not csrf:
        # Fall back to keeping any existing CSRF — many endpoints still work for ~hours
        old = json.loads(OUT.read_text()) if OUT.exists() else {}
        csrf = old.get("csrf_token", "")
        if csrf:
            print(f"  (could not auto-scrape — keeping prior CSRF {csrf[:8]}…)")
        else:
            print("  (could not auto-scrape; you may need to paste one cURL — see plan doc)")

    OUT.write_text(json.dumps({
        "user_agent": USER_AGENT,
        "csrf_token": csrf or "",
        "cookies": cookies,
    }, indent=2))
    print(f"wrote {OUT} ({len(cookies)} cookies, csrf={'set' if csrf else 'empty'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
