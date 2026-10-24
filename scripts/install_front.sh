#!/usr/bin/env bash
# Install or update the front door (jarvis-front, 127.0.0.1:8790) and the
# sign-up worker (jarvis-signup, root) from a commit of this repo.
#   python3 scripts/agent_lock.py run --who lead --why "front door" -- bash scripts/install_front.sh <sha>
# The secret is generated once and never printed. The Google client is handed
# to the service by LoadCredential, never copied.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "REFUSED: run as root"; exit 2; }
SHA="${1:?usage: install_front.sh <sha>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/jarvis-front/app
STATE=/var/lib/jarvis-front

id jarvis-front >/dev/null 2>&1 || useradd --system --no-create-home --home-dir "$STATE" --shell /usr/sbin/nologin jarvis-front

install -d -m 0755 -o root -g root /opt/jarvis-front
rm -rf "$APP.new"; install -d -m 0755 "$APP.new"
git -C "$REPO" archive "$SHA" front | tar -x -C "$APP.new"
echo "$SHA" > "$APP.new/REVISION"
chown -R root:root "$APP.new"; chmod -R go-w "$APP.new"
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"; mv "$APP.new" "$APP"

install -d -m 2770 -o jarvis-front -g jarvis-front "$STATE"
# The front door owns front.db; SQLite gives root's WAL side files the db file's owner.
systemctl stop jarvis-signup.service 2>/dev/null || true
for f in "$STATE"/front.db "$STATE"/front.db-wal "$STATE"/front.db-shm; do
  [ -e "$f" ] && chown jarvis-front:jarvis-front "$f" && chmod 660 "$f"
done
(cd "$APP" && runuser -u jarvis-front -- env FRONT_DB="$STATE/front.db" /opt/jarvis/venv/bin/python -c \
  "from front import store; store.connect().close()")

if [ ! -f /etc/jarvis/front.env ]; then
  (umask 077; printf 'FRONT_SECRET=%s\n' "$(head -c 48 /dev/urandom | base64 | tr -d '\n')" > /etc/jarvis/front.env)
fi
chown root:root /etc/jarvis/front.env; chmod 600 /etc/jarvis/front.env
[ -f /etc/jarvis/oauth_client.json ] || { echo "REFUSED: /etc/jarvis/oauth_client.json missing"; exit 2; }

install -m 0644 "$REPO/scripts/templates/jarvis-front.service" /etc/systemd/system/jarvis-front.service
install -m 0644 "$REPO/scripts/templates/jarvis-signup.service" /etc/systemd/system/jarvis-signup.service
systemctl daemon-reload
systemctl enable --now jarvis-front.service jarvis-signup.service
systemctl restart jarvis-front.service jarvis-signup.service

for _ in $(seq 1 30); do
  sleep 0.5
  if curl -sf -H "Host: join.yamin.io" http://127.0.0.1:8790/__front/health >/dev/null; then
    echo "jarvis-front up on 127.0.0.1:8790 at $SHA"; exit 0
  fi
done
echo "jarvis-front did not answer; journalctl -u jarvis-front"; exit 1
