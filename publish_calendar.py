"""Regenerate plan.ics and publish to the gist Google Calendar subscribes to.

Run any time the plan changes. Google Calendar refreshes subscribed URLs roughly
every 24 hours on its own schedule — you can force a refresh by unsubscribing
and re-subscribing if you need it sooner.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import calendar_export

GIST_ID = "6ec7c4771ee58d0870b05a925bb54f43"
ICS_PATH = Path(__file__).parent / "plan.ics"
SUBSCRIBE_URL = f"https://gist.githubusercontent.com/mmichelli/{GIST_ID}/raw/plan.ics"


def main() -> int:
    ICS_PATH.write_text(calendar_export.build())
    print(f"regenerated {ICS_PATH} ({ICS_PATH.stat().st_size} bytes)")
    result = subprocess.run(
        ["gh", "gist", "edit", GIST_ID, str(ICS_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"gist edit failed: {result.stderr}", file=sys.stderr)
        return result.returncode
    print(f"pushed to {SUBSCRIBE_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
