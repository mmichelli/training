"""AI coach — reads the plan, dashboard, and recent activity notes, then asks
Claude for a focused coaching response. Writes the result to coach.md.

Uses the `claude` CLI in print mode so no separate API key is needed.

Usage:
    uv run python coach.py                  # weekly check-in
    uv run python coach.py "question..."    # ad-hoc question
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
PLAN = ROOT / "nsa-two-oceans-2027-plan.md"
PRINCIPLES = ROOT / "principles.md"
DASHBOARD = ROOT / "dashboard.md"
ACTIVITIES_DIR = ROOT / "activities"
DATA_DIR = ROOT / "data"
COACH_OUT = ROOT / "coach.md"

SYSTEM = """You are Mario's running coach for the Two Oceans Ultra 56K on 3 April 2027.

The training philosophy is the Norwegian Singles Approach (NSA). The *load-bearing*
document is `principles.md` — read it carefully. When making any judgement call
(continue, modify, down-week, refer out), ground your reasoning in those principles
and cite them by section number (e.g. "per principles §5 traffic-light rule" or
"§9 — the hard sessions show up as more reps, not faster reps").

The plan itself is in `nsa-two-oceans-2027-plan.md`. The plan is the WHAT,
the principles are the WHY — both authoritative.

The DASHBOARD SIGNALS section gives you the latest numbers (HRV, RHR, sleep,
stress, weight, ACWR). The activities section gives recent workout notes.

Constraints on every response:
- Be concise. Coaches don't lecture.
- Lead with the call (continue / modify / take a down-week / refer to physio).
- Cite the specific principle (§N) that justifies the call.
- If you recommend deviating from the plan, name the specific session(s) and the swap.
- When numbers matter, cite them from the dashboard section — do not invent numbers.
- If data is missing, say so plainly.
- Knee history: respect it. Caution over heroics every time (§7).
- Weight loss is part of the plan (§7) — don't flag gradual loss as a problem.
- Boring is the goal (§9). If Mario is itching to go harder, the principles win.
"""

WEEKLY_PROMPT = """It is {today}. Provide this week's coaching check-in.

Cover:
1. Volume verdict — are we on, under, or over plan? Magnitude.
2. Quality verdict — did the sub-threshold sessions stay controlled? Flag anything that looks like a Z4 drift.
3. Recovery signal — anything in the activity notes (knee, sleep, perceived effort) that warrants action.
4. The call for the coming week — continue, modify (specify), or down-week.
5. One sentence on what to watch for next week.

