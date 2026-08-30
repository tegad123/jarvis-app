#!/bin/bash
# Boot the TESTCO CLONE's app on its own port. Never touches the live service.
set -euo pipefail
SP=/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad
CL=$SP/testco-clone
PORT=8702
LOG=$CL/app-testco-seeded.log

case "${1:-}" in
start)
  cd $CL/jarvis-app
  HERMES_HOME=$CL/hermes \
  JARVIS_SKILLS_ROOT=$CL/skills/openclaw-imports \
  JARVIS_ACTIVITY_FEED=$CL/hermes/state/activity_feed.jsonl \
  JARVIS_THREADS_DB=$CL/hermes/state/jarvis_threads.db \
  JARVIS_LEADS_DB=$CL/hermes/state/jarvis_leads.db \
  JARVIS_ACCOUNTS_DB=$CL/hermes/state/jarvis_accounts.db \
  JARVIS_APPROVALS=$CL/hermes/state/approvals.jsonl \
  JARVIS_APPROVAL_DECISIONS=$CL/hermes/state/approvals_decisions.jsonl \
  JARVIS_ATTACHMENTS_DIR=$CL/jarvis-app/data/attachments \
  APP_PORT=$PORT \
  nohup /root/jarvis-app/.venv/bin/python -m app.main > "$LOG" 2>&1 &
  echo $! > $CL/app-seeded.pid
  for _ in $(seq 40); do
    curl -sf -m 1 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1 && { echo "testco app up on $PORT"; exit 0; }
    sleep 0.3
  done
  echo "FAILED to start"; tail -20 "$LOG"; exit 1 ;;
stop)
  # By recorded PID only — pkill -f has matched and killed the calling shell
  # on this box before.
  [ -f $CL/app-seeded.pid ] && kill "$(cat $CL/app-seeded.pid)" 2>/dev/null || true
  rm -f $CL/app-seeded.pid; echo "stopped" ;;
*) echo "usage: $0 start|stop"; exit 2 ;;
esac
