"""Authenticated Garmin Connect client.

Auth recipe (battle-tested 2026-05-14):
  1. Read Garmin cookies from the user's local Zen/Firefox cookie store.
  2. Use curl_cffi with a Chrome TLS fingerprint to avoid Cloudflare's
     bot gate.
  3. Fetch /modern/ (the SPA shell) and scrape the current CSRF token
     out of `<meta name="csrf-token">`.
  4. Use cookies + fresh CSRF on every /gc-api/ call.

Self-healing: if the CSRF rotates mid-session, we re-scrape it on the next
401/403. The user only ever needs to be logged into Garmin Connect in
their browser — no logins prompted by us, no cookies pasted by hand.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import sqlite3
import tempfile
from typing import Any

from curl_cffi import requests as cc

API_HOST = "https://connect.garmin.com"
USER_AGENT_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "NK": "NT",
    "X-app-ver": "5.24.1.3a",
    "X-lang": "en-US",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://connect.garmin.com/modern/",
}


def _read_zen_garmin_cookies() -> list[tuple[str, str, str, str]]:
    """Pull Garmin cookies from a local Firefox-family (Zen/Firefox) profile."""
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
        raise SystemExit(
            "No Firefox-family cookie store found. Open Garmin Connect in "
            "your browser (Zen or Firefox) and log in, then retry."
        )
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


class Garmin:
    def __init__(self) -> None:
        rows = _read_zen_garmin_cookies()
        if not any(n == "JWT_WEB" for n, *_ in rows):
            raise SystemExit(
                "No authenticated Garmin session in your browser. Open "
                "https://connect.garmin.com and log in, then retry."
            )
        self.session = cc.Session(impersonate="chrome131")
        for n, v, host, path in rows:
            self.session.cookies.set(n, v, domain=host.lstrip("."), path=path or "/")
        self.csrf: str = ""
        self._display_name: str | None = None
        self._refresh_csrf()

    def _refresh_csrf(self) -> None:
        """Fetch the modern SPA page and scrape `<meta name='csrf-token'>`."""
        r = self.session.get(f"{API_HOST}/modern/", allow_redirects=True, timeout=20)
        m = re.search(r'name="csrf-token"\s+content="([0-9a-f-]{36})"', r.text)
        if not m:
            raise SystemExit(
                "Could not find CSRF token in /modern/ page. Garmin session "
                "may have expired — log in to https://connect.garmin.com again."
            )
        self.csrf = m.group(1)

    def _headers(self) -> dict[str, str]:
        return {**USER_AGENT_HEADERS, "Connect-Csrf-Token": self.csrf}

    def get(self, path: str, **params) -> Any:
        url = path if path.startswith("http") else f"{API_HOST}{path}"
        r = self.session.get(url, params=params, headers=self._headers(), timeout=30)
        if r.status_code in (401, 403):
            # CSRF may have rotated. Refresh and retry once.
            self._refresh_csrf()
            r = self.session.get(url, params=params, headers=self._headers(), timeout=30)
        if r.status_code in (400, 403, 404, 405):
            return None
        if r.status_code != 200:
            r.raise_for_status()
        if not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text

    @property
    def display_name(self) -> str:
        if self._display_name is None:
            r = self.get("/gc-api/userprofile-service/socialProfile")
            self._display_name = r["displayName"] if r else ""
        return self._display_name or ""

    # ── Specific endpoints ────────────────────────────────────────────────

    def activities(self, limit: int = 50, start: int = 0, start_date: str = "2020-01-01") -> list[dict]:
        return self.get(
            "/gc-api/activitylist-service/activities/search/activities",
            limit=limit, start=start, startDate=start_date,
        ) or []

    def daily_summary(self, ymd: str) -> dict | None:
        return self.get(
            f"/gc-api/usersummary-service/usersummary/daily/{self.display_name}",
            calendarDate=ymd,
        )

    def sleep(self, ymd: str) -> dict | None:
        return self.get(
            f"/gc-api/wellness-service/wellness/dailySleepData/{self.display_name}",
            date=ymd, nonSleepBufferMinutes=60,
        )

    def hrv(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/hrv-service/hrv/{ymd}")

    def stress(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/wellness-service/wellness/dailyStress/{ymd}")

    def weight(self, start_ymd: str, end_ymd: str) -> dict | None:
        return self.get(f"/gc-api/weight-service/weight/range/{start_ymd}/{end_ymd}")


if __name__ == "__main__":
    g = Garmin()
    print(f"signed in as: {g.display_name}")
    print(f"csrf: {g.csrf[:12]}…")
    print("recent activities:")
    for a in g.activities(limit=3, start_date="2024-01-01"):
        date_ = a.get("startTimeLocal", "")[:10]
        kind = a.get("activityType", {}).get("typeKey", "?")
        dist = (a.get("distance") or 0) / 1000
        print(f"  {date_}  {kind:<14} {dist:5.1f} km")
