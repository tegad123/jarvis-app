#!/usr/bin/env bash
# The front door for the verify sandbox: http://*.front.test:8719, mock Google,
# its own ledger in /tmp/front-verify. The account "verify" is the tasks
# sandbox on :8711, started with the front-door settings:
#
#   VERIFY_OAUTH_FRONT=http://join.front.test:8719 VERIFY_TENANT_SLUG=verify VERIFY_SIGNIN=google \
#   VERIFY_PUBLIC_ORIGIN=http://verify.front.test:8719 bash docs/tasks/tasks_sandbox.sh start
#   bash docs/front/front_sandbox.sh start|stop
set -euo pipefail
APP="$(cd "$(dirname "$0")/../.." && pwd)"
DIR=/tmp/front-verify
PORT=8719
PYTHON=/opt/jarvis/venv/bin/python
export FRONT_DB="$DIR/front.db" FRONT_BASE_DOMAIN=front.test FRONT_JOIN_HOST=join.front.test FRONT_SCHEME=http \
  FRONT_URL_PORT=":$PORT" FRONT_OAUTH_MOCK=1 FRONT_PORT=$PORT

case "${1:-}" in
  start)
    if ss -Hltn | grep -q "127.0.0.1:$PORT "; then echo "REFUSING: :$PORT is already listening"; exit 1; fi
    rm -rf "$DIR"; mkdir -m 700 "$DIR"
    (umask 077; head -c 48 /dev/urandom | base64 | tr -d '\n' > "$DIR/secret")
    export FRONT_SECRET="$(cat "$DIR/secret")"
    cd "$APP"
    "$PYTHON" -m front.cli route verify 8711 --owner dana@testco.example.com
    nohup "$PYTHON" -m front.main > "$DIR/front.log" 2>&1 &
    echo $! > "$DIR/front.pid"
    for _ in $(seq 1 30); do
      sleep 0.3
      if curl -sf -H "Host: join.front.test" "http://127.0.0.1:$PORT/__front/health" >/dev/null; then
        echo "front sandbox on :$PORT"; exit 0; fi
    done
    echo "front did not start; see $DIR/front.log"; exit 1 ;;
  stop)
    if [ -f "$DIR/front.pid" ]; then kill "$(cat "$DIR/front.pid")" 2>/dev/null || true; rm -f "$DIR/front.pid"; fi
    for _ in $(seq 1 30); do ss -Hltn | grep -q "127.0.0.1:$PORT " || break; sleep 0.2; done
    echo "front sandbox stopped" ;;
  env)
    # For the verifier and the worker: the same ledger and secret.
    echo "FRONT_DB=$FRONT_DB FRONT_BASE_DOMAIN=$FRONT_BASE_DOMAIN FRONT_JOIN_HOST=$FRONT_JOIN_HOST FRONT_SCHEME=http FRONT_URL_PORT=:$PORT FRONT_OAUTH_MOCK=1" ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
