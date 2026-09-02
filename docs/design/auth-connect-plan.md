# Staff Google sign-in and tenant Google connection

Plan only. No code in this document. Two separate Google relationships, one cookie, PIN and operator passphrase kept.

## What exists today

Staff prove who they are with `POST /api/login` (`app/main.py`). Two doors, one httponly cookie from `app/auth.py` `issue_cookie`:

| Door | Body | Session |
|------|------|---------|
| Person | `person_key` + `pin` | `role` from `accounts` (`owner` / `admin` / `member`) |
| Operator | `passphrase` | `role=operator`, `person_key=null`, optional `operator` label |

`accounts` (`jarvis_accounts.db`, per `HERMES_HOME`) has `person_key`, `role`, PIN hash/salt, `disabled`, timestamps. No email column. Identity already has emails: `identity.people.<key>.email` is a list (`_shared/identity.py`). An account is still a credential on that identity, not a new person.

The login screen (`frontend/src/screens/Login.tsx`) fetches `GET /api/people` unauthenticated (names and titles only, no addresses), then a PIN field. Operator access is a link, not a peer.

Google APIs on the box today go through `_shared/google_creds.py`: service account plus domain-wide delegation, impersonating `identity.assistant_address`, with a plaintext `google_token.json` fallback. That is not the verification OAuth app. `app/oauth_demo.py` on port 8085 is a recording harness only. Production must not import it.

Role gates and `require_bodies` stay as written in `app/auth.py`. This plan does not widen operator read of message bodies, does not let the operator approve, and does not let the operator hold a person key.

## Two Google flows, never one

**Staff sign-in** uses OpenID (`openid email profile`). It answers "which account row is this person." It must not request Gmail, Calendar, Sheets, or Drive. Mixing those scopes into login would put Restricted-scope consent on every staff member and would violate the privacy page's split.

**Tenant connection** is the five scopes from the verification submission:

- `gmail.modify`
- `gmail.send`
- `calendar.events`
- `spreadsheets`
- `drive.file`

One connection per tenant. Owner or admin starts it. Tokens are the tenant's mailbox grant, not a staff login. A member who signs in with Google does not thereby connect the team's Gmail.

Google granular consent can return a subset of those five. The connect callback MUST compare the granted scope string against all five requested scopes. On a partial grant, do not write a row and do not store a cipher. Reject with a visible page that says all five permissions are required.

Same OAuth client is allowed. Same callback path is allowed. The signed `state` value names the purpose (`login` vs `connect`) so the handler cannot confuse them. That state is not a single process-wide slot: see Route inventory for per-attempt nonce, expiry, consume-on-use, and why `app/oauth_demo.py`'s `_oauth_state` must not be copied.

## Hostname (every place it matters)

Assume the public origin is `https://app.yamin.io` and the Google-registered redirect URI is:

`https://app.yamin.io/oauth/callback`

That string is not a constant to sprinkle. It is derived from one setting, `APP_PUBLIC_ORIGIN` (scheme + host, no trailing slash), read through `app/config.py` the same way other secrets and deploy flags are (process env, then the tenant `.env`). Do not put the hostname in `tenant_config` / `runtime.json`. That file is skill settings. A hostname in runtime.json would be a second source of truth and a way to point callback at the wrong box.

Places the origin or the callback URI actually matter:

