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
DASHBOARD = ROOT / "dashboard.md"
ACTIVITIES_DIR = ROOT / "activities"
COACH_OUT = ROOT / "coach.md"

SYSTEM = """You are Mario's running coach for the Two Oceans Ultra 56K on 3 April 2027.

The training philosophy is the Norwegian Singles Approach (NSA): sub-threshold quality
3× per week, never crossing into Z4. The full plan is in nsa-two-oceans-2027-plan.md —
treat it as authoritative. Your job is to keep Mario honest to the plan and adjust
when life or the body says so.

Constraints on every response:
- Be concise. Coaches don't lecture.
- Lead with the call (continue / modify / take a down-week / see a physio).
- If you recommend deviating from the plan, name the specific session(s) and the swap.
- When numbers matter, cite them from the dashboard.
- If data is missing, say so plainly — do not invent numbers.
- Knee history: respect it. Recommend caution over heroics every time.
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


def build_context() -> str:
    parts = ["# PLAN\n", PLAN.read_text() if PLAN.exists() else "(plan missing)"]
    parts.append("\n\n# DASHBOARD\n")
    parts.append(DASHBOARD.read_text() if DASHBOARD.exists() else "(dashboard missing — run dashboard.py)")
    parts.append("\n\n# RECENT ACTIVITIES (last 21 days)\n")
    parts.append(read_recent_activities(21))
    return "".join(parts)


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
