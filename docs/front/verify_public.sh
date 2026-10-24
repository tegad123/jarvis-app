#!/usr/bin/env bash
# Public proof of the front door over the real internet (Cloudflare -> tunnel -> 8790).
#   bash docs/front/verify_public.sh [slug ...]      (default: tega eddie blake martin)
# Reads nothing secret and signs nobody in. Before the human tunnel/Access/Google steps
# every public check here FAILS; that is the canary that the checks can fire.
set -uo pipefail
SLUGS=("${@:-tega eddie blake martin}"); [ $# -eq 0 ] && SLUGS=(tega eddie blake martin)
pass=0; total=0
ck() { total=$((total+1)); if [ "$2" = 1 ]; then pass=$((pass+1)); echo "  PASS  $1${3:+ — $3}"; else echo "  FAIL  $1${3:+ — $3}"; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }

echo "join.yamin.io"
getent hosts join.yamin.io >/dev/null; ck "join.yamin.io resolves" $([ $? = 0 ] && echo 1 || echo 0)
c=$(code https://join.yamin.io/); ck "join page answers 200 (no Access login in front of it)" $([ "$c" = 200 ] && echo 1 || echo 0) "$c"
loc=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 15 https://join.yamin.io/signin/google)
ck "join Google sign-in goes to Google with the join callback" \
  $([[ "$loc" == https://accounts.google.com/* && "$loc" == *redirect_uri=https%3A%2F%2Fjoin.yamin.io%2Foauth%2Fcallback* ]] && echo 1 || echo 0)
c=$(code https://nosuch-account-x.yamin.io/api/ping); ck "unknown account name is 404 from the front door" $([ "$c" = 404 ] && echo 1 || echo 0) "$c"

for s in "${SLUGS[@]}"; do
  echo "$s.yamin.io"
  c=$(code "https://$s.yamin.io/api/ping"); ping=$c; ck "$s ping 200 (no Access redirect)" $([ "$c" = 200 ] && echo 1 || echo 0) "$c"
  m=$(curl -s --max-time 15 "https://$s.yamin.io/api/auth/methods")
  ck "$s public sign-in is Google only" $([ "$m" = '{"pin":false,"passphrase":false,"google":true}' ] && echo 1 || echo 0) "$m"
  c=$(code -X POST -H 'content-type: application/json' -d '{}' "https://$s.yamin.io/api/mac/claim")
  ck "$s Mac path reaches Jarvis (Jarvis refuses a bad claim, not Cloudflare)" $([[ "$c" = 400 || "$c" = 401 || "$c" = 422 ]] && echo 1 || echo 0) "$c"
  c=$(code -X POST -H 'content-type: application/json' -d '{}' "https://$s.yamin.io/api/mac/messages")
  ck "$s Mac upload without a device token is refused by Jarvis" $([[ "$c" = 401 || "$c" = 422 ]] && echo 1 || echo 0) "$c"
  # A tunnel catch-all also answers 404; only count it when this host reached Jarvis.
  c=$(code "https://$s.yamin.io/api/broker/health"); ck "$s internal broker path is 404 from outside (host reaches Jarvis)" $([ "$c" = 404 ] && [ "$ping" = 200 ] && echo 1 || echo 0) "$c, ping $ping"
done
echo; echo "$pass/$total passed"
[ "$pass" = "$total" ]
