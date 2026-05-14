"""Pull all Garmin streams into data/ and activities/.

Per-day JSON files for HRV / stress / sleep / daily summary, one markdown
per activity. Idempotent — re-running only fetches dates not yet stored.

    uv run python ingest.py            # last 90 days
    uv run python ingest.py --days 365 # last year
    uv run python ingest.py --force    # re-fetch existing
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from garmin import Garmin

ROOT = Path(__file__).parent
ACTIVITIES = ROOT / "activities"
DATA = ROOT / "data"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


def fmt_dur(s: float | None) -> str:
    if not s:
        return "—"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def activity_to_md(a: dict) -> tuple[str, str]:
    aid = a["activityId"]
    start = a.get("startTimeLocal", "")[:19].replace(" ", "T")
    ymd = start[:10] if start else "unknown"
    atype = a.get("activityType", {}).get("typeKey", "activity")
    name = a.get("activityName", atype)
    dist_km = (a.get("distance") or 0) / 1000
    dur = a.get("duration") or 0
    moving = a.get("movingDuration") or dur
    pace = (moving / dist_km) if dist_km else 0
    avg_hr = a.get("averageHR")
    max_hr = a.get("maxHR")
    fname = f"{ymd}-{slugify(atype)}-{aid}.md"
    body = "\n".join([
        "---",
        f"activity_id: {aid}",
        f"date: {ymd}",
        f"start: {start}",
        f"type: {atype}",
        f'name: "{name}"',
        f"distance_km: {dist_km:.2f}",
        f"duration_s: {int(dur)}",
        f"moving_s: {int(moving)}",
        f"avg_pace_s_per_km: {int(pace) if pace else 0}",
        f"avg_hr: {avg_hr or ''}",
        f"max_hr: {max_hr or ''}",
        f"elev_gain_m: {a.get('elevationGain') or ''}",
        f"calories: {a.get('calories') or ''}",
        f"avg_speed_mps: {a.get('averageSpeed') or ''}",
        f"avg_running_cadence: {a.get('averageRunningCadenceInStepsPerMinute') or ''}",
        f"max_running_cadence: {a.get('maxRunningCadenceInStepsPerMinute') or ''}",
        f"vo2max: {a.get('vO2MaxValue') or ''}",
        "---",
        "",
        f"# {name}",
        "",
        f"- **Date**: {ymd}",
        f"- **Type**: {atype}",
        f"- **Distance**: {dist_km:.2f} km",
        f"- **Duration**: {fmt_dur(dur)} (moving {fmt_dur(moving)})",
        f"- **HR**: avg {avg_hr or '—'}, max {max_hr or '—'}",
        f"- **Elevation gain**: {a.get('elevationGain') or '—'} m",
        f"- **Calories**: {a.get('calories') or '—'}",
        f"- **Avg cadence**: {a.get('averageRunningCadenceInStepsPerMinute') or '—'} spm",
        "",
        "## Notes",
        "",
        "<!-- felt: -->",
        "<!-- knee: -->",
        "<!-- sub-threshold controlled? -->",
        "",
    ])
    return fname, body


def daterange(days: int) -> list[date]:
    today = date.today()
    return [today - timedelta(days=i) for i in range(days)]


def write_json(stream: str, ymd: date, payload: dict | None) -> bool:
    """Returns True if file was written."""
    if not payload:
        return False
    d = DATA / stream
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{ymd.isoformat()}.json"
    f.write_text(json.dumps(payload, indent=2))
    return True


def ingest_activities(g: Garmin, days: int, force: bool) -> int:
    ACTIVITIES.mkdir(exist_ok=True)
    start_date = (date.today() - timedelta(days=days)).isoformat()
    all_a: list[dict] = []
    start = 0
    while True:
        batch = g.activities(limit=50, start=start, start_date=start_date)
        if not batch:
            break
        all_a.extend(batch)
        if len(batch) < 50:
            break
        start += 50
    written = 0
    for a in all_a:
        fname, content = activity_to_md(a)
        p = ACTIVITIES / fname
        if p.exists() and not force:
            existing = p.read_text()
            if "## Notes" in existing:
                user_notes = existing.split("## Notes", 1)[1]
                content = content.split("## Notes")[0] + "## Notes" + user_notes
        p.write_text(content)
        written += 1
    return written


def ingest_daily_streams(g: Garmin, days: int, force: bool) -> dict[str, int]:
    counts = {"hrv": 0, "stress": 0, "sleep": 0, "daily": 0}
    for d in daterange(days):
        ymd = d.isoformat()
        # HRV (confirmed working)
        if force or not (DATA / "hrv" / f"{ymd}.json").exists():
            if write_json("hrv", d, g.get(f"/gc-api/hrv-service/hrv/{ymd}")):
                counts["hrv"] += 1
        # Stress (confirmed working)
        if force or not (DATA / "stress" / f"{ymd}.json").exists():
            if write_json("stress", d, g.get(f"/gc-api/wellness-service/wellness/dailyStress/{ymd}")):
                counts["stress"] += 1
        # Sleep — displayName in path + nonSleepBufferMinutes is required
        if force or not (DATA / "sleep" / f"{ymd}.json").exists():
            r = g.get(
                f"/gc-api/wellness-service/wellness/dailySleepData/{g.display_name}",
                date=ymd, nonSleepBufferMinutes=60,
            )
            if r:
                write_json("sleep", d, r)
                counts["sleep"] += 1
        # Daily summary — displayName + calendarDate query
        if force or not (DATA / "daily" / f"{ymd}.json").exists():
            r = g.get(
                f"/gc-api/usersummary-service/usersummary/daily/{g.display_name}",
                calendarDate=ymd,
            )
            if r:
                write_json("daily", d, r)
                counts["daily"] += 1
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    g = Garmin()
    print(f"Pulling last {args.days} days for {g.get('/gc-api/userprofile-service/socialProfile').get('displayName')}…")

    n_act = ingest_activities(g, args.days, args.force)
    print(f"activities: {n_act} files written/updated")

    counts = ingest_daily_streams(g, args.days, args.force)
    for k, v in counts.items():
        print(f"{k}: {v} new days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
