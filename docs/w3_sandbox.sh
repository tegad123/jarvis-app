#!/bin/bash
echo "REFUSING: this script works on live (/root/jarvis-app or /root/.hermes); port it to the platform tree (VERIFY_APP_ROOT, VERIFY_SKILLS_ROOT) before running it" >&2; exit 2
# Stand up an ISOLATED jarvis-app for the W3 scoping suite.
#
# WHY THIS EXISTS. The mutation test removes guards on purpose. Every negative
# probe in the suite ("a member must NOT be able to rename this channel") then
# becomes a REAL WRITE against whatever store the app is pointed at. The first
# run of this suite renamed a live channel to "hijacked" and archived it.
#
# So: copies of every store, a private feed file, a private port. Nothing here
# can reach the live databases, and the live service is never restarted.
set -euo pipefail
SP=/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad
SB=$SP/w3-sandbox
PORT=8701

case "${1:-}" in
start)
  rm -rf "$SB"; mkdir -p "$SB"
  # sqlite3 .backup, not cp: WAL means the -wal file holds committed pages the
  # main file does not, and a plain copy can land mid-checkpoint.
  # sqlite3(1) is not installed on this box; python's sqlite3 has the same
  # backup API and is what the app itself uses.
  for db in threads leads accounts; do
    python3 -c "
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
" "/root/.hermes/state/jarvis_${db}.db" "$SB/${db}.db"
  done
  cp /root/.hermes/state/activity_feed.jsonl "$SB/feed.jsonl"

  # THE SANDBOX HAS NO OUTBOUND NETWORK, so the sheet-backed lead reader 503s
  # here no matter what. Force the app-native branch and seed it, otherwise the
  # leads assertions test nothing and the leads mutation lands in dead code —
  # which is exactly how the first version of this reported a kill it had not
  # earned. The SHEET branch is scope-verified separately, read-only, against
  # the live service (see the W3 notes).
  # BOTH env vars, not just the database. leads_store.create() emits a feed
  # event, so isolating the DB without isolating JARVIS_ACTIVITY_FEED writes
  # this sandbox's fake leads into the CUSTOMER'S REAL FEED. That is the third
  # time that class of bug has bitten on this box, hence the assertion below.
  JARVIS_LEADS_DB="$SB/leads.db" \
  JARVIS_ACTIVITY_FEED="$SB/feed.jsonl" \
  python3 - <<'PYSEED'
import os, sys
sys.path.insert(0, "/root/jarvis-app")
from app import leads_store
leads_store.init()
for row in leads_store.query():
    leads_store.set_archived(row["lead_id"], True)
SEED = [("Ada Whitfield","martin"), ("Ben Okafor","martin"), ("Cleo Marsh","spencer"),
        ("Dev Raman","spencer"), ("Esi Boateng","tega"), ("Unassigned Walk-in", None)]
for name, owner in SEED:
    leads_store.create(name=name, owner_person=owner, status="New", temperature="Warm")
print(f"  seeded {len(SEED)} sandbox leads")
PYSEED

  # Prove it stayed inside. A seeder that quietly writes to the live feed looks
  # exactly like one that does not, until someone counts.
  LIVE=$(grep -c . /root/.hermes/state/activity_feed.jsonl)
  if [ "$LIVE" != "82" ]; then
    echo "ABORT: live feed is $LIVE, expected 82 — the sandbox leaked into it"
    exit 1
  fi
  cd /root/jarvis-app
  JARVIS_THREADS_DB="$SB/threads.db" \
  JARVIS_LEADS_DB="$SB/leads.db" \
  JARVIS_ACCOUNTS_DB="$SB/accounts.db" \
  JARVIS_ACTIVITY_FEED="$SB/feed.jsonl" \
  JARVIS_LEADS_SOURCE=store \
  APP_PORT=$PORT \
  nohup .venv/bin/python -m app.main > "$SB/server.log" 2>&1 &
  echo $! > "$SB/pid"
  for _ in $(seq 40); do
    curl -sf -m 1 "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1 && { echo "sandbox up on $PORT"; exit 0; }
    sleep 0.3
  done
  echo "sandbox FAILED to start"; tail -20 "$SB/server.log"; exit 1
  ;;
stop)
  # Kill by recorded PID only. `pkill -f` on this box has previously matched
  # and killed the calling shell.
  [ -f "$SB/pid" ] && kill "$(cat "$SB/pid")" 2>/dev/null || true
  rm -f "$SB/pid"
  echo "sandbox stopped"
  ;;
*) echo "usage: $0 start|stop"; exit 2 ;;
esac
