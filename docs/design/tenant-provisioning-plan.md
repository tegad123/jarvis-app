# Tenant provisioning — second production-grade slot on the yamin stack

Plan only. No code in this document. No runtime changes, no systemd edits, no testco restart, no merge.

Goal: a second production-grade tenant slot on the `app.yamin.io` stack, standing alongside testco, ready for client one — plus the repeatable procedure it proves. The known future case of onboarding an *existing* operation (live 34dev) is reserved as a migration seam (slice 5), not built.

Out of scope: self-serve UI for config (concierge via manifest for now), billing, Spencer's live tenant as a target of this work, tenant deletion.

Human-yes gates that this plan does not waive: schema migrations, shared-venv package installs, send-path changes.

---

## What this is for

Today the droplet hosts two tenants by accident of history:

| Slot | Process | Port | Tree | Public names |
|------|---------|------|------|--------------|
| Live 34dev | `jarvis-app.service` | 8700 | `/root/jarvis-app` | quick tunnels (`cloudflared-jarvis-*`), not the named yamin tunnel |
| Testco | `testco-app.service` | 8710 | `/opt/testco/jarvis-app` | `app.yamin.io` / `gateway.yamin.io` / `hooks.yamin.io` |

Testco is the only tenant that has been *provisioned* as a second slot. It is also a rehearsal fixture: `.example` capture, `JARVIS_ALLOW_WRITES=1`, seeded PINs, a sheet fixture, Spencer-term preflight. A real client cannot be a copy of that. This plan inventories what testco actually is, classifies every per-tenant asset, and specifies PR-sized slices that stamp a *production* slot from a manifest — then prove the procedure on a scratch tenant `clientzero` that is torn down.

---

## Inventory: the actual shape of testco

Taken from the running units, `/opt/testco`, `/etc/cloudflared/config.yml`, and the skills tree on 2026-09-02. Values of secrets, PINs, Fernet keys, and `.env` contents are not recorded here — only that the files exist and which *keys* they hold.

### Tree: `/opt/testco`

```
/opt/testco/
  SEEDED_PINS.json          # dana, omar, priya, tester — must-NOT-copy to a real tenant
  testco_preflight.sh       # ExecStartPre on every testco unit
  app-testco-seeded.log
  .jarvis-snapshot-prep
  bin/testco-inject         # fixture-email injector; hardcodes testco paths + ALLOW_WRITES=1
  fixtures/                 # sheet JSON, xlsx, digest/meeting/showingsmart .eml files
  hermes/                   # HERMES_HOME
  jarvis-app/               # separate git checkout of this repo (can lag main)
                            # has its own .venv/ that units do NOT use
  skills/                   # HERMES skill root (not only openclaw-imports)
  skills/openclaw-imports/  # Jarvis product skills (own git repo)
```

`/opt/testco/hermes/skills` is a symlink to `/opt/testco/skills`, not a copy. That parent also holds Hermes curator packs (`apple`, `creative`, `github`, `productivity`, `research`, `mlops`, `media`, `software-development`, `autonomous-ai-agents`, `email`, plus empty `secrets/`). `JARVIS_SKILLS_ROOT` points at `…/openclaw-imports`; the gateway sees the whole parent via `HERMES_HOME/skills`. Manifest field `curator_packs: omit | defaults` is the policy; they are not Jarvis product and must-NOT-copy from testco's dirty tree.

`/opt/testco/jarvis-app/tools/preflight.sh` is branch/PR hygiene for agents. It is **not** the systemd ExecStartPre gate (`testco_preflight.sh`).

### HERMES_HOME (`/opt/testco/hermes`)

| Path | What it is | Copy class |
|------|------------|------------|
| `.env` | Tenant secrets. Keys present (values not recorded): `APP_SESSION_SECRET`, `API_SERVER_KEY`, `APP_PORT`, `HERMES_API_URL`, `APP_PASSPHRASE`, `APP_OPERATOR_LABEL`, `APP_OPERATOR_PASSPHRASES`. Mock OAuth is **not** here. | must-NOT-copy (generate fresh) |
| `.env.pre-*` | Rotation backups | must-NOT-copy |
| `config.yaml` | Hermes agent config (model, WAL journal_mode, personalities, platforms). Tenant-neutral structure; paths and platform ids are not. | templated-per-tenant |
| `auth.json` | Gateway OAuth-provider login state (nous device tokens, etc.) | must-NOT-copy |
| `credentials/` | Empty on testco. Live 34dev keeps Google token / SA material under `~/.hermes/credentials`. | empty dir; never copy live files |
| `state/jarvis_accounts.db` (+ WAL) | Staff accounts, PIN hashes, roles | empty (lazy migrate); never copy seeded PINs |
| `state/jarvis_leads.db` | Leads store | empty |
| `state/jarvis_tasks.db` | Tasks store | empty |
| `state/jarvis_threads.db` (+ WAL) | Channels / messages | empty |
| `state/sweeps.db` | Sweep idempotency | empty |
| `state/activity_feed.jsonl` | Feed | empty file ok; never copy |
| `state/approvals.jsonl`, `approvals_decisions.jsonl` | Approvals | empty |
| `state/operator_inject.jsonl` | Inject ledger | empty |
| `state/digest_last_run.json` | Digest slot watermark | omit (first tick creates) |
| `state/metrics_monthly_last.json` | Monthly report watermark | omit |
| `state/gateway.heartbeat`, `gateway.lifecycle.json` | Gateway liveness | omit (runtime) |
| `state/inject/`, `state/meetings/` | Inject / meeting scratch | empty dirs |
| `kanban.db` (+ WAL/shm) | Hermes kanban — the reason `--force` exists | empty / let gateway create; never copy live |
| `state.db` (+ WAL) | Hermes gateway state | never copy |
| `response_store.db` | Hermes responses | never copy |
| `outbox/` | Captured mail (`captured_mail.jsonl`) — testco boundary | must-NOT-copy; production has no capture outbox |
| `workspace/`, `media/`, `logs/`, `cache/`, `sessions/`, `memories/`, `sandboxes/`, `hooks/`, `pairing/`, `pending_messages/`, `platforms/`, `audio_cache/`, `image_cache/`, `cron/` | Hermes runtime dirs | empty skeleton |
| `SOUL.md`, `channel_directory.json` | Agent soul / channel index | templated or empty |
| `models_dev_cache.json` | Model catalog cache | omit (regenerates) |
| `gateway.lock`, `gateway.pid`, `gateway-starts.log`, `gateway_state.json` | Live process files | must-NOT-copy |
| `verification_evidence.db` | Hermes verification | omit |

`config.yaml` top-level keys: `_config_version`, `agent`, `browser`, `code_execution`, `compression`, `database` (`journal_mode: wal`), `delegation`, `display`, `fallback_model`, `group_sessions_per_user`, `memory`, `model`, `platform_toolsets`, `platforms`, `prompt_caching`, `session_reset`, `skills`, `streaming`, `stt`, `telemetry`, `terminal`, `tool_loop_guardrails`, `updates`.

### systemd — testco units and every Environment line

**`testco-app.service`** — WD `/opt/testco/jarvis-app`, `ExecStartPre=/opt/testco/testco_preflight.sh`, `ExecStart=/root/jarvis-app/.venv/bin/python -m app.main` (shared live venv).

Environment keys (values are paths/ports, not secrets):

