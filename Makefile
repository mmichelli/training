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

# Daily flow: pull activities, regenerate dashboard, ask coach
daily: sync dashboard coach

# Show today's prescribed session
today:
	@uv run python plan_lookup.py
