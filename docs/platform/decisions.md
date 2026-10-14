# Decisions and open questions

This is Section 15 of [docs/SPEC.md](../SPEC.md), kept as a working log. Date every entry. Evidence for the current state is in [GAP-ANALYSIS.md](GAP-ANALYSIS.md).

## Decided 2026-10-13 (human decisions)

These resolve the five architecture forks below. Each is recorded as the human stated it. A decision says which way to go; it does not approve the work. Each dependency, migration, send-path change, and `AGENTS.md` edit it implies still needs its own explicit human yes (`AGENTS.md` rule 6).

1. **Runtime: one runtime per user (Docker), as the spec says.** Resolves fork 1 (option A).
2. **Storage: Postgres for new platform tables only:** users, roles, approvals, grants, policies, audit log. Existing per-tenant SQLite stores are **not** migrated now; they stay as they are until the Phase 11 migration. Resolves fork 2 (a scoped option A).
3. **Credentials: the first step, before anything else, is to remove the service-account key and plaintext `google_token.json` fallbacks** (`_shared/google_creds.py` in the skills origin, and `GOOGLE_TOKEN_PATH` in `app/config.py`). The spec's Phase 3 item adds that the remaining no-connection failure must be loud (`AGENTS.md`: a fallback that does not announce itself is a false green). Resolves fork 3's first step. **End state: assumed per spec 9.6 (surrogate tokens at egress). The human did not state it explicitly; needs human confirmation.**
4. **Roles: adopt `owner/admin/agent/assistant`**, and keep `operator` as break-glass with the never-bodies rule. The accounts migration and the `AGENTS.md` update are required later, and each needs its own human yes. Resolves fork 4 (option A).
5. **Live: onboard Eddie, Blake, and Martin first, in shadow mode, on the new layout.** Spencer's live setup stays where it is and keeps getting bug fixes. Spencer cuts over in Phase 11 to the provisioned layout, keeping `/root/jarvis-app` and `/root/.hermes` intact for at least two weeks as the rollback path. Resolves fork 5 (option B, then a supervised cutover) and open question 1.

Where the spec contradicted decisions 2 and 5, notes were added in place in `docs/SPEC.md` (Section 6 header, Phase 0 storage note, Phase 11, Section 15). The sections were not rewritten.

## Decided 2026-10-13: testco retired

**Decision (human):** Bao no longer tests on testco, so retire it instead of patching it. Approved: stop and disable its services and tunnel, archive `/opt/testco` to a root-only tarball kept 30 days, remove the world-readable token drop-in, repoint the skills repo's origin to GitHub, move the verify sandboxes off testco, and update `AGENTS.md`, `.cursor/rules/jarvis.mdc` and the `verify-jarvis` skill.

**Why:** testco's `oauth.conf` drop-in was mode 644 and `systemctl show` exposed its `APP_GOOGLE_TOKEN_KEY` to any local user (shown as `nobody` before removal). Testco had 0 `google_connection` rows, so no Google grant was exposed and nothing needed revoking. The pending non-root tenant work would have made that a real cross-tenant path.

**Live did not depend on it:** live's app (8700) and gateway (8642) had no `/opt/testco` references in their environments and no testco files open, no live unit referred to it, no symlink pointed into it, and nothing was connected to 8710/8652. Live's pids were unchanged through the retirement. Not checked: the git remote of live's skills checkout under `/root/.hermes` (off-limits).

**What was done:**

- Pushed testco's local branch `testco` (`48346d2`, the 2026-08-30 baseline snapshot, which existed nowhere else) to GitHub as `archive/testco-baseline` after a canaried secret scan (2 commits, 351 blobs, 0 hits).
- Killed the four ad-hoc quick tunnels to 8710 (started 2026-08-25). Stopped and disabled `testco-app`, `testco-gateway` and the five timers.
- Removed the `app`, `gateway` and `hooks.yamin.io` ingress rules from `/etc/cloudflared/config.yml` and restarted `cloudflared-yamin` (rehearsal was down about 1 s). Those hostnames now hit the 404 catch-all. **Their DNS records still exist in Cloudflare; deleting them is a dashboard step for a human.**
- Archived `/opt/testco` (7820 entries), all 12 unit files and the drop-in, and the old tunnel config to `/root/archives/testco-retired-2026-10-13.tar.gz` (root, 600, directory 700), sha256 `746cebb96a6e388e2744273c4455de8513ba2e5f02a9595f11d2314ce8ddeb45`. Then deleted the units, the drop-in and `/opt/testco`.
- The skills bare repo `/root/git/openclaw-imports.git` had `origin` = `/opt/testco/skills/openclaw-imports`. Its `github` remote was renamed to `origin` (`github-skills:tegad123/jarvis-skills.git`). Every testco skills commit was already in the bare repo.

