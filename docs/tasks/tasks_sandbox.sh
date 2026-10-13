#!/bin/bash
# Stand up an ISOLATED jarvis-app, pointed at TESTCO's config, for the tasks suites.
#
# WHY THIS EXISTS. The mutation harness removes permission guards on purpose.
# Every negative probe ("a member must NOT be able to archive this") then
# becomes a REAL WRITE against whatever store the app is pointed at. The W3
# sandbox exists because the first run of that suite renamed a live channel to
# "hijacked". Same discipline here.
#
# WHY TESTCO'S CONFIG AND NOT 34dev's. The tasks feature only exists for
# app-native tenants, and 34dev is sheet-backed: pointed there, every task
# assertion would run against the legacy branch and the suite would pass by
# testing nothing. The identity registry has to be testco's too, or `dana`
# is not a person and every assignee is rejected.
#
# Copies of every store, a private feed, a private port. Nothing here can
# reach a live database, and no live service is restarted.
set -euo pipefail

SB="${TASKS_SANDBOX:-/tmp/claude-0/-root/tasks-sandbox}"
PORT="${TASKS_PORT:-8711}"
TESTCO_HOME=/opt/testco/hermes
TESTCO_SKILLS=/opt/testco/skills/openclaw-imports
# Code under test; config stays testco's.
APP="${VERIFY_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SKILLS_CODE="${VERIFY_SKILLS_ROOT:-$TESTCO_SKILLS}"
PYTHON="${VERIFY_PYTHON:-/root/jarvis-app/.venv/bin/python}"

case "${1:-}" in
start)
  rm -rf "$SB"; mkdir -p "$SB"

  # sqlite3 .backup, not cp: WAL means the -wal file holds committed pages the
  # main file does not, and a plain copy can land mid-checkpoint.
  for db in threads accounts leads; do
    SRC="$TESTCO_HOME/state/jarvis_${db}.db"
    if [ -f "$SRC" ]; then
      python3 -c "
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
" "$SRC" "$SB/${db}.db"
    fi
  done
  # Tasks start EMPTY, not copied: the suite asserts on exact counts and a
  # carried-over row would make "one new event" mean two.
  : > "$SB/feed.jsonl"

  LIVE_FEED_BEFORE=$(grep -c . "$TESTCO_HOME/state/activity_feed.jsonl" 2>/dev/null || echo 0)

  cd "$APP"
  HERMES_HOME="$TESTCO_HOME" \
  JARVIS_SKILLS_ROOT="$SKILLS_CODE" \
  JARVIS_APP_ROOT="$APP" \
  JARVIS_RUNTIME_CONFIG="$TESTCO_SKILLS/_shared/runtime.json" \
  JARVIS_IDENTITY_CONFIG="$TESTCO_SKILLS/_shared/identity.json" \
  JARVIS_TASKS_DB="$SB/tasks.db" \
  JARVIS_THREADS_DB="$SB/threads.db" \
  JARVIS_ACCOUNTS_DB="$SB/accounts.db" \
  JARVIS_LEADS_DB="$SB/leads.db" \
  JARVIS_ACTIVITY_FEED="$SB/feed.jsonl" \
  JARVIS_TASKS_SOURCE="${TASKS_SOURCE:-store}" \
  JARVIS_ALLOW_WRITES=1 \
  APP_PORT="$PORT" \
  APP_HOST=127.0.0.1 \
  nohup "$PYTHON" -m app.main > "$SB/app.log" 2>&1 &
  echo $! > "$SB/app.pid"

  for _ in $(seq 1 40); do
    sleep 0.5
    if curl -sf "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then break; fi
  done
  if ! curl -sf "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then
    echo "sandbox did not come up; log:"; tail -20 "$SB/app.log"; exit 1
  fi

  # Prove it stayed inside. A sandbox that quietly writes to the live feed
  # looks exactly like one that does not, until someone counts.
  LIVE_FEED_AFTER=$(grep -c . "$TESTCO_HOME/state/activity_feed.jsonl" 2>/dev/null || echo 0)
  if [ "$LIVE_FEED_BEFORE" != "$LIVE_FEED_AFTER" ]; then
    echo "REFUSING: the tenant's live feed grew during sandbox start ($LIVE_FEED_BEFORE -> $LIVE_FEED_AFTER)"
    exit 1
  fi
  echo "tasks sandbox up on :$PORT  (box=$SB, source=${TASKS_SOURCE:-store})"
  ;;

stop)
  if [ -f "$SB/app.pid" ]; then kill "$(cat "$SB/app.pid")" 2>/dev/null || true; fi
  sleep 0.5
  echo "tasks sandbox stopped"
  ;;

*)
  echo "usage: $0 start|stop"; exit 2;;
esac
