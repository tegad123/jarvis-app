# Part A: eddie, blake and martin on {slug}.yamin.io behind Cloudflare Access

Human-run. The agent drafted it and must not run the Cloudflare steps itself
(AGENTS.md: cloudflared tunnels are human-only).

Order matters: Access goes on **before** DNS exists, so there is never a
moment when a tenant answers on the internet without Access in front.

| Step | Who | What |
|------|-----|------|
| 0 | agent | PIN lockout deployed and proved on all three (done before this runbook is used) |
| 1 | human | Cloudflare Access: login method, optional verifier token, one app per host |
| 2 | human | Tunnel config, DNS routes, tunnel restart |
| 3 | agent | `public_origin` to https, `--reconfigure`, prove Access, lockout and chat on the public address |
| 4 | human | Hand each person their PIN, privately |

## 1. Cloudflare Access (Zero Trust dashboard)

### 1a. Login method, once per account

Settings → Authentication → Login methods → Add new → **One-time PIN**.

### 1b. Verifier service token (optional, but needed for step 3)

The agent's public-address probes cannot do an email one-time code. Access →
Service auth → Service tokens → Create `jarvis-verify` (duration: 1 month; delete
after step 3 if you prefer). Put the two values on the host without showing
them in chat, client id on the first line and client secret on the second:

```sh
install -m 600 -o root -g root /dev/stdin /root/onboarding/cf-access-verify-token.txt
# paste the client id, Enter, the client secret, Enter, then Ctrl-D
```

### 1c. One application per hostname (repeat for eddie, blake, martin)

Access → Applications → Add an application → **Self-hosted**:

- **Name:** `Jarvis - Eddie` (Blake, Martin)
- **Domain:** `eddie.yamin.io`, path empty (the whole host)
- **Session duration:** 24 hours (the PWA asks for the code again once a day;
  7 days is the usual alternative if that is too often)
- **Identity providers:** One-time PIN only, with *Instant Auth* on (skips the
  provider picker)
- **Policy 1, `Eddie only`:** Action Allow; Include → Emails → *Eddie's own
  address*. Nothing else, no "Everyone", no email domain.
- **Policy 2, `Jarvis verifier`** (only if you made 1b): Action Service Auth;
  Include → Service Token → `jarvis-verify`.

Then a second, narrower application on the same host so the phone can install
the app. Browsers fetch the web-app manifest without cookies, so Access would
answer it with a login redirect:

- **Name:** `Jarvis - Eddie (install files)`
- **Domains:** `eddie.yamin.io` path `manifest.webmanifest`, and
  `eddie.yamin.io` path `icons/` (the add-another-domain field)
- **Policy:** Action Bypass; Include → Everyone. These are the static icon and
  manifest files, the same for every tenant, with no data in them.

The same through the API, for one host (fill in the account id, an API token
with Access: Apps and Policies Edit, the One-time PIN provider id from
`GET /accounts/$CF_ACCOUNT/access/identity_providers`, the token id from 1b,
and the person's email):

```sh
curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT/access/apps" \
  -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
  --data '{
    "name": "Jarvis - Eddie",
    "type": "self_hosted",
    "domain": "eddie.yamin.io",
    "session_duration": "24h",
    "allowed_idps": ["<ONE_TIME_PIN_IDP_ID>"],
    "auto_redirect_to_identity": true,
    "app_launcher_visible": false,
    "policies": [
      {"name": "Eddie only", "decision": "allow", "precedence": 1,
       "include": [{"email": {"email": "<EDDIE_EMAIL>"}}]},
      {"name": "Jarvis verifier", "decision": "non_identity", "precedence": 2,
       "include": [{"service_token": {"token_id": "<JARVIS_VERIFY_TOKEN_ID>"}}]}
    ]
  }'

curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT/access/apps" \
  -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
  --data '{
    "name": "Jarvis - Eddie (install files)",
    "type": "self_hosted",
    "domain": "eddie.yamin.io/manifest.webmanifest",
    "self_hosted_domains": ["eddie.yamin.io/manifest.webmanifest", "eddie.yamin.io/icons/"],
    "app_launcher_visible": false,
    "policies": [
      {"name": "Install files", "decision": "bypass", "precedence": 1,
       "include": [{"everyone": {}}]}
    ]
  }'
```

Inline `policies` on app create is the current API shape; if Cloudflare
rejects it, create the policies under the app afterwards
(`POST .../access/apps/<app_id>/policies`) with the same bodies.

## 2. Tunnel (on the host, as root)

The new config is `docs/platform/ingress/yamin-config.yml` in the platform
repo: today's `/etc/cloudflared/config.yml` plus three app rules. Diffed
against the live file and validated with `cloudflared ... ingress validate`
before handing over.

```sh
cd /root/jarvis-platform
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
install -m 600 /etc/cloudflared/config.yml /root/archives/cloudflared-config.yml.before-partA-$STAMP

diff /etc/cloudflared/config.yml docs/platform/ingress/yamin-config.yml   # expect: only the three app rules and a comment
cloudflared tunnel --config docs/platform/ingress/yamin-config.yml ingress validate

install -m 644 -o root -g root docs/platform/ingress/yamin-config.yml /etc/cloudflared/config.yml

cloudflared tunnel route dns yamin eddie.yamin.io
cloudflared tunnel route dns yamin blake.yamin.io
cloudflared tunnel route dns yamin martin.yamin.io

# The tunnel also carries rehearsal: this is a few-second blip for it.
systemctl restart cloudflared-yamin.service
systemctl is-active cloudflared-yamin.service

# Each must answer with a redirect to the Access login, never the app itself:
for h in eddie blake martin; do
  printf '%s ' $h; curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://$h.yamin.io/api/ping
done
curl -s -o /dev/null -w 'rehearsal %{http_code}\n' https://rehearsal.yamin.io/api/ping
```

Rollback: put the archived file back with `install -m 644`, restart
`cloudflared-yamin.service`, and delete the three DNS records in the
Cloudflare dashboard (`cloudflared` has no command to remove a route).

## 3. Agent, after step 2 (under the agent lock)

- Set `public_origin: https://<slug>.yamin.io` in each manifest; run
  `provision_tenant.py --manifest docs/tenants/<slug>.yaml --reconfigure`.
- With no Access headers, each host redirects to the Access login.
- With the verifier token: `/api/ping` 200, and
  `docs/auth/verify_pin_lockout.py --url https://<slug>.yamin.io --pin-file
  /root/onboarding/<slug>-owner-pin.txt --unit <slug>-app --access-token-file
  /root/onboarding/cf-access-verify-token.txt --wait-expiry` passes. This locks
  the owner for 15 minutes, so it runs before PINs are handed out.
- The Ask round trip on the public address (`verify_tenant_chat.py`) passes.

## Known gap

The login form's message for a lock says "Too many attempts. Wait a few
minutes and try again." The lock is 15 minutes. The deployed frontend build
(`static/`) predates later frontend source changes, so fixing the wording
means a full rebuild: a separate, supervised task.