1. **Google Cloud Console.** Authorized JavaScript origins and authorized redirect URIs. Production: `https://app.yamin.io` and `https://app.yamin.io/oauth/callback`. Adding a URI is an ops step, not a code deploy.
2. **Token exchange `redirect_uri`.** Must byte-match the registered URI. Built as `{APP_PUBLIC_ORIGIN}/oauth/callback`.
3. **OAuth `authorization_url`.** Same origin. If `APP_PUBLIC_ORIGIN` is missing, refuse to start the flow (loud), do not guess `request.base_url` (that is how a tunnel hostname or `127.0.0.1` leaks into Google).
4. **Session cookie `Secure`.** `APP_COOKIE_SECURE` already defaults on because the live app is reached over HTTPS (`app/config.py`). `app.yamin.io` stays HTTPS. Testco loopback HTTP keeps `APP_COOKIE_SECURE=0`.
5. **Cookie `Domain`.** Host-only on `app.yamin.io`. Do not set `Domain=.yamin.io` or the marketing site would see the session name.
6. **SameSite=Lax.** Keep it. Google's redirect to `/oauth/callback` is a top-level GET, so Lax sends the CSRF state cookie. Do not switch to None.
7. **cloudflared / DNS.** `api.ts` notes the current tunnel hostname can rotate. `app.yamin.io` has to be a stable name on the tunnel that serves this FastAPI process. That is DNS and the tunnel config, which this repo must not edit in an ordinary PR (`AGENTS.md`).
8. **Testco.** `:8710` is `http://127.0.0.1:8710`. It cannot complete a real Google redirect aimed at `app.yamin.io`. Live Google-against-testco would need a second registered URI such as `http://localhost:8710/oauth/callback` (Google treats `localhost` more kindly than `127.0.0.1`). Default verification of the Google doors on testco is a mocked token endpoint, not a real consent screen.
9. **One tenant per process.** The callback resolves tenant implicitly by which process serves `APP_PUBLIC_ORIGIN`. Today that is one hostname (`app.yamin.io`) per tenant process. Multi-tenant on a shared hostname would need tenant identity inside the signed state; that is deferred, not decided. Slice 7 (ops hostname) inherits this assumption: one origin, one process, one tenant.
10. **`app/oauth_demo.py`.** Stays on `http://localhost:8085/oauth/callback`. If the production client is reused, that URI is a third Console entry. Prefer a separate Cloud client for the demo so a recording cannot mint production cookies.
11. **CORS.** None today (same origin). Do not add a CORS allowlist of `yamin.io` that would let the public site call `/api/login`.
12. **`GET /api/health` and absolute links** in connection status (Drive/Calendar htmlLinks). Those are Google URLs, not ours.
13. **Privacy/terms copy** on yamin.io already describes scopes. The app hostname in that copy should stay `yamin.io` / `app.yamin.io` as written, not a tunnel hash.

## Data model

All of this lives under the tenant's `HERMES_HOME` (`config.HERMES_HOME / "state"`). There is no shared accounts database across tenants. Every query already carries tenant identity by opening that tenant's file. Do not add a global token table.

### `accounts.email`

Add a nullable `email` column on `accounts`. Store one login address, lowercased, trimmed.

Backfill: for each `person_key`, copy `identity.people[key].email[0]` if present. Identity can hold several addresses; the account holds the one Google sign-in will match. Operators set or fix it with `accounts_cli` (`set-email`). Do not auto-create an account from a Google profile. Unknown email is a failed login, same message as a wrong PIN, so the form does not enumerate who has an account.

Google login maps that email to the account row, then MUST check `accounts.disabled` before `issue_cookie`. A disabled account with a matching email fails login with the same generic message as a wrong PIN (no enumeration).

If two identity people share `email[0]`, the unique index on `lower(email)` will reject the second insert. The backfill MUST skip-and-log the duplicate and continue. It must not abort mid-run.

Unique index on `lower(email)` where `email` is not null. Two people cannot share a login address.

Optional later: `google_sub` for a stable Google subject if someone changes their Gmail. Not required for v1 if email is the join.

PIN columns stay. A person can have email and PIN, email only, or PIN only. Google-primary staff should still get a PIN minted at concierge so Bao-style recovery and scripted logins work.

Migration is a `CREATE TABLE IF NOT EXISTS` plus `ALTER TABLE ... ADD COLUMN` guarded so a second boot is a no-op. This is a schema change. It needs an explicit human yes before it lands (`AGENTS.md` rule 6).

### Tenant Google tokens

New table `google_connection` in the same `jarvis_accounts.db` (one row, tenant-scoped by the file):

- `google_email`: mailbox that granted access (display only)
- `scopes`: granted scope string
- `token_cipher`: Fernet blob of refresh token + access token + expiry. Never logged, never copied to git, never written to `runtime.json`
- `status`: `connected` / `needs_reauth` / `disconnected`
- `connected_by`: `person_key` of the owner or admin who finished consent
- `connected_at`, `refreshed_at`
- `last_error`: class of failure (`invalid_grant`, `http_401`), not the token response body

A file under `HERMES_HOME/credentials/` would rhyme with `google_token.json` and would keep plaintext habits. Encrypt in SQLite instead. `config_apply.py` already refuses to write `credentials.*` through chat. Keep that.

