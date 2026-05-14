"""One-time Garmin login via a real browser to capture a session.

Garmin's mobile-API SSO endpoint is permanently rate-limited (March 2026 onwards
— see garth issue #217, python-garminconnect issue #344). The workaround used by
this codebase: open a real Chromium window, let you log in by hand, then save
the resulting session cookies. `sync.py` uses those cookies to call the same
JSON endpoints the Garmin Connect web app uses.

Run once after the cookies expire (≈ months — Garmin keeps web sessions long).

Usage:
    uv run python garmin_login.py
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

SESSION_PATH = Path(__file__).parent / ".garmin_session.json"
LOGIN_URL = "https://connect.garmin.com/signin/"
DASHBOARD_PATTERN = "**/modern/**"


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        print(f"Opening {LOGIN_URL} — log in manually in the browser window.")
        print("Solve any captcha or email verification Garmin throws at you.")
        page.goto(LOGIN_URL)
        print("Waiting for the dashboard (up to 5 min)…")
        page.wait_for_url(DASHBOARD_PATTERN, timeout=300_000)
        # Give the SPA a moment to finish setting cookies / local storage.
        page.wait_for_timeout(2000)

        cookies = context.cookies()
        user_agent = page.evaluate("() => navigator.userAgent")

        SESSION_PATH.write_text(json.dumps({"cookies": cookies, "user_agent": user_agent}, indent=2))
        print(f"Saved session → {SESSION_PATH}")
        print("You can close the browser. Run `make sync` to test.")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