**Purge:** delete the tarball and its `.sha256` on or after **2026-11-12**. Manual; no timer was added.

**Rollback (until the purge):** `tar -C / -xzpf /root/archives/testco-retired-2026-10-13.tar.gz`, then copy `/root/archives/cloudflared-config.yml.pre-testco-retire` back to `/etc/cloudflared/config.yml`, `systemctl daemon-reload`, enable and start the testco units, and restart `cloudflared-yamin`.

**Verify sandboxes moved to a frozen fixture, not to rehearsal.** The request was to repoint them at rehearsal. Rehearsal runs live posture (`POSTURE="live"`, real `.com` identity, no `.example` recipients, `JARVIS_ALLOW_WRITES=1`), so copying its config into a sandbox would test with a real identity and writes on. Instead, before archiving, testco's config and stores were frozen into `/opt/jarvis-verify-fixture` (root, 700) in testco's layout, with:

- a regenerated `hermes/.env`: same variable names and operator labels, every secret replaced with a random value, `HERMES_API_URL` set to the dead port `127.0.0.1:9`;
- the `runtime.json` paths rewritten to the fixture, and `gateway.base_url` changed from `127.0.0.1:8642`. **8642 is live's gateway.** testco's config named it and only testco's `.env` override kept traffic away.

`docs/tasks/env.py` and `docs/tasks/tasks_sandbox.sh` read it through `VERIFY_FIXTURE_ROOT`. The sandbox runs mock Google OAuth with a per-start Fernet key and its own copy of the fixture's `hermes` directory, so nothing writes into the fixture. Scripts that needed a running testco now exit with `RETIRED`.

**Found while moving the gate:**

- **The old PROMPT 2 and 6 runs exercised testco's stale app, not the tree under test.** Skills resolve the app only through `runtime.json` `paths.app_root`, which named `/opt/testco/jarvis-app`. The harness now writes a box `runtime.json` whose `app_root` is the code under test.
- **The auth suites took their skills code from a stray shell export** rather than from the harness. They now honour `VERIFY_SKILLS_ROOT`.
- **`task-manager/scripts/generate_digest.py` (the digest-reply path) creates an empty `task-manager/data/tasks.db` inside whichever skills tree runs it.** That empty file crashed `verify_tasks_chat.py`'s legacy guard on the next run. The guard now counts a table-less file as 0 rows; a canary shows it still counts real rows. The skill-side write itself is still open.

- **The fixture holds one real address.** testco's `accounts` row for `dana` has a real (non-`.example`) mailbox address as its `email`, not the identity's `dana@testco.example.com`; it was set on testco, probably for a real Google login test. Because of it, `verify_auth_google.py` (mock login as the `.example` address gets 401) and `verify_email_column.py` fail. `verify_email_column.py` also asserts a pre-migration schema with no `email` column. Both would fail the same way on testco. Left as found; correcting the fixture needs a human yes.
- **Some suites wrote straight into testco's state by design.** `verify_fullbody_fixture.py` appended to its approvals queue and `verify_showingsmart.py` used its outbox and skills data, so on the first run they wrote into the fixture. Those five files were restored from the archive. The scripts now use the sandbox's hermes copy or a scratch home. The fixture is `chattr +i` (its databases switched to `journal_mode=DELETE`, contents proven identical), so any future write fails loudly. **To delete or modify it, run `chattr -R -i /opt/jarvis-verify-fixture` first.**