- `HERMES_HOME=/opt/testco/hermes`
- `HOME=/root`
- `PYTHONUNBUFFERED=1`
- `APP_PORT=8710`
- `JARVIS_SKILLS_ROOT=/opt/testco/skills/openclaw-imports`
- `JARVIS_TASKS_DB`, `JARVIS_THREADS_DB`, `JARVIS_LEADS_DB`, `JARVIS_ACCOUNTS_DB` under `HERMES_HOME/state`
- `HERMES_API_URL=http://127.0.0.1:8652`
- `JARVIS_RUNTIME_CONFIG`, `JARVIS_IDENTITY_CONFIG` under the skills `_shared/`
- `JARVIS_ACTIVITY_FEED`, `JARVIS_APPROVALS`, `JARVIS_APPROVAL_DECISIONS`
- `JARVIS_ATTACHMENTS_DIR=/opt/testco/jarvis-app/data/attachments`
- `JARVIS_LEADS_SOURCE=store`
- `JARVIS_SHEET_FIXTURE=/opt/testco/fixtures/sheet_fixture.json`
- `JARVIS_ALLOW_WRITES=1`

`HOME=/root` is the live-fallback hazard: Hermes, given `HOME=/root` and a missing `HERMES_HOME`, opens `/root/.hermes`. New-tenant templates do not copy this (slice 2: `HOME=/opt/{slug}` + preflight). Do not change testco's unit in this docs work.

Unit comment still says "no cloudflared". That is **stale**: the named yamin tunnel now serves `:8710` as `app.yamin.io`.

**Drop-in** `/etc/systemd/system/testco-app.service.d/oauth.conf` — Environment *keys* only (do not copy values):

- `APP_PUBLIC_ORIGIN` (currently `https://app.yamin.io`)
- `APP_COOKIE_SECURE`
- `APP_GOOGLE_TOKEN_KEY` (Fernet; unique per tenant)
- `YAMIN_OAUTH_CLIENT` (path to the shared Google OAuth client JSON)
- `OAUTHLIB_RELAX_TOKEN_SCOPE`

`APP_GOOGLE_OAUTH_MOCK` is **absent** (real Google). `APP_HOST` is unset (code default `127.0.0.1`).

**`testco-gateway.service`** — WD `/opt/testco`, same preflight, `ExecStart=/usr/local/lib/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --force`.

Environment: `HERMES_HOME`, `HOME`, `PYTHONUNBUFFERED`, `JARVIS_SKILLS_ROOT`, `JARVIS_RUNTIME_CONFIG`, `JARVIS_IDENTITY_CONFIG`, `JARVIS_ACTIVITY_FEED`, `JARVIS_ALLOW_WRITES=1`.

`--force` exists because a live gateway already occupies the host and Hermes refuses a second instance (stated hazard: shared kanban DB). That live process is **not** a system unit named `jarvis-gateway`: it is the **user** unit `hermes-gateway.service` (`/root/.config/systemd/user/hermes-gateway.service`, WD `/root/.hermes`, listens **8642**, no `--force`). A different `HERMES_HOME` already separates kanban. On a dedicated droplet there is no first gateway and no `--force`. A third tenant on *this* host still needs `--force`.

Shared interpreter: `/usr/local/lib/hermes-agent/venv` is one hermes-agent install for live + testco + any new tenant. Same class of risk as the app venv.

**Timers / oneshot services** (all `ExecStartPre` preflight, all `JARVIS_ALLOW_WRITES=1`, all shared app venv):

| Unit | OnBootSec | Cadence | Script |
|------|-----------|---------|--------|
| `testco-task-digest` | 120s | `OnUnitActiveSec=300` (5 min tick; slots from `digest_times_local`) | `task-manager/scripts/digest_tick.py` |
| `testco-sweep-stale-email` | 180s | 1800s | `sweeps/scripts/sweep_tick.py --sweep stale_email` |
| `testco-sweep-task-followup` | 240s | 1800s | `--sweep task_followup` |
| `testco-sweep-quiet-lead` | 300s | 1800s | `--sweep quiet_lead` |
| `testco-metrics-monthly` | — | `OnCalendar=*-*-* 08:30:00` | `sweeps/scripts/monthly_report.py` |

Sweep/digest oneshots also set `JARVIS_TASKS_SOURCE=store` and the three DB paths + feed.

Live contrast (do not copy onto a new yamin-stack tenant without a decision): `jarvis-app.service` `:8700`; user unit `hermes-gateway.service` `:8642`; `JARVIS_SKILLS_ROOT=/root/.hermes/skills/openclaw-imports`; secrets from `~/.hermes/.env` not `EnvironmentFile`; `jarvis-gmail-webhook.service` (port 18790, **inactive/disabled**); `jarvis-gmail-poller.timer` every 60s (**inactive/disabled**); three `cloudflared-jarvis-*` quick tunnels (`cloudflared-jarvis.service` still targets `:18790`). Testco has **no** gmail-poller or webhook systemd unit; `hooks.yamin.io` still points at `127.0.0.1:18791`. No `EnvironmentFile=` on the matching system units.

### Preflight (`/opt/testco/testco_preflight.sh`)

Three refusals, all ExecStartPre:

1. `JARVIS_ALLOW_WRITES=1` without a working `outbound_boundary` (module asked, not the JSON file).
2. Boundary suffixes that are not RFC 2606 reserved (`.example` / `example.com` / `.test` / `.invalid` / `.localhost`). Proves refuse of real domains and allow of `.example`.
3. Prior-tenant terms `34dev|cqhouston|spencerhuck` in reachable config (`runtime.json`, `identity.json`, `channels.json`, `hermes/config.yaml`). Pattern is canary-proved before use.

must-NOT-copy as-is to a production tenant. The forbidden-term list and the `.example` probes are testco-specific. Production (and scratch) preflight **also** asserts `HERMES_HOME` is set and is a path under `/opt/{slug}` — testco's script does not; that hole is slice 2. Production preflight inverts the second check: refuse leftover testco / `FIXTURE-` / `.example` terms in a real tenant's reachable config; refuse `ALLOW_WRITES=1` without a *documented real-send* posture (no capture suffix pretending to be production). Scratch `clientzero` may keep the RFC 2606 preflight so verify can arm writes without mailing anyone.

### Cloudflared (`/etc/cloudflared/config.yml`)

Named tunnel `yamin`, credentials `/etc/cloudflared/yamin.json`, unit `cloudflared-yamin.service`. Comment in the file (the scheme this plan inherits):

```
#   app.yamin.io           the app UI/API of the tenant at the bare hostnames (testco)
#   gateway.yamin.io       that tenant's Hermes gateway API
#   hooks.yamin.io         Gmail Pub/Sub push webhook
#   {slug}.app.yamin.io    a further tenant's app
#   {slug}.gateway.yamin.io
#   {slug}.hooks.yamin.io
# Add a tenant = three ingress rules + `cloudflared tunnel route dns yamin <host>` each.
# The live tenant (34dev) keeps its quick tunnels until it is moved by decision.
```

Ingress today:

- `app.yamin.io` → `http://127.0.0.1:8710` (testco app)
- `gateway.yamin.io` → `http://127.0.0.1:8652` (testco gateway)
- `hooks.yamin.io` → `http://127.0.0.1:18791` (testco Gmail webhook listener, when enabled)
- catch-all `http_status:404`

`APP_PUBLIC_ORIGIN` stays process env, not `runtime.json` (auth-connect plan: one origin, one process, one tenant). Adding a tenant is ops: three ingress lines + three `cloudflared tunnel route dns yamin <host>` + Google Console redirect URI for that origin. This repo must not edit `/etc/cloudflared` in an ordinary app PR; the provisioner documents the commands and a human runs them (or a later ops PR with explicit yes).

### Ports in use (2026-09-02 `ss`)

