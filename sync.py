"""Sync Garmin activities into markdown files under activities/.

Uses a session captured by garmin_login.py — bypasses Garmin's rate-limited
mobile SSO endpoint by reusing a logged-in browser session's cookies against
the same JSON endpoints the Connect web app calls.

If the session is missing or expired, run `uv run python garmin_login.py`.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).parent
SESSION_PATH = ROOT / ".garmin_session.json"
ACTIVITIES_DIR = ROOT / "activities"
API_BASE = "https://connect.garmin.com"


def load_session() -> httpx.Client:
    if not SESSION_PATH.exists():
        sys.exit("No session yet. Run: uv run python garmin_login.py")
    data = json.loads(SESSION_PATH.read_text())
    jar = httpx.Cookies()
    for c in data["cookies"]:
        jar.set(c["name"], c["value"], domain=c.get("domain", ".garmin.com"), path=c.get("path", "/"))
    client = httpx.Client(
        base_url=API_BASE,
        cookies=jar,
        headers={
            "User-Agent": data["user_agent"],
            "NK": "NT",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{API_BASE}/modern/",
        },
        timeout=30,
        follow_redirects=False,
    )
    return client


def fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(seconds_per_km: float | None) -> str:
    if not seconds_per_km or seconds_per_km <= 0:
        return "—"
    m, s = divmod(int(round(seconds_per_km)), 60)
    return f"{m}:{s:02d}/km"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


def activity_to_markdown(a: dict) -> tuple[str, str]:
    aid = a["activityId"]
    start = a.get("startTimeLocal", "")[:19].replace(" ", "T")
    date = start[:10] if start else "unknown"
    atype = a.get("activityType", {}).get("typeKey", "activity")
    name = a.get("activityName", atype)
    distance_m = a.get("distance") or 0
    distance_km = distance_m / 1000
    duration_s = a.get("duration") or 0
    moving_s = a.get("movingDuration") or duration_s
    pace = (moving_s / distance_km) if distance_km else 0
    avg_hr = a.get("averageHR")
    max_hr = a.get("maxHR")
    elev_gain = a.get("elevationGain")
    calories = a.get("calories")
    avg_speed = a.get("averageSpeed")

    fname = f"{date}-{slugify(atype)}-{aid}.md"
    fm = [
        "---",
        f"activity_id: {aid}",
        f"date: {date}",
        f"start: {start}",
        f"type: {atype}",
        f'name: "{name}"',
        f"distance_km: {distance_km:.2f}",
        f"duration_s: {int(duration_s)}",
        f"moving_s: {int(moving_s)}",
        f"avg_pace_s_per_km: {int(pace) if pace else 0}",
        f"avg_hr: {avg_hr or ''}",
        f"max_hr: {max_hr or ''}",
        f"elev_gain_m: {elev_gain or ''}",
        f"calories: {calories or ''}",
        f"avg_speed_mps: {avg_speed or ''}",
        "---",
        "",
        f"# {name}",
        "",
        f"- **Date**: {date}",
        f"- **Type**: {atype}",
        f"- **Distance**: {distance_km:.2f} km",
        f"- **Duration**: {fmt_duration(duration_s)} (moving {fmt_duration(moving_s)})",
        f"- **Pace**: {fmt_pace(pace)}",
        f"- **HR**: avg {avg_hr or '—'}, max {max_hr or '—'}",
        f"- **Elevation gain**: {elev_gain or '—'} m",
        f"- **Calories**: {calories or '—'}",
        "",
        "## Notes",
        "",
        "<!-- felt: -->",
        "<!-- knee: -->",
        "<!-- sub-threshold controlled? -->",
        "",
    ]
    return fname, "\n".join(fm)


def fetch_activities(client: httpx.Client, days: int) -> list[dict]:
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    all_acts: list[dict] = []
    start = 0
    limit = 50
    while True:
        r = client.get(
            "/proxy/activitylist-service/activities/search/activities",
            params={"limit": limit, "start": start, "startDate": start_date},
        )
        if r.status_code in (301, 302, 401, 403):
            sys.exit(
                f"Garmin session rejected ({r.status_code}). "
                f"Re-run: uv run python garmin_login.py"
            )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        all_acts.extend(batch)
        if len(batch) < limit:
            break
        start += limit
    return all_acts


def sync(days: int = 90) -> int:
    client = load_session()
    ACTIVITIES_DIR.mkdir(exist_ok=True)
    activities = fetch_activities(client, days)

    written = 0
    skipped = 0
    for a in activities:
        fname, content = activity_to_markdown(a)
        path = ACTIVITIES_DIR / fname
        head = content.split("## Notes")[0]
        if path.exists():
            existing = path.read_text()
            if existing.split("## Notes")[0] == head:
                skipped += 1
                continue
            # Preserve user / auto-filled notes on update
            if "## Notes" in existing:
                user_notes = existing.split("## Notes", 1)[1]
                content = head + "## Notes" + user_notes
        path.write_text(content)
        written += 1
    print(f"synced: {written} new/updated, {skipped} unchanged ({len(activities)} total)")
    return written


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    sync(days)
