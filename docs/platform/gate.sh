#!/usr/bin/env bash
# The full sandbox verify gate, run by the lead before any tenant change. Prints one line
# per suite, then the fixture hash, live's PID and any write into live's tree.
cd "$(dirname "$0")/../.." || exit 2; unset $(env | sed -n 's/^\(JARVIS_[A-Z_]*\|HERMES_HOME\|TASKS_[A-Z_]*\|AUTH_BASE\|VERIFY_BASE\)=.*/\1/p'); export PYTHONDONTWRITEBYTECODE=1 VERIFY_APP_ROOT=$PWD VERIFY_SKILLS_ROOT=/root/jarvis-skills-platform; export VERIFY_OPERATOR_PASSPHRASE="$(sed -n 's/^APP_PASSPHRASE=//p' /opt/jarvis-verify-fixture/hermes/.env)"; PY=/opt/jarvis/venv/bin/python; SB=/tmp/claude-0/-root/tasks-sandbox
touch /tmp/gate.start
KH(){ (cd /opt/jarvis-verify-fixture && find . -type f -print0 | sort -z | xargs -0 sha256sum) | sha256sum | cut -c1-16; }; K0=$(KH)
bash docs/tasks/tasks_sandbox.sh stop >/dev/null 2>&1; bash docs/tasks/tasks_sandbox.sh start 2>&1 | tail -1
run(){ n="$1"; shift; out=$(timeout 900 "$@" 2>&1); rc=$?; echo "$n | $(echo "$out" | rg -o '(\d+/\d+ (passed|PASS).*|\d+ passed.*)$' | tail -1) | rc=$rc"; [ $rc -ne 0 ] && echo "$out" | rg 'FAIL|Error|not permitted' | head -4; }
run "verify_token_refresh    " $PY docs/auth/verify_token_refresh.py
run "verify_google_connection" env JARVIS_ACCOUNTS_DB=$SB/accounts.db JARVIS_THREADS_DB=$SB/threads.db VERIFY_APP_LOG=$SB/app.log $PY docs/auth/verify_google_connection.py
run "verify_creds_cutover    " env VERIFY_HTTP_ACCOUNTS_DB=$SB/accounts.db JARVIS_THREADS_DB=$SB/threads.db VERIFY_APP_LOG=$SB/app.log $PY docs/auth/verify_creds_cutover.py
run "PROMPT 1  tasks_api     " $PY docs/tasks/verify_tasks_api.py
run "PROMPT 2  tasks_chat    " $PY docs/tasks/verify_tasks_chat.py --stub
run "PROMPT 3  tasks_digest  " $PY docs/tasks/verify_tasks_digest.py
run "PROMPT 3b chat_honesty  " $PY docs/tasks/verify_chat_honesty.py
run "PROMPT 4  sweeps        " $PY docs/sweeps/verify_sweeps.py
run "PROMPT 5  meeting       " $PY $VERIFY_SKILLS_ROOT/meeting-intake/tests/verify_meeting_intake.py --stub-extract --stub
run "PROMPT 6  metrics       " $PY docs/metrics/verify_metrics.py
run "showingsmart            " $PY $VERIFY_SKILLS_ROOT/showingsmart-autoreply/tests/verify_showingsmart.py
run "outbox                  " $PY docs/operator-inject/verify_outbox.py
run "fullbody_fixture        " $PY docs/approvals/verify_fullbody_fixture.py
run "broker                  " $PY docs/broker/verify_broker.py
run "sentinel_gate           " python3 docs/sentinel/verify_sentinel_gate.py
run "sentinel_broker         " $PY docs/sentinel/verify_sentinel_broker.py
run "model_proxy             " $PY docs/model/verify_model_proxy.py
run "switches                " $PY docs/sentinel/verify_switches.py
run "postgres                " $PY docs/platform/verify_postgres.py
run "provision_isolation     " python3 docs/platform/verify_provision_isolation.py
run "agent_lock              " python3 docs/platform/verify_agent_lock.py
run "auth_email_column       " $PY docs/auth/verify_email_column.py
run "auth_google (sandbox)   " $PY docs/auth/verify_auth_google.py
run "no_live_targets         " $PY docs/platform/verify_no_live_targets.py
bash docs/tasks/tasks_sandbox.sh stop >/dev/null; K1=$(KH); echo "fixture (all files incl. sidecars) $K0 -> $K1 unchanged=$([ $K0 = $K1 ] && echo yes || echo NO)"; echo "/opt/testco exists: $([ -e /opt/testco ] && echo YES || echo no); live MainPID=$(systemctl show -p MainPID --value jarvis-app); /root/jarvis-app status_sha=$(git -C /root/jarvis-app status --porcelain | sha256sum | cut -c1-12)"
echo "live tree files written during the gate (outside .git and .venv): $(find /root/jarvis-app -path /root/jarvis-app/.git -prune -o -path /root/jarvis-app/.venv -prune -o -newer /tmp/gate.start -print | wc -l)"