| Port | Who |
|------|-----|
| 8700 | live app |
| 8710 | testco app |
| 8642 | live Hermes gateway |
| 8652 | testco Hermes gateway |
| 18790 | live webhook unit definition (not listening at inventory time) |
| 18791 | cloudflared target for `hooks.yamin.io` (listener not up at inventory time) |
| 8085 | `app/oauth_demo.py` (out of scope; never a tenant port) |

`runtime.json` `gateway.base_url` on testco is still `http://127.0.0.1:8642`. The app unit's `HERMES_API_URL=http://127.0.0.1:8652` wins for the app and its subprocesses. Skills that read only `gateway.base_url` can still hit live. Dual source — the provisioner must write **both** to the new tenant's port.

Scratch `clientzero` unused-port recommendation (confirm with `ss` at verify time): app **8720**, gateway **8662**, hooks **18792**.

### identity.json / runtime.json (testco)

`identity.json`: `client: testco`, `internal_domains: [testco.example.com]`, `assistant_address: assistant@testco.example.com`, people keys `dana` (owner), `omar`, `priya`, `assistant`, `tester` — all `@testco.example.com`. Permissions are generic role strings. `enforcement: strict`.

`runtime.json` (generated by `_shared/provision_client.py` on 2026-08-20):

- timezone `America/Chicago`
- `safety.allow_writes: true` + suffixes `.example`, `example.com`
- paths under `/opt/testco/...`
- `gateway.base_url` 8642 (stale vs unit)
- `credentials.*` still point at `~/.hermes/credentials/...` and `~/.hermes/.env` (live-shaped, stale for a `/opt/testco` tenant)
- `paths.tasks_db` still the skill-local `task-manager/data/tasks.db`; the app uses `HERMES_HOME/state/jarvis_tasks.db`
- showingsmart `sheet_id: FIXTURE-testco-lead-tracker-copy`, sender `notifications@showingsmart.example`
- `skills.task-manager.digest_times_local: ["05:00","15:00"]`
- `skills.sweeps.*` as listed in the knob table below; **`lead_revival_hours` is absent** (code default 48)
- `email-router.lookback_days` 7, `recording_subject_prefix: "PLAUD:"`, `deal_channel_prefix: "deals-"`
- `email-monitor.lookback_days` 14
- `gmail-push-trigger.webhook_port` **18790** (cloudflared hooks is **18791**)
- `cost-guard` 20 / 30 USD
- `metrics.retention_days` 180
- `leads.source` / `tasks.source` store
- `app.feed.team_destinations`: ops, deals
- custom skills present in JSON but `enabled: false` (`har-deal-tracker`, `shopify-stock-monitor`, `jarvis-render`)
- `recording-handler` enabled (directory on disk is `discord-recording-handler`)

Existing `_shared/provision_client.py` + `client-config.template.json` already stamp identity.json + runtime.json + a workspace skeleton. They do **not** stamp `/opt/{slug}`, systemd, ports, cloudflared, venv, or empty app DBs. The new provisioner is a layer **above** this and must not reinvent identity/runtime generation. `provision_client.py` **refuses `safety.allow_writes: true` at provision time** (arm writes later via env) — slice 2 must keep that refusal. Live `_shared/34dev-provision.json` is the reconstructed 34dev input (slug `34dev`); slice 5 may read it; slice 2 must-NOT-copy it onto a new tenant.

Several **core skill directories exist on testco without a matching `runtime.json` skill key** (call-action-items, universal-bridge, read-email, owner-email, …). Live runtime has a different key set (includes those; lacks sweeps / showingsmart / meeting-intake). The loadout (slice 4) must write an `enabled` key for every shipped skill so a missing key is not a silent `enabled() → False`.

### Skills tree — generic vs testco-fixture vs Spencer-specific

Testco skills are a **git repo** at `/opt/testco/skills/openclaw-imports` (HEAD at inventory: slice-6 google_creds merge). Live skills at `/root/.hermes/skills/openclaw-imports` are a **separate divergent repo with no shared remote**. Copying with `cp` is how those trees drifted. Slice 0 creates a bare canonical origin; until it exists, slice 2 must not clone from either working tree.

`SKILLS_MANIFEST.md` (28 skills) is the existing classification. Testco's *directory* tree already omitted custom skills (`gbrain-memory`, `har-deal-tracker`, `jarvis-render`, `shopify-stock-monitor` exist on live, not on testco). Runtime.json still carries disabled stubs for three of those.

| Tier | Skills | New-tenant default |
|------|--------|--------------------|
| **core** (ship enabled) | email-router, sweeps, task-manager, email-monitor, email-to-calendar, google-calendar, read-email, owner-email, owner-email-voice, lead-reply, showing-notifications, meeting-briefing, call-action-items, cost-guard, universal-bridge, gmail-push-trigger | enabled |
| **optional** (manifest choice; default off unless the loadout says on) | crm-contact-suggest, crm-weekly-brief, discord-recording-handler, showingsmart-autoreply, meeting-intake, read-pdf, excel-xlsx, create-presentation | off unless listed |
| **optional-and-blocked** | gbrain-memory | never enable until the GBrain decision |
| **custom — exclude** | har-deal-tracker, shopify-stock-monitor, jarvis-render | not cloned onto the tenant tree |

Testco-fixture (not Spencer-custom, but not production-real): `FIXTURE-*` sheet ids, `@testco.example.com` roster, `PLAUD:` recording prefix, showingsmart `.example` sender, seeded PINs, sheet_fixture.json, `.eml` inject corpus, `tester` person, `testco-inject`. A production loadout keeps the *skill code* and replaces the *values*.

Spencer-specific content that must not leak into client one's tree: HAR deal-finder, Shopify restock URLs, Fusion/render, live identity people keys, live sheet/calendar/mailbox ids, `34dev` / `cqhouston` / `spencerhuck` strings.

`_shared/` is generic product code (tenant_config, write_guard, outbound_boundary, calendar_boundary, google_creds, provision_client, send_gate). It ships to every tenant. `write_guard.py` still mentions a Mini cutover and `~/hermes_migration_worklist.md` — product debt, not a copy of 34dev data. Calendar writes have a twin of the outbound boundary (`calendar_boundary.py`, same `active()`). `_shared/purge_tenant_data.sh` is a purge tool, not a provisioner; do not run it from slice 2.

### Seeded vs fixture data

| Asset | Class |
|-------|--------|
| `/opt/testco/SEEDED_PINS.json` (keys dana, omar, priya, tester) | must-NOT-copy to production. Scratch tenant may seed **one** throwaway owner PIN via `accounts_cli`, not this file. |
| `fixtures/sheet_fixture.json`, `testco-leads-sheet.xlsx` | must-NOT-copy |
| `JARVIS_SHEET_FIXTURE` env | must-NOT-copy |
| digest / meeting / showingsmart `.eml` files | must-NOT-copy (verify corpus, not product) |
| `PASS0_INVENTORY.json` | must-NOT-copy |
| `.example` / `example.com` outbound suffix + capture outbox | must-NOT-copy to production |
| `JARVIS_ALLOW_WRITES=1` | must-NOT-copy as a default (see slice 3) |
| `APP_GOOGLE_OAUTH_MOCK` | must-NOT-copy (production real-Google) |
| Gateway `--force` | templated: set only when another gateway already runs on the host |
| Seeded people dana/omar/priya/tester | fixture-that-must-become-real (client roster) |
| `FIXTURE-testco-lead-tracker-copy` | fixture-that-must-become-real (real sheet id) |
| Empty calendar_id / mailbox / gcp_project | fixture-that-must-become-real (or remain empty until wired) |

### Venv (shared-venv risk)