Gate on the fixture, 2026-10-13, `platform`: `verify_google_connection` 72/72, `verify_creds_cutover` 42/42, PROMPT 1 65/65, 2 20/20 (1 skipped), 3 32/32, 3b 39/39, 4 119/119, 5 69/69 (9 skipped), 6 62/62.

## Decided 2026-10-13: rehearsal credential sequence; the Google broker is next after isolation

**Decision (human):** move rehearsal's skills to the fail-closed `google_creds` so agent-side Google skills give the clear "reconnect Google" refusal instead of a service-account fallback warning. Record the app-side Google broker as **the next build item after the two-user isolation**, not a someday item.

Order on rehearsal (each step needs its own restart yes):

1. **Re-pin code.** Skills `f026b75` → `431525f` and app `c717614` → `10ffbce`, **together**. Rehearsal's app `health.py` calls `google_creds.sa_key_path()`, `mode()` and `subject()`, which `431525f` removes. Re-pinning the skills alone would turn the Sheets health check red with an `AttributeError`; `10ffbce` is the app commit that stops calling them. Rehearsal's uncommitted `app/google_oauth.py` edit is byte-identical to `ecda61c`, so nothing is lost. Rehearsal has a stored connection (1 row with a cipher), so the app's own Google use does not change.
2. **Two-user isolation** (app `jarvis-rehearsal`, agent `jarvis-rehearsal-agent`). The agent then has no Google token access, as today: the gateway never had `APP_GOOGLE_TOKEN_KEY`.
3. **App-side Google broker: the next build item.** This is fork 3 option A. The app keeps `APP_GOOGLE_TOKEN_KEY` and the connection; it exposes scoped Gmail, Calendar and Sheets operations to the tenant's gateway over loopback, authenticated per tenant. The agent-side importers (`email-router`, `gmail-push-trigger`, `meeting-briefing`, `task-manager`) call the broker instead of `google_creds.credentials()`.
   - Acceptance: on rehearsal, those skills work through the broker. As the agent user, opening the accounts database, the token key, the OAuth client, and the app's `/proc/<pid>/environ` still fails (the isolation proof stays green). The Phase 3 check "the runtime never sees the OAuth token" passes.
   - It is a send-path change and may add a dependency, so its design and build each need an explicit human yes (`AGENTS.md` rule 6). Sending through the broker stays behind `gated_send`.
   - Until it ships, agent-side Google skills on rehearsal refuse. This is a known, announced gap, not a fallback.

**Found while planning:** with an unreadable connection store, `431525f` `google_creds` logs `connection row read failed` but then refuses with `no stored connection` ("reconnect Google"). On an isolated agent, that sentence is false: the tenant is connected, and the agent cannot read the connection. **Fixed in skills `8436f6e` (human yes):** a read failure raises `GoogleConnectionUnreadable` (not a `GoogleNotConnected`), counts `google_creds_connection_unreadable`, and `source()` reports `connection_unreadable`. `_shared/tests/test_google_creds_unreadable.py` runs against the real app modules. It fails on `431525f`, passes on `8436f6e`, and has a presence twin for the not-connected path. Rehearsal is pinned to `8436f6e`, not `431525f`.

**Threads database stays shared: a known gap to close with the broker (human decision).** The isolation moves only `jarvis_accounts.db` (PIN hashes and the encrypted Google connection) into the app-only directory. `jarvis_threads.db` stays in the shared, group-writable `hermes/state`. Agent-side skills write it: `app_channels` creates deal channels and `app_counters` bumps counters, including the `google_creds` counters above. Moving it would make those writes fail with only a stderr line. The cost: the agent user can read every thread body in the tenant. The broker work closes this by giving the agent app-side calls for channels and counters, after which the threads database moves to the app-only directory.

**The gateway must name the accounts path.** `app/accounts.py` creates its parent directory and database when they are missing. A gateway that falls back to the default `hermes/state/jarvis_accounts.db` after the move would get a fresh, empty database in the shared directory and report "no stored connection". The isolation drop-in therefore sets `JARVIS_ACCOUNTS_DB` on the gateway to the app-only path, so its read fails as unreadable, and the isolation proof checks that no stray accounts database appears.