Encryption key: `APP_GOOGLE_TOKEN_KEY` in the tenant `.env`, read with `config.require`. Not `tenant_config`. Rotation uses MultiFernet: `APP_GOOGLE_TOKEN_KEY` accepts a comma-separated new,old key list during the swap window, decrypt tries all, encrypt uses the first; drop the old key after re-encryption. A missing key is a boot failure for connect routes only, not for PIN login.

`cryptography` is not in `requirements.txt` today. Importing it (or `google-auth*`) into `/root/jarvis-app/.venv` is a shared-venv install that hits live and testco. Human yes required before that pip.

## Config reads

Skill settings (sheet id, timezone, skill toggles) stay on `tenant_config.get` / `require`. Do not store OAuth client id, client secret, token key, or refresh tokens there.

OAuth client file or env (`YAMIN_OAUTH_CLIENT` / `GOOGLE_OAUTH_CLIENT`) stays on `app/config.py`. Path only in logs, never contents.

Connection *status* for the UI comes from the `google_connection` row, not from runtime.json. Health can expose `connected` / `needs_reauth` without scopes dumped as secrets.

When `google_creds` later prefers the tenant connection, a service-account fallback must log and increment a counter. A silent fallback is a false green.

## Route inventory

Unchanged on purpose:

- `POST /api/login` person+PIN
- `POST /api/login` passphrase (operator)
- `POST /api/logout`, `GET /api/session`
- `GET /api/people` (still names/titles, still no emails)
- Every `auth.require_*` dependency
- Operator inject/outbox

New:

| Method | Path | Who | Does |
|--------|------|-----|------|
| GET | `/api/auth/google/start` | anonymous | Redirect to Google with OpenID scopes. Sets a signed state cookie (per-attempt nonce, purpose, expiry). |
| GET | `/oauth/callback` | Google redirect | Consumes state (nonce + purpose + expiry); cookie cleared; replay rejected. Login: map email → account, refuse if `disabled`, then `issue_cookie`. Connect: require owner/admin session, require all five scopes, else reject; only then store cipher. **Hostname:** this path is the registered URI under `APP_PUBLIC_ORIGIN`. |
| GET | `/api/auth/google/start` failure | anonymous | Throttle like PIN. |
| GET | `/api/google/connection` | owner or admin (`require_admin` + `require_person`) | Status, google_email, connected_at, status. No token fields. Operator 403. Member 403. |
| GET | `/api/google/connect/start` | owner or admin | Redirect to Google with the five scopes, `access_type=offline`, `prompt=consent`. State purpose `connect`. Must already have a person session (cookie sent because Lax top-level... actually this start is an in-app navigation, cookie is sent). |
| POST | `/api/google/connection/disconnect` | owner or admin | Revoke at Google, delete cipher, status `disconnected`. |

`GET /oauth/callback` is unauthenticated at the FastAPI layer because Google's browser has no session on the *login* purpose. For `connect` purpose, the callback still needs the staff cookie (Lax, top-level GET from accounts.google.com). If that cookie is missing, show "sign in again then connect," do not store tokens.

The signed state cookie carries a per-attempt random nonce, the purpose (`login` vs `connect`), and an expiry. The cookie is cleared at the callback. A state value is consumed on first use; a replay is rejected. Do not copy `app/oauth_demo.py`'s single-slot `_oauth_state` into production. That pattern is one in-memory value for the whole process and cannot represent two in-flight attempts or survive a worker restart.

Login purpose: map email to the account row, then MUST check `accounts.disabled` before `issue_cookie`. Disabled, unknown email, and missing row all fail with the same generic message as a wrong PIN (no enumeration).

Connect purpose: after the token exchange, compare the granted scope string against all five requested scopes. A partial grant is rejected with a visible "all five permissions are required" page. Do not write any `google_connection` row and do not store a cipher.

Changed:

- `GET /api/session` may add `google_login: true` later. Not required for v1.
- `GET /api/health` sheets check should report connection status once `google_creds` reads the new row. Until then, do not pretend health proves OAuth.

Frontend:

- `Login.tsx`: primary control "Continue with Google" (`window.location` to `/api/auth/google/start`). Secondary "Sign in with PIN" reveals the existing picker and PIN field. Operator link unchanged.
- New Settings surface for owner/admin: Connect Google card (status, connect, disconnect). See UI below.
- `api.ts` stays relative `/api/...` and `credentials: 'include'`. Do not bake `app.yamin.io`.

## UI surfaces

### Login

Keep the glass card. Order:

1. Continue with Google
2. Sign in with PIN (disclosure; current tiles + PIN)
3. Operator access (unchanged)