Testco app **shares** `/root/jarvis-app/.venv` on purpose (unit comment: identical deps, code tree separate). `/opt/testco/jarvis-app/.venv` exists and is **unused** by the units — do not copy it, do not assume it is the interpreter. A `pip install` in the live venv hits **live 34dev and testco and any new tenant using it**. `AGENTS.md` already forbids new packages without a human yes. This plan does not change that.

Hermes gateway shares `/usr/local/lib/hermes-agent/venv` the same way.

Recommendation for a production slot on this host: keep the shared venvs for `clientzero` (proves the provisioner without a venv fork) and document the risk in the manifest. A later, human-yes slice may freeze a per-tenant venv. Do not silently `pip install`. Do not point a new tenant at a venv that live does not share without saying so.

### Backups, log rotation, monitoring (what exists)

- **Backups:** none. No cron job dumps the WAL-mode SQLite files (`config.yaml` `database.journal_mode: wal`). Copying `*.db` while a writer is up without a WAL-inclusive backup is a torn snapshot.
- **Log rotation:** no `/etc/logrotate.d` entry for jarvis/testco/hermes. Stdout is journald. JSONL files (`activity_feed.jsonl`, approvals, outbox, operator_inject) grow unbounded.
- **Monitoring beyond `/api/health`:** `GET /api/health/summary`, `GET /api/ping`, journald units, `state/gateway.heartbeat`, cost-guard spend pause, metrics monthly feed post, sweep/digest feed heartbeats (every run announces). No Prometheus, no alerting, no external uptime check on `app.yamin.io`.

### Live 34dev (contrast only — out of scope as a target)

Identity `client: 34dev`. People keys: spencer, tega, martin, eddie, cassandra, jarvis (assistant). Aliases present (andrew, carlos, prachir, shah, trey). Internal domains are **not** `.example`. `allow_writes: false` and **no** `outbound_recipient_suffix`. Skills tree includes the custom three plus `gbrain-memory`. Public ingress is still the three quick tunnels until a human moves it onto the named tunnel. Files named `yamin_*` under `/root/.hermes` (oauth client JSON, demo sheet/token) are **naming only** — they are not the identity slug. Live `_shared/34dev-provision.json` is the import input to reserve, not a source for client one.

---

## Per-tenant asset classification

Every asset a second slot needs. Classes:

- **copied-as-is** — product code, identical for every tenant (via git, not `cp` of a dirty tree).
- **templated-per-tenant** — same shape, slug/ports/paths/ids filled from the manifest.
- **fixture-that-must-become-real** — testco has a fake; production needs the real value or an explicit empty + later concierge step.
- **must-NOT-copy** — testco rehearsal, live 34dev, or secrets.

| Asset | Class | Notes |
|-------|--------|--------|
| jarvis-app source | copied-as-is | `git clone` / `git worktree` of this repo at a pinned SHA. Not `/opt/testco/jarvis-app` (that checkout can lag). |
| skills `openclaw-imports` at loadout | copied-as-is filtered | Clone **only** from the slice 0 bare origin at a pinned SHA; then sparse to the loadout. Never clone `/opt/testco/skills` or `/root/.hermes/skills`, never `cp`. |
| `_shared/` modules | copied-as-is | Ships with the skills repo. |
| `SKILLS_MANIFEST.md` | copied-as-is | Source of loadout tiers. |
| Hermes-agent install | copied-as-is | `/usr/local/lib/hermes-agent` — shared binary/venv; `--force` when a sibling gateway exists. |
| App venv | copied-as-is **with risk** | Shared `/root/jarvis-app/.venv` until a human-yes per-tenant freeze. |
| `/opt/{slug}/` layout | templated-per-tenant | `jarvis-app/`, `hermes/`, `skills/`, `bin/`, preflight. No `fixtures/` on production. |
| `hermes/skills` symlink | templated-per-tenant | → `/opt/{slug}/skills` |
| `identity.json` | templated-per-tenant | via existing `provision_client.py`. Roster is fixture-that-must-become-real. |
| `runtime.json` | templated-per-tenant | via `provision_client.py` from the manifest. Paths, ports, sheet ids, suffixes. |
| `channels.json` | templated-per-tenant | Empty team channels or from manifest. |
| `config.yaml` | templated-per-tenant | WAL on; no live platform tokens. |
| systemd `{slug}-app.service` | templated-per-tenant | From a unit template. `HOME=/opt/{slug}`. No `JARVIS_SHEET_FIXTURE`. `ALLOW_WRITES` from manifest posture. |
| systemd `{slug}-gateway.service` | templated-per-tenant | Same `HOME`. `--force` only if another gateway is active. |
| digest / sweep / metrics timers | templated-per-tenant | Cadences from manifest (slice 1 knobs). |
| oauth drop-in | templated-per-tenant | `APP_PUBLIC_ORIGIN`, `APP_COOKIE_SECURE` **0 on scratch loopback / 1 on production HTTPS**, **fresh** `APP_GOOGLE_TOKEN_KEY`, shared `YAMIN_OAUTH_CLIENT`, `OAUTHLIB_RELAX_TOKEN_SCOPE=1`. No mock. |
| `HERMES_HOME/.env` | templated-per-tenant | Fresh `APP_SESSION_SECRET`, `APP_PASSPHRASE`, `API_SERVER_KEY`, operator passphrase keys; never copy testco or 34dev. |
| Hermes curator skill packs | templated-per-tenant | Manifest `curator_packs: omit \| defaults`. Not `cp` from `/opt/testco/skills/{apple,creative,…}`. |
| Unused `/opt/testco/jarvis-app/.venv` | must-NOT-copy | Units use `/root/jarvis-app/.venv`. |
| Preflight script | templated-per-tenant | Production vs scratch variants. Asserts `HERMES_HOME` set and under `/opt/{slug}`. Not `testco_preflight.sh` as-is. |
| cloudflared ingress + DNS | templated-per-tenant | Three hostnames per scheme. Commands in the provisioner; human applies. |
| Google Console redirect URI | templated-per-tenant | Ops, not code. `{origin}/oauth/callback`. |
| Empty `state/jarvis_*.db` | templated-per-tenant | Omit files; lazy migrations create schema. |
| Owner PIN / accounts row | fixture-that-must-become-real | Concierge sets via `accounts_cli`. Scratch may seed one PIN. |
| Real sheet id, calendar id, mailbox, GCP Pub/Sub | fixture-that-must-become-real | Manifest fields; empty is allowed until wired. |
| Boundary domains | fixture-that-must-become-real | Production: real staff domains, **no** RFC 2606 capture. Scratch: `.example`. |
| Timezone | fixture-that-must-become-real | Manifest. |
| Skill loadout optional set | fixture-that-must-become-real | Manifest. |
| `SEEDED_PINS.json` | must-NOT-copy | |
| `fixtures/` including sheet_fixture and `.eml` | must-NOT-copy | |
| `JARVIS_SHEET_FIXTURE` | must-NOT-copy | |
| `JARVIS_ALLOW_WRITES=1` as a default | must-NOT-copy | Production starts writes-guard **on** (writes blocked) until cutover. |
| `APP_GOOGLE_OAUTH_MOCK` | must-NOT-copy | |
| `.example` capture outbox as production send | must-NOT-copy | |
| Testco `outbound_recipient_suffix` | must-NOT-copy | |
| Live `~/.hermes/.env`, `auth.json`, `credentials/*`, `google_token.json`, SA key | must-NOT-copy | |
| Live / testco `kanban.db`, `state.db`, JSONL ledgers, WAL files | must-NOT-copy | |
| `APP_GOOGLE_TOKEN_KEY` from another tenant | must-NOT-copy | Cipher is unreadable with the wrong key. |
| Custom skills (HAR / Shopify / render) | must-NOT-copy | |
| 34dev identity people and sheet ids | must-NOT-copy | Slice 5 import only, later. |
| Live `_shared/34dev-provision.json` | must-NOT-copy | Import input later, not client one's seed. |
| `testco-inject` | must-NOT-copy | Optional scratch tool; not production. |
| Forbidden-term list targeting 34dev | must-NOT-copy into production preflight as the *only* check | Production forbids leftover **testco** / fixture terms; 34dev terms stay forbidden unless this *is* the 34dev import. |

