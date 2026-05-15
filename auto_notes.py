"""Auto-fill the Notes section of each activity file.

Claude reads the activity's frontmatter, classifies the session against the plan,
and writes a structured notes block. Idempotent — skips activities whose notes
have already been filled (anything other than the initial HTML-comment scaffold).

Usage:
    uv run python auto_notes.py            # process all unfilled activities
    uv run python auto_notes.py --force    # rewrite even filled notes
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from plan_lookup import prescription_for

ROOT = Path(__file__).parent
ACTIVITIES_DIR = ROOT / "activities"

SCAFFOLD_MARKERS = ("<!-- felt:", "<!-- knee:", "<!-- sub-threshold")


def _hrmax_calibrated() -> bool:
    """True if the hrmax-test task is marked done. Until then, max-HR-based
    Z4-drift flags are unreliable (no measured max) — suppress that bullet."""
    try:
        import tasks as _tasks
        return any(t.id == "hrmax-test" and t.done for t in _tasks.load())
    except Exception:
        return False


def _system_prompt() -> str:
    hr_bullet = (
        "- **HR discipline**: based on avg/max HR and the sub-threshold ceiling "
        "(top of Z3 ≈ 75-78% HRR, Z4 starts ~80% HRR), was effort controlled? "
        "Flag if max HR suggests Z4 drift."
        if _hrmax_calibrated() else
        "- **HR discipline**: HRmax has NOT been measured yet — Z4-drift flagging is "
        "suppressed. Report avg/max HR observed and note the calibration gap; do not "
        "assert Z4 drift without a measured max."
    )
    return (
        "You are Mario's running coach. Your task: read a single Garmin activity and write a "
        "concise structured note about it. Output Markdown bullets only, no preamble.\n\n"
        "Required bullets:\n"
        "- **Session type**: one of [easy Z2, sub-threshold quality, long run, hill, recovery, "
        "cross-training, race, other]\n"
        "- **Vs. plan**: did this match the prescribed session? (yes / no / partial — one sentence)\n"
        f"{hr_bullet}\n"
        "- **Notable**: any single thing worth flagging (very strong pace, unusually high HR, "
        'short duration, etc.) — one line. If nothing notable, write "nothing".\n'
        "- **Suggested label**: short tag like \"easy/Z2-controlled\" or \"quality/Z3-clean\" or "
        "\"long/aerobic\" — useful for grep.\n\n"
        "Be terse. No filler. If data is missing, say so."
    )


SYSTEM = _system_prompt()  # backwards-compat for any external import


def is_filled(text: str) -> bool:
    if "## Notes" not in text:
        return False
    notes = text.split("## Notes", 1)[1]
    return not any(m in notes for m in SCAFFOLD_MARKERS) and notes.strip() != ""


def ask_claude(activity_text: str, prescribed: str) -> str:
    # Resolve at call-time so the calibration flag reflects current task state.
    prompt = (
        f"{_system_prompt()}\n\n"
        f"# PRESCRIBED FOR THIS DATE\n{prescribed}\n\n"
        f"# ACTIVITY\n{activity_text}\n"
    )
    result = subprocess.run(
        ["claude", "-p", prompt], capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr}")
    return result.stdout.strip()


def process(force: bool = False) -> int:
    if not ACTIVITIES_DIR.exists():
        print("no activities/ directory yet")
        return 0
    n = 0
    for path in sorted(ACTIVITIES_DIR.glob("*.md")):
        text = path.read_text()
        if is_filled(text) and not force:
            continue
        try:
            d = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        p = prescription_for(d)
        prescribed = f"Plan week {p.plan_week} {p.weekday}: {p.title} (purpose: {p.purpose})"
        print(f"→ {path.name}")
        try:
            note = ask_claude(text, prescribed)
        except Exception as e:
            print(f"  failed: {e}")
            continue
        head, _, _ = text.partition("## Notes")
        path.write_text(f"{head}## Notes\n\n_Auto-filled {datetime.now():%Y-%m-%d %H:%M}._\n\n{note}\n")
        n += 1
    print(f"filled {n} activities")
    return n


if __name__ == "__main__":
    process(force="--force" in sys.argv)
