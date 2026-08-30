# Testco dress rehearsal — first full golden-image cycle
2026-08-20 · same-host clone · live box untouched (asserted, see §6)

> **FOLLOW-UP, 2026-08-21 — the clone is no longer empty.** It has been seeded with a synthetic
> working week for the tester: 40 leads, 90 feed events, 4 deal channels, 3 pending approvals,
> and a lead-sheet copy fixture. See **SEEDED-WORKLOAD.md**.
>
> `verifyTestco.mjs` in this directory asserts a FRESH tenant ("feed is empty", "leads empty",
> "only the seeded direct line exists") and is now expected to fail against the seeded clone.
> That is correct, not a regression — it is the provisioning gate and still the right check
> immediately after a purge+provision. The seeded-state gate is `seed/verifyTestcoSeeded.mjs`,
> which keeps the leak checks and replaces the emptiness assertions with workload ones.

## 1. Timings — the runbook's backbone
| Step | Duration | Manual intervention |
|---|---|---|
| Clone tree (rsync, 3 paths) | **5s** | none |
| Purge clone + verify green | **1s** | create the marker file the guard demands |
| Author testco config | ~2 min | **all of it** — no wizard exists yet (that is W2) |
| Provision (dry-run → execute) | **<1s** | 2 config errors to fix first, see §2 |
| Mint per-tenant secrets | **<1s** | **all of it** — see §3, this is a real gap |
| Boot gateway | **3s** once configured | 2 failed attempts first, see §2 |
| Boot app | **4s** | none |
| Acceptance (login/feed/chat/DOM) | ~1 min | none |
| **Total wall clock** | **~25 min** | dominated by diagnosis, not by the machinery |

The machinery itself is fast: a clone-purge-provision-boot cycle is well under a minute
of actual work. Everything else was finding out what nobody had run end to end before.

## 2. Surprises — five, in the order they bit

**S1. The provisioner could not provision an app-native tenant. (BLOCKER, fixed)**
`REQUIRED_WHEN_ENABLED["lead-reply"] = ["sheet_id", "lead_id_prefix"]` — so a config whose
only sin was having no Google Sheet was rejected. Workstream 1 made app-native storage the
source of truth and the spec's acceptance criterion is literally *"a tenant with ZERO Google
Sheets runs a full lead lifecycle entirely in-app"*. That tenant was unprovisionable.
Worse, `lead_id_prefix` no longer exists anywhere — W1 step 1 removed it from template and
runtime (prefixes are per-agent and derived) and I did not grep for consumers. The
provisioner required a field the product had deleted.
FIX: new `CONDITIONAL_REQUIRED` — sheet_id is required only when
`skills.leads.source == "sheet"`; the lead_id_prefix requirement is gone.

**S2. THE BIG ONE — cross-tenant data disclosure. (fixed)**
The freshly provisioned testco app rendered **34dev's real activity feed**: 50 events of a
live customer's email classifications and tasks, on a brand-new tenant's first screen.
CAUSE: the two halves of the product disagree about where "home" is. `jarvis-app` honours
`$HERMES_HOME`; the skills tree hardcoded `Path.home()/".hermes"` — `notify.DEFAULT_FEED_PATH`
and `jarvis_runtime`'s env/skills/credentials paths. Booting a tenant with HERMES_HOME set
therefore ran an app whose *reads* pointed at the other tenant's files.
Nothing was corrupted — the read is read-only — but this is a DISCLOSURE, and it is the exact
"isolating the database is not isolating the side effects" pattern, in production code rather
than a harness.
FIX: `jarvis_runtime.hermes_home()` honours `$HERMES_HOME`; `notify.feed_path()` resolves
through it at call time. On a single-tenant droplet this resolves exactly as before.
>>> This is why the rehearsal existed. No unit test would have found it: every component was
    correct in isolation, and the bug lived in the disagreement between them.

**S3. A second gateway refuses to start.** `hermes gateway run` detects the running systemd
service and blocks, citing concurrent writes to a shared kanban DB. Justified before forcing:
a different `HERMES_HOME` means a different state dir, so the stated hazard cannot occur.
Then `--force`. On a real droplet there is no first gateway and this never arises.

**S4. The gateway port is config-file-driven, not env-driven.** `HERMES_API_PORT` was my
invention; the real knob is `platforms.api_server.port` in config.yaml — a block that did not
exist in the file at all (the 8642 default is implicit). Had to append it.

**S5. Four of fifteen core-tier skills have no config block in the template** — read-email,
owner-email, call-action-items, universal-bridge. They need no settings, so `enabled` cannot
be expressed for them, and `tenant_config.enabled()` returns False for unknown skills. The
manifest says core/on; the config cannot say it. **Matters to W2**, whose wizard uses the
manifest as its menu: a tenant toggling "read-email" has nowhere for that toggle to land.
NOT fixed — it is a template/manifest reconciliation, and W2 should decide the shape.

## 3. Manual interventions required (each one is a runbook step, or a wizard feature)
1. Author the tenant config by hand — **W2's entire reason to exist.**
2. Create the snapshot-prep marker before purging.
3. **Mint per-tenant secrets.** `.env` is deliberately excluded from the image (it holds
   APP_PASSPHRASE, APP_SESSION_SECRET, API_SERVER_KEY). Provisioning does NOT generate them,
   so a fresh droplet boots with no way to log in. **Recommend: provision_client.py should
   mint APP_PASSPHRASE + APP_SESSION_SECRET and print the passphrase once.**
4. Set the gateway port (same-host only).
5. Everything on the provisioner's own 8-item manual list: bot invites, Google OAuth, SMTP
   password, Gmail push, cron, voice corpus, smoke test.

## 4. What the image contains — see 01-image-manifest.md
Headline decision, made explicitly: **kokoro weights EXCLUDED** (429 MB, ~40% of the image,
for a capability that is off). Clone is 495 MB purged.

## 5. Same-host clone vs a real DigitalOcean snapshot → new droplet
| | This rehearsal | Real snapshot flow |
|---|---|---|
| Paths | `--root <clone>` indirection | real paths; purge runs with NO `--root`, gated on hostname + `/etc/jarvis-snapshot-prep` |
| Ports | +10 offset to dodge the live stack | unchanged 8642/8700 — different machine |
| Gateway | needs `--force` past the running service | only one gateway; starts normally under systemd |
| HERMES_HOME | set explicitly, which is what EXPOSED S2 | default `~/.hermes` — **S2 would have stayed hidden** |
| Secrets | minted by hand into the clone | same gap, but fatal: nobody can log in until someone SSHes in |
| systemd | processes run from a shell | units enabled at boot; 8.3's boot-survival work applies |
| Tunnel | none, loopback only | needs the named tunnel + `{slug}` hostname (8.2, still open) |
| Verification | curl + Playwright against loopback | same, over the tenant's real hostname |
>>> The most important row is HERMES_HOME. The rehearsal's own artificiality is what found the
    disclosure bug; a faithful snapshot test would have passed while the defect sat there.

## 6. Live box, before → after
feed 82 → 82 · tasks 592 → 592 · threads 5 → 5 · writes_allowed False → False ·
identity 34dev → 34dev · app healthy · one gateway on 8642. Clone services stopped;
artifacts left at scratchpad/testco-clone and scratchpad/rehearsal for inspection.