**Rehearsal was running live, not blocked (found 2026-10-13).** The manifest (`docs/tenants/rehearsal.yaml`) says `writes_posture: blocked`. The running units had `JARVIS_ALLOW_WRITES=1`, the preflight had `POSTURE="live"`, `runtime.safety.allow_writes` was `true`, and the only boundary was `identity.internal_domains` = `gmail.com`. It was switched to blocked in window 1 (human yes): the preflight is blocked, `JARVIS_ALLOW_WRITES` is unset, and runtime safety is `false`. Arming it again needs a human yes.

**Window 1 done 2026-10-13** (backups and `rollback.sh` in `/root/archives/rehearsal-window1/`, root-only):
- **What changed:** skills `f026b75` → `8436f6e`, app `c717614` → `10ffbce`, posture blocked.
- **Two restarts, not one.** A second agent run restarted rehearsal at 00:13:12 UTC on the applied files and wrote the first version of this entry. The verified restart was at 00:15:29 UTC: both ports were listening again 5.8 s after the restart began, with `NRestarts=0`. The human stopped the second run; one runner from window 2 on.
- **Startup:** both services passed `posture=blocked`, `writes=0`; no tracebacks or `ERROR` lines in the journal since; `JARVIS_ALLOW_WRITES` absent from both process environments.
- **Guard:** under a copy of the app process's exact environment, `write_guard.block_if_readonly` raised `WritesDisabled`. Canary: the same environment plus `JARVIS_ALLOW_WRITES=1` allows. `send_gate.gated_send` calls the guard first, before any transport (read, not executed). The app's approvals executor sends only through `gated_send`. `app/oauth_demo.py` sends without the guard, but it is a separate demo app that `app.main` never imports.
- **Google:** `google_creds.source()` under the app's environment = `tenant_connection`; the service-account functions are gone; `google_creds_sa_fallback` stayed at 1.
- **Tests:** the unit test passed 2/2 on rehearsal's tree.
- **HTTP:** `/` 200, `/api/session` 401, gateway `/health` 200, public origin 200.
- **Checkouts:** skills status identical to before (same tenant edits and loadout pruning); `channels.json` and `identity.json` byte-identical; `runtime.json` differs only in `safety.allow_writes`; app tree clean at `10ffbce`.
- **Preflight canary:** with `JARVIS_ALLOW_WRITES=1`, the blocked preflight refuses to start.
- **Live:** pid `2185197` unchanged; `/root/jarvis-app` status unchanged.

**Window 2 done 2026-10-13: two-user isolation** (scripts, backups, the ownership manifest and `rollback.sh` in `/root/archives/rehearsal-window2/`, root-only):
- **Users:** the app runs as `jarvis-rehearsal`, the gateway as `jarvis-rehearsal-agent`; both are in the group `rehearsal`. The app runs from `/opt/jarvis/venv`, a `cp -a` of live's venv.
- **App-only:**
  - `/opt/rehearsal/app-private` (700) holds `oauth_client.json`, installed with the human's `install -m 600` command (sha256 matched), and `jarvis_accounts.db`.
  - `/etc/jarvis/rehearsal-app.env` (root, 600) holds `APP_GOOGLE_TOKEN_KEY`, `APP_PASSPHRASE`, `APP_SESSION_SECRET` and `API_SERVER_KEY`, copied by key name. Each value's sha256 matches its source and the running app's environment. `oauth.conf` no longer carries the key.
