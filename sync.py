"""Sync Garmin activities into markdown files under activities/."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import garth

ROOT = Path(__file__).parent
TOKEN_DIR = ROOT / ".garth"
ACTIVITIES_DIR = ROOT / "activities"


def load_secrets() -> tuple[str, str]:
    path = ROOT / ".secrets"
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["GARMIN_EMAIL"], env["GARMIN_PASSWORD"]


def login() -> None:
    TOKEN_DIR.mkdir(exist_ok=True)
    try:
        garth.resume(str(TOKEN_DIR))
        garth.client.username  # forces a token check
    except Exception:
        email, password = load_secrets()
        garth.login(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
        garth.save(str(TOKEN_DIR))


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
    avg_speed = a.get("averageSpeed")  # m/s

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


def sync(days: int = 90) -> int:
    login()
    ACTIVITIES_DIR.mkdir(exist_ok=True)
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    written = 0
    skipped = 0
    start = 0
    limit = 50
    while True:
        batch = garth.connectapi(
            "/activitylist-service/activities/search/activities",
            params={"limit": limit, "start": start, "startDate": start_date},
        )
        if not batch:
            break
        for a in batch:
            fname, content = activity_to_markdown(a)
            path = ACTIVITIES_DIR / fname
            if path.exists() and path.read_text().split("## Notes")[0] == content.split("## Notes")[0]:
                skipped += 1
                continue
            # preserve user notes if file exists
            if path.exists():
                existing = path.read_text()
                if "## Notes" in existing:
                    user_notes = existing.split("## Notes", 1)[1]
                    content = content.split("## Notes")[0] + "## Notes" + user_notes
            path.write_text(content)
            written += 1
        if len(batch) < limit:
            break
        start += limit

    print(f"synced: {written} new/updated, {skipped} unchanged")
    return written


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    sync(days)