Do not list emails. Do not auto-select a person for Google. Google returns the email; the server maps it.

If `APP_PUBLIC_ORIGIN` or the client file is missing, the Google button can render but start returns a visible error page. Do not hide a 500 behind "Checking…"

### Connect Google card

There is no Settings tab today (`TabBar`: feed, ask, approvals, leads, tasks). Approvals is owner-only and is the wrong home (approvals are outbound decisions, not mailbox grants).

Add a Settings tab, visible in the UI for `owner` and `admin` only, enforced with `require_admin` + `require_person` on every new route. Members see no tab. Operators see the tab if we show them structure... do not. Connecting Gmail is an act on tenant data. Operator observes; they do not connect or disconnect. Hide Settings for `role=operator`.

The card shows: connected mailbox (the Google email, which is the team's inbox, not a secret token), status, last refresh time or `needs_reauth`, Connect / Disconnect. Disconnect is a confirm. No token, no refresh token, no client secret on the page.

Do not put this card on Feed as a nag for members.

## Token custody

- Encrypt before write. Decrypt only inside the Google client builder.
- Refresh on use (`credentials.refresh`). On success, write a new cipher, set `refreshed_at`.
- `invalid_grant` / revoked: set `needs_reauth`, fire a feed event and a counter (`google_connection_needs_reauth`). Skills that would send mail must refuse loudly, not silently skip.
- Disconnect: `https://oauth2.googleapis.com/revoke`, then delete the row or zero the cipher. Ignore revoke HTTP errors after logging status code only.
- Never print `creds.to_json()`, never log `authorization_response`, never log `.env`.
- Process memory will hold a decrypted token during a Gmail call. That is unavoidable. Do not put it in feed attachments or health JSON.

Failure states the UI must distinguish:

- Never connected
- Connected
- Needs reauth (refresh failed)
- Disconnect in progress / disconnected

## Bao and PIN test flows

Keep working without changes to their bodies:

- `POST /api/login` `{person_key, pin}`
- `GET /api/people` still driven by `has_pin`
- Named operator `bao` via `APP_OPERATOR_PASSPHRASES`
- `/opt/testco/SEEDED_PINS.json` readers listed in the verify-jarvis skill
- `verify_testco_service.py` dana PIN
- `verify_inject.py`, `verify_named_operator.py`, `verify_outbox.py`

Do not require Google for any existing script. Do not remove PIN minting from `accounts_cli`.

UI Playwright that assumes the PIN field is the first control will need a click on "Sign in with PIN". That is the only expected break in current suites (see verification).

## Migration order

1. Land schema + PIN-preserving login on a branch, merge to `main` when the human says yes to the migration. Do not restart `jarvis-app.service`. Live `:8700` keeps serving the old process until a later, explicit restart.
2. Do not copy this into `/opt/testco/jarvis-app` as a helpful sync. Testco is stale relative to main (`AGENTS.md`). Reconcile testco to main as its own human-supervised task, then restart `testco-app` if they ask, then run verify on `:8710`.
3. Production Google Console URIs and `APP_PUBLIC_ORIGIN=https://app.yamin.io` are an ops step after the code can read that env. DNS/tunnel are out of band.
4. Cut `google_creds` over only after a tenant connection exists on that box and the SA fallback is noisy. Tenant zero may keep SA + domain-wide delegation for a while. The OAuth connection is what Google verification and non-Workspace tenants need.

## Verification plan

`docs/verify_w3_auth.py` is hardcoded to `:8700` and mutates live accounts. Do not run it. Copy its *discipline* onto a new `:8710` script.

### What existing scripts do

Stay green if PIN login is untouched:

- `docs/testco-rehearsal/verify_testco_service.py` (dana PIN)
- inject / approvals / tasks / metrics live sections that login as dana/priya/omar

Likely need a one-line UI click if they drive `Login.tsx` instead of `POST /api/login`:

- `verifyTestcoSeeded.mjs`, `docs/desktop-layout/verify.mjs` (confirm against source)

Unrelated to auth; do not "fix" them in these PRs.

### New scripts (canary discipline)

`docs/auth/verify_auth_google.py` against `http://127.0.0.1:8710` only:

**Presence (the new doors fire):**

- `GET /api/auth/google/start` with origin configured in the test box redirects to accounts.google.com (or, in mock mode, to a local fake) and sets a state cookie.
- Connect start as dana (owner) is 302. As omar (member) is 403. As operator is 403. Logged out is 401.
- `GET /api/google/connection` as dana returns JSON with `status` and no keys named `token`, `refresh`, `client_secret`.
- Callback with a forged state is 401/400, no session cookie issued.
- Mock Google login for `dana@testco.example.com` issues a person session `person_key=dana`, `role=owner`.
- After that mock Google login, hit one `require_bodies`-gated endpoint and assert the response is identical to the same call on a dana PIN session. Parity is proven by invocation, not inferred from shared `issue_cookie`.
- Replay: complete one mock login, resend the identical callback URL; the second attempt must fail with 401/400 and issue no session cookie.
- Mock token endpoint returns 4 of 5 connection scopes: the `google_connection` row count must stay 0 (reject page; no row, no cipher).

**Absence (PIN and operator still exist):**

- dana PIN login still 200, still `role=owner`.
- Wrong PIN still 401 `That PIN is not right.`
- Unknown person+PIN still that same message.
- Operator passphrase still `person_key=null`, `role=operator`.
- `GET /api/people` still has no `@`, no `role`, no `disabled`.
- Disable dana in a scratch/testco copy, mock Google login as dana's address, expect 401 with the generic message; re-enable after.

**Companion mutate** `docs/auth/mutate_auth_google.py`:

- Map dana's `accounts.email` to a different address; mock Google login as dana's real address must fail; PIN login must still pass. Restore after.
- If that mutate cannot fail the Google path, the verify script is a false green.

Ledger: `accounts.last_login` before/after PIN and after mock Google. `google_connection` row count 0→1 on a mocked connect, 1→0 on disconnect. Never print cipher bytes.

Health: after connect, `/api/health` must not include the refresh token.

Do not point any of this at `:8700`.

## Build sequence (PR-sized)

Each slice is one branch, one human yes if it migrates schema or adds venv packages, verify on testco only after reconcile.

1. **This plan.** `docs/design/auth-connect-plan.md`. No runtime change.

2. **Email column.** `accounts.email` + unique index + `accounts_cli set-email` + backfill from identity. Roster/CLI show whether email is set, not the Google flow. PIN tests unchanged. Verify: `accounts` row has email for dana after backfill; unique index rejects a duplicate in a scratch DB; scratch DB with a deliberate duplicate proves the backfill completes and logs the skip.

3. **Staff Google login, PIN fallback UI.** OpenID start + `/oauth/callback` login purpose + `Login.tsx` button order. Mock token endpoint for tests. `APP_PUBLIC_ORIGIN` required to start. No five-scope connect yet. Verify: new `verify_auth_google.py` PIN/operator/people canaries plus mock Google login, disabled-account Google 401, callback replay 401/400, and `require_bodies` parity vs dana PIN. Flag: `google-auth-oauthlib` in the shared venv.

4. **Tenant connection store + APIs.** Encrypted `google_connection`, connect start (five scopes), callback connect purpose, status GET, disconnect POST. No Settings tab yet; curl is enough. Verify: member 403, operator 403, owner mock-connect writes one row, disconnect clears cipher, status JSON has no secrets, mock 4-of-5 scopes leaves `google_connection` row count at 0.

5. **Settings tab + Connect Google card.** Frontend only plus the GET the card needs (already in 4). Hidden for member and operator. Server gates already in 4. Verify: UI not required for merge if 4's API tests are green; a Playwright smoke can wait.

6. **`google_creds` cutover.** Prefer decrypted tenant connection; SA fallback logs and counters. Health sheets check uses the same path. Human yes: this touches send-adjacent skill credentials. Verify: boxed unit with a fake cipher; live testco only if a disposable Google account is connected, never tenant zero.

7. **Ops hostname.** Console redirect URI, `APP_PUBLIC_ORIGIN=https://app.yamin.io`, tunnel DNS. Out of this repo's systemd/tunnel files. Demo client stays on `:8085`.

Do not combine 3 and 4 in one PR. Login OpenID and mailbox Restricted scopes failing together is how a reviewer (or a staff member) gets the wrong consent screen.

## Out of scope

- Changing `require_bodies` / operator inject
- Wiring `app/oauth_demo.py` into `app/main.py`
- Self-serve account signup (still concierge + `accounts_cli`)
- Putting tokens in git, feed, or health
- Reconciling `/opt/testco/jarvis-app` to main
- Restarting `jarvis-app.service`
