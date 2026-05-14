"""Authenticated Garmin Connect client — OAuth2 against connectapi.garmin.com.

The auth model:
1. First time only, run `uv run python garmin_oauth.py login` — opens a real
   Chromium, you log in, OAuth1+OAuth2 tokens are saved to `.tokens/`.
2. After that, every API call uses the OAuth2 Bearer token. On 401, the token
   is silently refreshed via the OAuth1 → OAuth2 exchange. No browser needed.
3. The OAuth1 token lasts ~1 year; only then do you need to re-run the login.

Why we switched from cookie scraping (2026-05-14): cookies/CSRF rotate on a
fraction-of-a-day cadence and require constant manual refresh. OAuth2 against
`connectapi.garmin.com` is the API path Garmin's official mobile app uses;
it's stable and self-refreshing.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from garmin_oauth import current_access_token, fetch_oauth2

API_BASE = "https://connectapi.garmin.com"
USER_AGENT = "com.garmin.android.apps.connectmobile"


class Garmin:
    def __init__(self) -> None:
        self.client = httpx.Client(http2=False, timeout=30, base_url=API_BASE)
        self._display_name: str | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {current_access_token()}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "NK": "NT",
        }

    def get(self, path: str, **params) -> Any:
        # Strip the /gc-api/ prefix if the caller still uses it — the
        # connectapi host does NOT use that prefix.
        api_path = re.sub(r"^/gc-api", "", path)

        r = self.client.get(api_path, params=params, headers=self._headers())
        if r.status_code == 401:
            # Token may have expired between current_access_token()'s check
            # and the actual request. Force a refresh and retry once.
            fetch_oauth2()
            r = self.client.get(api_path, params=params, headers=self._headers())
        if r.status_code in (400, 403, 404, 405):
            return None
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
            r = self.get("/userprofile-service/socialProfile")
            self._display_name = r["displayName"] if r else ""
        return self._display_name or ""

    # ── Specific endpoints ────────────────────────────────────────────────

    def activities(self, limit: int = 50, start: int = 0, start_date: str = "2020-01-01") -> list[dict]:
        return self.get(
            "/activitylist-service/activities/search/activities",
            limit=limit, start=start, startDate=start_date,
        ) or []

    def daily_summary(self, ymd: str) -> dict | None:
        return self.get(
            f"/usersummary-service/usersummary/daily/{self.display_name}",
            calendarDate=ymd,
        )

    def sleep(self, ymd: str) -> dict | None:
        return self.get(
            f"/wellness-service/wellness/dailySleepData/{self.display_name}",
            date=ymd, nonSleepBufferMinutes=60,
        )

    def hrv(self, ymd: str) -> dict | None:
        return self.get(f"/hrv-service/hrv/{ymd}")

    def stress(self, ymd: str) -> dict | None:
        return self.get(f"/wellness-service/wellness/dailyStress/{ymd}")

    def weight(self, start_ymd: str, end_ymd: str) -> dict | None:
        return self.get(f"/weight-service/weight/range/{start_ymd}/{end_ymd}")


if __name__ == "__main__":
    g = Garmin()
    print(f"signed in as: {g.display_name}")
    print("recent activities:")
    for a in g.activities(limit=3, start_date="2024-01-01"):
        date_ = a.get("startTimeLocal", "")[:10]
        kind = a.get("activityType", {}).get("typeKey", "?")
        dist = (a.get("distance") or 0) / 1000
        print(f"  {date_}  {kind:<14} {dist:5.1f} km")
