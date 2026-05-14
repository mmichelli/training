"""Authenticated Garmin Connect client.

Bypasses the rate-limited mobile SSO entirely by using a browser session
captured into .garmin_session.json (see garmin_login.py or paste cookies in).

Why custom HTTP plumbing: Garmin's Cloudflare gate is picky about default
httpx headers. Passing Cookie as a raw header + Sec-Fetch-* + disabling
HTTP/2 reliably bypasses it; the default httpx cookie-jar path does not.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).parent
SESSION_PATH = ROOT / ".garmin_session.json"
BASE = "https://connect.garmin.com"


class Garmin:
    def __init__(self) -> None:
        if not SESSION_PATH.exists():
            raise SystemExit("No session yet. Paste a cURL or run garmin_login.py")
        data = json.loads(SESSION_PATH.read_text())
        self.user_agent = data["user_agent"]
        self.csrf = data.get("csrf_token", "") or ""
        self.cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in data["cookies"])
        self.client = httpx.Client(http2=False, timeout=30, base_url=BASE)
        self._display_name: str | None = None
        if not self.csrf:
            self._refresh_csrf()

    @property
    def display_name(self) -> str:
        if self._display_name is None:
            r = self.get("/gc-api/userprofile-service/socialProfile")
            self._display_name = r["displayName"] if r else ""
        return self._display_name

    def _refresh_csrf(self) -> None:
        """Fetch CSRF token by scraping a modern page."""
        r = self.client.get("/modern/", headers={
            "User-Agent": self.user_agent,
            "Accept": "text/html",
            "Cookie": self.cookie_header,
        })
        # Search for Connect-Csrf-Token or csrf-token-like values in HTML/JS payloads
        m = re.search(r'"csrf[Tt]oken"\s*:\s*"([0-9a-f-]{36})"', r.text)
        if not m:
            m = re.search(r'csrfToken\s*=\s*[\'"]([0-9a-f-]{36})[\'"]', r.text)
        if m:
            self.csrf = m.group(1)
            data = json.loads(SESSION_PATH.read_text())
            data["csrf_token"] = self.csrf
            SESSION_PATH.write_text(json.dumps(data, indent=2))

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en",
            "NK": "NT",
            "X-app-ver": "5.24.1.3a",
            "X-lang": "en-US",
            "X-Requested-With": "XMLHttpRequest",
            "Connect-Csrf-Token": self.csrf,
            "Cookie": self.cookie_header,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": f"{BASE}/modern/",
        }

    def get(self, path: str, **params) -> Any:
        r = self.client.get(path, params=params, headers=self._headers())
        if r.status_code == 401:
            raise SystemExit(
                f"Garmin session expired (401 on {path}). "
                "Paste a fresh cURL into .garmin_session.json."
            )
        if r.status_code == 404:
            return None
        if r.status_code in (400, 403, 405):
            return None
        r.raise_for_status()
        if not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text

    # ── Specific endpoints ───────────────────────────────────────────────

    def activities(self, limit: int = 50, start: int = 0, start_date: str = "2020-01-01") -> list[dict]:
        return self.get(
            "/gc-api/activitylist-service/activities/search/activities",
            limit=limit, start=start, startDate=start_date,
        ) or []

    def daily_summary(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/usersummary-service/usersummary/daily/{ymd}")

    def sleep(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/wellness-service/wellness/dailySleepData", date=ymd)

    def hrv(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/hrv-service/hrv/{ymd}")

    def stress(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/wellness-service/wellness/dailyStress/{ymd}")

    def body_battery(self, ymd: str) -> dict | None:
        return self.get(
            f"/gc-api/wellness-service/wellness/bodyBattery/reports/daily",
            startDate=ymd, endDate=ymd,
        )

    def weight(self, start_ymd: str, end_ymd: str) -> dict | None:
        # Path-templated, not query — and the only weight endpoint that responds.
        return self.get(f"/gc-api/weight-service/weight/range/{start_ymd}/{end_ymd}")

    def training_readiness(self, ymd: str) -> dict | None:
        return self.get(f"/gc-api/metrics-service/metrics/trainingreadiness/{ymd}")


if __name__ == "__main__":
    g = Garmin()
    print("activities (first 3):")
    for a in g.activities(limit=3, start_date="2024-01-01"):
        print(f"  {a.get('startTimeLocal','')[:10]} {a.get('activityType',{}).get('typeKey','?'):<10} "
              f"{(a.get('distance') or 0)/1000:5.1f} km  HR avg/max {a.get('averageHR') or '—'}/{a.get('maxHR') or '—'}")