---

## Where testco's slug goes when a real tenant takes prominence

Recommend, do not decide. The cloudflared comment already encodes two layers: bare names for "the tenant at the bare hostnames" (today: testco), `{slug}.*` for further tenants.

**Option A — client one takes `{slug}.app.yamin.io`; testco keeps the bare names.** Tester bookmarks, Bao's origin, and the currently registered Google redirect URI (`https://app.yamin.io/oauth/callback`) stay valid. Client one's OAuth URI is a new Console entry. Marketing copy that says `app.yamin.io` still points at the rehearsal tenant.

**Option B — testco moves to `testco.app.yamin.io` (and `testco.gateway` / `testco.hooks`); client one takes the bare names.** Matches "the product lives at app.yamin.io". Cost: Google Console redirect URI swap, `APP_PUBLIC_ORIGIN` on testco-app, cookie host, Bao/external-tester docs, and a tunnel/DNS cutover. Testco loopback `:8710` is unchanged.

Recommendation to carry into the ops PR, not a decision: **B when client one is the first paying tenant and will be demoed at the product hostname; A if testco must remain the public rehearsal without a Console change.** Slice 2's scratch `clientzero` uses `{slug}.*` (or loopback-only) so it does not steal the bare names.

34dev stays on quick tunnels until a human decision (slice 5).

---

## Slice 0 — Canonical skills origin (prerequisite)

**PR / ops:** a bare git origin the provisioner can clone. Not a tenant stamp. Not a `cp` of either working tree onto a third.

The two `openclaw-imports` checkouts are separate histories with **no shared remote**. Testco carries the slice-6 google_creds cutover and the actor fix; live does not. Cloning from either working tree (or `cp`) is how a new tenant inherits the wrong HEAD, or a dirty worktree, with no pin to name.

Specify:

1. Create a bare canonical origin, e.g. `/root/git/openclaw-imports.git`.
2. Seed it from the **TESTCO** repo (`/opt/testco/skills/openclaw-imports`), not live.
3. Add that origin as a remote on **both** working trees (testco and `/root/.hermes/skills/openclaw-imports`).
4. Record live's divergence as a named branch on the origin (e.g. `live-34dev` at live's HEAD). Do not rewrite live's history. Do not merge that branch into the provisioner pin.
5. The provisioner clones **only** from the bare origin at a **pinned SHA** (manifest field, or the SHA recorded next to the loadout). Never `--branch` floating `main` of a working tree.

Until the bare origin exists and both working trees have the remote, **slice 2 step 2 is blocked**. A provisioner dry-run that would clone from `/opt/testco/skills` or `/root/.hermes/skills` is a failed verify, not a workaround.

This slice does not restart testco or live. Adding a git remote is not a send-path change. Pushing live's tree as a named branch is the record; it is not the tenant loadout.

### Verify (slice 0)

- `git --git-dir=/root/git/openclaw-imports.git rev-parse HEAD` equals testco's skills HEAD (seed).
- `git remote -v` on both working trees lists the bare origin.
- `git --git-dir=/root/git/openclaw-imports.git branch` shows the live-divergence branch; its tip matches live's skills HEAD.
- A clone of the origin at the pinned SHA has the slice-6 `google_creds` path and does **not** contain live-only custom skill dirs unless that SHA is the live-divergence branch (the provisioner pin must not be that branch).

---

## Slice 1 — Tenant manifest + knobs into `tenant_config`

**PR:** one manifest schema (file per tenant, e.g. `docs/tenants/{slug}.yaml` or next to the provisioner), plus the code change that makes every tenant-preference knob read through `tenant_config` with **announcing** fallbacks. No provisioner yet. No schema migration unless a knob cannot live in `runtime.json` — if it cannot, stop and ask.

### Manifest fields (minimum)

- `slug` (filesystem + systemd prefix + hostname label)
- `display_name`, `business_description`, `timezone`
- `ports`: `app`, `gateway`, `hooks`
- `public_origin` (feeds `APP_PUBLIC_ORIGIN` in the drop-in, not `runtime.json`)
- `sheet_id` (real; empty allowed)
- `boundary_domains` / outbound suffix policy (scratch vs production)
- `skill_loadout` (name of a loadout in slice 4, plus optional enables)
- `skills_origin_sha` (pin; provisioner clones the slice 0 bare origin at this SHA only)
- `curator_packs`: `omit` | `defaults` (Hermes curator packs under `/opt/{slug}/skills`; omit until a tenant needs them)
- digest / cron knobs listed below
- `writes_posture`: `blocked` | `captured` (testco/scratch) | `live` (production after cutover)
- `venv`: `shared` | `per-tenant` (per-tenant is human-yes)

Secrets stay out of the manifest: session secret, passphrase, Fernet key, API keys. The provisioner generates or prompts.

Existing `client-config.template.json` covers identity/runtime skill slots. The new manifest is the *ops* file the provisioner reads; it may include or generate that JSON. Do not fork two sources for timezone or sheet id.

### Knobs that are tenant preferences — enumerate and move

`tenant_config` is already the single read path (`AGENTS.md`). `get`/`require`/`enabled` raise rather than inventing tenant literals **except** where callers pass a default. Those defaults are silent today. Slice 1 makes every fallback **announce** (log + counter). A fallback that does not announce is a false green.

#### Already in `runtime.json` / `tenant_config` (keep; announce if missing)

