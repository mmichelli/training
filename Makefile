.PHONY: sync dashboard publish coach all daily

# Pull recent Garmin activities into activities/
sync:
	uv run python sync.py 30

# Regenerate dashboard.md from activities/
dashboard:
	uv run python dashboard.py

# Regenerate plan.ics and push to public repo for Google Calendar subscription
publish:
	uv run python publish_calendar.py

# Ask the AI coach for a weekly check-in (writes coach.md)
coach:
	uv run python coach.py

# Ad-hoc coach question:   make ask Q="should I skip Tuesday this week?"
ask:
	uv run python coach.py "$(Q)"

# Garmin Fenix structured workouts (requires auth working)
workouts-dry:
	uv run python workouts.py --dry-run

workouts:
	uv run python workouts.py

# Auto-fill the Notes section of activities that don't have notes yet
auto-notes:
	uv run python auto_notes.py

# Daily flow: pull activities, fill notes, regenerate dashboard, ask coach
daily: sync auto-notes dashboard coach

# Same as daily but tolerates sync failures (so a Garmin 429 doesn't kill the whole run)
daily-auto:
	-uv run python sync.py 7
	-uv run python auto_notes.py
	uv run python dashboard.py
	uv run python coach.py

# Show today's prescribed session
today:
	@uv run python plan_lookup.py
