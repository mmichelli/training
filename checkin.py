"""Sunday check-in: storage, streak math, and AI evaluation.

Persists one JSON per ISO-week (the week ending Sunday) under data/checkins/.
Each submission is evaluated by Claude — grounded in principles.md and the
latest dashboard signals — and the evaluation is stored alongside the answers.

Two streaks are tracked:
  - `checkin_streak`: consecutive Sunday weeks where a check-in was submitted.
  - `green_streak`:   consecutive Sunday weeks where the check-in passed (3+/5).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
CHECKINS = ROOT / "data" / "checkins"
PRINCIPLES = ROOT / "principles.md"

# The five canonical questions (principles §6). Order matters — used as keys.
QUESTIONS = [
    ("sleep_7h",              "Slept 7+ hours per night on average?"),
    ("knee_ok",               "Knee equal to or better than last Sunday?"),
    ("long_run_room",         "Saturday's long run felt like I could have done more?"),
    ("both_gym_sessions",     "Hit both gym sessions?"),
    ("subthreshold_controlled","Sub-threshold sessions felt controlled — not crushing?"),
]


# ─── Date helpers ─────────────────────────────────────────────────────────

def sunday_of(d: date) -> date:
    """Sunday at or before d (week-ending Sunday)."""
    return d + timedelta(days=(6 - d.weekday())) if d.weekday() != 6 else d


def previous_sunday(s: date) -> date:
    return s - timedelta(days=7)


# ─── Storage ──────────────────────────────────────────────────────────────

def path_for(week_ending: date) -> Path:
    return CHECKINS / f"{week_ending.isoformat()}.json"


def load(week_ending: date) -> dict | None:
    p = path_for(week_ending)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save(record: dict) -> None:
    CHECKINS.mkdir(parents=True, exist_ok=True)
    p = path_for(date.fromisoformat(record["week_ending"]))
    p.write_text(json.dumps(record, indent=2))


def latest() -> dict | None:
    if not CHECKINS.exists():
        return None
    files = sorted(CHECKINS.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


# ─── Streak math ──────────────────────────────────────────────────────────

@dataclass
class Streaks:
    checkin_streak: int       # consecutive submitted weeks
    green_streak: int         # consecutive passing weeks (3+/5)
    total_checkins: int
    total_green: int
    last_submitted: date | None


def streaks(today: date | None = None) -> Streaks:
    """Walk backwards from today's most-recent already-passed Sunday."""
    today = today or date.today()
    # The Sunday whose check-in is "due" — today if Sunday, else last Sunday.
    cursor = sunday_of(today)
    if cursor > today:
        cursor = previous_sunday(cursor)

    checkin_streak = 0
    green_streak = 0
    total_checkins = 0
    total_green = 0
    last_submitted: date | None = None
    in_checkin_run = True
    in_green_run = True

    # Walk back collecting stats; stop running streaks when we hit a gap.
    scan = cursor
    for _ in range(520):  # ~10 years cap
        rec = load(scan)
        if rec:
            total_checkins += 1
            if last_submitted is None:
                last_submitted = scan
            yes_count = rec.get("yes_count", 0)
            passed = yes_count >= 3
            if passed:
                total_green += 1
            if in_checkin_run:
                checkin_streak += 1
            if in_green_run:
                if passed:
                    green_streak += 1
                else:
                    in_green_run = False
        else:
            in_checkin_run = False
            in_green_run = False
        scan = previous_sunday(scan)
        if not in_checkin_run and not in_green_run and total_checkins > 0:
            # Continue counting totals but no need to walk forever
            # (limit ourselves to 2 years of history for totals).
            if (cursor - scan).days > 730:
                break
    return Streaks(
        checkin_streak=checkin_streak,
        green_streak=green_streak,
        total_checkins=total_checkins,
        total_green=total_green,
        last_submitted=last_submitted,
    )


# ─── AI evaluation ────────────────────────────────────────────────────────

EVAL_SYSTEM = """You are Mario's running coach. He has just submitted his Sunday check-in.

Your task: give a tight 3–5 sentence evaluation, grounded in principles.md.

Rules:
- Lead with the call: "continue", "modify" (and name the modification), or "down-week".
- Cite the relevant principle by section number (e.g. "per §6").
- If the dashboard signals contradict the self-report, surface that gently.
- Don't lecture. No "great job" or "keep it up". This is a logbook, not a coach app.
- If 2 or fewer "yes", the rule is automatic: next week is a down-week (§6).
- If "knee" answered no, that's a yellow flag even if total yes-count is 3+.
- Reply in 80–140 words. No preamble.
"""


def evaluate(record: dict, signals_text: str) -> str:
    """Call Claude to evaluate the check-in. Returns a 3–5 sentence verdict."""
    answers_lines = []
    for key, q in QUESTIONS:
        v = record["answers"].get(key)
        answers_lines.append(f"- {q}  →  {'YES' if v else 'NO'}")
    body = (
        f"Week ending: {record['week_ending']}\n"
        f"Yes count: {record['yes_count']}/5\n"
        f"Auto-rule outcome: {record['result']}\n\n"
        "Self-report:\n" + "\n".join(answers_lines) +
        f"\n\nNotes from Mario:\n{record.get('notes','') or '(none)'}\n\n"
    )
    principles = PRINCIPLES.read_text() if PRINCIPLES.exists() else "(principles missing)"
    prompt = (
        f"{EVAL_SYSTEM}\n\n"
        f"# PRINCIPLES\n{principles}\n\n"
        f"# DASHBOARD SIGNALS (latest)\n{signals_text}\n\n"
        f"# CHECK-IN SUBMISSION\n{body}\n"
        f"Your evaluation:"
    )
    try:
        r = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode != 0:
            return f"(coach unavailable: {r.stderr[:200]})"
        return r.stdout.strip()
    except Exception as e:
        return f"(coach unavailable: {e})"


# ─── Submission flow ──────────────────────────────────────────────────────

def submit(week_ending: date, answers: dict[str, bool], notes: str, signals_text: str) -> dict:
    yes_count = sum(1 for k, _ in QUESTIONS if answers.get(k))
    result = "continue" if yes_count >= 3 else "down-week"
    record = {
        "week_ending": week_ending.isoformat(),
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "answers": {k: bool(answers.get(k)) for k, _ in QUESTIONS},
        "yes_count": yes_count,
        "result": result,
        "notes": (notes or "").strip(),
    }
    record["ai_verdict"] = evaluate(record, signals_text)
    save(record)
    return record


# ─── CLI for cron / testing ───────────────────────────────────────────────

if __name__ == "__main__":
    s = streaks()
    last = s.last_submitted.isoformat() if s.last_submitted else "(never)"
    print(f"check-in streak: {s.checkin_streak} weeks  ·  green streak: {s.green_streak} weeks")
    print(f"totals: {s.total_checkins} check-ins · {s.total_green} green · last: {last}")