| Knob | Testco value | Read site |
|------|----------------|-----------|
| `client.timezone` | America/Chicago | digest_tick, sweeps, tasks_store, metrics, inject, feed |
| `client.display_name` / `business_description` | Testco Realty / fictional brokerage | email-router prompts |
| `skills.task-manager.digest_times_local` | `["05:00","15:00"]` | `digest_tick.py` (code fallback `["05:00"]` — silent today) |
| `skills.task-manager.digest_lookback_days` | **absent** → 30 | `generate_digest.py` `timedelta(days=30)` window |
| `skills.task-manager.digest_cutoff_hours` | **absent** → 48 | `generate_digest.py` `timedelta(hours=48)` cutoff |
| `skills.task-manager.digest_transport` | **absent** → `gmail_api` | `generate_digest.py` |
| `skills.task-manager.default_assignee` | `""` | task-manager |
| `skills.sweeps.stale_email_hours` | 24 | stale_email.py |
| `skills.sweeps.email_grace_days` | 2 | stale_email.py |
| `skills.sweeps.task_quiet_hours` | 24 | task_followup.py |
| `skills.sweeps.task_escalate_after` | 3 | task_followup.py |
| `skills.sweeps.lead_quiet_days` | 3 | quiet_lead.py |
| `skills.sweeps.hot_lead_quiet_days` | 1 | quiet_lead.py |
| `skills.sweeps.lead_nudge_backoff_days` | 3 | quiet_lead.py |
| `skills.sweeps.min_interval_minutes` | 30 | sweep_tick.py |
| `skills.sweeps.lead_revival_hours` | **absent** → 48 | quiet_lead.py / monthly_report.py |
| `skills.email-router.lookback_days` | 7 | fetch_candidates.py |
| `skills.email-router.recording_subject_prefix` | `PLAUD:` | email-router (fixture-shaped) |
| `skills.email-router.deal_channel_prefix` | `deals-` | handle_deal_intel.py |
| `skills.email-monitor.lookback_days` | 14 | email-monitor |
| `skills.gmail-push-trigger.webhook_port` | 18790 | webhook_listener.py, setup_pubsub.py, tenant_config docstring |
| `skills.gmail-push-trigger.mailbox` / `gcp_project` / topics / `tunnel_hostname` | empty | gmail-push-trigger |
| `skills.cost-guard.spend_warn_usd` / `spend_pause_usd` | 20 / 30 | runtime.json **and** `check_daily_spend.py` env fallbacks (dual source; script does not read `tc`) |
| `skills.gmail-push-trigger.watchdog_lookback_hours` | **absent** → 2 | `email_watchdog.py` `LOOKBACK_HOURS = 2` |
| `skills.showingsmart-autoreply.past_window_hours` / `default_duration_hours` | **absent** → 24 / 1 | `showing_parse.py` |
| `skills.crm-weekly-brief.local_time` | **absent** → Monday 08:00 CT in SKILL.md | no testco systemd timer; schedule is documentation-only until sliced |
| `skills.metrics.retention_days` | 180 | `app/metrics.py` (`DEFAULT_RETENTION_DAYS = 180`) |
| `skills.leads.source` / `skills.tasks.source` | store | app + skills; **also shadowed by** `JARVIS_LEADS_SOURCE` env |
| `skills.leads.export_enabled` / `export_sheet_id` | false / empty | leads_export.py |
| `skills.lead-reply.sheet_id` / `exclude_tabs` | empty / SEND LOG, OLD LEADS | leads_import.py |
| `skills.showingsmart-autoreply.*` | fixture sender + FIXTURE sheet | showingsmart |
| `skills.google-calendar.default_calendar_id` | empty | create_calendar_event.py |
| `skills.email-to-calendar.calendar_id` / `always_attendee` | empty | email-to-calendar |
| `skills.meeting-intake.subject_prefix` / `max_transcript_chars` | `Meeting:` / 12000 | meeting-intake, gmail poll |
| `skills.meeting-briefing.owner_person` | empty | meeting-briefing |
| `skills.owner-email-voice.voice_corpus_path` | empty | owner-email-voice |
| `app.feed.team_destinations` | ops, deals | `app/feed.py` |
| `safety.outbound_recipient_suffix` | `.example`, `example.com` | outbound_boundary |
| `safety.allow_writes` | true | documented; **runtime kill switch is `JARVIS_ALLOW_WRITES`** (`write_guard.py`) |
| `gateway.base_url` | 8642 (stale) | several skills; overridden by `HERMES_API_URL` when set |
| `paths.app_root` | `/opt/testco/jarvis-app` | many skills; **code fallback `/root/jarvis-app` is live-shaped and must announce** |

#### In systemd / timers today — not in `tenant_config`

These are tenant preferences pretending to be unit files. Move the *values* into the manifest → `runtime.json` (or a `schedule` object the provisioner templates into units). The timer still has to exist (systemd cannot read JSON), but the numbers have one source.

| Knob | Testco value | Why it is a preference |
|------|----------------|------------------------|
| `schedule.digest_tick_seconds` | 300 | How often the digest *asks*; slots are `digest_times_local` |
| `schedule.sweep_tick_seconds` | 1800 | How often each sweep asks; `min_interval_minutes` is the real gate |
| `schedule.metrics_calendar` | `*-*-* 08:30:00` | When the monthly numbers post is *considered* |
| `schedule.digest_on_boot_sec` | 120 | Stagger; keep as infra unless a tenant needs a quiet boot |
| `schedule.sweep_on_boot_sec` | 180 / 240 / 300 | Stagger between the three sweeps |
| `schedule.digest_accuracy_sec` | 30 | systemd AccuracySec |
| `schedule.sweep_accuracy_sec` | 60 | |
| `schedule.metrics_accuracy` | 5m | |

Live-only (not on testco; still a knob if a tenant enables the poller): Gmail history poller `OnUnitActiveSec=60`. Put `skills.gmail-push-trigger.poll_seconds` in tenant_config before any new tenant gets a poller unit.

Hermes-cron (not systemd, lives under `HERMES_HOME/cron` / cost-guard SKILL.md): spend check every 15m/30m/2h; prune `0 8 * * *` UTC. These are tenant-preference cadences with no `tenant_config` key today. `poll_gmail_history.py` and several SKILL.md files still hardcode **America/Chicago** instead of `client.timezone` — same slice: read `tc`, announce if missing.

#### Dual sources to collapse (announce on mismatch)

| Pair | Hazard |
|------|--------|
| `HERMES_API_URL` vs `gateway.base_url` | Testco skills can still call :8642 |
| `APP_PORT` vs nothing in runtime | Correct as process env; do not add a second port in JSON |
| `JARVIS_LEADS_SOURCE` vs `skills.leads.source` | Env wins; provisioner must set both the same |
| `skills.gmail-push-trigger.webhook_port` 18790 vs cloudflared 18791 | Hooks miss the listener |
| `paths.tasks_db` (skill-local) vs `JARVIS_TASKS_DB` (HERMES_HOME/state) | Two task databases |
| `credentials.*` paths under `~/.hermes` vs `/opt/{slug}/hermes` | Wrong tenant's tokens |
| `skills.cost-guard.*` vs `check_daily_spend.py` env WARN/PAUSE | Two spend thresholds |
| `APP_PUBLIC_ORIGIN` vs hostname in runtime.json | Auth-connect plan forbids putting hostname in runtime.json — keep that |

#### Silent code defaults that must announce

If the key is missing, log + bump a counter named for the key, then use the tenant-neutral default. Never use `/root/jarvis-app` or port 8642 as a "neutral" default — those are live 34dev. Neutral defaults are: timezone UTC (and announce), digest `["05:00"]` (announce), sweep hour/day counts as today, webhook port **unset → refuse to bind** rather than 18790, `paths.app_root` **require**, `gateway.base_url` **require** unless `HERMES_API_URL` is set.

### Verify (slice 1)

1. Grep the skills tree and `app/` for remaining time/cadence literals (`05:00`, `08:30`, `1800`, `lookback_days`, `stale_email_hours`, `18790`, `/root/jarvis-app`, `LOOKBACK_HOURS`, `America/Chicago`, `timedelta(days=30)`, `Monday`). Every remaining literal is either in the knob table or a bug.
2. Boxed unit tests: missing `client.timezone` announces and falls back; missing `digest_times_local` announces; missing `paths.app_root` refuses (no live-path fallback).
3. Presence canary for `lead_revival_hours`: a runtime with the key omitted still runs and the announcing-fallback counter fires (today the code silently uses 48). **Conditional:** run against testco only after that announcing-fallback change is deployed there; otherwise run it boxed (in-process, `JARVIS_RUNTIME_CONFIG` pointed at a fixture that omits the key). Absence of a counter on undeployed testco is not a failed canary.
4. No testco restart unless the human yes's a code deploy of the announcing-fallback change onto 8710. The code PR can land on main and wait.

---

## Slice 2 — `provision_tenant` stamps `/opt/{slug}/`

**PR:** `scripts/provision_tenant.py` (or `.sh` wrapping Python) + systemd unit templates + production/scratch preflight templates. Calls existing `provision_client.py` for identity/runtime. Reads the slice 4 loadout. Does not edit live units. Does not `pip install`. **Blocked on slice 0** for the skills clone (step 2).

### What it does