Keep it under 250 words. No preamble.
"""


def read_recent_activities(days: int = 21) -> str:
    if not ACTIVITIES_DIR.exists():
        return "(no activities yet)"
    cutoff = date.today() - timedelta(days=days)
    files = sorted(ACTIVITIES_DIR.glob("*.md"))
    chunks: list[str] = []
    for f in files:
        # filename starts with YYYY-MM-DD
        try:
            d = date.fromisoformat(f.name[:10])
        except ValueError:
            continue
        if d < cutoff:
            continue
        chunks.append(f"--- {f.name} ---\n{f.read_text()}")
    return "\n\n".join(chunks) if chunks else "(no activities in the last {0} days)".format(days)


def latest_signals() -> str:
    """Pull the most recent dashboard signals as plain text the coach can cite."""
    try:
        # Lazy import — coach can run without dashboard deps if data isn't there yet
        import dashboard_web as dw
        hrv = dw.load_hrv_summaries()
        rhr = dw.load_daily_summaries()
        sleep = dw.load_sleep_summaries()
        stress = dw.load_stress_summaries()
        weight = dw.load_weight()
        light, reasons = dw.readiness_verdict()
    except Exception as e:
        return f"(dashboard signals unavailable: {e})"

    out = [f"Today's traffic-light verdict: **{light.upper()}**"]
    for r in reasons:
        out.append(f"  - {r}")
    out.append("")

    if not hrv.empty:
        last = hrv.iloc[-1]
        out.append(f"HRV last night: {last['last_night_avg']:.0f} ms  ·  7-day avg: {last['weekly_avg']:.0f} ms  ·  status: {last['status']}")
        out.append(f"  Garmin's personalized balanced range: {last['baseline_balanced_low']:.0f}-{last['baseline_balanced_upper']:.0f} ms")

    if not rhr.empty:
        last = rhr.iloc[-1]
        out.append(f"Resting HR today: {last['rhr']:.0f} bpm  ·  7-day avg: {last['rhr_7d']:.0f} bpm")
        if len(rhr) >= 14:
            baseline = rhr.iloc[-14:-1]["rhr"].mean()
            out.append(f"  14-day baseline: {baseline:.1f} bpm  ·  delta today: {last['rhr'] - baseline:+.1f}")

    if not sleep.empty:
        last = sleep.iloc[-1]
        wk = sleep.tail(7)["total_h"].mean() if len(sleep) >= 3 else None
        out.append(f"Sleep last night: {last['total_h']:.1f}h  ·  7-day avg: {wk:.1f}h" if wk else f"Sleep last night: {last['total_h']:.1f}h")
        if pd.notna(last.get("avg_stress")):
            out.append(f"  Avg sleep stress: {last['avg_stress']:.0f}/100")

    if not stress.empty:
        last = stress.iloc[-1]
        out.append(f"Daytime stress: avg {last['avg_stress']:.0f} · peak {last['max_stress']:.0f} (0-100)")

    try:
        alc = dw.load_alcohol()
    except Exception:
        alc = pd.DataFrame()
    if not alc.empty:
        cutoff = pd.Timestamp(date.today()) - pd.Timedelta(days=14)
        recent = alc[alc["date"] >= cutoff]
        if not recent.empty:
            total = recent["units"].sum()
            drink_days = int((recent["units"] > 0).sum())
            out.append(f"Alcohol (last 14d): {total:.1f} units across {drink_days} drinking day(s)")
            insight = dw.alcohol_hrv_insight()
            if insight:
                out.append(f"  {insight}")

    if not weight.empty:
        last = weight.iloc[-1]
        to_goal = last["kg"] - 75.0
        out.append(f"Weight: {last['kg']:.1f} kg  ·  to 75 kg goal: {to_goal:+.1f} kg")
        if len(weight) >= 2:
            prior = weight.iloc[-2]
            out.append(f"  Last weigh-in delta: {last['kg'] - prior['kg']:+.2f} kg over {(last['date'] - prior['date']).days} days")

    return "\n".join(out)


def build_context() -> str:
    import pandas as _pd  # ensure available for latest_signals  (already imported but make explicit)
    parts = ["# PRINCIPLES (load-bearing — cite by section)\n"]
    parts.append(PRINCIPLES.read_text() if PRINCIPLES.exists() else "(principles missing)")
    parts.append("\n\n# PLAN\n")
    parts.append(PLAN.read_text() if PLAN.exists() else "(plan missing)")
    parts.append("\n\n# DASHBOARD SIGNALS (latest)\n")
    parts.append(latest_signals())
    parts.append("\n\n# RECENT ACTIVITIES (last 21 days)\n")
    parts.append(read_recent_activities(21))
    return "".join(parts)


# pandas needed by latest_signals
import pandas as pd  # noqa: E402


def ask_claude(question: str) -> str:
    context = build_context()
    full_prompt = f"{SYSTEM}\n\n# CONTEXT\n\n{context}\n\n# REQUEST\n\n{question}"
    result = subprocess.run(
        ["claude", "-p", full_prompt],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        sys.exit(f"claude CLI failed: {result.stderr}")
    return result.stdout.strip()


def main() -> None:
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        header = f"# Coach — Q&A · {date.today()}\n\n**Q:** {question}\n\n"
    else:
        question = WEEKLY_PROMPT.format(today=date.today().isoformat())
        header = f"# Coach — weekly check-in · {date.today()}\n\n"

    answer = ask_claude(question)
    COACH_OUT.write_text(header + answer + "\n")
    print(f"wrote {COACH_OUT}")
    print()
    print(answer)


if __name__ == "__main__":
    main()
