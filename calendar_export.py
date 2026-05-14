"""Generate plan.ics — all prescribed sessions as calendar events.

Subscribe in Google Calendar / Apple Calendar to see the plan on phone, laptop,
and (via watch calendar widget) on Garmin. Re-run any time the plan changes;
UIDs are deterministic so updates replace prior events on re-import.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, UTC
from pathlib import Path

from plan_lookup import PLAN_START, WEEKDAYS, prescription_for

ROOT = Path(__file__).parent
ICS_PATH = ROOT / "plan.ics"

# Default times for each weekday's session (local time). Tweak to taste.
DEFAULT_TIMES: dict[str, tuple[time, int]] = {
    "Mon": (time(17, 30), 75),   # gym + run
    "Tue": (time(17, 30), 75),   # sub-threshold
    "Wed": (time(12, 0), 60),    # CrossFit at work
    "Thu": (time(17, 30), 75),   # sub-threshold
    "Fri": (time(17, 30), 75),   # easy + gym
    "Sat": (time(9, 0), 120),    # long-ish / hill
    "Sun": (time(9, 0), 240),    # long run
}


def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def fold(line: str) -> str:
    """ICS folds lines >75 octets with CRLF + space."""
    if len(line) <= 75:
        return line
    out = [line[:75]]
    rest = line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


def event(uid: str, start: datetime, duration_min: int, summary: str, description: str) -> list[str]:
    end = start + timedelta(minutes=duration_min)
    dtstamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return [
        "BEGIN:VEVENT",
        fold(f"UID:{uid}"),
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}",
        f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}",
        fold(f"SUMMARY:{ics_escape(summary)}"),
        fold(f"DESCRIPTION:{ics_escape(description)}"),
        "END:VEVENT",
    ]


def build() -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//training//NSA Two Oceans 2027//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:NSA Two Oceans 2027",
        "X-WR-TIMEZONE:Europe/Oslo",
    ]
    # 48 weeks × 7 days
    for week_idx in range(48):
        for day_idx in range(7):
            d = PLAN_START + timedelta(weeks=week_idx, days=day_idx)
            p = prescription_for(d)
            # Skip rest days — they clutter the calendar
            if p.title.strip() == "Rest":
                continue
            t, dur = DEFAULT_TIMES[p.weekday]
            start = datetime.combine(d, t)
            wd = WEEKDAYS[day_idx]
            summary = f"W{p.plan_week} {wd} · {p.title}"
            description = (
                f"Plan week {p.plan_week} · {p.weekday}  ({p.phase})\n"
                f"Purpose: {p.purpose}\n"
                f"Weekly target: {p.target_hours:.1f}h\n"
                "\n"
                f"{p.description}"
            )
            uid = f"to27-w{p.plan_week:02d}-{wd.lower()}@training"
            lines.extend(event(uid, start, dur, summary, description))
    # Races as all-day
    races = [
        (date(2026, 7, 12), "Porsgrunn parkrun 5K — benchmark", "Set HR zones from this."),
        (date(2026, 9, 12), "Oslo Half Marathon 21K — fitness check", "Sub-threshold effort, NOT race pace. Target 2:10–2:15."),
        (date(2027, 2, 14), "Sevilla Marathon — qualifier", "Target 4:30–4:45. Sub-5:00 cushion."),
        (date(2027, 4, 3), "TWO OCEANS ULTRA 56K — A-RACE", "Blue medal target sub-7:00. Race plan in nsa-two-oceans-2027-plan.md."),
    ]
    for d, summary, desc in races:
        uid = f"to27-race-{d.isoformat()}@training"
        lines.extend([
            "BEGIN:VEVENT",
            fold(f"UID:{uid}"),
            f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
            fold(f"SUMMARY:🏁 {ics_escape(summary)}"),
            fold(f"DESCRIPTION:{ics_escape(desc)}"),
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


if __name__ == "__main__":
    ICS_PATH.write_text(build())
    print(f"wrote {ICS_PATH} ({ICS_PATH.stat().st_size} bytes)")
