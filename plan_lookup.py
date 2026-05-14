"""What does the plan prescribe for a given date?

Pure-data module — no I/O. Imported by dashboard.py and usable as a CLI:
    uv run python plan_lookup.py            # today
    uv run python plan_lookup.py 2026-09-10
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime

PLAN_START = date(2026, 5, 11)  # Monday of plan week 1 — keep in sync with dashboard.py


@dataclass
class Prescription:
    plan_week: int
    phase: str
    weekday: str
    session: str
    purpose: str
    target_hours: float


# Phase ranges (inclusive)
PHASES = [
    (1, 8, "Reconditioning + base intro"),
    (9, 20, "NSA introduction (1 quality / wk)"),
    (21, 32, "Full NSA + hill insurance"),
    (33, 40, "Marathon build → Sevilla"),
    (41, 44, "Recovery + ultra-specific bridge"),
    (45, 48, "Peak, taper, heat, race"),
]

WEEKLY_TOTAL = {
    1: 2.0, 2: 2.5, 3: 3.0, 4: 2.5, 5: 3.5, 6: 4.0, 7: 4.5, 8: 3.5,
    9: 6.0, 10: 6.5, 11: 7.0, 12: 5.5, 13: 6.5, 14: 7.0, 15: 7.5, 16: 5.75,
    17: 7.5, 18: 8.0, 19: 8.5, 20: 6.0,
    21: 8.0, 22: 8.5, 23: 8.5, 24: 6.5, 25: 9.0, 26: 9.0, 27: 9.5, 28: 7.0,
    29: 10.0, 30: 10.5, 31: 10.5, 32: 7.5,
    33: 10.0, 34: 10.5, 35: 10.5, 36: 8.0, 37: 11.0, 38: 11.5, 39: 8.0, 40: 0.0,
    41: 5.5, 42: 8.0, 43: 9.0, 44: 11.0,
    45: 12.0, 46: 7.5, 47: 5.0, 48: 0.0,
}

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def phase_for(week: int) -> str:
    for lo, hi, name in PHASES:
        if lo <= week <= hi:
            return f"Months {((lo-1)//4)+1}–{((hi-1)//4)+1} · weeks {lo}-{hi} · {name}"
    return "outside plan window"


def session_for(week: int, weekday_idx: int) -> tuple[str, str]:
    """Return (session, purpose) for plan week and weekday (0=Mon)."""
    wd = WEEKDAYS[weekday_idx]
    # Months 1-2: reconditioning
    if 1 <= week <= 8:
        table = {
            "Mon": ("Gym A + 20–40 min run-walk", "Strength + easy aerobic"),
            "Tue": ("30–50 min Z2", "Aerobic"),
            "Wed": ("Rest, or 30 min easy + CrossFit (scaled)", "Recovery / cross-train"),
            "Thu": ("Gym B + 30–50 min Z2", "Strength + aerobic"),
            "Fri": ("Rest", "Recovery"),
            "Sat": ("Long run-walk (Z2, 9:1)", "Endurance base"),
            "Sun": ("30–45 min Z1 or hike", "Recovery"),
        }
        return table[wd]
    # Months 3-5: NSA intro — Thursday sub-threshold
    if 9 <= week <= 20:
        table = {
            "Mon": ("Easy 50–60 min Z2", "Aerobic"),
            "Tue": ("Easy 50–60 min Z2", "Aerobic"),
            "Wed": ("Easy 40 min + CrossFit (scaled)", "Aerobic + cross-train"),
            "Thu": ("SUB-THRESHOLD intervals + Gym B", "Threshold development"),
            "Fri": ("Easy 40–50 min + Gym A", "Aerobic + strength"),
            "Sat": ("Easy 60–75 min OR long run", "Aerobic"),
            "Sun": ("Long run Z2 (75 min → 2:30)", "Endurance"),
        }
        return table[wd]
    # Months 6-8: full NSA
    if 21 <= week <= 32:
        hill_weeks = {22, 25, 28, 31}
        table = {
            "Mon": ("Easy 60 min Z2", "Aerobic"),
            "Tue": ("SUB-THRESHOLD short reps + Gym A", "Threshold (short)"),
            "Wed": ("Easy 50 min + CrossFit (scaled)", "Aerobic + cross-train"),
            "Thu": ("SUB-THRESHOLD medium reps + Gym B", "Threshold (medium)"),
            "Fri": ("Easy 50–60 min Z2", "Aerobic"),
            "Sat": (
                "HILL session (10×90s)" if week in hill_weeks else "Easy 60–75 min",
                "Quad insurance" if week in hill_weeks else "Aerobic",
            ),
            "Sun": ("Long run Z2 (2:30 → 3:30)", "Endurance"),
        }
        return table[wd]
    # Months 9-10: marathon build
    if 33 <= week <= 40:
        if week == 40:
            return ("SEVILLA MARATHON week — taper + race Sun", "Qualifier")
        table = {
            "Mon": ("Easy 60 min", "Aerobic"),
            "Tue": ("SUB-THRESHOLD long reps (2000–3000m)", "Threshold (long)"),
            "Wed": ("Easy 50 min + CrossFit (scaled)", "Aerobic"),
            "Thu": ("SUB-THRESHOLD continuous tempo (10→40 min)", "Marathon-specific"),
            "Fri": ("Easy 50–60 min + Gym", "Aerobic + strength"),
            "Sat": ("Long run OR hill session (every 3rd wk)", "Endurance / quads"),
            "Sun": ("Long run Z2 (3:30 → 4:00)", "Endurance"),
        }
        return table[wd]
    # Months 11: recovery + ultra-specific
    if 41 <= week <= 44:
        if week == 41:
            return ("Recovery week — walk, mobility, light gym", "Post-marathon recovery")
        table = {
            "Mon": ("Easy 60 min", "Aerobic"),
            "Tue": ("SUB-THRESHOLD medium reps", "Threshold"),
            "Wed": ("Easy 60 min + CrossFit (no leg killers)", "Aerobic"),
            "Thu": ("HILL session — Constantia simulation", "Climbing-specific"),
            "Fri": ("Easy 60 min + Gym (lighter)", "Aerobic + strength"),
            "Sat": ("Long run (3:00 → 4:00)", "Endurance"),
            "Sun": ("B2B medium-long (1:30 → 2:30)", "Back-to-back endurance"),
        }
        return table[wd]
    # Final 4 weeks
    if 45 <= week <= 48:
        if week == 45:
            table = {
                "Mon": ("Easy 60 min", "Aerobic"),
                "Tue": ("4×1500m sub-threshold", "Peak threshold"),
                "Wed": ("Easy + CrossFit (scaled)", "Aerobic"),
                "Thu": ("HILL: 4×1km uphill", "Climbing"),
                "Fri": ("Easy 50 min", "Aerobic"),
                "Sat": ("4:30 long, last 45 min @ race effort", "Race-specific peak"),
                "Sun": ("2:30 B2B", "Endurance"),
            }
        elif week == 46:
            table = {
                "Mon": ("Easy 45 min", "Taper"),
                "Tue": ("Sub-threshold light", "Maintenance"),
                "Wed": ("Easy + sauna 25–30 min", "Heat adaptation start"),
                "Thu": ("Easy + sauna", "Heat"),
                "Fri": ("Easy + sauna", "Heat"),
                "Sat": ("2:30, 45 min @ TO goal pace (7:00–7:15/km)", "Goal-pace rehearsal"),
                "Sun": ("1:30 easy + sauna", "Endurance + heat"),
            }
        elif week == 47:
            table = {
                "Mon": ("Easy 45 min + sauna", "Travel taper"),
                "Tue": ("4×1km @ TO goal pace + sauna", "Pace rehearsal"),
                "Wed": ("Easy 30 min, TRAVEL TO CAPE TOWN", "Travel"),
                "Thu": ("Easy 30 min Newlands + 4×100m strides", "Shake out"),
                "Fri": ("Easy 20 min, expo, bib pickup", "Pre-race"),
                "Sat": ("RACE — TWO OCEANS ULTRA 56K", "A-RACE"),
                "Sun": ("Recovery walk, beer", "Recovery"),
            }
        else:  # 48
            table = {wd: ("Recovery", "Post-race") for wd in WEEKDAYS}
        return table[wd]
    return ("(outside plan window)", "")


def prescription_for(d: date) -> Prescription:
    week = ((d - PLAN_START).days // 7) + 1
    weekday_idx = d.weekday()
    session, purpose = session_for(week, weekday_idx)
    return Prescription(
        plan_week=week,
        phase=phase_for(week),
        weekday=WEEKDAYS[weekday_idx],
        session=session,
        purpose=purpose,
        target_hours=WEEKLY_TOTAL.get(week, 0.0),
    )


if __name__ == "__main__":
    d = datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else date.today()
    p = prescription_for(d)
    print(f"Date: {d} ({p.weekday})")
    print(f"Plan week: {p.plan_week}")
    print(f"Phase: {p.phase}")
    print(f"Weekly target: {p.target_hours:.1f}h")
    print(f"Session: {p.session}")
    print(f"Purpose: {p.purpose}")
