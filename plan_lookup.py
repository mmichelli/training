"""What does the plan prescribe for a given date?

Pure-data module — no I/O. Imported by dashboard.py and calendar_export.py.

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
    title: str            # short calendar-friendly headline
    purpose: str          # one-line "why"
    description: str      # plain-language how-to (multi-paragraph, calendar DESCRIPTION)
    target_hours: float


PHASES = [
    (1, 8, "Reconditioning + base intro"),
    (9, 20, "NSA introduction (1 quality / wk)"),
    (21, 32, "Full NSA + hill insurance"),
    (33, 40, "Marathon build → Sevilla"),
    (41, 44, "Recovery + ultra-specific bridge"),
    (45, 48, "Peak, taper, heat, race"),
]

# Realistic reconditioning weekly hours — most of it walking, not running.
WEEKLY_TOTAL = {
    1: 1.5, 2: 2.0, 3: 2.5, 4: 2.0, 5: 3.0, 6: 3.5, 7: 4.0, 8: 3.5,
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
            return f"weeks {lo}-{hi} · {name}"
    return "outside plan window"


# ---------- Plain-language session descriptions ----------

REST = (
    "Rest",
    "Recovery",
    "No training. A 15-minute walk to loosen up is fine, but optional. Sleep, eat, hydrate.",
)


def _walk(minutes_low: int, minutes_high: int, why: str = "Aerobic base") -> tuple[str, str, str]:
    return (
        f"Walk {minutes_low}–{minutes_high} min",
        why,
        f"Just walk. Brisk pace if you feel good, easy stroll if not. {minutes_low}–{minutes_high} minutes "
        "total. No jogging required. If your knee feels off, cut it short — there is no penalty.",
    )


def _walk_jog(minutes: int, jog_s: int, walk_s: int, why: str = "Run-walk reintroduction") -> tuple[str, str, str]:
    cycles = minutes * 60 // (jog_s + walk_s)
    return (
        f"Walk-jog {minutes} min ({jog_s}s jog / {walk_s}s walk)",
        why,
        (
            f"Total session: {minutes} minutes.\n\n"
            f"Pattern: jog {jog_s} seconds, walk {walk_s} seconds, repeat. About {cycles} cycles.\n\n"
            "Effort during the jog: you should be able to talk in full sentences. If you're breathing hard, "
            "slow the jog or take a longer walk. The goal is consistency, not speed. If anything hurts more "
            "than mild discomfort, stop and walk the rest."
        ),
    )


def _easy_run(minutes_low: int, minutes_high: int) -> tuple[str, str, str]:
    return (
        f"Easy run {minutes_low}–{minutes_high} min",
        "Aerobic base",
        (
            f"Easy {minutes_low}–{minutes_high} min continuous run, Z2 effort.\n\n"
            "Z2 = conversational. Full sentences without gasping. Nose-breathing should be possible most of the time. "
            "If HR zones aren't set yet, go by feel — when in doubt, slower. The point of an easy run is to be easy."
        ),
    )


GYM_A = (
    "Gym A (lower body)",
    "Strength — lower body",
    (
        "Lower-body strength session, ~45 min.\n\n"
        "1. Back squat — 3 × 5 (Starting Strength linear progression — add 2.5 kg per session)\n"
        "2. Romanian deadlift — 3 × 8\n"
        "3. Bulgarian split squat — 3 × 8 each leg\n"
        "4. Plank — 3 × 30–60s\n"
        "5. Pull-up or lat pulldown — 3 × 8\n\n"
        "Form is the priority over weight. Book a trainer session in week 1 for squat depth, RDL hinge, "
        "and hip thrust setup — non-negotiable."
    ),
)

GYM_B = (
    "Gym B (posterior chain)",
    "Strength — posterior chain + accessory",
    (
        "Posterior chain + accessory, ~45 min.\n\n"
        "1. Hip thrust — 3 × 8\n"
        "2. Single-leg calf raise — 3 × 12 each\n"
        "3. Glute bridge with band — 3 × 12\n"
        "4. Side plank — 3 × 30–45s each side\n"
        "5. Single-arm row — 3 × 8 each\n"
        "6. Eccentric step-down — 3 × 10 each leg (knee insurance — control the lowering for 3 full seconds)\n"
    ),
)


def _easy_long_run(minutes: int) -> tuple[str, str, str]:
    return (
        f"Long run {minutes} min Z2",
        "Endurance base",
        (
            f"Long aerobic run, {minutes} minutes continuous at Z2.\n\n"
            "This is the most important session of the week. Pace is whatever lets you finish feeling like "
            "you could have done another 20 min. If you blow up here, you took it too hard. "
            "Walk breaks are fine if needed — better to finish slow than to push and not finish."
        ),
    )


# ---------- Week-by-week reconditioning schedule (weeks 1–8) ----------
# Each week is a dict: weekday -> (title, purpose, description)

RECON_SCHEDULE: dict[int, dict[str, tuple[str, str, str]]] = {
    1: {
        "Mon": _walk(20, 30, "Activation walk"),
        "Tue": _walk_jog(20, 30, 90, "First run-walk — get the legs moving"),
        "Wed": REST,
        "Thu": _walk(20, 30, "Recovery walk"),
        "Fri": REST,
        "Sat": _walk_jog(25, 60, 120),
        "Sun": _walk(30, 45, "Aerobic walk or hike"),
    },
    2: {
        "Mon": _walk(25, 35),
        "Tue": _walk_jog(25, 45, 75),
        "Wed": REST,
        "Thu": _walk_jog(20, 30, 90),
        "Fri": REST,
        "Sat": _walk_jog(30, 60, 90),
        "Sun": _walk(30, 45),
    },
    3: {
        "Mon": GYM_A,  # introduce first gym session
        "Tue": _walk_jog(25, 60, 90),
        "Wed": REST,
        "Thu": _walk_jog(25, 60, 90),
        "Fri": REST,
        "Sat": _walk_jog(35, 60, 60),
        "Sun": _walk(30, 45),
    },
    4: {  # down-week — keep volume lower while body absorbs week 3 gym
        "Mon": GYM_A,
        "Tue": _walk_jog(20, 60, 90),
        "Wed": REST,
        "Thu": _walk(25, 35),
        "Fri": REST,
        "Sat": _walk_jog(30, 60, 60),
        "Sun": _walk(20, 30),
    },
    5: {
        "Mon": GYM_A,
        "Tue": _walk_jog(30, 90, 60),
        "Wed": REST,
        "Thu": GYM_B,  # introduce second gym session
        "Fri": REST,
        "Sat": _walk_jog(40, 90, 60),
        "Sun": _walk(40, 50),
    },
    6: {
        "Mon": GYM_A,
        "Tue": _walk_jog(35, 120, 60),
        "Wed": _walk(30, 40, "Easy aerobic"),
        "Thu": GYM_B,
        "Fri": REST,
        "Sat": _easy_run(30, 40),  # first continuous easy run
        "Sun": _walk(45, 60),
    },
    7: {
        "Mon": GYM_A,
        "Tue": _easy_run(30, 40),
        "Wed": _walk(30, 40),
        "Thu": GYM_B,
        "Fri": REST,
        "Sat": _easy_long_run(50),
        "Sun": _walk(45, 60),
    },
    8: {  # down-week before NSA introduction
        "Mon": GYM_A,
        "Tue": _easy_run(25, 35),
        "Wed": REST,
        "Thu": GYM_B,
        "Fri": REST,
        "Sat": _easy_long_run(40),
        "Sun": _walk(30, 45),
    },
}


def session_for(week: int, weekday_idx: int) -> tuple[str, str, str]:
    """Return (title, purpose, description) for a plan week + weekday (0=Mon)."""
    wd = WEEKDAYS[weekday_idx]

    if 1 <= week <= 8:
        return RECON_SCHEDULE[week][wd]

    # Months 3-5: NSA intro — Thursday sub-threshold
    if 9 <= week <= 20:
        table = {
            "Mon": ("Easy 50–60 min Z2", "Aerobic", "Easy 50–60 min run, Z2 only. Conversational."),
            "Tue": ("Easy 50–60 min Z2", "Aerobic", "Easy 50–60 min run, Z2 only."),
            "Wed": ("Easy 40 min + CrossFit (scaled)",
                    "Aerobic + cross-train",
                    "40 min easy run OR CrossFit class. Tell coach: no max squats, no heavy box jumps, no AMRAP burpees. Want Z3 cardio + upper-body strength, NOT legs destroyed before Thursday."),
            "Thu": ("SUB-THRESHOLD intervals + Gym B",
                    "Threshold development",
                    "Sub-threshold session (see week-specific intervals in plan). Sub-threshold = top of Z3 (75–78% HRR), NEVER Z4. Could-do-another-rep feeling at the end. Then Gym B."),
            "Fri": ("Easy 40–50 min + Gym A", "Aerobic + strength", "40–50 min easy run + Gym A session."),
            "Sat": ("Easy 60–75 min OR long run", "Aerobic", "60–75 min easy run, or alternate as a long run depending on schedule."),
            "Sun": ("Long run Z2 (75 min → 2:30)", "Endurance", "Long Z2 run. Most important session of the week."),
        }
        return table[wd]

    if 21 <= week <= 32:
        hill_weeks = {22, 25, 28, 31}
        table = {
            "Mon": ("Easy 60 min Z2", "Aerobic", "Easy 60 min run, Z2."),
            "Tue": ("SUB-THRESHOLD short reps + Gym A",
                    "Threshold (short)",
                    "Short reps at sub-threshold (8–10×600m or 10–12×400m). Top of Z3 only. Then Gym A."),
            "Wed": ("Easy 50 min + CrossFit (scaled)", "Aerobic + cross-train", "50 min easy + scaled CrossFit."),
            "Thu": ("SUB-THRESHOLD medium reps + Gym B",
                    "Threshold (medium)",
                    "Medium reps at sub-threshold (5–6×1500m, building to 4×2000m). Top of Z3 only. Then Gym B."),
            "Fri": ("Easy 50–60 min Z2", "Aerobic", "Easy 50–60 min run."),
            "Sat": (
                "HILL session (10×90s)" if week in hill_weeks else "Easy 60–75 min",
                "Quad insurance" if week in hill_weeks else "Aerobic",
                "Hill day: 2 km warmup, 10×90s uphill at top-Z3, jog down recovery, 2 km cooldown. Find ~6–10% grade or treadmill at 8%." if week in hill_weeks else "60–75 min easy.",
            ),
            "Sun": ("Long run Z2 (2:30 → 3:30)", "Endurance", "Long Z2 run, building toward 3:30."),
        }
        return table[wd]

    if 33 <= week <= 40:
        if week == 40:
            return ("SEVILLA MARATHON week", "Qualifier", "Race Sun. Tapering all week. See plan doc for full race-week schedule.")
        table = {
            "Mon": ("Easy 60 min", "Aerobic", "Easy 60 min Z2."),
            "Tue": ("SUB-THRESHOLD long reps (2000–3000m)", "Threshold (long)", "Long reps at sub-threshold."),
            "Wed": ("Easy 50 min + CrossFit", "Aerobic", "50 min easy + scaled CrossFit."),
            "Thu": ("SUB-THRESHOLD continuous tempo",
                    "Marathon-specific",
                    "Continuous tempo blocks (10 min → 40 min). Top-Z3 ceiling. This is the marathon-pace work."),
            "Fri": ("Easy 50–60 min + Gym", "Aerobic + strength", "50–60 min easy + Gym."),
            "Sat": ("Long run OR hill (every 3rd wk)", "Endurance / quads", "Long run, or hill session on weeks 34/37."),
            "Sun": ("Long run Z2 (3:30 → 4:00)", "Endurance", "Long Z2 run — peak block."),
        }
        return table[wd]

    if 41 <= week <= 44:
        if week == 41:
            return ("Recovery week", "Post-marathon", "Walk, mobility, light gym. No quality sessions. 5–6h total max.")
        table = {
            "Mon": ("Easy 60 min", "Aerobic", "Easy 60 min Z2."),
            "Tue": ("SUB-THRESHOLD medium reps", "Threshold", "Sub-threshold session (5×1500m or 4×2000m)."),
            "Wed": ("Easy 60 min + CrossFit (no leg killers)", "Aerobic", "60 min easy + scaled CrossFit."),
            "Thu": ("HILL — Constantia simulation",
                    "Climbing-specific",
                    "Uphill 1 km reps at sub-threshold (3–5 reps). 5–8% treadmill grade or local hill."),
            "Fri": ("Easy 60 min + Gym (lighter)", "Aerobic + strength", "60 min easy + light gym."),
            "Sat": ("Long run (3:00 → 4:00)", "Endurance", "Long Z2 run."),
            "Sun": ("B2B medium-long (1:30 → 2:30)", "Back-to-back endurance", "Back-to-back long run — second consecutive day on tired legs."),
        }
        return table[wd]

    if 45 <= week <= 48:
        if week == 45:
            return ("Peak training week", "Peak", "See plan doc — peak block before taper.")
        if week == 46:
            return ("Taper week 1 + sauna", "Taper + heat", "Volume drops to ~7:30. Saturday 2:30 with 45 min at goal pace. Sauna 4× this week, 25–30 min at 80°C post-run.")
        if week == 47:
            return ("Travel taper", "Pre-race", "~5h total. Travel to Cape Town. Sauna 5× this week, 30–35 min sessions. Hydrate aggressively.")
        return ("TWO OCEANS ULTRA 56K — RACE DAY", "A-RACE", "Race Saturday. See plan doc for race strategy.")

    return ("(outside plan window)", "", "")


def prescription_for(d: date) -> Prescription:
    week = ((d - PLAN_START).days // 7) + 1
    weekday_idx = d.weekday()
    title, purpose, description = session_for(week, weekday_idx)
    return Prescription(
        plan_week=week,
        phase=phase_for(week),
        weekday=WEEKDAYS[weekday_idx],
        title=title,
        purpose=purpose,
        description=description,
        target_hours=WEEKLY_TOTAL.get(week, 0.0),
    )


if __name__ == "__main__":
    d = datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else date.today()
    p = prescription_for(d)
    print(f"Date: {d} ({p.weekday})")
    print(f"Plan week: {p.plan_week}")
    print(f"Phase: {p.phase}")
    print(f"Weekly target: {p.target_hours:.1f}h")
    print(f"Session: {p.title}")
    print(f"Purpose: {p.purpose}")
    print()
    print(p.description)