- **Agent-only:** the hermes home (mode 0710, so the app can pass through it without listing it), `.env` trimmed to `API_SERVER_KEY`, and `state.db`, `kanban.db`, `response_store.db`, `sessions` and `logs`.
- **Shared, group `rehearsal`, setgid 2770, `UMask=0007`:** `hermes/state` (threads, tasks, leads, activity feed, approvals), `hermes/outbox`, and the skills' `data/` directories (`owner-email` holds `send_gate`'s `approvals.json` and `external_sends.log`; `task-manager` holds the authz audit log).
- **Read-only code:** `jarvis-app` and `skills` are `root:rehearsal` and not writable by either user. That includes `scripts/backup_tenant.py`, which root runs.
- **Sandboxing:** both units set `ProtectSystem=strict` with explicit `ReadWritePaths`, plus `ProtectHome=yes`, `PrivateTmp=yes` and `NoNewPrivileges=yes`.
- **Backup:** the unit stays root, because it must read the agent-only databases. It runs the rehearsal tree's script and gets the accounts path from `JARVIS_ACCOUNTS_DB`. A run after the cutover wrote 6 databases, including accounts; it reported only `sweeps.db` missing, which was always absent.
- **Rehearsal's app** is at `2b20c5d`: `app/` is identical to `10ffbce`, and the commit brings the fixed backup script.
- **Proof** (`prove.py`): 63/63 checks.
  - Under both `runuser` and the unit's sandbox, the agent is denied the OAuth client, the accounts database, the env file, the app's `/proc/<pid>/environ` and `sa_jarvis_prod.json`. Each denial has a root canary.
  - The app is denied the agent's `.env`, `state.db`, `response_store.db` and environment, and cannot list the agent's home.
  - Both users write the shared paths, and neither can write code.
  - As the agent, `systemctl show rehearsal-app` has no token key; a canary unit proves `systemctl show` would expose it.
  - As the agent, `google_creds` reports `connection_unreadable`, the counter moves by +2, and no stray accounts database appears. As the app, `source()` is `tenant_connection`.
  - Operator login returns 200.
- **Found and fixed during the window:**
  - Hermes chmods its home to 0700 on every start (`hermes_cli/config.py` `_secure_dir`). That locked the app out of `hermes/state`, and the app crash-looped (8 failed starts) until the mode was set back. The gateway unit now sets `HERMES_HOME_MODE=0710`, and a gateway restart proved the mode holds.
  - Fifty files in the hermes home were world-readable (`state.db` was 644), so the app could read them. The proof caught this, and `chmod -R o-rwx` fixed it.
  - The rehearsal app's transcription spool defaulted to `/root/jarvis-app/data/tmp-transcribe`, which is live's tree. It is now `jarvis-app/data/tmp-transcribe`.
- **Downtime:** the app was down 52 s (stopped 00:24:52 UTC, serving again 00:25:44), almost all of it the crash loop. The gateway was down a few seconds at the cutover, then 3.6 s for the `HERMES_HOME_MODE` restart.
- **Pre-existing, not caused by isolation: production token refresh cannot work.** `app/google_connection.build_credentials` passes `client_id=None` and `client_secret=None` outside mock mode, in `c717614`, `10ffbce`, `2b20c5d` and live's checkout. Once the stored access token expires, every refresh fails with `RefreshError`. The window 1 check read only `source()`, which does not refresh. The window 2 health probe ran the refresh, and the connection row moved from `connected` to `needs_reauth` with `last_error=RefreshError`. That is true, so it was left as is; no alert fired, because alerts are for `invalid_grant` only. Reconnecting would work for about an hour. The fix is to read the client from `YAMIN_OAUTH_CLIENT`; it changes the credential path and needs a human yes.
- **Known gaps:**
  - `scripts/provision_tenant.py` knows neither the two users nor the venv copy (it accepts only `venv: shared`). Re-provisioning rehearsal would undo this window.
  - `inbound_queue.db` and `inbound_cursor.db` would be created in the read-only skills `_shared` directory; they must move to `hermes/state` before inbound runs on an isolated tenant.
  - `app/inject.py` still defaults its fixtures to the retired `/opt/testco/fixtures`.
  - Rehearsal's gateway has no model provider configured and has never held a conversation (0 sessions), so chat was not testable before or after.

## Decided (from the spec, recorded 2026-10-10)

- Customer #1: 34 Dev team.
- Hosting: Docker containers on shared DigitalOcean hosts, managed over Tailscale. *(Spec decision; nothing of this is installed yet. Confirmed for runtimes by decision 1 on 2026-10-13.)*
- Channels for v1: WhatsApp, Slack, PWA. No SMS or Twilio.
- iMessage: read through a Mac companion app, read-only, opt-in per chat.
- CRM: adapter interface; Google Sheets first.
- HAR: out of v1. ShowingSmart: in (email layer first, browser layer flagged).
- Autonomy: customizable per user and per skill, with safe defaults and a locked class.
- Agent backbone: Hermes. Builder: Tega in Cursor.

