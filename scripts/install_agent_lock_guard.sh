#!/bin/sh
# Install the agent-lock systemctl guard: a root-owned copy of agent_lock.py and
# /usr/local/sbin/systemctl pointing at it (ahead of /usr/bin on root's PATH).
# Undo: rm /usr/local/sbin/systemctl
set -eu
SRC="$(dirname "$0")/agent_lock.py"
install -d -m 755 -o root -g root /usr/local/lib/jarvis
install -m 755 -o root -g root "$SRC" /usr/local/lib/jarvis/agent_lock.py
ln -sfn /usr/local/lib/jarvis/agent_lock.py /usr/local/sbin/systemctl
echo "installed: $(readlink /usr/local/sbin/systemctl) sha256 $(sha256sum /usr/local/lib/jarvis/agent_lock.py | cut -c1-16)"
