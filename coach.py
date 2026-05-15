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

You will receive several context blocks. Use all of them:

- **PRINCIPLES / PLAN** — the authoritative docs.
- **PLAN-WEEK STATUS** — current plan week, phase, hours target vs actual,
  down-week flag if applicable.
- **CALIBRATION STATUS** — whether HRmax has been measured and whether the
  knee MRI is filed. If HRmax is NOT measured, do not assert Z4 drift or
  cite specific HR-zone violations — note the calibration gap instead.
- **OPERATIONAL TASKS** — overdue and due-soon items. Address them in
  your response if blocking (e.g. trainer session overdue, gym membership
  not active, HRmax test due).
- **DASHBOARD SIGNALS** — latest HRV, RHR, sleep, stress, weight, alcohol.
- **PROTEIN** — last 7-day mean vs floor. With weight loss in flight,
  call this out explicitly when below floor (Type II preservation, §7).
- **LAST SUNDAY CHECK-IN** — Mario's own answers to the §6 questions.
- **PRIOR COACH CALL** — last week's "watch for next week" line, for
  continuity (was the prediction borne out? what's drifted?).
- **RECENT ACTIVITIES** — last 21 days of session notes.

Constraints on every response:
- Be concise. Coaches don't lecture.
- Lead with the call (continue / modify / take a down-week / refer to physio).
- Cite the specific principle (§N) that justifies the call.
- If you recommend deviating from the plan, name the specific session(s) and the swap.
- When numbers matter, cite them from the context blocks — do not invent numbers.
- If data is missing or calibration is incomplete, say so plainly.
- Knee history: respect it. Caution over heroics every time (§7).
- Weight loss is part of the plan (§7) — don't flag gradual loss as a problem.
  But DO flag if protein is under floor while in deficit (Type II loss risk).
- Boring is the goal (§9). If Mario is itching to go harder, the principles win.
- For continuity, reference the prior coach call's "watch for next week"
  line and say whether the prediction held.
"""

WEEKLY_PROMPT = """It is {today}. Provide this week's coaching check-in.

Cover, in this order:
1. **Continuity** — did last week's "watch for next week" prediction hold?
2. **Volume** — on, under, or over plan? Magnitude. Note if this is a down-week.
3. **Quality** — sub-threshold sessions controlled? Flag drift only if HRmax
   is measured (see CALIBRATION).
4. **Recovery** — readiness signals, knee notes, sleep, alcohol. Reference
   the latest Sunday check-in answers.
5. **Operational** — any overdue/blocking tasks that affect this week
   (trainer session, gym membership, HRmax test, etc.).
6. **Protein / weight** — protein vs floor, weight trend.
7. **The call** — continue / modify (specify sessions) / down-week.
8. **Watch for next week** — one specific signal in one sentence.

Keep it under 300 words. No preamble.
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


def plan_week_status() -> str:
    """Current plan week, phase, target hours vs actual."""
    try:
        import dashboard_web as dw
        from plan_lookup import phase_for, PLAN_START
        today = date.today()
        days_since_start = (today - PLAN_START).days
        if days_since_start < 0:
            return f"Plan starts {PLAN_START.isoformat()} — not yet underway."
        plan_week = days_since_start // 7 + 1
        phase = phase_for(plan_week)
        vol = dw.weekly_volume()
        if vol.empty:
            return f"Plan week {plan_week} · phase: {phase} · no volume data yet."
        last = vol.iloc[-1]
        actual = float(last["actual_h"])
        target = float(last["target_h"])
        pct = (actual / target * 100) if target else 0
        # Down-week heuristic: target_h notably lower than running max in last 8 wks
        recent_max = float(vol.tail(8)["target_h"].max())
        down = target < recent_max * 0.8 and plan_week > 1
        flag = "  ⚠ DOWN-WEEK (target intentionally low)" if down else ""
        return (
            f"Plan week {plan_week} · phase: {phase}{flag}\n"
            f"This week: target {target:.1f}h · done {actual:.1f}h ({pct:.0f}%)"
        )
    except Exception as e:
        return f"(plan-week status unavailable: {e})"


def calibration_status() -> str:
    """HRmax measured? Knee MRI filed? Reads tasks.yaml."""
    try:
        import tasks as _tasks
        ts = {t.id: t for t in _tasks.load()}
        lines = []
        hr = ts.get("hrmax-test")
        if hr and hr.done:
            lines.append(f"✓ HRmax measured (task done {hr.done_on}). Z4-drift flagging is reliable.")
        else:
            lines.append("✗ HRmax NOT measured yet. Do NOT assert Z4 drift or cite specific HR-zone violations.")
        mri = ts.get("knee-mri")
        if mri and mri.done:
            lines.append("✓ Knee MRI filed in references/.")
        else:
            lines.append("✗ Knee MRI not filed. Lean conservative on knee-loading recommendations.")
        return "\n".join(lines)
    except Exception as e:
        return f"(calibration status unavailable: {e})"


def operational_tasks() -> str:
    """Overdue + due ≤14d from tasks.yaml."""
    try:
        import tasks as _tasks
        today = date.today()
        open_ts = _tasks.open_tasks(today)
        overdue = [t for t in open_ts if t.urgency(today) == "overdue"]
        due_soon = [t for t in open_ts if t.urgency(today) in ("red", "amber")]
        if not overdue and not due_soon:
            return "(no overdue or due-soon tasks)"
        out: list[str] = []
        if overdue:
            out.append("OVERDUE:")
            for t in overdue:
                days = t.days_until(today) or 0
                out.append(f"  • {t.title} ({-days}d late) — {t.context[:120]}")
        if due_soon:
            out.append("DUE ≤14d:")
            for t in due_soon:
                d = t.days_until(today)
                label = "today" if d == 0 else f"in {d}d"
                out.append(f"  • {t.title} ({label}, {t.due}) — {t.context[:120]}")
        return "\n".join(out)
    except Exception as e:
        return f"(operational tasks unavailable: {e})"


def protein_status() -> str:
    """Last 7-day mean vs floor, with weight trend context."""
    try:
        import dashboard_web as dw
        df = dw.load_protein()
        floor_g, high_g, kg = dw.protein_target_floor()
        if df.empty:
            return f"No protein entries logged. Floor at {kg:.0f} kg = {floor_g:.0f} g/day."
        last7 = df.tail(7)
        avg = float(last7["grams"].mean())
        pct = (avg / floor_g * 100) if floor_g else 0
        flag = "⚠ UNDER FLOOR" if avg < floor_g else "✓ on/above floor"
        return (
            f"{flag} — 7-day mean {avg:.0f} g/day ({pct:.0f}% of floor) · "
            f"floor {floor_g:.0f} g · roof {high_g:.0f} g (at {kg:.1f} kg)"
        )
    except Exception as e:
        return f"(protein unavailable: {e})"


def last_checkin() -> str:
    """Most recent Sunday check-in record."""
    try:
        import checkin as _checkin
        rec = _checkin.latest()
        if not rec:
            return "(no Sunday check-in recorded yet)"
        lines = [f"Week ending {rec.get('week_ending', '?')}:"]
        for q, a in (rec.get("answers") or {}).items():
            lines.append(f"  • {q}: {a}")
        if rec.get("notes"):
            lines.append(f"  Notes: {rec['notes']}")
        return "\n".join(lines)
    except Exception as e:
        return f"(last check-in unavailable: {e})"


def prior_coach_call() -> str:
    """Last coach.md — the bottom 'watch for next week' section if extractable."""
    try:
        if not COACH_OUT.exists():
            return "(no prior coach call)"
        text = COACH_OUT.read_text()
        # Try to extract the "Watch for next week" line; fall back to last 400 chars
        for marker in ("Watch for next week", "**Watch for next week", "## Watch"):
            if marker in text:
                tail = text.split(marker, 1)[1][:500].strip()
                return f"Prior week's 'watch for next week':\n{tail}"
        return f"Prior coach call tail:\n{text[-500:].strip()}"
    except Exception as e:
        return f"(prior coach call unavailable: {e})"


def build_context() -> str:
    parts = ["# PRINCIPLES (load-bearing — cite by section)\n"]
    parts.append(PRINCIPLES.read_text() if PRINCIPLES.exists() else "(principles missing)")
    parts.append("\n\n# PLAN\n")
    parts.append(PLAN.read_text() if PLAN.exists() else "(plan missing)")
    parts.append("\n\n# PLAN-WEEK STATUS\n")
    parts.append(plan_week_status())
    parts.append("\n\n# CALIBRATION STATUS\n")
    parts.append(calibration_status())
    parts.append("\n\n# OPERATIONAL TASKS\n")
    parts.append(operational_tasks())
    parts.append("\n\n# DASHBOARD SIGNALS (latest)\n")
    parts.append(latest_signals())
    parts.append("\n\n# PROTEIN\n")
    parts.append(protein_status())
    parts.append("\n\n# LAST SUNDAY CHECK-IN\n")
    parts.append(last_checkin())
    parts.append("\n\n# PRIOR COACH CALL\n")
    parts.append(prior_coach_call())
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
