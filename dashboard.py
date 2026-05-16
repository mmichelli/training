"""Generate dashboard.md from synced activity markdown files.

Reads activities/*.md frontmatter, joins against the plan's expected weekly volume,
and writes a human + LLM friendly summary at dashboard.md.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from plan_lookup import PLAN_START, WEEKDAYS, WEEKLY_TOTAL as PLAN_HOURS, prescription_for

ROOT = Path(__file__).parent
ACTIVITIES_DIR = ROOT / "activities"
DASHBOARD = ROOT / "dashboard.md"


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def load_activities() -> list[dict]:
    out = []
    if not ACTIVITIES_DIR.exists():
        return out
    for p in sorted(ACTIVITIES_DIR.glob("*.md")):
        fm = parse_frontmatter(p.read_text())
        if not fm.get("date"):
            continue
        try:
            d = datetime.strptime(fm["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append({
            "date": d,
            "type": fm.get("type", ""),
            "name": fm.get("name", ""),
            "distance_km": float(fm.get("distance_km") or 0),
            "duration_s": int(fm.get("duration_s") or 0),
            "moving_s": int(fm.get("moving_s") or 0),
            "avg_hr": int(float(fm["avg_hr"])) if fm.get("avg_hr") else None,
            "max_hr": int(float(fm["max_hr"])) if fm.get("max_hr") else None,
            "elev_gain_m": float(fm["elev_gain_m"]) if fm.get("elev_gain_m") else None,
        })
    return out


def plan_week_for(d: date) -> int:
    return ((d - PLAN_START).days // 7) + 1


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def fmt_h(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:.1f}h"


def fmt_pace(seconds_per_km: float) -> str:
    if seconds_per_km <= 0:
        return "—"
    m, s = divmod(int(round(seconds_per_km)), 60)
    return f"{m}:{s:02d}/km"


def latest_activity_file_mtime() -> datetime | None:
    if not ACTIVITIES_DIR.exists():
        return None
    files = list(ACTIVITIES_DIR.glob("*.md"))
    if not files:
        return None
    return datetime.fromtimestamp(max(f.stat().st_mtime for f in files))


def generate() -> str:
    acts = load_activities()
    today = date.today()
    current_week_start = monday_of(today)

    # Group by week (Monday-start)
    by_week: dict[date, list[dict]] = defaultdict(list)
    for a in acts:
        by_week[monday_of(a["date"])].append(a)

    lines: list[str] = []
    lines.append("# Two Oceans 2027 — Training Dashboard")
    lines.append("")
    lines.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from `activities/`._")
    lines.append("")
    lines.append(f"Plan week 1 starts: **{PLAN_START}** · Today: **{today}** · "
                 f"Current plan week: **{plan_week_for(today)}**")
    lines.append("")

    # Sync freshness — so it's obvious whether the latest run made it in.
    lines.append("## Sync status")
    lines.append("")
    last_mtime = latest_activity_file_mtime()
    if acts:
        latest = max(acts, key=lambda a: a["date"])
        lines.append(
            f"- Activities synced: **{len(acts)}** · "
            f"latest: **{latest['date']}** — {latest['name']} "
            f"({latest['distance_km']:.2f} km, {fmt_h(latest['duration_s'])})"
        )
    else:
        lines.append("- _No activities synced yet._")
    if last_mtime:
        delta = datetime.now() - last_mtime
        if delta < timedelta(minutes=1):
            ago = "just now"
        elif delta < timedelta(hours=1):
            ago = f"{int(delta.total_seconds() // 60)} min ago"
        elif delta < timedelta(days=1):
            ago = f"{int(delta.total_seconds() // 3600)}h ago"
        else:
            ago = f"{delta.days}d ago"
        lines.append(f"- Last sync wrote a file **{ago}** ({last_mtime.strftime('%Y-%m-%d %H:%M')})")
    lines.append("")

    # Current week
    lines.append("## This week")
    lines.append("")
    cw = sorted(by_week.get(current_week_start, []), key=lambda a: a["date"])
    if cw:
        total_s = sum(a["duration_s"] for a in cw)
        total_km = sum(a["distance_km"] for a in cw)
        wk = plan_week_for(today)
        target = PLAN_HOURS.get(wk, 0)
        actual_h = total_s / 3600
        pct = (actual_h / target * 100) if target else 0
        lines.append(f"- Plan week {wk} target: **{target:.1f}h** · "
                     f"actual so far: **{actual_h:.1f}h** ({pct:.0f}%) · "
                     f"distance: **{total_km:.1f} km**")
        lines.append("")
        lines.append("| Day | Type | Distance | Time | Pace | Avg HR | Max HR |")
        lines.append("|---|---|---|---|---|---|---|")
        for a in cw:
            pace = (a["moving_s"] / a["distance_km"]) if a["distance_km"] else 0
            lines.append(
                f"| {a['date'].strftime('%a %d')} | {a['type']} | "
                f"{a['distance_km']:.2f} km | {fmt_h(a['duration_s'])} | "
                f"{fmt_pace(pace)} | {a['avg_hr'] or '—'} | {a['max_hr'] or '—'} |"
            )
    else:
        lines.append("_No activities yet this week._")
    lines.append("")

    # Coming up — next 7 days from the plan, so it's obvious what's prescribed
    lines.append("## Coming up — next 7 days")
    lines.append("")
    lines.append("| Day | Date | Session | Purpose |")
    lines.append("|---|---|---|---|")
    for offset in range(7):
        d = today + timedelta(days=offset)
        p = prescription_for(d)
        if not (1 <= p.plan_week <= 48):
            continue
        marker = " ←" if offset == 0 else ""
        weekday = WEEKDAYS[d.weekday()]
        lines.append(
            f"| {weekday} {d.strftime('%d')}{marker} | {d} | {p.title} | {p.purpose} |"
        )
    lines.append("")

    # Last 8 weeks
    lines.append("## Last 8 weeks — volume vs. plan")
    lines.append("")
    lines.append("| Week start | Plan wk | Target | Actual | Δ | Distance | Long run |")
    lines.append("|---|---|---|---|---|---|---|")
    for i in range(7, -1, -1):
        ws = current_week_start - timedelta(weeks=i)
        wk_acts = by_week.get(ws, [])
        actual_h = sum(a["duration_s"] for a in wk_acts) / 3600
        km = sum(a["distance_km"] for a in wk_acts)
        long_run = max((a["duration_s"] for a in wk_acts if a["type"].startswith("running")), default=0)
        wk = plan_week_for(ws)
        target = PLAN_HOURS.get(wk, 0)
        delta = actual_h - target
        delta_str = f"{delta:+.1f}h" if target else "—"
        lines.append(
            f"| {ws} | {wk if 1 <= wk <= 48 else '—'} | "
            f"{target:.1f}h | {actual_h:.1f}h | {delta_str} | "
            f"{km:.1f} km | {fmt_h(long_run)} |"
        )
    lines.append("")

    # Sub-threshold check (intervals / workouts with high avg HR for the duration)
    lines.append("## Recent quality sessions")
    lines.append("")
    lines.append("Flagged: activities tagged as workout/interval, or HR avg above 75% of observed max.")
    lines.append("")
    observed_max = max((a["max_hr"] or 0) for a in acts) if acts else 0
    threshold_hr = int(observed_max * 0.75) if observed_max else 0
    quality = [
        a for a in acts
        if a["date"] >= today - timedelta(days=28)
        and (
            "interval" in a["type"].lower()
            or "workout" in (a["name"] or "").lower()
            or (a["avg_hr"] and threshold_hr and a["avg_hr"] >= threshold_hr)
        )
    ]
    if quality:
        lines.append("| Date | Type | Distance | Avg HR | Max HR | Note |")
        lines.append("|---|---|---|---|---|---|")
        for a in sorted(quality, key=lambda x: x["date"], reverse=True)[:10]:
            note = "⚠ over Z3?" if a["max_hr"] and observed_max and a["max_hr"] > observed_max * 0.88 else "ok"
            lines.append(
                f"| {a['date']} | {a['type']} | {a['distance_km']:.2f} km | "
                f"{a['avg_hr'] or '—'} | {a['max_hr'] or '—'} | {note} |"
            )
    else:
        lines.append("_No quality sessions in last 28 days._")
    lines.append("")

    # Weekly check-in scaffold
    lines.append("## Weekly check-in (fill Sunday evening)")
    lines.append("")
    lines.append(f"Week of {current_week_start}:")
    lines.append("- [ ] Slept 7+ hours per night")
    lines.append("- [ ] Knee ≥ last Sunday")
    lines.append("- [ ] Saturday long run felt like I could do more")
    lines.append("- [ ] Hit both gym sessions")
    lines.append("- [ ] Sub-threshold felt controlled (not crushing)")
    lines.append("")
    lines.append("Rule: 3+ yes → continue. 2 or fewer → next week is a down-week. No negotiation.")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    DASHBOARD.write_text(generate())
    print(f"wrote {DASHBOARD}")