1. Read the manifest. Refuse reserved slugs (`testco`, `root`, a path escape). Collision checks are **structural**, not `ss` alone: refuse if `/opt/{slug}` already exists, if any `{slug}-*.service` unit file is installed, or if any of the manifest ports appear in an installed unit's `Environment=` (`APP_PORT`, `HERMES_API_URL`, webhook port). `ss` only sees listeners; a disabled unit or a stopped scratch leftover would pass `ss` and still collide.
2. **Requires slice 0.** `mkdir /opt/{slug}` with `jarvis-app` as a git clone/worktree of this repo at the manifest SHA; `skills/openclaw-imports` cloned **only** from the bare origin at the pinned SHA, then sparse to the loadout (not `cp` from testco or live). Hermes curator packs: `curator_packs` from the manifest (`omit` or hermes-agent `defaults`) — never `cp -a /opt/testco/skills`. If the bare origin is missing, stop; do not fall back to a working tree.
3. Symlink `hermes/skills` → `/opt/{slug}/skills`.
4. Empty `hermes/state`, `hermes/credentials`, `hermes/workspace`, attachments dir. No SQLite files. Do not copy `/opt/testco/jarvis-app/.venv`.
5. Generate `HERMES_HOME/.env` (fresh `APP_SESSION_SECRET`, `APP_PASSPHRASE`, `API_SERVER_KEY`, operator passphrase keys) and a systemd drop-in with a **fresh** `APP_GOOGLE_TOKEN_KEY`. Print the owner passphrase once to the operator tty, not to a file in the tree.
6. Run `provision_client.py` so identity.json / runtime.json match the manifest (ports, paths, timezone, sheet id, boundary, loadout enables — **an `enabled` key for every shipped skill**). Keep provision_client's refusal of `allow_writes: true`. `gateway.base_url` **equals** `HERMES_API_URL`. `webhook_port` **equals** the hooks port and the cloudflared target.
7. Write systemd units from templates (`{slug}-app`, `{slug}-gateway`, digest/sweep/metrics). ExecStart uses the chosen venv. ExecStartPre is the templated preflight. **`HOME=/opt/{slug}`** on every unit (testco's `HOME=/root` plus an accidentally-missing `HERMES_HOME` makes Hermes fall back to `/root/.hermes` — live's tree). Preflight asserts `HERMES_HOME` is set **and** lies inside `/opt/{slug}` before start; a missing or live-shaped `HERMES_HOME` is a refused boot, not a default. Scratch: RFC 2606 boundary + optional `ALLOW_WRITES=1` + `APP_COOKIE_SECURE=0` (loopback HTTP). Production: `ALLOW_WRITES` unset, no sheet fixture, no mock, `APP_COOKIE_SECURE=1`.
8. Print (do not silently run, unless `--apply-ingress` with an explicit flag) the three cloudflared ingress snippets and:

   `cloudflared tunnel route dns yamin {slug}.app.yamin.io`  
   `cloudflared tunnel route dns yamin {slug}.gateway.yamin.io`  
   `cloudflared tunnel route dns yamin {slug}.hooks.yamin.io`

   Plus the Google Console reminder for `{origin}/oauth/callback`.
9. `systemctl daemon-reload` + enable --now the two long-running units (timers optional until knobs land).
10. `--teardown {slug}` disables units, removes `/etc/systemd/system/{slug}-*`, removes `/opt/{slug}`, prints the DNS unroute commands. Refuses slug `testco`. Refuses anything under `/root`.

Venv: default `shared` with a loud log line that a pip install hits live. Do not create a second venv in this slice.

Gateway `--force`: detect another `gateway run` on the host (today: user unit `hermes-gateway.service` on 8642 **and** `testco-gateway` on 8652); set `--force` if so.

### Verify — scratch tenant `clientzero`

Unused ports at inventory: **8720 / 8662 / 18792**. Re-check structural collision (tree, unit files, Environment ports) **and** `ss` before starting. Slice 0 origin must exist.