## Open

1. ~~Spencer: an earlier decision kept Spencer on OpenClaw with new clients on Hermes. Does Spencer move to the new platform, and when?~~ **Answered 2026-10-13** (decision 5): yes, in Phase 11, after Eddie, Blake, and Martin, with a two-week rollback window.
2. Model choice and routing: which model for the main agent, which for Sentinel, which for cheap tasks like parsing fallback and digests?
3. ~~Is Hermes's skill-body loading behavior the same gotcha as OpenClaw's? Check in Phase 2.~~ **Answered 2026-10-10, line numbers re-checked and recorded in the spec 2026-10-13.** Yes, the same default.
   - The system prompt carries only each skill's name and description: `hermes:agent/prompt_builder.py` `_parse_skill_file` (lines 1511–1531) and `build_skills_system_prompt` (line 1584), rendering `<available_skills>` at lines 1836–1863 with the instruction to "load it with skill_view(name)".
   - Bodies load on demand through `skill_view` (`hermes:tools/skills_tool.py` line 962).
   - Bodies are injected without the model choosing only for CLI/TUI preloads (`hermes:agent/skill_commands.py` `build_preloaded_skills_prompt`, lines 747–812) and for cron jobs that list `skills:` (`hermes:cron/scheduler.py` lines 2444–2645, `skill_view` at line 2606).
   - Consequence: rules that must hold go in code gates (`gated_send`, `outbound_boundary`, `write_guard`), the frontmatter `description`, or a preload, not only the SKILL.md body.
   - `hermes:` = `/usr/local/lib/hermes-agent`, branch `scale-vault-local`, `276e5c7`.
4. Pricing per seat, to compare against measured cost per user after Phase 11.
5. ShowingSmart terms of use for automated access (gate for Phase 12).
6. Data retention for client emails and transcripts kept in events.
7. Who on the team is the org owner vs admin in 34 Dev?
8. Which apps from the 8.8 catalog does each 34 Dev agent actually use? That sets the Next-tier order.

## Architecture forks (2026-10-10; all five resolved 2026-10-13)

The spec assumes things that differ from how `jarvis-app` is built. These five choices shape Phases 1 to 5 and 11. They were listed with tradeoffs only; all five were decided by a human on 2026-10-13 (see "Decided 2026-10-13" above). The tradeoffs are kept for the record.

### Fork 1: Runtime granularity — one runtime per user, or per tenant — **RESOLVED 2026-10-13: A (per user, Docker), decision 1**

Today: one app, one Hermes gateway, and one `HERMES_HOME` per tenant, stamped by `scripts/provision_tenant.py` under systemd.

- **A. Per user, as the spec says.** Gives per-user memory and isolation for free, and makes "a runtime can't reach another runtime" testable. Costs: a container runtime (Docker, a new dependency), a Runtime Manager, roughly N times the gateway memory per tenant, and team features (delegation, shared tasks) must move to the control plane, since the skills today assume one shared tasks store per tenant.
- **B. Per tenant, as today, with per-user scoping inside.** Reuses the provisioner, loadouts, and verify suites. Costs: per-user memory, policies, and audit must be scoped by `person_key` inside shared stores and Hermes' shared memory directory, and one compromised or injected gateway still sees every user in the tenant.
- **C. Hybrid.** Per-tenant control plane and stores; per-user Hermes processes (systemd or containers) pointing at per-user `HERMES_HOME`s. Middle cost; still needs a decision on where shared tasks live.

### Fork 2: Storage — Postgres control plane, or per-tenant SQLite — **RESOLVED 2026-10-13: Postgres for new platform tables only; SQLite untouched until Phase 11, decision 2**

Today: per-tenant SQLite under `$HERMES_HOME/state/` plus JSONL files; backups by `scripts/backup_tenant.py`.

- **A. Postgres.** Matches Section 6, makes cross-org queries, an admin view, and point-in-time recovery straightforward. Costs: a new dependency and server, a migration of every store, a tenant-scoped query layer that must be proven by a cross-tenant canary, and a new backup path.
- **B. Keep per-tenant SQLite.** Isolation stays physical (one file set per tenant), existing verify suites keep working, no new server. Costs: the Section 6 tables (`policies`, `actions`, `events`, `usage`) become per-tenant schema migrations; anything org-wide (owner admin view across users, billing) needs a small separate store or aggregation job.

