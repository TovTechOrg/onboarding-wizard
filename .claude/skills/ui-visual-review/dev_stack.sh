#!/usr/bin/env bash
# Idempotent throwaway Postgres + dev-server bootstrap for ui-visual-review.
# This project refuses to boot without a reachable DATABASE_URL (main.py's
# own boot gate) but doesn't need real data for a screenshot pass, so this
# spins up a disposable local container instead of touching Supabase.
#
# Usage:
#   .claude/skills/ui-visual-review/dev_stack.sh start   # idempotent
#   .claude/skills/ui-visual-review/dev_stack.sh stop
set -euo pipefail

CONTAINER=onboarding-wizard-uivisual-pg
PG_PORT=55432
APP_PORT=8099
PIDFILE=/tmp/onboarding-wizard-uivisual-uvicorn.pid
LOGFILE=/tmp/onboarding-wizard-uivisual-uvicorn.log
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

start() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" \
    -e POSTGRES_PASSWORD=postgres \
    -p "${PG_PORT}:5432" \
    postgres:16-alpine >/dev/null

  export DATABASE_URL="postgresql://postgres:postgres@localhost:${PG_PORT}/postgres"

  for _ in $(seq 1 30); do
    if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
  fi

  (
    cd "$PROJECT_DIR"
    # --env-file supplies every other required setting (e.g.
    # ONBOARDING_SESSION_ENCRYPTION_KEY) from the real .env; the DATABASE_URL
    # already exported above takes precedence over --env-file's copy, so the
    # throwaway container is what actually gets used.
    DATABASE_URL="$DATABASE_URL" uv run --env-file .env uvicorn main:app --port "$APP_PORT" \
      >"$LOGFILE" 2>&1 &
    echo $! >"$PIDFILE"
  )

  for _ in $(seq 1 30); do
    if curl -sf "http://localhost:${APP_PORT}/" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  echo "DATABASE_URL=${DATABASE_URL}"
  echo "App running at http://localhost:${APP_PORT}/ (log: ${LOGFILE})"
}

stop() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
  fi
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
