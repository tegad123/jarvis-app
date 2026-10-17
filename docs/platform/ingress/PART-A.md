# Part A: Eddie, Blake and Martin reach their Jarvis at <slug>.yamin.io

Human yes 2026-10-16: hostnames `eddie.yamin.io`, `blake.yamin.io`,
`martin.yamin.io`. The human runs the Cloudflare steps (tunnels and Access are
human-only); the lead agent does the app side.

Only each person's **app** is published. Gateways stay on loopback; the app
talks to its gateway itself. A hooks hostname for Gmail push comes with Part C
if it is wanted, and needs its own Access bypass because Google cannot log in.

Order matters: Access goes in **before** DNS, so no hostname is ever reachable
without it.

## 0. Preconditions (lead agent, done before you start)

- PIN lockout deployed on all three (5 wrong PINs lock the person key for 15
  minutes, logged and counted), proved by `docs/auth/verify_pin_lockout.py`.
- `public_origin` is still `http://127.0.0.1:<port>`; it changes in step 4.

## 1. Cloudflare Access (Zero Trust dashboard), one application per person

Login method, once per account: Settings → Authentication → Login methods →
add **One-time PIN**.

Per person (Access → Applications → Add an application → Self-hosted):

| Field | Eddie | Blake | Martin |
|---|---|---|---|
| Application name | Jarvis – Eddie | Jarvis – Blake | Jarvis – Martin |
| Domain | `eddie.yamin.io` (no path) | `blake.yamin.io` | `martin.yamin.io` |
| Session duration | 24 hours (suggested; longer means fewer one-time codes on the phone) | same | same |
| Identity providers | One-time PIN only; "Instant Auth" on | same | same |
| Policy 1 | **Allow**, Include → Emails → `<EDDIE_EMAIL>` | `<BLAKE_EMAIL>` | `<MARTIN_EMAIL>` |
| Policy 2 (optional, for the lead's proof) | **Service Auth**, Include → Service Token → `jarvis-verify` | same | same |

Policy 2 lets the lead agent run the lockout proof against the public address
from this host. Create the token under Access → Service credentials → Service
Tokens, name `jarvis-verify`, duration 1 month. Then put its client id and
secret on two lines in `/root/onboarding/cf-access-verify-token.txt` (root
600), without them passing through chat or a command line:

```sh
install -m 600 -o root -g root /dev/stdin /root/onboarding/cf-access-verify-token.txt
# paste the client id, Enter, the client secret, Enter, then Ctrl-D
```

The agent sends them as headers and never prints them. Delete the token
and the policy when you no longer want host-side proofs.

PWA install bypass, per hostname. Browsers fetch the web-app manifest
without cookies, so behind Access the phone's "Add to Home Screen" would get the
login page instead. Add a second self-hosted application per person:

| Field | Value |
|---|---|
| Name | Jarvis – Eddie (install files) |
| Domains | `eddie.yamin.io/manifest.webmanifest` and `eddie.yamin.io/icons` |
| Policy | **Bypass**, Include → Everyone |

These are static files with no tenant data. Everything else on the hostname
stays behind the person's Allow policy.

The same through the API, if you prefer. `$CF_ACCOUNT_ID`, `$CF_API_TOKEN`
(Access: Apps and Policies edit), `$OTP_IDP_ID` (from
`GET /accounts/$CF_ACCOUNT_ID/access/identity_providers`, the `onetimepin`
entry) and `$VERIFY_TOKEN_ID` are yours. Check the body against the current
Access API reference before running it:

```bash
for p in "eddie:<EDDIE_EMAIL>" "blake:<BLAKE_EMAIL>" "martin:<MARTIN_EMAIL>"; do
  slug=${p%%:*}; email=${p#*:}; Name="$(echo ${slug:0:1} | tr a-z A-Z)${slug:1}"
  curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access/apps" \
    -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
    --data @- <<JSON | jq '{success, id: .result.id, domain: .result.domain, errors}'
{"name": "Jarvis - $Name", "domain": "$slug.yamin.io", "type": "self_hosted",
 "session_duration": "24h", "auto_redirect_to_identity": true,
 "allowed_idps": ["$OTP_IDP_ID"], "app_launcher_visible": false,
 "policies": [
   {"name": "$Name only", "decision": "allow", "include": [{"email": {"email": "$email"}}]},
   {"name": "Jarvis verifier", "decision": "non_identity",
    "include": [{"service_token": {"token_id": "$VERIFY_TOKEN_ID"}}]}
 ]}
JSON
  curl -sS -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/access/apps" \
    -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" \
    --data @- <<JSON | jq '{success, id: .result.id, errors}'
{"name": "Jarvis - $Name (install files)", "type": "self_hosted",
 "self_hosted_domains": ["$slug.yamin.io/manifest.webmanifest", "$slug.yamin.io/icons"],
 "policies": [{"name": "install files", "decision": "bypass", "include": [{"everyone": {}}]}]}
JSON
done
```

## 2. Tunnel ingress and DNS (on this host, as root)

The proposed config is `docs/platform/ingress/yamin-config.yml`: today's
`/etc/cloudflared/config.yml` plus three rules, nothing else changed. Checked
locally with `cloudflared tunnel ingress validate` and `ingress rule`. Each
hostname matches its own app port, and rehearsal still matches 8730.

```bash
cd /root/jarvis-platform
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
cp -p /etc/cloudflared/config.yml /root/archives/cloudflared-config.yml.before-partA-$STAMP
diff /etc/cloudflared/config.yml docs/platform/ingress/yamin-config.yml   # expect only the 9 added lines
install -m 644 -o root -g root docs/platform/ingress/yamin-config.yml /etc/cloudflared/config.yml
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
for h in eddie blake martin; do cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://$h.yamin.io/; done
for h in eddie blake martin; do cloudflared tunnel route dns yamin $h.yamin.io; done
systemctl restart cloudflared-yamin.service   # rehearsal blinks for a few seconds
systemctl is-active cloudflared-yamin.service
```

Rollback: `install -m 644 /root/archives/cloudflared-config.yml.before-partA-$STAMP /etc/cloudflared/config.yml && systemctl restart cloudflared-yamin.service`,
then delete the three CNAMEs in the yamin.io DNS zone.

## 3. Check Access is in front (anyone, from anywhere)

```bash
for h in eddie blake martin; do
  echo "$h: $(curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}' https://$h.yamin.io/api/ping)"
done
```

Expect `302` to `*.cloudflareaccess.com` for each, **not** `200`. A 200 means
Access is not in front: stop and fix that before step 4.

## 4. App side (lead agent, after your go)

- `public_origin: https://<slug>.yamin.io` in each manifest, then
  `provision_tenant.py --manifest docs/tenants/<slug>.yaml --reconfigure`
  (no mailbox connected, so no restart gate). Proof 123/123 each.
- Lockout proof on the public address (locks that owner for 15 minutes, so
  run it before the PIN is handed over):
  `verify_pin_lockout.py --url https://<slug>.yamin.io --person-key owner
  --pin-file /root/onboarding/<slug>-owner-pin.txt --unit <slug>-app
  --access-token-file /root/onboarding/cf-access-verify-token.txt --wait-expiry`
- One chat round trip per tenant through the public address
  (`verify_tenant_chat.py` gets a `--url` option for this; today it talks to
  the loopback port).

## 5. Hand-over (human)

Send each person their hostname and, separately, their PIN (from
`/root/onboarding/<slug>-owner-pin.txt`). They sign in with the one-time code
sent to their email, then pick their own name and enter the PIN. On a phone: Share →
Add to Home Screen.