### Fork 3: Credential boundary — app-hosted tools, or in-process `google_creds` — **RESOLVED 2026-10-13 (first step): remove the SA-key and plaintext fallbacks first, decision 3; end state assumed per spec 9.6, needs human confirmation**

**Sequenced 2026-10-13:** option A (the app-side broker) is the next build item after rehearsal's two-user isolation; see "rehearsal credential sequence" above. The fallbacks this section mentions were removed in skills `ccae204`/`431525f`.

Today: skills in the gateway and timers import `app.google_connection` through `origin:_shared/google_creds.py` and decrypt tokens in their own process; a plaintext `google_token.json` fallback remains.

- **A. App-hosted connector tools (the spec).** The app (control plane) holds `APP_GOOGLE_TOKEN_KEY` and exposes Gmail, Calendar, and Sheets as MCP or HTTP tools scoped to the caller. A compromised gateway cannot exfiltrate tokens. Costs: a send-path change, rewriting every `google_creds` importer (gmail-push-trigger, email-router, task-manager, sheet_tab, meeting-briefing, and others) to call tools instead, possibly an MCP library (dependency), and an app process that becomes a hard dependency for every skill.
- **B. Keep in-process `google_creds`, harden it.** Remove the plaintext fallback, keep the key off the gateway environment, and constrain scopes. Cheaper and incremental. Costs: the Phase 3 acceptance check ("the runtime never sees the OAuth token") cannot pass, and Section 12 stays violated.

### Fork 4: Role vocabulary — **RESOLVED 2026-10-13: A (owner/admin/agent/assistant), operator kept as break-glass, decision 4**

Today: `owner`, `admin`, `member` in `app/accounts.py` `ROLES`, plus a session-only `operator` (break-glass, structure but never bodies) in `app/auth.py`.

- **A. Adopt the spec's `owner/admin/agent/assistant`.** Rename `member` to `agent`; add `assistant` with grants to specific agents' data. Costs: an accounts migration, every role gate and verify script that names `member`, and `AGENTS.md`'s role invariant must be updated.
- **B. Keep the code's names and map them.** `member` = spec `agent`; add `assistant` as a new role or as a grant on `member`; keep `operator` as a platform role outside the org. Costs: two vocabularies to translate between in docs.
- Either way, `operator` has no spec equivalent and its "never bodies" rule must survive.

### Fork 5: Spencer and the live migration — **RESOLVED 2026-10-13: B first (Eddie, Blake, Martin in shadow mode), then Spencer cuts over in Phase 11 with a two-week rollback window, decision 5**

Today: live (34 Dev) runs from `/root/jarvis-app` with `HERMES_HOME=/root/.hermes` on port 8700, outside the provisioner's `/opt/{slug}` layout. The skills origin keeps a separate `live-34dev` branch (`7b9bb7f`, baseline 2026-09-02). Testco is 17 commits behind `main`.

- **A. Re-provision live as `/opt/34dev`** with `scripts/provision_tenant.py`, then cut over (tokens, stores, Gmail watch, DNS or tunnel). One layout for every tenant; live gets the loadout completeness guard and verify suites. Costs: a supervised cutover with downtime risk on the only paying tenant; requires touching `/root/.hermes`, systemd, and the tunnel, all human-only per `AGENTS.md`.
- **B. Leave Spencer where he is; onboard Eddie, Blake, and Martin on the platform first** (spec Phase 11 order). No risk to live. Costs: two layouts and two skills trees to maintain, and team features cannot span Spencer and the others until he moves.
- **C. Freeze live, build the platform, migrate once at the end.** Least churn on live. Costs: live drifts further from `main` and the cutover gets larger.
- Precondition for any option: sync testco to `main` (human-supervised, per `AGENTS.md`). *(Obsolete 2026-10-13: testco was retired; verification uses the fixture-backed sandbox. See "Decided 2026-10-13: testco retired".)*
