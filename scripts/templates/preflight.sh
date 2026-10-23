#!/bin/bash
# Tenant preflight — ExecStartPre. Exit non-zero and systemd does not start.
# Placeholders filled by scripts/provision_tenant.py.
set -uo pipefail

SLUG="{{SLUG}}"
OPT="{{OPT}}"
POSTURE="{{POSTURE}}"   # captured | blocked | live
LIVE_WRITES="{{LIVE_WRITES}}"   # approved: the manifest's human yes to real writes, app service only
UNIT_ROLE="${1:-}"              # "app" from the app unit; empty for the gateway and timers
fail() { echo "PREFLIGHT REFUSED: $*" >&2; exit 1; }

echo "PREFLIGHT: slug=$SLUG posture=$POSTURE HERMES_HOME=${HERMES_HOME:-UNSET}"

# ── HERMES_HOME: set, and inside /opt/{slug}. Missing or live-shaped is a
# refused boot, not a default. HOME=/opt/{slug} is not a substitute.
if [ -z "${HERMES_HOME:-}" ]; then
  fail "HERMES_HOME is unset — refusing to start (never fall back to \$HOME/.hermes)"
fi
resolved="$(realpath -m "$HERMES_HOME")"
case "$resolved" in
  /root/.hermes|/root/.hermes/*)
    fail "HERMES_HOME is live-shaped ($HERMES_HOME → $resolved)"
    ;;
  "$OPT"|"$OPT"/*) ;;
  *)
    fail "HERMES_HOME is not under $OPT: $HERMES_HOME → $resolved"
    ;;
esac

SKILLS="${JARVIS_SKILLS_ROOT:-$OPT/skills/openclaw-imports}"
RUNTIME="${JARVIS_RUNTIME_CONFIG:-$SKILLS/_shared/runtime.json}"
[ -f "$RUNTIME" ] || fail "no runtime config at $RUNTIME"

# ── prior-tenant terms. Pattern is canary-proved before use.
FORBIDDEN='34dev|cqhouston|spencerhuck'
CANARY='a 34dev b cqhouston c spencerhuck d'
if ! echo "$CANARY" | grep -qE "$FORBIDDEN"; then
  fail "the forbidden-term pattern matches nothing — it would pass every check. Pattern: $FORBIDDEN"
fi
if echo "this line is clean" | grep -qE "$FORBIDDEN"; then
  fail "the forbidden-term pattern matches clean text — it would fail every check."
fi

# Production inverts the scratch check: leftover testco / FIXTURE- / .example
# in a real tenant's reachable config is contamination.
if [ "$POSTURE" = "live" ] || [ "$POSTURE" = "blocked" ]; then
  PROD_FORBID='testco|FIXTURE-|\.example'
  PROD_CANARY='a testco b FIXTURE- x c foo.example d'
  if ! echo "$PROD_CANARY" | grep -qE "$PROD_FORBID"; then
    fail "the production forbidden-term pattern matches nothing"
  fi
fi

for f in "$RUNTIME" "$SKILLS/_shared/identity.json" "$SKILLS/_shared/channels.json" \
         "$HERMES_HOME/config.yaml"; do
  [ -f "$f" ] || continue
  if grep -qiE "$FORBIDDEN" "$f"; then
    fail "prior-tenant term found in reachable config: $f"
  fi
  if [ "$POSTURE" = "live" ] || [ "$POSTURE" = "blocked" ]; then
    if grep -qiE "$PROD_FORBID" "$f"; then
      fail "production leftover (.example / testco / FIXTURE-) in reachable config: $f"
    fi
  fi
done

# Production refuses mock / sheet-fixture flags (env or unit-shaped leftovers).
if [ "$POSTURE" = "live" ] || [ "$POSTURE" = "blocked" ]; then
  case "${APP_GOOGLE_OAUTH_MOCK:-}" in
    1|true|yes|TRUE) fail "production posture refuses APP_GOOGLE_OAUTH_MOCK=$APP_GOOGLE_OAUTH_MOCK" ;;
  esac
  if [ -n "${JARVIS_SHEET_FIXTURE:-}" ]; then
    fail "production posture refuses JARVIS_SHEET_FIXTURE"
  fi
fi

# ── writes vs boundary (dual source: env is the kill switch; posture must match)
if [ "${JARVIS_ALLOW_WRITES:-0}" = "1" ] && [ "$POSTURE" = "live" ] && [ "$LIVE_WRITES" = "approved" ]; then
  # Real recipients, by a recorded human decision: the app only, and never a capture suffix.
  if [ "$UNIT_ROLE" != "app" ]; then
    fail "live writes are approved for the app service only; this unit must not have JARVIS_ALLOW_WRITES=1"
  fi
  LIVE_CHECK=$(JARVIS_RUNTIME_CONFIG="$RUNTIME" python3 -c "
import sys; sys.path.insert(0,'$SKILLS/_shared')
import outbound_boundary as ob
print(','.join(ob.suffixes()))
" 2>&1) || fail "live writes: the recipient boundary could not be read — $LIVE_CHECK"
  if echo "$LIVE_CHECK" | grep -qiE '\.example|example\.com'; then
    fail "live writes but runtime still has an RFC 2606 capture suffix"
  fi
  SUFFIX="${LIVE_CHECK:-none (real recipients, live writes approved)}"
elif [ "${JARVIS_ALLOW_WRITES:-0}" = "1" ]; then
  if [ "$POSTURE" = "live" ] || [ "$POSTURE" = "blocked" ]; then
    fail "JARVIS_ALLOW_WRITES=1 is not a $POSTURE posture — writes-guard must stay on (testco combination refused)"
  fi
  SUFFIX=$(JARVIS_RUNTIME_CONFIG="$RUNTIME" python3 -c "
import sys; sys.path.insert(0,'$SKILLS/_shared')
import outbound_boundary as ob
if not ob.active():
    sys.exit('INACTIVE: no outbound_recipient_suffix configured')
bad = ob.reserved_violations()
if bad:
    sys.exit('NOT RESERVED: ' + ', '.join(bad))
print(','.join(ob.suffixes()))
" 2>&1) || fail "writes armed but the recipient boundary is unusable — $SUFFIX"

  JARVIS_RUNTIME_CONFIG="$RUNTIME" python3 -c "
import sys; sys.path.insert(0,'$SKILLS/_shared')
import outbound_boundary as ob
for addr in ('probe@34dev.com', 'probe@gmail.com', 'probe@cqhouston.com'):
    try:
        ob.check(addr, announce=False); sys.exit('boundary ALLOWED ' + addr)
    except ob.RecipientRefused:
        pass
ob.check('probe@fixture.example', announce=False)
" >/dev/null 2>&1 || fail "the boundary did not behave correctly on a live probe"
else
  if [ "$POSTURE" = "captured" ]; then
    fail "writes_posture=captured but JARVIS_ALLOW_WRITES is not 1"
  fi
  # Production + reserved suffix pretending to be live is the testco clone hazard.
  if [ "$POSTURE" = "live" ] || [ "$POSTURE" = "blocked" ]; then
    LIVE_CHECK=$(JARVIS_RUNTIME_CONFIG="$RUNTIME" python3 -c "
import sys; sys.path.insert(0,'$SKILLS/_shared')
import outbound_boundary as ob
print(','.join(ob.suffixes()))
" 2>&1) || true
    if echo "$LIVE_CHECK" | grep -qiE '\.example|example\.com'; then
      fail "production posture but runtime still has an RFC 2606 capture suffix"
    fi
  fi
  SUFFIX="none"
fi

echo "PREFLIGHT SUCCESS: writes=${JARVIS_ALLOW_WRITES:-0} boundary='${SUFFIX:-none}' HERMES_HOME=$resolved"
exit 0
