"""Regenerate plan.ics and push to the public repo so Google Calendar can subscribe.

Re-running this any time the plan changes will, on the next Google Calendar
refresh (~24h), update all events on the subscribed calendar.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import calendar_export

ROOT = Path(__file__).parent
ICS_PATH = ROOT / "plan.ics"
SUBSCRIBE_URL = "https://raw.githubusercontent.com/mmichelli/training/main/plan.ics"


def main() -> int:
    ICS_PATH.write_text(calendar_export.build())
    print(f"regenerated {ICS_PATH} ({ICS_PATH.stat().st_size} bytes)")

    status = subprocess.run(["git", "status", "--porcelain", "plan.ics"], capture_output=True, text=True, cwd=ROOT)
    if not status.stdout.strip():
        print("plan.ics unchanged — nothing to publish")
        return 0

    for cmd in (
        ["git", "add", "plan.ics"],
        ["git", "commit", "-m", "update plan.ics"],
        ["git", "push"],
    ):
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"{' '.join(cmd)} failed:\n{result.stderr}", file=sys.stderr)
            return result.returncode

    print(f"published → {SUBSCRIBE_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