1. Manifest for `clientzero`: RFC 2606 boundary, writes captured or blocked, no sheet fixture, loopback origin `http://127.0.0.1:8720`, `APP_COOKIE_SECURE=0` (no cloudflared required for this verify; print the ingress commands and assert they are correct in the script output).
2. Both services `active`. Preflight did not fail. Units show `HOME=/opt/clientzero` and `HERMES_HOME=/opt/clientzero/hermes`. A canary: preflight with `HERMES_HOME` unset (or set to `/root/.hermes`) refuses start.
3. `GET http://127.0.0.1:8720/api/health` and `/api/health/summary` answer. Ledger: empty DBs exist after first request (lazy migrate) — show `ls` of `jarvis_accounts.db` etc.
4. Login works via PIN: seed one owner with `accounts_cli`, `POST /api/login`, cookie works on `/api/me` (or equivalent). Do not copy `SEEDED_PINS.json`. `APP_COOKIE_SECURE=1` on this loopback origin is a failed verify, not a production-hardening win.
5. Tenant-agnostic verify battery against `AUTH_BASE=http://127.0.0.1:8720` with env `HERMES_HOME=/opt/clientzero/hermes` and the matching `JARVIS_*` paths. **In:** health/ping, PIN login, people list (names only), boxed store tests that take a path. **Out:** anything hardcoded to `/opt/testco`, `dana@testco.example.com`, `SEEDED_PINS.json`, `verifyTestco*.mjs`, `verify_boundary.py` (unless clientzero uses the same reserved suffixes — then it may run with `HERMES_HOME` pointed at clientzero). **Out:** scripts that restart `jarvis-app.service` or talk to `:8700`.
6. Teardown: units gone, `/opt/clientzero` gone, `ss` shows 8720/8662 closed, **and** a second provision of the same slug would pass the structural checks (no leftover `{slug}-*.service`, no `/opt/clientzero`). Testco `:8710` still healthy (health + a PIN login on dana's testco account — do not disturb Bao). Live `:8700` untouched.

Do not enable clientzero on the yamin tunnel during scratch verify unless a human wants a public name.

---

## Slice 3 — Production-hardening delta vs testco

**PR:** docs + templates the provisioner uses for `writes_posture: live` or `blocked`. No toggle of testco's `ALLOW_WRITES`.

### Writes-guard posture (document, then template)

Testco: `JARVIS_ALLOW_WRITES=1` + RFC 2606 capture. That is a **tester** posture. Copying it to client one is how a rehearsal becomes a second writer on a real mailbox.

Production template:

- Default **writes-guard ON** (`JARVIS_ALLOW_WRITES` unset/0). Reads work. Sends raise `WritesDisabled` loudly.
- Cutover (human checklist, not the provisioner): prior system disabled, one writer, then set `JARVIS_ALLOW_WRITES=1` **without** a capture suffix. Preflight on a production unit **refuses** the testco combination (writes armed + reserved suffix) so a cloned testco unit cannot be renamed into "production."
- `safety.allow_writes` in runtime.json stays in sync with the env; `write_guard` reads the env. Dual source: provisioner writes both.

"Writes-guard OFF" in production means the tenant is the sole sender, after that checklist — not "like testco."

### Real-Google drop-in template

Two postures. Production HTTPS:

- `APP_PUBLIC_ORIGIN=https://{host}`
- `APP_COOKIE_SECURE=1`
- `APP_GOOGLE_TOKEN_KEY=` (generated)
- `YAMIN_OAUTH_CLIENT=` (shared client JSON path is allowed)
- `OAUTHLIB_RELAX_TOKEN_SCOPE=1`

**Scratch / loopback HTTP** (`http://127.0.0.1:{app_port}`): same keys except `APP_COOKIE_SECURE=0` and `APP_PUBLIC_ORIGIN` is the loopback origin. Secure cookies are not sent on HTTP; PIN-login verify against clientzero fails by construction if the scratch template copies production's `=1`. Production stays `=1`.

Absent in both: `APP_GOOGLE_OAUTH_MOCK`, `JARVIS_SHEET_FIXTURE`. Scratch may use loopback origin; production must not.

PKCE verifier-in-state-cookie and app INFO logging to journald are already on main (`a8ae9bf`); new tenants inherit via the jarvis-app clone.

### WAL-inclusive backups

Scheduled (systemd timer per tenant or one host timer that iterates `/opt/*/hermes`):

- `sqlite3` backup or `.backup` / `VACUUM INTO` **after** a WAL checkpoint, for `jarvis_accounts.db`, `jarvis_leads.db`, `jarvis_tasks.db`, `jarvis_threads.db`, `sweeps.db`, and optionally `state.db` / `kanban.db`.
- Copy JSONL (`activity_feed`, approvals) as files.
- Destination and retention are manifest knobs. Do not write backups into the tenant tree that the app serves.
- Prove restore on `clientzero` before claiming the timer works: backup, teardown-adjacent copy, open the restored DB, row count matches.

Testco has none of this. Live has none of this.

### Log rotation

- journald: `SystemMaxUse` / per-unit `LogRateLimit` in a drop-in — host-level, human-yes if it changes global journald.
- JSONL: rotate `activity_feed.jsonl` (and approvals) by size/age; the app must open by path so rotation is copytruncate or a reopen signal. If reopen is a code change, that is a separate PR with a human yes.

### Monitoring beyond `/api/health`

Ship as a checklist in the template, not a new SaaS:

- `GET /api/health` and `/api/health/summary` on the tenant origin (external probe later).
- `state/gateway.heartbeat` age.
- journald `is-failed` for `{slug}-app` and `{slug}-gateway`.
- cost-guard pause counter.
- sweep/digest feed heartbeats (absence of a heartbeat after 2× tick is the alert, not a green empty feed).

No obligation to install Prometheus in this slice.

### Verify (slice 3)

Templates exist and `provision_tenant --print-units --posture production` shows: no `ALLOW_WRITES=1`, no sheet fixture, no mock, oauth keys present including `OAUTHLIB_RELAX_TOKEN_SCOPE`, `APP_COOKIE_SECURE=1`, `HOME=/opt/{slug}`, backup timer documented. `--posture scratch` (or clientzero) shows `APP_COOKIE_SECURE=0`. Restore drill on clientzero SQLite. Testco units unchanged (`systemctl show testco-app -p Environment` still has the tester posture).

---

## Slice 4 — Skills base loadout the provisioner reads

**PR:** a machine-readable loadout next to `SKILLS_MANIFEST.md` (e.g. `skills/openclaw-imports/LOADOUTS.yaml`) that the provisioner reads. Human-readable SKILLS_MANIFEST stays the comment. Do not install custom skills "disabled" — omit the directories.

### `base` (every new tenant)

Core sixteen from the manifest, plus `_shared`. Directories cloned. `runtime.json` `enabled: true` for core.

### `base+optional` (named extras)

crm-contact-suggest, crm-weekly-brief, discord-recording-handler, showingsmart-autoreply, meeting-intake, read-pdf, excel-xlsx, create-presentation. Default off unless the tenant manifest lists them.

### Excluded always (unless slice 5 import of 34dev explicitly names them)

har-deal-tracker, shopify-stock-monitor, jarvis-render, gbrain-memory.

### Testco loadout (not the production default)

Testco today enables several optionals (showingsmart, meeting-intake, recording-handler) and keeps custom stubs in JSON disabled. Production `base` does not enable showingsmart until the vendor and a real sheet tab exist.

The loadout (slice 4) must write an `enabled` key for every shipped skill so a missing key is not a silent `enabled() → False` (testco runtime omits several core dirs). Naming debt to fix in this slice or call out: runtime key `recording-handler` vs directory `discord-recording-handler`; `skills.crm` vs directories `crm-contact-suggest` / `crm-weekly-brief`.

### Verify (slice 4)

Provisioner dry-run for `clientzero` with loadout `base`: tree listing contains every core dir, contains `_shared`, does **not** contain `har-deal-tracker`, `shopify-stock-monitor`, `jarvis-render`, `gbrain-memory`. Runtime.json has no `enabled: true` for those. A second dry-run with `showingsmart-autoreply` on includes that dir only.

---

## Slice 5 — Migration seam for an existing operation (design only)

**No implementation.** The manifest and provisioner must not preclude onboarding an existing operation later. The known future case is live **34dev**.

### What an import would carry

| Kind | Examples |
|------|----------|
| App DBs | `jarvis_accounts.db`, `jarvis_leads.db`, `jarvis_tasks.db`, `jarvis_threads.db` (+ WAL/shm) |
| Sweep / digest watermarks | `sweeps.db`, `digest_last_run.json`, `metrics_monthly_last.json` |
| Ledgers | `activity_feed.jsonl`, `approvals.jsonl`, `approvals_decisions.jsonl` |
| Identity / runtime | `identity.json` (people keys, aliases, real domains, assistant address), `runtime.json` (real sheet ids, calendar ids, skill configs), `channels.json`, live `_shared/34dev-provision.json` as the reconstructed input |
| Google | `google_connection` cipher **and that tenant's `APP_GOOGLE_TOKEN_KEY`**; optional leftover `google_token.json` / SA — do not mix keys |
| Hermes | `config.yaml`, `auth.json` (provider login), `SOUL.md`; **not** a naive copy of `kanban.db` / `state.db` without a gateway-stopped copy |
| External wiring | Discord/Slack tokens and guild ids, Zapier destinations, dedicated mailbox / assistant_address, Pub/Sub project + topic, cloudflared-jarvis quick-tunnel hostnames until moved onto `{slug}.*` or the bare names |
| Skills | 34dev tree includes custom skills; import may keep them **for that tenant only**, still excluded from `base` |

### Where it slots in the flow

```
manifest → stamp empty /opt/{slug}/ tree → (IMPORT SEAM) → generate secrets
    if they were not imported → systemd → preflight → first start
```

The reserved hook is `provision_tenant.py --import-from <HERMES_HOME>` **after** the empty tree exists and **before** the first start, so lazy migrations do not create empty DBs that then collide with copied ones. Import copies DBs with a WAL checkpoint, rewrites paths in runtime.json to `/opt/{slug}`, does not start units until a human diffs identity people and sheet ids.

Do not build this. Do not copy `/root/.hermes` in slice 2. Refuse `--import-from` if the flag is absent (the CLI may accept the flag and exit with "reserved").

Live 34dev stays on its quick tunnels until a human decision (same sentence as the cloudflared comment).

---

## Order, gates, and what "done" means

| Slice | Depends on | Human-yes if |
|-------|------------|----------------|
| 0 Canonical skills origin | — | creating `/root/git/openclaw-imports.git`; adding remotes on live and testco working trees |
| 1 Manifest + announcing knobs | — | any schema change; deploying the fallback change onto testco |
| 4 Loadout file | — | can land with 1 |
| 2 Provisioner + clientzero | **0**, 1, 4 | shared-venv change; applying cloudflared; enabling a public hostname. Step 2 blocked until 0 exists. |
| 3 Production templates + backups | 2 | host journald policy; backup destination |
| 5 Import seam | reserved | entire 34dev move |

Definition of done for the *procedure*: clientzero provisioned, health + PIN login, tenant-agnostic verifies, teardown, testco and live undisturbed. Client one is a later run of the same provisioner with a real manifest — not a special case in code.

No code, no runtime changes, and no merge accompany this document.
