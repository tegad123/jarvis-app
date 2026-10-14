#!/bin/bash
# Stand up an ISOLATED jarvis-app, pointed at the verify fixture (testco's
# frozen config, /opt/jarvis-verify-fixture), for the tasks, auth and metrics
# suites. Google OAuth is mocked and the token key is a throwaway made per start.
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
FIXTURE="${VERIFY_FIXTURE_ROOT:-/opt/jarvis-verify-fixture}"
TESTCO_HOME="$FIXTURE/hermes"
TESTCO_SKILLS="$FIXTURE/skills/openclaw-imports"
[ -f "$TESTCO_SKILLS/_shared/identity.json" ] || { echo "REFUSING: verify fixture missing at $FIXTURE"; exit 1; }
# Code under test; config stays the fixture's.
APP="${VERIFY_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SKILLS_CODE="${VERIFY_SKILLS_ROOT:-$TESTCO_SKILLS}"
# The platform venv: the app needs psycopg, which live's venv must not have.
PYTHON="${VERIFY_PYTHON:-/opt/jarvis/venv/bin/python}"
PGDB="jarvis_verify_${PORT}"

case "${1:-}" in
start)
  # A leftover app on this port would answer the readiness ping for a new one
  # that failed to bind, and every check after that tests the wrong code.
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/ping"; then
    echo "REFUSING: something already answers on :$PORT"; exit 1
  fi
  rm -rf "$SB"; mkdir -p "$SB"
  # The app derives approvals, outbox and inject journals from HERMES_HOME, so
  # it gets a private copy: nothing the sandbox does can land in the fixture.
  cp -a "$TESTCO_HOME" "$SB/hermes"

  # sqlite3 .backup, not cp: WAL means the -wal file holds committed pages the
  # main file does not, and a plain copy can land mid-checkpoint.
  for db in threads accounts leads; do
    SRC="$TESTCO_HOME/state/jarvis_${db}.db"
    if [ -f "$SRC" ]; then
      python3 -c "
import sqlite3, sys
src = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
" "$SRC" "$SB/${db}.db"
    fi
  done
  # Tasks start EMPTY, not copied: the suite asserts on exact counts and a
  # carried-over row would make "one new event" mean two.
  : > "$SB/feed.jsonl"

  LIVE_FEED_BEFORE=$(grep -c . "$TESTCO_HOME/state/activity_feed.jsonl" 2>/dev/null || echo 0)

  (umask 077; "$PYTHON" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > "$SB/token_key")
  # A fresh platform database per start: approvals, grants, audit log, usage.
  PG_DSN=$("$PYTHON" "$APP/scripts/pg_tenant.py" verify-db create "$PGDB" | tail -1)
  case "$PG_DSN" in postgresql://*) echo "$PG_DSN" > "$SB/pg_dsn" ;; *) echo "REFUSING: verify database not created"; exit 1 ;; esac
  # Skills find the app only through paths.app_root; aim it at the code under test.
  python3 -c "
import json, sys
cfg = json.load(open(sys.argv[1])); cfg.setdefault('paths', {})['app_root'] = sys.argv[2]
open(sys.argv[3], 'w').write(json.dumps(cfg, indent=2) + '\n')
" "$TESTCO_SKILLS/_shared/runtime.json" "$APP" "$SB/runtime.json"
  # Env wins over the fixture's .env, so a caller-supplied passphrase is the one the app checks.
  if [ -n "${VERIFY_OPERATOR_PASSPHRASE:-}" ]; then export APP_PASSPHRASE="$VERIFY_OPERATOR_PASSPHRASE"; fi

  cd "$APP"
  APP_GOOGLE_OAUTH_MOCK=1 \
  APP_GOOGLE_TOKEN_KEY="$(cat "$SB/token_key")" \
  APP_PUBLIC_ORIGIN="http://127.0.0.1:$PORT" \
  APP_COOKIE_SECURE=0 \
  HERMES_HOME="$SB/hermes" \
  JARVIS_SKILLS_ROOT="$SKILLS_CODE" \
  JARVIS_APP_ROOT="$APP" \
  JARVIS_RUNTIME_CONFIG="$SB/runtime.json" \
  JARVIS_IDENTITY_CONFIG="$TESTCO_SKILLS/_shared/identity.json" \
  JARVIS_TASKS_DB="$SB/tasks.db" \
  JARVIS_THREADS_DB="$SB/threads.db" \
  JARVIS_ACCOUNTS_DB="$SB/accounts.db" \
  JARVIS_LEADS_DB="$SB/leads.db" \
  JARVIS_ACTIVITY_FEED="$SB/feed.jsonl" \
  JARVIS_TASKS_SOURCE="${TASKS_SOURCE:-store}" \
  JARVIS_ALLOW_WRITES="${VERIFY_ALLOW_WRITES:-1}" \
  JARVIS_GOOGLE_API_ENDPOINT="${VERIFY_GOOGLE_API_ENDPOINT:-}" \
  JARVIS_PG_DSN="${VERIFY_PG_DSN-$PG_DSN}" \
  JARVIS_SENTINEL_MODE="${VERIFY_SENTINEL_MODE:-enforce}" \
  JARVIS_SWITCH_BROKER="${VERIFY_SWITCH_BROKER:-on}" \
  JARVIS_SWITCH_SENTINEL="${VERIFY_SWITCH_SENTINEL:-on}" \
  JARVIS_SWITCH_MODEL_PROXY="${VERIFY_SWITCH_MODEL_PROXY:-on}" \
  JARVIS_MODEL_UPSTREAM="${VERIFY_MODEL_UPSTREAM:-}" \
  ANTHROPIC_API_KEY="${VERIFY_MODEL_KEY:-}" \
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
  if [ -f "$SB/app.pid" ]; then
    PID="$(cat "$SB/app.pid")"
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 40); do kill -0 "$PID" 2>/dev/null || break; sleep 0.25; done
    kill -9 "$PID" 2>/dev/null || true
  fi
  echo "tasks sandbox stopped"
  ;;

*)
  echo "usage: $0 start|stop"; exit 2;;
esac
