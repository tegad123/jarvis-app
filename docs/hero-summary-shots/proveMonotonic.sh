#!/bin/bash
# Adding an event must make the count RISE. Under the old windowed hero it could
# FALL, because a new event pushed an older one out of the 50-event page.
set -e
SP=/tmp/claude-0/-root/84fc1bc9-4f40-4eb5-bd70-60f6beaacf1a/scratchpad
FEED=/root/.hermes/state/activity_feed.jsonl
COOKIE=$(mktemp)
PASS=$(grep -oP '(?<=^APP_PASSPHRASE=).*' /root/.hermes/.env | tr -d '"'"'"'')
BASE=$(cat $SP/tunnel.url)

summary_count () {
  curl -s -b $COOKIE "$BASE/api/feed/summary?tz=UTC" | python3 -c "
import sys,json; print(json.load(sys.stdin)['counts'].get('task.created', 0))"
}
window_count () {
  curl -s -b $COOKIE "$BASE/api/feed?limit=50" | python3 -c "
import sys,json
evs=json.load(sys.stdin)['events']
print(sum(1 for e in evs if e['kind']=='task.created'))"
}

curl -s -c $COOKIE -X POST "$BASE/api/login" -H 'Content-Type: application/json' \
  -d "{\"passphrase\": \"$PASS\"}" > /dev/null

cp $FEED $SP/feed.MONOTONIC_CHECK.jsonl
BEFORE_S=$(summary_count); BEFORE_W=$(window_count)
echo "  before:  hero(server, whole day)=$BEFORE_S   old windowed count=$BEFORE_W"

JARVIS_DEMO_SEED=1 python3 - <<'PY' > /dev/null 2>&1
import sys
sys.path.insert(0, '/root/.hermes/skills/openclaw-imports/_shared')
import notify
notify.notify('tasks', 'monotonicity probe', kind='task.created', skill='task-manager',
              headline='Created a task: monotonicity probe', detail='probe')
PY

AFTER_S=$(summary_count); AFTER_W=$(window_count)
echo "  after +1 task.created:"
echo "    hero (server, whole day) : $BEFORE_S -> $AFTER_S"
echo "    old windowed behaviour   : $BEFORE_W -> $AFTER_W"

# Restore: the probe is the last line, appended by us.
head -n $(wc -l < $SP/feed.MONOTONIC_CHECK.jsonl) $FEED > $SP/.trim && mv $SP/.trim $FEED
cmp -s $SP/feed.MONOTONIC_CHECK.jsonl $FEED && echo "    feed restored: BYTE-IDENTICAL" || echo "    RESTORE FAILED"
rm -f $COOKIE

if [ "$AFTER_S" -eq $((BEFORE_S + 1)) ]; then
  echo "  PASS  the hero count ROSE by exactly 1"
else
  echo "  FAIL  expected $((BEFORE_S + 1)), got $AFTER_S"; exit 1
fi
