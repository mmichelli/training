#!/usr/bin/env bash
# Launch the training dashboard. Refreshes session, pulls recent data, starts
# the FastAPI server, and opens the browser when it's ready.
set -e
cd "$(dirname "$0")"

# Refresh cookies from browser store (no-op if browser isn't logged in)
uv run python refresh_session.py || true

# Pull last 7 days in the background — dashboard works on whatever's already there
( uv run python ingest.py --days 7 > /tmp/training-ingest.log 2>&1 & ) || true

PORT=${PORT:-8765}
URL="http://localhost:${PORT}"

# If something's already on the port, just open the browser
if curl -fsS "${URL}/" -o /dev/null 2>&1; then
  xdg-open "${URL}" >/dev/null 2>&1 &
  exec sleep 1
fi

# Wait-and-open in the background so we can exec uvicorn
(
  for _ in {1..30}; do
    if curl -fsS "${URL}/" -o /dev/null 2>&1; then
      xdg-open "${URL}" >/dev/null 2>&1
      break
    fi
    sleep 0.3
  done
) &

exec uv run uvicorn dashboard_web:app --host 127.0.0.1 --port "${PORT}"
