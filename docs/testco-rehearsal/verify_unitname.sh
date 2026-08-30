#!/bin/bash
# Triggered assert for the unit-name fix in hermes_cli/gateway.py.
#
# Two THROWAWAY homes, so neither the live tenant nor testco is disturbed —
# and both real units are hash-checked at the end to prove that claim rather
# than assert it.
cd /usr/local/lib/hermes-agent
PY=./venv/bin/python
A=/opt/unittest-alpha/hermes
B=/opt/unittest-beta/hermes
UDIR=/root/.config/systemd/user
ok=0; fail=0
ck() { if [ "$2" = "1" ]; then echo "  PASS  $1${3:+ — $3}"; ok=$((ok+1)); else echo "  FAIL  $1${3:+ — $3}"; fail=$((fail+1)); fi; }
name_for() { HERMES_HOME=$1 $PY -c "import sys;sys.path.insert(0,'.');from hermes_cli.gateway import get_service_name;print(get_service_name())" 2>/dev/null | tail -1; }

read LIVE_BASE TESTCO_BASE < /root/.unit-baseline

echo "── 1. unit NAMES derive from the home ─────────────────────────"
NA=$(name_for $A); NB=$(name_for $B); ND=$(name_for /root/.hermes)
ck "alpha and beta derive DIFFERENT names" "$([ "$NA" != "$NB" ] && echo 1 || echo 0)" "$NA vs $NB"
ck "neither collides with the default name" "$([ "$NA" != hermes-gateway ] && [ "$NB" != hermes-gateway ] && echo 1 || echo 0)"
ck "the DEFAULT home keeps the legacy name (backward compatible)" "$([ "$ND" = hermes-gateway ] && echo 1 || echo 0)" "$ND"
ck "the hash is deterministic across calls" "$([ "$NA" = "$(name_for $A)" ] && echo 1 || echo 0)"

echo
echo "── 2. START A, THEN B — via the CLI's own install path ─────────"
for H in $A $B; do
  HERMES_HOME=$H $PY -c "
import sys; sys.path.insert(0,'.')
from hermes_cli.gateway import systemd_install
systemd_install(force=True, system=False, enable_on_startup=True, non_interactive=True)" >/dev/null 2>&1
done
UA=$UDIR/$NA.service; UB=$UDIR/$NB.service
ck "A installed its own unit" "$([ -f "$UA" ] && echo 1 || echo 0)" "$(basename $UA)"
ck "B installed its own unit" "$([ -f "$UB" ] && echo 1 || echo 0)" "$(basename $UB)"
ck "A's unit is pinned to A's home" "$(grep -q "HERMES_HOME=$A" "$UA" 2>/dev/null && echo 1 || echo 0)"
ck "B's unit is pinned to B's home" "$(grep -q "HERMES_HOME=$B" "$UB" 2>/dev/null && echo 1 || echo 0)"
ck "B did NOT overwrite A's unit"   "$(grep -q "HERMES_HOME=$A" "$UA" 2>/dev/null && echo 1 || echo 0)"
systemctl --user start "$NA.service" "$NB.service" 2>/dev/null; sleep 12

echo
echo "── 3. REFUSAL: rewriting a unit that holds a different home ────"
cp "$UB" /tmp/ub.bak
cp "$UA" "$UB"        # stage the pre-fix collision: B's file now carries A's home
OUT=$(HERMES_HOME=$B $PY -c "
import sys; sys.path.insert(0,'.')
from hermes_cli.gateway import systemd_install
systemd_install(force=True, system=False, enable_on_startup=False, non_interactive=True)" 2>&1)
echo "$OUT" | grep -q "Refusing to rewrite" && R=1 || R=0
ck "it REFUSED the rewrite" "$R"
{ echo "$OUT" | grep -q "$A" && echo "$OUT" | grep -q "$B"; } && N=1 || N=0
ck "the refusal names BOTH homes" "$N"
ck "the file on disk was left untouched" "$(grep -q "HERMES_HOME=$A" "$UB" && echo 1 || echo 0)"
echo "$OUT" | sed 's/^/        │ /'
cp /tmp/ub.bak "$UB"; rm -f /tmp/ub.bak
systemctl --user daemon-reload

echo
echo "── 4. REBOOT-SIM: daemon-reload + restart both ─────────────────"
systemctl --user daemon-reload
systemctl --user restart "$NA.service" "$NB.service" 2>/dev/null
sleep 14
for pair in "$NA|$A|A" "$NB|$B|B"; do
  IFS='|' read -r N H L <<< "$pair"
  PID=$(systemctl --user show "$N.service" -p MainPID --value)
  RH=$([ "$PID" != 0 ] && tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep ^HERMES_HOME= | cut -d= -f2-)
  ck "$L came back under ITS OWN unit ($N)" "$([ "$PID" != 0 ] && echo 1 || echo 0)" "pid=$PID"
  ck "$L is running ITS OWN home"           "$([ "$RH" = "$H" ] && echo 1 || echo 0)" "$RH"
  ck "$L is NOT running the other's home"   "$([ "$RH" != "$([ "$L" = A ] && echo $B || echo $A)" ] && echo 1 || echo 0)"
done

echo
echo "── 5. THE TWO REAL TENANTS ARE UNTOUCHED ───────────────────────"
LNOW=$(sha256sum $UDIR/hermes-gateway.service | cut -c1-16)
TNOW=$(sha256sum /etc/systemd/system/testco-gateway.service | cut -c1-16)
ck "live gateway unit byte-identical to baseline" "$([ "$LNOW" = "$LIVE_BASE" ] && echo 1 || echo 0)" "$LIVE_BASE -> $LNOW"
ck "testco gateway unit byte-identical to baseline" "$([ "$TNOW" = "$TESTCO_BASE" ] && echo 1 || echo 0)" "$TESTCO_BASE -> $TNOW"
LP=$(systemctl --user show hermes-gateway -p MainPID --value)
LH=$(tr '\0' '\n' < /proc/$LP/environ 2>/dev/null | grep ^HERMES_HOME= | cut -d= -f2-)
ck "the live gateway still runs its own home" "$([ "$LH" = /root/.hermes ] && echo 1 || echo 0)" "$LH"
TP=$(systemctl show testco-gateway -p MainPID --value)
TH=$(tr '\0' '\n' < /proc/$TP/environ 2>/dev/null | grep ^HERMES_HOME= | cut -d= -f2-)
ck "testco still runs its own home" "$([ "$TH" = /opt/testco/hermes ] && echo 1 || echo 0)" "$TH"
ck "no throwaway unit hijacked a real name" \
   "$([ "$LH" != "$A" ] && [ "$LH" != "$B" ] && [ "$TH" != "$A" ] && [ "$TH" != "$B" ] && echo 1 || echo 0)"

echo
echo "$ok passed, $fail failed"
[ "$fail" = "0" ]
