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

**Threads database stays shared: a known gap to close with the broker (human decision).** The isolation moves only `jarvis_accounts.db` (PIN hashes and the encrypted Google connection) into the app-only directory. `jarvis_threads.db` stays in the shared, group-writable `hermes/state`. Agent-side skills write it: `app_channels` creates deal channels and `app_counters` bumps counters, including the `google_creds` counters above. Moving it would make those writes fail with only a stderr line. The cost: the agent user can read every thread body in the tenant. The broker work closes this by giving the agent app-side calls for channels and counters, after which the threads database moves to the app-only directory. *Closed for newly provisioned tenants on 2026-10-14 (platform build item 4, below); rehearsal still has the gap.*

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
  - `scripts/provision_tenant.py` knows neither the two users nor the venv copy (it accepts only `venv: shared`). Re-provisioning rehearsal would undo this window. *(Closed 2026-10-14 by platform build item 3, below.)*
  - `inbound_queue.db` and `inbound_cursor.db` would be created in the read-only skills `_shared` directory; they must move to `hermes/state` before inbound runs on an isolated tenant.
  - `app/inject.py` still defaults its fixtures to the retired `/opt/testco/fixtures`.
  - Rehearsal's gateway has no model provider configured and has never held a conversation (0 sessions), so chat was not testable before or after.

## Decided 2026-10-14: token refresh reads the OAuth client (platform build item 1)

**Approval (human):** item 1 of the platform build goal: "Token refresh fix (YAMIN_OAUTH_CLIENT, app-side only) + verify."

- `build_credentials` takes `client_id`, `client_secret` and `token_uri` from the file `YAMIN_OAUTH_CLIENT` names (`web` or `installed` key), the same file the connect flow uses. Mock mode keeps `mock`/`mock`.
- No client file, or one without both fields, raises `OAuthClientMissing` before any network call. It logs, bumps `google_connection_oauth_client_missing` and marks the row `needs_reauth` with `last_error=oauth_client_missing`. Before, the same case built a credential that failed later with `RefreshError`.
- A successful refresh now sets `status=connected`. Before, it cleared `last_error` but left `needs_reauth`, so a connection could never recover without a reconnect.
- Found by the gate while verifying this, unrelated to Google: `tasks_store` judged "done on time" against the UTC date, not the tenant's, so a task due today and finished in the evening (US time) counted as late. It now uses the tenant date. `verify_metrics.py` caught it only between 00:00 UTC and tenant midnight.
- **Proof:** `docs/auth/verify_token_refresh.py` 24/24. It runs the real refresh through skills `google_creds.credentials()` against a local fake token endpoint, and checks what was posted, the re-encrypted row and the status. Its built-in canary runs the same case on `2b20c5d`'s `google_connection.py` and gets the production `RefreshError`. Reverting only the status line fails one check. Full sandbox gate green (13 suites); fixture unchanged.
- **Not deployed.** Rehearsal still runs `2b20c5d` and its connection reads `needs_reauth`. Deploying needs a restart of a tenant with a real mailbox, which is a STOP gate. The stored refresh token was never sent to Google, so it was never rejected. After the restart, the next app-side refresh should succeed and flip the row back to `connected` without a reconnect. That is unproven until it happens.

## Decided 2026-10-14: one agent changes the server at a time (platform build item 2)

**Decision (human):** "You alone change the server (restarts, users, permissions, services, provisioning). Take /root/.jarvis-agent-lock before any server change; release it after." Helpers work only in their own worktree on a feature branch and never touch the server.

- `AGENTS.md` hard rule 8 states it. `scripts/agent_lock.py` takes the lock with `O_EXCL`, refuses a second holder (including the same name), lets only the holder release, and never breaks a stale lock on its own.
- This followed an unannounced second agent restarting rehearsal on 2026-10-13 (see window 1).
- **Proof:** `docs/platform/verify_agent_lock.py` 16/16, including 20 simultaneous acquires with exactly one winner. Canaries: without `O_EXCL`, 5 checks fail (all 20 racers win); without the ownership check, 4 fail.
- Rule 5 now says why rehearsal restarts need a yes (a real mailbox is connected). It had still called rehearsal live-posture.

## Decided 2026-10-14: the provisioner stamps the two-user layout (platform build item 3)

**Approval (human):** item 3 of the platform build goal: "Provisioner produces the two-user isolated layout; acceptance = the 63-check proof on a fresh tenant."

- **Manifest:** `isolation: two_user | none` is required. Blocked and live tenants must be `two_user`; `none` is for captured loopback scratch only. `two_user` requires `venv: platform` (`/opt/jarvis/venv`), because tenant users cannot reach `/root` under `ProtectHome`. Slugs longer than 19 characters are refused, because `jarvis-{slug}-agent` must fit in 32.
- **What it stamps:** the same layout rehearsal got by hand in window 2.
  - Users `jarvis-{slug}` (app) and `jarvis-{slug}-agent` (gateway), both in group `{slug}`.
  - App secrets in `/etc/jarvis/{slug}-app.env` (root 600). `hermes/.env` holds `API_SERVER_KEY` only.
  - `app-private/` (app 700) holds the OAuth client and the accounts database.
  - Each unit gets a `10-isolation.conf` drop-in: the app, the gateway (with `HERMES_HOME_MODE=0710`), the digest, sweep and metrics jobs (which run as the app user) and backup (root, the tenant's own script). The OAuth drop-in carries no key.
  - Ownership and modes are as in window 2's `apply.sh`.
- **Teardown** also removes the users, the group (only if its members are exactly that tenant's two users, read before the users are deleted) and the env file.
- **OAuth client source:** `/etc/jarvis/oauth_client.json` (root 600), installed with `install` from rehearsal's app-private copy (same sha256). The provisioner no longer reads `/root/.hermes` for two-user tenants.
- **`backup.service` ran live's code:** the template had `/root/jarvis-app/scripts/backup_tenant.py` hardcoded for every tenant. It now runs the tenant's own tree.
- **Proof:** `scripts/prove_isolation.py --slug isocheck` 77/77 on a freshly provisioned scratch tenant (blocked, no mailbox, `.invalid` addresses). It generalizes window 2's 63 checks and adds posture, shared-state permission, and wrong-passphrase checks. The `sa_jarvis_prod.json` probe is replaced by "the agent cannot list `/root`", so the proof reads nothing under `/root/.hermes`. `docs/platform/verify_provision_isolation.py` 30/30 offline. Canaries: removing the isolation-field refusal fails one check, and only the last-resort port refusal stops the run; reverting `backup.service` fails two. Full sandbox gate green.
- **Found by the proof on the first stamp (all fixed, then re-stamped from scratch):**
  - `provision_client.py` (skills) minted its own `APP_PASSPHRASE` and `APP_SESSION_SECRET` into the agent's `hermes/.env` and printed the passphrase. The provisioner now stages the env-file values there while `provision_client` runs, trims `hermes/.env` to `API_SERVER_KEY` afterwards, refuses if `provision_client` mints anyway, and redacts any printed passphrase. That first scratch tenant's passphrase appeared once in the agent's own tool output. The tenant was torn down minutes later.
  - SQLite creates databases 0644 whatever the umask, and hermes wrote `activity_feed.jsonl` 0644. The first user to open a shared store made it read-only to the other: the agent's counter bump failed, and the app could not append to the feed. The provisioner now pre-creates the shared state files 660 (`SHARED_STATE`). `-wal` and `-shm` take the database's mode.
  - The accounts database is created lazily, so it did not exist in `app-private` until first use. It is now pre-created empty, app 600.
- **Kept:** `isocheck` (8740/8682) stays as the on-host test target; see `AGENTS.md` topology.
- **Rehearsal gap, needs a human yes (it touches `/opt/rehearsal/hermes`, `AGENTS.md` rule 3):** rehearsal has no `activity_feed.jsonl`, `approvals.jsonl` or `approvals_decisions.jsonl` yet. Whichever user creates one first will make it unwritable for the other. The fix is to pre-create the three files as `jarvis-rehearsal-agent:rehearsal` 660. No restart is needed.

## Decided 2026-10-14: app-side Google broker; the threads database leaves shared state (platform build item 4)

**Approval (human):** item 4 of the platform build goal: "App-side Google broker: agent requests Gmail/Calendar actions through an app endpoint and never holds tokens. Move threads-DB writes behind it too." This closes the threads-database gap recorded above, for newly provisioned tenants.

- **The endpoint:** `app/broker.py`, `/api/broker/*`.
  - Loopback only; a request carrying a forwarding header (`CF-Connecting-IP` and the like) is refused even with the key. Auth is `Authorization: Bearer {API_SERVER_KEY}`, the key the agent already holds to call its own gateway.
  - `POST /google` runs one method from a fixed allowlist (Gmail v1: profile, messages list/get/attachments, threads, history, labels, send, modify, watch, stop; Calendar v3: calendarList.list, events list/get/insert/patch/delete), using `google_creds.credentials()` inside the app process. A method off the list is 403 and counted.
  - Writes need `write_guard.writes_allowed()`; `messages.send` also passes `outbound_boundary.check` on the parsed recipients. Both refusals are counted (`broker_google_write_refused`, `broker_google_recipient_refused`) and come back to the skill as `WritesDisabled`.
  - `GET /google/status`, `POST /counters` (bump, record) and `POST /channels` (`ensure_deal_channel`) cover the agent's other reasons to open `jarvis_threads.db`.
- **Skills side (jarvis-skills `0edd352`):** setting `JARVIS_APP_BROKER_URL` puts `google_creds`, `app_counters` and `app_channels` in broker mode. `gmail()` and `calendar()` return a proxy with the same call shape as the Google client; errors map back to `GoogleNotConnected`, `HttpError` (with Google's status) or `WritesDisabled`. `credentials()` and `gspread_client()` raise `TokensAreAppSide`, counted. `create_calendar_event` and `meeting-briefing/lib_google` moved off raw access tokens onto `google_creds`.
- **Provisioner:** the two-user gateway drop-in sets `JARVIS_APP_BROKER_URL=http://127.0.0.1:{app port}`. App, jobs, backup and gateway all name `app-private/jarvis_threads.db` (pre-created app 600; the agent is denied it, so a direct open fails loudly). `jarvis_threads.db` is no longer in `SHARED_STATE`. `backup_tenant.py` honours `JARVIS_THREADS_DB`, or it would have stopped backing the database up without saying so. The app refuses to boot if it is given a broker URL.
- **Found on the way:** `ensure_deal_channel` looked up existing channels with `list_threads()` and no viewer. Since channel scoping, that returns no channels, so every call opened a duplicate deal channel. The broker and the in-process path now look up with `unscoped=True`. Any tenant on skills older than `0edd352` with a scoped app still has this bug.
- **Proof:**
  - `docs/broker/verify_broker.py` 41/41 against a fake Google in two sandboxes (8712 writes off, 8713 writes on behind `.example`). It covers the rewritten callers, invoked as the skills invoke them. Canaries: removing the write gate, the recipient check or the key check each fails 2–3 checks; the pre-change skills fail 23.
  - `verify_provision_isolation.py` 37/37; dropping the broker URL from the gateway template fails two.
  - `prove_isolation.py --slug isocheck` 91/91 on isocheck re-stamped at app `ec1a02c`, skills `0edd352`. On the host:
    - The agent's `source()` comes from the app through the broker; `credentials()` is refused; a send is refused by the blocked posture and counted app-side; a counter bump lands in the app-private database.
    - Without the broker URL the agent is refused as unreadable and writes nothing.
    - With the wrong expected source, two checks fail (canary).
  - A real `isocheck-backup` run captured the app-private threads database.
  - Full sandbox gate green.
- **Known gaps:**
  - Sheets: `gspread_client()` and `sheet_tab` refuse in broker mode, and Sheets is not on the allowlist. Sheet-backed skills do not work on a two-user tenant until it is.
  - The agent still reads and writes the shared tasks and leads databases, the activity feed and the approvals journals. Item 4 moved only the threads database.
  - **Rehearsal is not migrated.** It still runs app `2b20c5d`, skills from before the broker, and a shared threads database. Moving it means a restart of a tenant with a real mailbox connected: a STOP gate (needs a human yes).

## Decided 2026-10-14: Sentinel's deterministic gate and shadow mode (platform build item 5, partial)

**Approval (human):** item 5 of the platform build goal: "Spec Section 9: action classes, approvals (structured callbacks only), grant scopes, taint rule, Sentinel deterministic gate. Shadow mode = every write logged, none executed."

- **Built (`app/sentinel.py`, pure, no I/O):**
  - The six action classes and their defaults (9.1).
  - The effective mode: the user's override, never looser than the org ceiling; `locked` ignores the user (9.2).
  - Grants bound to one connector, one destination (an address or `@domain`) and one skill, with scopes once, session, task, until and always. An approval's grant must name exactly one destination; `locked` takes once only (9.3).
  - Signed structured approval callbacks: HMAC-SHA256 over the approval id, the user it was issued to, the answer, the scope and the expiry. Free text, a swapped body, another key, another user's session and an expired token are all refused (9.3).
  - Deterministic verdicts: unknown class, recipient boundary, rate limits (20 external sends per hour, 3 per recipient per day), blocked mode, then grants, auto and taint (9.4, 9.5).
- **In the broker:** every Google write is classified (internal vs external from `identity.internal_domains`) and decided. The decision is appended to an app-only ledger (`JARVIS_SENTINEL_LEDGER`, `app-private/sentinel_ledger.jsonl`) before anything runs; a decision that cannot be recorded does not run. The ledger holds structure (class, destinations, skill, run, taint, verdict, outcome), never subjects or bodies.
  - **Taint:** reading mail from outside the org, a thread containing such mail, thread snippets, an attachment, or an event organised outside the org taints the run. The run is `X-Jarvis-Run` (from `JARVIS_RUN_ID`, else the Hermes session); a write with no run identity is decided as tainted.
  - **Shadow mode** (`JARVIS_SENTINEL_MODE=shadow`): every Google write is ledgered with what Sentinel would have done and none is executed. The skill gets `write_guard.ActionHeld` (a `WritesDisabled` subclass). Shadow is checked before the writes switch, so it holds even with writes on. The app refuses to boot on an unknown mode.
  - **Recipient parsing:** Python 3.12's strict `getaddresses()` collapses a whole list to one empty address when any element is empty or malformed. The broker therefore saw no recipients for a message with an empty `Cc:` header. That failed closed, but it would have refused a legitimate message. Headers are now parsed one value at a time, and an unparseable non-empty one refuses the send.
- **Provisioning:** two-user manifests must say `sentinel_mode: shadow | enforce` (all checked-in manifests say `shadow`). `approvals_decisions.jsonl` moved to `app-private`. In the shared state directory the agent could append "approved" to the queue's history. Nothing would have executed, because only the app's `decide()` executes, but the queue would show consent nobody gave. Backup follows the ledger and the decisions file.
- **Proof:**
  - `verify_sentinel_gate.py` 64/64 offline; `verify_sentinel_broker.py` 27/27 against two sandboxes (enforce; shadow with writes on); `verify_broker.py` 43/43; `verify_provision_isolation.py` 48/48.
  - Canaries:
    - Ignoring taint fails 3; dropping rate limits fails 2; skipping the callback signature check fails 2.
    - Removing the shadow check fails 6, and the fake Google then receives the send and the insert.
    - A no-op taint hook fails 3; trusting a missing run identity fails 1; removing the `sentinel_mode` refusal fails 2.
  - `prove_isolation.py --slug isocheck` 103/103 on isocheck re-stamped in shadow mode at app `a99de0e`, skills `6e56172`. The agent's send through the broker came back `ActionHeld`, with one shadowed `external_send` in the ledger and no body. A real backup run captured the ledger and the decisions file. Full sandbox gate green.
- **Not built yet:**
  - ~~**Approvals and grants have no store.**~~ **Built 2026-10-14 on Postgres** (section "Section 9 on Postgres" below). The JSONL ledger and decisions file described above are gone.
  - The model check (9.4, second half), surrogate tokens, the egress proxy and email scrubbing (9.6).
- **Known gaps:**
  - Sentinel runs inside the app process: outside the agent's user and process, but not a separate process as 9.4 says.
  - Run identity is asserted by the agent, so a run could claim a fresh id to shed taint. Gateway-issued run tokens would close this.
  - Taint is held in app memory; an app restart forgets it for runs still in progress (the taint events stay in the ledger).
  - Rate limits rescan the ledger, which has no rotation.
- ~~**Interpretation to confirm (human):**~~ **Answered 2026-10-14 and widened** (section "Section 9 on Postgres" below). Original reading: shadow mode covers writes to the outside world, meaning everything that goes through the broker to Google. Jarvis's own stores (tasks, leads, channels, counters, the feed) still write in shadow, and the agent still replies in its chat channel. Without that, practice mode could not show what Jarvis would have done.

## Decided 2026-10-14: tenant agents reach Claude through the app's model proxy (platform build item 6)

**Approval (human):** item 6 of the platform build goal: "Model provider for tenant agents (Claude via API key from /etc/jarvis env, app-side) so agents can chat; chat tests pass."

- **The proxy:** `app/model_proxy.py`, `POST /api/model/anthropic/v1/messages` and `/v1/messages/count_tokens`. It accepts the agent's `API_SERVER_KEY` (as `x-api-key` or a bearer token), from loopback only and never through the tunnel. It forwards with the platform's `ANTHROPIC_API_KEY` and streams the answer back unchanged. Only the Anthropic version and beta headers, content type and accept are passed upstream; the agent's key and any `Authorization` header are not. No key configured is a 503 `model_not_configured`, counted and logged, never a fallback.
- **Where the key lives:** `/etc/jarvis/anthropic.env` (`ANTHROPIC_API_KEY=...`, root 600), read by the app unit as an optional `EnvironmentFile`. One platform key for every tenant. The agent cannot list `/etc/jarvis`.
- **Hermes:** `config.yaml` gets `model: {provider: anthropic, default: <agent_model>, base_url: http://127.0.0.1:<app port>/api/model/anthropic}`. Hermes honours a custom `base_url` for provider `anthropic` only when its path ends in `/anthropic`; any other path silently becomes `https://api.anthropic.com` (`hermes_cli/runtime_provider.py`, `_anthropic_base_url_override_ok`). `hermes/.env` holds `API_SERVER_KEY` and `ANTHROPIC_API_KEY` set to the same value, the stand-in Hermes sends. `agent_model` is a required manifest field (`claude-*`); the checked-in manifests now carry `claude-sonnet-5-5` (open question 2, answered provisionally 2026-10-14).
- **Proof:**
  - `docs/model/verify_model_proxy.py` 21/21. It drives Hermes's own `build_anthropic_client` through the proxy against a fake Anthropic: create, stream, count_tokens, and an upstream 429 passed through. The upstream saw the platform key on every request and never the agent's key. A sandbox with no key gets the 503.
  - The same verify checks Hermes's base-URL rule: it accepts `/api/model/anthropic` and drops `/api/model` (canary).
  - Canaries: forwarding the agent's key fails 2; removing the key check fails 5.
  - `verify_provision_isolation.py` 56/56.
  - `prove_isolation.py --slug isocheck` 110/110 at app `4bc1181`: hermes config, agent `.env`, `/etc/jarvis` denied, and the agent's stand-in getting `503 model_not_configured` (wrong key 401, canary).
  - End to end on isocheck: a chat sent to the real Hermes gateway went to the proxy. `model_proxy_not_configured` rose by 3, one per Hermes retry, and the agent answered "HTTP 503: No model key is configured for this tenant's app." Full sandbox gate green.
- **Blocked (needs a human):**
  - ~~**No model key is installed.**~~ **Installed by a human 2026-10-15** (section "Tenant agents chat" below).
  - ~~**The model** (open question 2) for the three per-person tenants.~~ **Answered 2026-10-14, provisional** (section "Section 9 on Postgres" below).
- **Known gaps:**
  - Hermes's auxiliary model paths (compression, vision), if enabled, may call `api.anthropic.com` directly with the stand-in. They would fail with 401 and leak no real key.
  - The proxy does not cap tokens or cost per tenant. It meters them (2026-10-14); the cost guard warns and pauses on the metered spend.

## Decided 2026-10-14: Section 9 on Postgres, widened practice mode, model choice, switches (platform build items 5 and 6 completed)

**Approvals (human, 2026-10-14, verbatim in the conversation):**
- *Storage:* "Postgres, per decision 2", listening on localhost only, agent users unable to connect, one database role per tenant app limited to its own data, schema via Alembic, the audit log append-only, the database in the nightly backup with a proven restore, live untouched.
- *Practice mode scope:* hold "all Google writes (mail, labels, calendar), plus any Slack/Discord post or channel creation that other people can see, and any message to anyone other than the user themselves"; allow Jarvis-internal records and the agent's replies to the user; log every held action as "would have done X" with its source record, visible in the feed.
- *Model (spec open question 2, provisional until a bake-off):* claude-sonnet-5-5 for tenant agents, claude-haiku-5-5 for background parsing and extraction, both per-tenant manifest settings; tokens and cost per tenant per day logged for the cost guard.
- *Rehearsal files:* approvals are app-only ("a tricked agent could approve its own actions"); the activity feed may be agent:rehearsal 660.
- *Switches:* broker, Sentinel and model proxy each behind a config switch, so one piece can be turned off without a rollback.

**Postgres (decision 2, conditions met):**
- PostgreSQL 16 from Ubuntu's archive, cluster 16/main, RSS about 60 MB. `scripts/setup_postgres.py` writes `listen_addresses = ''` (no TCP listener at all), max_connections 40, shared_buffers 64MB, and a pg_hba of local peer rules only: root and postgres map to the postgres role, everything else comes from `hba.d/<slug>.conf`. The shipped files are kept as `.dist`. Its `check` asserts all of this against the running cluster.
- Per tenant (`scripts/pg_tenant.py create`, run by the provisioner from the tenant's own code): database `jarvis_<slug>`, a NOLOGIN owner role that owns every object, and a LOGIN app role `jarvis_<slug>_app` that only the tenant's app OS user can become (peer auth through ident map `jarvis_<slug>`). Agent users get no rule; Postgres refuses them before any password exchange. The app role may SELECT and INSERT on `actions` and `held_actions` (no UPDATE or DELETE), SELECT/INSERT/UPDATE on approvals, grants, policies and usage, and nothing else.
- Schema: Alembic migration `0001` in `app/migrations` creates policies, approvals, grants, `actions` (the audit log), `held_actions` (full proposals for practice-mode holds) and usage. A trigger refuses UPDATE and DELETE on `actions` per row and TRUNCATE per statement, so even the superuser cannot rewrite it without dropping the trigger. Downgrade raises. Users and roles stay in the accounts SQLite for now; this migration holds only the Section 9 tables.
- Backup: `backup_tenant.py` dumps the database to `platform.dump` (pg_dump -Fc, 0600) and proves every dump restores: it is restored into a scratch database, the table digests are taken, the append-only trigger is checked, and the restored row counts go into `MANIFEST.json`. A configured database that cannot be dumped or restored fails the unit. Teardown dumps and restore-checks the database into `/root/backups/<slug>/teardown-*-platform.dump` before dropping it.
- Verify sandboxes run as root against `jarvis_verify_*` databases (root maps to role `jarvis_verify` for those names only), created fresh at start and dropped at stop.

**Approvals, grants and the audit log (9.2, 9.3):**
- An `ask` verdict files an approval holding the exact action (origin `broker`). Skills file producer requests through `POST /api/broker/approvals` (agent) or the store with `JARVIS_PG_DSN` (app-side jobs). There is no file fallback.
- A decision arrives only as a signed callback (HMAC key derived from `APP_SESSION_SECRET`, carrying the approval, the user, the answer and the grant scope) or as the owner's PWA session decision, recorded as scope once, `via: pwa_session`. The owner gate comes first; the operator and the agent's key are refused. Resolution is a conditional UPDATE on `pending`, so a decision lands once.
- Approving a broker action re-runs it through the gate with that approval. A scope other than once creates a grant bound to one connector, one destination and one skill; a once grant is consumed atomically. Scopes offered: once, session, task, always, and only for an action with exactly one destination that is not locked. `until` is not offered: its duration is unanswered. Approvals expire after 24 hours.
- Policies (user overrides and org ceilings), grants, rate limits (counted from `actions`) and taint are read from the database on every decision, so an app restart forgets nothing. If the database is unavailable the write answers 503 `sentinel_unavailable` and is counted; nothing runs.
- **Source record rule (spec 6):** a write that names no source record is denied. The broker derives one where it can (modifying a message names it; a send with a threadId names the thread; watch and stop are `system`); otherwise the skill names it (`app_broker.source(kind, id)` or `JARVIS_SOURCE`), sent as `X-Jarvis-Source`.

**Practice mode (shadow), widened as approved:**
- Held: every Google write, and every outbound kind: chat posts, uploads, channel and thread creation, and SMTP mail. In broker mode the skills' chat adapter becomes `BrokeredAdapter`, which proposes through `/api/broker/outbound` and never reports a post as delivered. `send_gate` sends Gmail through the broker and proposes SMTP as `email_send`.
- Each hold writes an `actions` row (structure only), the full proposal in `held_actions`, and a feed line of kind `sentinel.would_have`: "Practice mode: Jarvis would have tried to …", with the class, Sentinel's verdict and reason, the source record (or "no source record"), "held action #id" and "nothing was sent". The owner (not the operator) can open the proposal at `GET /api/sentinel/held/{id}`. An approved producer send in practice mode is held the same way.
- Allowed: Jarvis-internal records (tasks, thread and channel rows, feed, counters) and the agent's replies to the user.
- Outside practice mode, outbound kinds answer 501 `not_brokered`, counted: the platform has no brokered chat or SMTP sender yet.

**Model (open question 2, provisional):** `agent_model` defaults to `claude-sonnet-5-5` and `background_model` to `claude-haiku-5-5`. Both are manifest fields, rendered into Hermes's config, `runtime.json` `models` and the app and job drop-ins. Nothing consumes `background_model` yet; it is the setting jobs will read. The model proxy meters every `v1/messages` response (JSON or SSE) into `usage` per UTC day, model and purpose (`x-jarvis-purpose: agent | background`), priced from `app/model_prices.json`. **The prices there are provisional** (USD per million tokens: sonnet-5-5 3/15, haiku-5-5 1/5, cache write and read 1.25× and 0.1× input) until checked against Anthropic's price list. A model with no price is recorded as unpriced, counted, never as $0. `GET /api/broker/usage` serves the totals, and the cost guard reads today's spend from it, says when unpriced requests make the number a floor, and exits 1 with an ops alert when it cannot read usage.

**Switches:** `JARVIS_SWITCH_BROKER`, `JARVIS_SWITCH_SENTINEL`, `JARVIS_SWITCH_MODEL_PROXY`, manifest `switches`, default on. Off fails closed: broker off answers 503 `broker_disabled` to every broker call; Sentinel off answers 503 `sentinel_off` to every write, outbound proposal and approval while reads still work, and lets the app boot without the database; model proxy off answers 503 `model_disabled`. Each refusal is counted. Any other value refuses boot, and so does Sentinel on with no reachable database at schema head. A tenant without two-user isolation has no database, so its app unit sets Sentinel off.

**Display names (provisional) and reconfigure:** `provision_tenant.py --manifest M --reconfigure` re-applies display names, timezone, models and switches to a running two-user tenant (identity, runtime.json, SOUL.md, Hermes config, unit drop-ins, each rewritten with its owner and mode kept), then restarts app and gateway and waits up to two minutes for both to be healthy. It refuses a tenant with a Google mailbox connected unless `--restart-approved` is passed (a STOP gate: that restart needs a human yes). Ports, posture and isolation change only by re-provisioning. The names of the three per-person tenants are provisional; the agents may rename theirs. Connecting their real mailboxes is a STOP gate.
- **Placeholder mailboxes are `<slug>-assistant@assistant.invalid`** (owners `<slug>@assistant.invalid`), not the `@34dev.invalid` first chosen. Every tenant's preflight refuses to start if its config contains `34dev`, `cqhouston` or `spencerhuck` (prior-tenant terms), and it refused eddie. The human chose to change the domain and keep the guard (2026-10-14). Connecting real `@34dev` mailboxes will hit the same guard and needs a decision at that STOP gate.

**Incident: a pip install reached live's venv (2026-10-14).** `/opt/jarvis/venv` is a copy of live's venv, and its `pip` script still started `/root/jarvis-app/.venv/bin/python3`. `pip install psycopg[binary] alembic` therefore installed psycopg, psycopg-binary, SQLAlchemy, alembic, Mako and greenlet (plus the `alembic` and `mako-render` scripts) into live's venv. The change was additive and live was not restarted. With the human's yes, exactly those six were uninstalled from live's venv; the before/after freeze diff showed only those lines, and live kept running (same PID). The packages were reinstalled with `/opt/jarvis/venv/bin/python -m pip`, and all 40 scripts in `/opt/jarvis/venv/bin` were repointed to `/opt/jarvis/venv/bin/python`. The provisioner and `prove_isolation.py` now refuse any platform venv script whose interpreter is under `/root`; `AGENTS.md` says to install only with `python -m pip`.

**Proof (app `e5f5624` plus the docs and sandbox commit, skills `74c0fb1`):**
- Sandbox gate, 21 suites green, among them: `verify_sentinel_gate.py` 69/69, `verify_sentinel_broker.py` 69/69 (Postgres, two sandboxes), `verify_switches.py` 25/25, `verify_postgres.py` 33/33, `verify_model_proxy.py` 32/32, `verify_fullbody_fixture.py` 17/17, showingsmart 66/66, `verify_provision_isolation.py` 66/66, `verify_metrics.py` 62/62, `verify_broker.py` 45/45. Fixture hash unchanged; live PID unchanged.
- Canaries, each in a copied tree:
  - Sentinel broker: removing the shadow branch or the callback signature check each fail six or more checks; ignoring grants fails 2; never reading taint 3; dropping the would-have feed line 4; un-gating the held view 1; an unbrokered chat adapter 5; removing the source rule 3. Skipping the issued-to-user check is caught by the unit suite (1).
  - Switches: a typo read as on fails 2; ignoring the broker switch 5; the model switch 2; the Sentinel switch 2; skipping the boot database check 4.
  - Postgres: an inert host/trust/all rule on disk makes `setup_postgres check` fail on all three counts; a source that moved after a dump is reported as a mismatch; a disabled trigger is reported; probing as root instead of the agent fails 7.
  - Approved send not held in practice mode fails 6; a producer that does not write fails 7; ignoring SSE output deltas fails 1; costing an unpriced model as $0 fails 2; bypassing the cost guard's broker path fails 3; YAML booleans not normalised and no Sentinel-off line for root tenants fail 3.
- On the host: isocheck re-stamped at those commits, `prove_isolation.py --slug isocheck` 119/119. New checks: the agent is refused by pg_hba under three role and database combinations (so it cannot write approvals); the app connects as its role at schema 0001 and cannot delete from `actions`; the agent's key cannot decide an approval; the app's DSN and switches match `/api/health`; no venv shebang under `/root`; the agent's send is held with one shadowed audit row, its proposal in `held_actions` and one "would have" feed line naming the held action. On-host canary: `--reconfigure` with Sentinel off failed exactly those 7 held-write checks; reconfiguring back restored 119/119. A real `isocheck-backup` run dumped the database and proved the restore (append-only trigger intact, 2 audit rows).
- `verify_postgres.py` B: `jarvis-rehearsal-agent` and `jarvis-isocheck-agent` are each refused by pg_hba for their own tenant's database, the verify database and `postgres`, and find nothing listening on TCP.

**Known gaps:**
- Sentinel runs inside the app process, outside the agent's user but not a separate process (9.4).
- The run id is asserted by the agent, so a run could claim a fresh id to shed taint.
- The model check (9.4, second half), surrogate tokens, the egress proxy and email scrubbing (9.6) are not built.
- The PWA scope picker is not built: the frontend was not rebuilt (no node_modules here; static was built from live's tree). The API offers the scopes and the owner's session approves once.
- `until` grant durations are undecided.
- The legacy free-text `approval_handler.py` token path still exists in the skills; it needs SMTP, which two-user tenants do not have.
- The usage day boundary is UTC; prices are provisional.

## Decided 2026-10-14: Eddie, Blake and Martin each have their own Jarvis in practice mode (platform build item 7)

**Approval (human, verbatim in the conversation):** "Provision all three now in shadow mode with provisional values; the agents can rename theirs later." Display names "Eddie's Assistant", "Blake's Assistant", "Martin's Assistant", timezone America/Chicago. Mailbox domain changed to `assistant.invalid` by the human (see the placeholder note above).

- **Tenants:** `docs/tenants/{eddie,blake,martin}.yaml`, provisioned from app `3a349fb`, skills `74c0fb1`. Each has two-user isolation (`jarvis-<slug>` app, `jarvis-<slug>-agent` gateway), its own Postgres database `jarvis_<slug>`, sentinel shadow, writes blocked, switches on, claude-sonnet-5-5 and claude-haiku-5-5, loopback only (apps 8750/8760/8770, gateways 8752/8762/8772), a nightly backup timer, no tunnel, and no Google account.
- **Proof:**
  - `prove_isolation.py` 119/119 for each of eddie, blake and martin. This includes the agent's send through the broker held with one audit row, its proposal stored and one "would have" feed line; the agent refused by Postgres; the agent's key refused on approvals.
  - `verify_postgres.py` 72/72: every agent user, including the three new ones, is refused for its own database, every other tenant's, the verify database and `postgres`.
  - Cross-tenant file probe: each new agent was denied all 57 reads of the other tenants' secrets, private state, Hermes config and runtime config; each can read its own runtime config (presence twin).
  - Units report posture blocked, `JARVIS_ALLOW_WRITES` absent, `JARVIS_SENTINEL_MODE=shadow`.
- **Not yet usable by the people themselves:**
  - ~~No model key is installed.~~ Installed 2026-10-15; the agents answer through the proxy.
  - **No owner account exists.** The passphrase in each app env file is the operator's break-glass login, which may not see bodies and so cannot chat (`require_bodies`). Each person needs an owner PIN (`app.accounts_cli set-pin owner --role owner`, run as the app user), handed to them directly. Minting credentials for real people waits for a human yes.
  - There is no tunnel, so they can only be reached from the host. Ingress (cloudflared) is outside the agent's permissions.
- **Memory:** each tenant uses about 200 MB (app about 74 MB, gateway about 126 MB). About 1.5 GB is available with all five tenants and live running.

## Decided 2026-10-14: rehearsal moved to the platform layout in its approved restart window

**Approval (human, verbatim in the conversation):** "Yes, one restart window", on these conditions: the full verify gate green on the exact commits first; take the agent lock and report posture blocked and `JARVIS_ALLOW_WRITES` unset; broker, Sentinel and model proxy behind switches; after the restart, the isolation proof plus a refresh, agent refused by Postgres and the OAuth client, and a held write as "would have"; automatic rollback within 2 minutes; report, release the lock, push.

- **How: `provision_tenant.py --upgrade`.** Rehearsal's two-user layout was made by hand in window 1, so re-provisioning would have meant deleting it. `--upgrade` instead:
  - stages the new app and skills clones and renders their config while the old version still runs;
  - stops the tenant and snapshots `/opt/<slug>` plus its units (`/root/backups/<slug>/upgrade-<stamp>/opt.tar`, `units.tar`, root 600);
  - swaps the code in, carrying skill `data/` dirs and `jarvis-app/data` over;
  - copies the shared threads database behind the app with the SQLite backup API, checking row counts table by table;
  - archives every old drop-in, renders units fresh, re-trims the agent `.env`, and applies ownership without replacing the app's databases or OAuth client (a refresh token only works with the client that issued it);
  - creates the tenant's Postgres database and starts the tenant.
  If app or gateway is not healthy (unit active, `/api/ping`, gateway `/health` ok, no `JARVIS_ALLOW_WRITES`) within 120 s, it restores the snapshot, drops the new database after dumping it, and restarts the old version. It refuses a mailbox-connected tenant without `--restart-approved`, a manifest that arms writes, and legacy approval files holding records.
- **Proof before the restart:**
  - `docs/platform/verify_upgrade.py` 38/38 at `6d847b0`. It builds a replica of rehearsal's tree (same commits, rendered config, preflight, hand-made drop-ins and modes; fresh secrets; no data copied) and seeds tasks, leads, a thread, a counter, an account and skill data files.
  - A commit whose app cannot start rolled back by itself: old commits, byte-identical units, no database or pg_hba rule left, nothing lost, old version healthy.
  - The real upgrade kept every row and file, moved threads behind the app (agent denied, the shared tasks database still readable as the twin), archived `writes.conf` and `10-tree.conf`, and passed `prove_isolation.py` 119/119.
  - Canaries:
    - Wiping the app databases, skipping the threads move and keeping old drop-ins failed 8 checks; with the app databases wiped, the app still reported healthy, which is why the verify checks data separately.
    - A rollback that does not restore units failed its 3 checks.
  - Full sandbox gate (21 suites) green at `6d847b0`.
- **The window (2026-10-14 04:54 UTC):**
  - Before the restart: lock taken; posture blocked; `JARVIS_ALLOW_WRITES` absent from both units and the env file; `runtime.safety.allow_writes` false.
  - `--upgrade --restart-approved` finished in 10 s with app and gateway healthy; no rollback.
  - Data delta: tasks, leads, threads, the account and the connected Google row unchanged; the OAuth client and both skill data files byte-identical. Counters grew from 4 to 10 rows, from the proof runs.
- **After the restart:** `prove_isolation.py --slug rehearsal --expect-app-source tenant_connection --refresh-check` passed 118 of 121.
  - Passed: a real token refresh as the app user (`refreshed_at` moved, no error); the agent refused by pg_hba three ways; the agent denied the OAuth client; the agent's send through the broker held, with one audit row, its proposal and one "would have" feed line; and the rest of the isolation proof.
  - **Failed: 3 checks, one cause.** The app unit has no `JARVIS_APP_ROOT`, so `google_creds` inside the app cannot find the connection and the broker reports the connected mailbox as `not_connected`. Window 1's `writes.conf` set it, and the upgrade archived that drop-in. The templates never carried it, and the proof's probe supplied it itself, so new tenants (no mailbox, `not_connected` either way) could not show it.
  - Fixed in `c96c500`: the variable is in the app and job templates; the proof reads it from the running process and its probe no longer supplies it (`verify_provision_isolation.py` 67/67, canary fails 1). Eddie, Blake, Martin and isocheck were reconfigured and pass 120/120.
  - **Corrective restart, 2026-10-14 19:45 UTC (human yes with three conditions):**
    - Rehearsal's drop-ins and the four config files `--reconfigure` may rewrite were archived first to `/root/archives/rehearsal-before-approot-20261014T194518Z/` (root 700). The rollback is one command, `sh …/rollback.sh`, and was stated before the restart.
    - Posture before and after: blocked; `JARVIS_ALLOW_WRITES` absent from both units, the env file and the app process; `allow_writes` false; Sentinel shadow.
    - `--reconfigure --restart-approved` changed exactly the six app and job drop-ins and came back healthy.
    - `prove_isolation.py --slug rehearsal --expect-app-source tenant_connection --refresh-check` passed 121/121: the broker now reports `tenant_connection`, and a real refresh moved `refreshed_at`.
    - Condition 3 (the provisioner bug): the templates carry `JARVIS_APP_ROOT` (`c96c500`), and the post-provision proof checks the running app's value. Eddie, Blake and Martin each have it in the app process and in all six app and job drop-ins.
- **Known gap:** `google_creds` returning `not_connected` when it cannot find the app is a silent fallback. The proof now catches the cause on every tenant, but the skills code should log and count it.

## Decided 2026-10-15: tenant agents chat through the app (platform build item 6 completed)

**Human action:** the platform key was installed at `/etc/jarvis/anthropic.env` (root 600) and checked with a 200 from the API, by a human. The agent checked only owner, mode and the line's shape, never its value.

- **Restart:** under the lock, the apps of the mailbox-less tenants (eddie, blake, martin, isocheck) were restarted. Each came back healthy with the key in the app process, no `JARVIS_ALLOW_WRITES`, and Sentinel shadow. Rehearsal (real mailbox) was not restarted: it picks the key up at its next human-approved restart.
- **Agents reach Claude:** `prove_isolation.py` passes 121/121 on all four. With a key installed it checks that the app's key matches the file by hash, that the gateway does not hold it, and that the agent's stand-in through the proxy gets a real model answer (`200 message`).
- **The real Ask round trip:** `docs/model/verify_tenant_chat.py --slug isocheck` passes 11/11. Nothing is stubbed:
  - the owner signs in and opens a fresh thread, and the message streams back with no error;
  - the app's own claim check confirms the task;
  - a `source: chat` task with a source reference appears in the tenant's tasks database, and the transcript holds the answer;
  - the usage ledger grew (4 requests, 863 tokens, $0.158, priced, none unpriced);
  - no `executed` audit row was added.
  The isocheck owner PIN was minted with `accounts_cli` straight into `/root/onboarding/isocheck-owner-pin.txt` (root 600) and never printed.
- **Bug found by the round trip:** on its first run the app told the person "No task was created" while the row existed. `_tasks_written_since` swallowed a `PermissionError`: isocheck's hermes home had become 0700 (it is provisioned 0710), which locks the app out of the shared state. The trigger was not found; the proof run, the chat, `accounts_cli` and the gateway (`HERMES_HOME_MODE=0710`) were each ruled out. Fixes:
  - `app/chat.py`: an unreadable store is logged, counted (`chat_verify_store_unreadable`) and shown as "could not read your task list, check the Tasks tab", never as "nothing was written". `verify_chat_honesty.py` section 5; the old fallback fails 4 checks.
  - `prove_isolation.py` checks the home is 0710 and that the app can open the shared tasks database. On-host canary: 0700 fails 7 checks, including the held write's feed line (the app could not write the feed either).
  - The running tenants still have the old `chat.py` until their code is redeployed. `--upgrade` refuses a tenant that already has its database, so there is no redeploy path for new-layout tenants yet. *(Fixed 2026-10-15: eddie, blake, martin redeployed; see below.)*
- **Not done for the three people:** they have no owner account (see item 7 above), so the chat round trip ran on isocheck only. Their agents' model path is proven by `prove_isolation.py`.

**Live hygiene, fixed in the same pass (2026-10-15):**
- `verify_email_column.py` imported live's code from `/root/jarvis-app`, and Python wrote `app/__pycache__/accounts_cli.cpython-312.pyc` into live's tree. It is a bytecode cache of live's unchanged source: live was not restarted (same PID), `git status` is clean, and it was left in place, since deleting it would be another write.
- A scan found 20 scripts that imported live, bound a working path to live code or `/root/.hermes`, or ran live's venv. Two canaries (`mutate_config_apply.py`, `verify_failure_event.py`) rewrite live source files in place; `mutate_dedupe.py` targets `/root/.hermes`.
  - Scripts in use were repointed to the platform tree (`VERIFY_APP_ROOT`) and platform venv: the auth suites and their canaries, metrics, approval executor, sweeps and tasks canaries.
  - The rest now refuse to start until ported.
  - `provision_tenant.py` retires `venv: shared` (live's interpreter).
- `docs/platform/verify_no_live_targets.py` fails on any such script unless it refuses to start; its canary fires on each rule and not on guards. It is in the gate.
- `docs/platform/gate.sh` is the gate, now in the repo. It ends by counting files written into live's tree during the run: 0.

**Verify fixture, as asked (human, earlier session):** dana's account email was a real `34dev.com` address. It is now `dana@testco.example.com`, matching the fixture's `identity.json`. The fixture was unfrozen for that one row, then re-frozen (`chattr +i`): integrity ok, no real-domain rows left, fixture hash `1930464a3c2bc0fc` → `690fde70ee171b51`.
- The two auth suites now pass: `verify_email_column.py` 29/29 and `verify_auth_google.py` 24/24. They work on a scratch copy put back in the pre-migration shape and on the sandbox's own accounts copy, never the frozen fixture. Canaries: backfill disabled fails 6; `mutate_auth_google.py` 5/5.
- Still in the fixture: `hermes/state/activity_feed.jsonl.pre-mask` holds 8 lines with real addresses (a pre-masking backup). Removing it is deleting data, so it waits for a human yes. *(Shredded 2026-10-15 with a human yes; see the next section.)*

**Gate:** 24 suites green at this commit (the 21 before, plus the two auth suites and the live-target scan). Fixture unchanged during the run; live PID 2185197; 0 files written into live's tree.

## Decided 2026-10-15: model key on rehearsal, three people redeployed, cost check corrected, pre-mask file shredded

Human yes for each item, 2026-10-15.

- **Rehearsal restart for the model key.** Lock held. Drop-ins and config archived first to `/root/archives/rehearsal-before-keyrestart-20261015T230846Z/` (rollback: `sh .../rollback.sh`). Posture before and after: preflight `posture=blocked`, `writes=0`, `JARVIS_ALLOW_WRITES` unset on app and gateway. Healthy 6 s after restart, inside the 2-minute auto-rollback window. The app now loads the key, and the gateway still does not. `prove_isolation.py --expect-app-source tenant_connection --refresh-check`: 124/124, including the 0710 home check, a model answer through the proxy, and a real token refresh.
- **`--upgrade` for tenants that already have their database.** It refused eddie (`jarvis_eddie already exists`), because it was written for rehearsal's first move. Now:
  - an existing database is kept, dumped into the snapshot (root 600), and its grants are re-applied;
  - rollback never drops it;
  - if `SCHEMA_HEAD` in the staged `app/platform_db.py` differs from the database's alembic version, the upgrade refuses before anything stops, because a migration is a human yes.
- **`verify_upgrade.py --existing-db`.** Sections A–D need an old-layout source, and none is left: rehearsal is new-layout, so its replica cannot start. This mode provisions the replica from eddie's manifest at eddie's commit (3a349fb) and runs section E: 14/14. Canaries:
  - a schema mismatch is refused, and the same app process keeps running;
  - a broken commit rolls back, the database and its seeded row survive, and the app is back at 3a349fb.
- **Eddie, Blake, Martin** went from 3a349fb to b38cb64, which carries the `chat.py` unreadable-store fix. Proofs are 123/123 each. Ledger delta per tenant:
  - accounts 0 → 0, threads 1 → 1, usage rows 1 → 1, approvals 0 → 0, schema 0001 → 0001;
  - `actions` 3 → 4: the proof's own probe, a held `external_send` to `probe@isolation.invalid` (outcome `shadowed`, verdict `deny`);
  - the tasks databases are still empty files.
  Posture after: blocked, writes unset, the key in the app only.
- **The cost check, corrected.** The price table is USD per million, and `model_proxy.price` divides correctly. The ledger recomputes by hand: isocheck's day row at 80 in, 2,562 out, 259,530 cache read and 78,054 cache write gives $0.409231, matching the ledger exactly. The fault was `verify_tenant_chat.py`, which reported only input + output tokens (the "932 tokens"). It now reports all four classes and recomputes the cost from the split. Measured round trip:
  - 7 requests and 221,898 tokens (16 in, 1,273 out, 184,413 cache read, 36,196 cache write);
  - $0.210202, a blended $0.95/M;
  - cache writes are about 65% of the cost.
  Canary `--price-scale 1000` fails both cost checks.
- **`activity_feed.jsonl.pre-mask`.** It was never committed: no ref in the app repo (GitHub origin included) or the skills repo has the path, and its blob hash is in neither object store. It was shredded (`shred -u -n 3 -z`) from the fixture, which was then re-frozen. Fixture: 682 → 681 files, hash `690fde70ee171b51` → `4b747e7cd87522e4`. Identical copies (same sha256) remain in nine verify sandboxes under `/tmp/claude-0/-root/*/hermes/state/`; deleting those waits for a human yes.
- **Hermes-home watcher.** A root bpftrace program logs any mode other than 0710 set on `/opt/*/hermes` (see AGENTS.md, current known state). Remove by 2026-10-29. The hourly ctime changes on the homes were file creation (`kanban.db-wal`), not mode changes. Neither the isolation proof nor a full chat round trip reproduced the 0700 reset under trace.

## Decided 2026-10-15: owner logins for Eddie, Blake and Martin; their Jarvis holds a real request; the 0700 cause found

Human yes, 2026-10-15, for the owner logins and for deleting the sandbox copies of the pre-mask file.

- **Owner logins.** One owner PIN per tenant, minted with `app.accounts_cli set-pin owner --role owner` as the app user, written straight to `/root/onboarding/<slug>-owner-pin.txt` (root 600) and never printed. Accounts 0 → 1 each. The databases stay app-owned, mode 600.
- **Chat on each person's tenant.** `verify_tenant_chat.py` passes 13/13 on eddie, blake and martin: a task asked for in chat lands in that tenant's own list, sourced from chat. Each round trip cost $0.14–$0.16, priced, and executed rows stayed 0.
- **A real outbound request, held.** The owner asked each Jarvis to email `buyer@client.invalid`.
  - Eddie and Martin tried at once. Blake's agent first drafted the email and asked for approval, then tried after "send it".
  - In all three, the attempt is one `actions` row: `shadowed`, `external_send`, `email_send`, to `buyer@client.invalid`, verdict `deny`.
  - A feed line reads "Practice mode: Jarvis would have tried to send an email to buyer@client.invalid".
  - Executed rows stayed 0 and the outbox stayed empty.
  - It is now `docs/model/verify_tenant_outbound.py`: 5/5 on each of eddie, blake and martin, with a fresh recipient per run and the home still 0710 afterwards. Canary `--pg-db` pointed at another tenant's ledger fails both audit checks.
- **The 0700 cause.** The first chats failed the app's own check (`verified ok: None`): "Permission denied" on `hermes/state`, because the homes were 0700 again. The watcher caught the gateway process itself, running as the agent user, calling `chmod(/opt/<slug>/hermes, 0700)` the same second it rewrote `auth.json`.
  - The code path is `hermes_cli/auth.py` `_save_auth_store` → `hermes_constants.secure_parent_dir`. It hardcodes 0700 on the parent of `auth.json` (the home) and ignores `HERMES_HOME_MODE`, which only `_secure_dir` honours.
  - When it happens: on isocheck, at its first chat; on eddie, blake and martin, at their first chat after `--upgrade` rewrote the agent's `.env`. A plain gateway restart followed by a chat did not reproduce it on isocheck.
  - The homes were restored to 0710, and a second chat on each kept 0710.
  - Fixing it means patching the shared Hermes install that live's gateway also runs, so it waits for a human yes.
  - `verify_tenant_chat.py` now checks that the home is still 0710 after the chat. Canary: home set to 0700 by hand fails that check, 13/14.
- **Sandbox copies of the pre-mask file.** All nine were already gone. The gate rebuilt those sandboxes from the fixture after the original was shredded. They were removed by the rebuild, not shredded. No copy is left on the filesystem.

## Decided 2026-10-16: Hermes keeps the home at 0710; owners carry internal; chat errors are counted

- **Hermes patch (human yes: "patch").** Hermes scale-vault-local commit 0f8bc16 (local only; origin is upstream NousResearch, never pushed).
  - Change: `secure_parent_dir` uses `HERMES_HOME_MODE` when the parent is `HERMES_HOME` itself, and 0700 everywhere else.
  - Proof: three new unit tests (the mode is kept for the home, only for the home, and 0700 without the variable); 53 passed. A real `_save_auth_store` run as isocheck's agent kept 0710; the same run without the variable set 0700.
  - Live sets no `HERMES_HOME_MODE`, so its behaviour is unchanged.
  - Rehearsal's gateway started before the patch and runs the old code until a restart. That restart is a human STOP because the tenant has a real mailbox.
- **Owners were missing the internal role.** Every checked-in manifest gave the owner `roles: ["owner"]`.
  - Task-skill roles are literal and `task.add` needs internal or assistant, so an owner-attributed add was denied. Example: "owner lacks required role 'internal or assistant' for 'task.add'" on martin. Whether chat filed a task depended on which `--actor` the model passed.
  - The verify fixture's owner (dana) is owner + internal; the manifests now match it.
  - `validate_manifest` refuses an owner without internal. `verify_provision_isolation.py` checks this refusal; with the check removed, it fails 68/69.
  - `--reconfigure` now re-applies each existing person's roles from the manifest. It refuses a person the tenant doesn't have, because adding people still needs a re-provision.
  - Canary on the live tenants: the same `add_task.py --actor <owner email>` run as each agent was DENY before the reconfigure and ALLOW after, on eddie, blake and martin.
  - Rehearsal's manifest is fixed in the file only. Applying it needs a reconfigure, which restarts a tenant with a real mailbox (human STOP).
- **`--reconfigure` waits for health.** It restarts the units, then fails unless the app answers /api/ping and the gateway /health within the upgrade timeout.
- **Chat errors are counted.** Every error event the Ask tab shows bumps `chat_error_shown` and logs a warning. `verify_chat_honesty.py` section 6 points the gateway URL at a dead port and expects +1 and a ConnectError in the log (49/49; with the change reverted, it fails 2). Eddie's one transient post-restart chat error was never reproduced; it would now be counted.
- **Rollout.** eddie, blake and martin run app bfd501b (`--upgrade`, existing databases kept, proofs 123/123).
- **After the roles fix**, on each of eddie, blake and martin:
  - Proof 123/123, `verify_tenant_chat` 14/14 (the home still 0710), `verify_tenant_outbound` 5/5.
  - The task-skill authz log shows 0 DENY since the reconfigure (eddie 1 ALLOW and 1 DEFAULT, blake 2 ALLOW, martin 2 ALLOW).
  - The proof runs left a few test tasks ("Roles fix proof …", "Schedule the inspection for …") in each tenant's list. They were cleared the same day; see below.

## Decided 2026-10-16: rehearsal reconfigured, the Hermes patch kept on GitHub, proof tasks cleared

Human yes, 2026-10-16, for all three.

- **Rehearsal `--reconfigure --restart-approved`.** Same conditions as the key restart:
  - Agent lock held. Posture before and after: writes unset, sentinel shadow, app ping 200.
  - Drop-ins and config saved to `/root/archives/rehearsal-before-rolesrestart-20261016T031207Z/`, with `rollback.sh` next to them.
  - Only `identity.json` changed: the owner gained `internal`.
  - Proof (`--expect-app-source tenant_connection --refresh-check`) 124/124, including the home at 0710 and a real token refresh.
  - Two-minute watch: 12 of 12 app-ping and gateway-health checks returned 200, the PIDs didn't change, and NRestarts stayed 0.
  - The gateway now runs the patched Hermes.
- **Where the Hermes patch lives.** Branch `hermes-agent/scale-vault-local` in github.com/tegad123/jarvis-skills, head `0f8bc1642be6bee78c678651b394ccede905278e`. On top of upstream 36cb5ae it carries 276e5c7 (the unit-name fix) and 0f8bc16 (`secure_parent_dir`).
  - The branch is unrelated to the skills history: it is upstream's full history plus the two local commits.
  - The installed copy at /usr/local/lib/hermes-agent is a 3-commit shallow clone, and pushing from it fails because GitHub needs the ancestors. So the branch was pushed from a throwaway full clone of upstream that fetched the two local commits; the hashes are unchanged.
  - The installed copy's remotes and object store were left as they were, and both local commits scanned clean for secrets before the push.
- **Proof tasks cleared.** eddie 6, blake 4, martin 3. Every one was a proof task: "Schedule the inspection for …" and "Order the survey for …" from `verify_tenant_chat`, plus "Roles fix proof …" canaries. The script refused to delete if any other title was present.
  - Backups: `/root/backups/<slug>/tasks-before-proof-cleanup-20261016T032103Z.db` (600).
  - All three lists are at 0, and the database and its side files are still agent-owned, 660.
- **Proofs stay out of tenant data from now on.** `verify_tenant_chat.py` deletes the one task it created (by `task_id`, after its checks) and asserts that the list ends exactly as it began.
  - Each of eddie, blake and martin: 15/15, list 0 → 0.
  - Canary `--keep-task` on isocheck fails that check (8 → 9); the leftover was removed.
  - Not covered: the verifiers' Ask-tab conversations stay in each tenant's chat history (there is no thread-delete route), and their `actions` rows and "would have" feed lines stay because the audit log is append-only.

## Decided 2026-10-16: Part A at <slug>.yamin.io behind Access; PIN lockout first; assistant accounts on Workspace

Human decisions: Part A approved with `<slug>.yamin.io`. The human runs the Cloudflare tunnel and Access steps from `docs/platform/ingress/PART-A.md`. No tenant goes public before PIN lockout. Assistant accounts will be Google Workspace. Order: A, then C (Google), then B (Slack) later.

- **PIN lockout.** Five wrong PINs for one person key lock it for 15 minutes (`APP_LOGIN_LOCK_FAILURES`, `APP_LOGIN_LOCK_SECONDS`), from any address. The right PIN is refused while locked. Keys that don't exist lock the same way, so the lock never reveals which keys are real.
  - Every failure, lock and refused attempt is logged (`jarvis.auth`) and counted (`login_pin_failed`, `login_locked`, `login_refused_locked`).
  - State lives in the app's memory, so a restart clears locks. The per-IP throttle (10 per 5 minutes) stays.
  - `docs/auth/verify_pin_lockout.py`: sandbox 7/7, in the gate. Canary `--canary-no-lock` gives 2/7.
- **Not yet fixed:** the deployed login screen says "Wait a few minutes" on a 429. The committed `static/` build dates from 2026-08-30, and a rebuild would ship every later frontend change, so it is its own supervised task (human, 2026-10-17: later).
- **OAuth client.** `/etc/jarvis/oauth_client.json` is a web client in Google Cloud project `jarvis-platform-507204` (number 222647815845). Its registered redirects are `localhost:8085`, `app.yamin.io` and `rehearsal.yamin.io`.
  - The owning organization is not in the file. Whether 34dev accounts count as internal depends on it: an Internal consent screen only admits the owning organization's users.
  - Rehearsal connected a consumer `@gmail.com` account, which only an External app allows.
- **Tunnel draft.** `docs/platform/ingress/yamin-config.yml` is today's config plus one rule per person's app. It passes `cloudflared ingress validate`, and each host maps to its own port. The gateways stay on loopback.
- **PWA install bypass.** The manifest fetch carries no cookies, so `/manifest.webmanifest` and `/icons` get an Access bypass.

## Decided 2026-10-17: a second runner, the lock made enforced, single lead only

- **What happened.** At 02:30 UTC on 10-17 a Cursor agent worker (`cursor-agent ... worker start`, pid 2051561, detached under systemd, working directory `/root/jarvis-app`) started three seconds after this server's Cursor host reconnected. It then ran a second copy of the lead's own conversation:
  - Only that conversation's ID appears in the agent-exec log.
  - Its agent store and the user store logged `lock_lost` to another writer from 02:36 on.
  - At 02:47 it committed the lead's in-progress work as `fdc89b7` and ran `--upgrade` on eddie, blake and martin, while the lead held the advisory lock.
  - The advisory lock could not stop it: the duplicate believed it was "lead" and already held the lock.
  - The lead never launched it; the only helper used before was a read-only research agent on 10-16.
- **Stopped** 2026-10-17 23:3x with SIGTERM. It did not respawn within 45 s.
  - Its command line held a Cursor API key in clear text, readable by anyone who can list processes on this host. Rotating it is the human's call.
- **What it changed.**
  - `fdc89b7`: `app/auth.py`, `app/config.py`, `app/main.py`, `verify_pin_lockout.py` and `gate.sh` are byte-identical to the lead's verified versions.
  - `PART-A-RUNBOOK.md` duplicated `PART-A.md`. It is deleted; its stdin token-install snippet was kept.
  - `yamin-config.yml` was replaced by the lead's validated version.
  - On the host: the three upgrades (units, drop-ins, logrotate and pg_hba rewritten as `--upgrade` does, snapshots `upgrade-20261017T0247*`). Nothing else changed in `/etc` or `/usr/local`, and it made no skills or Hermes commits.
  - The writes in `/root/jarvis-app/.git` are the shared object store of the platform worktree. Live's HEAD, reflog and tree are unchanged.
  - Posture after its upgrades: writes unset, sentinel shadow. Proofs after it are below.
- **The lock is enforced (human decision).** `flock(2)` on `/root/.jarvis-agent-lock`; the kernel drops it when the holder exits, so a lock can't go stale.
  - `agent_lock.py run --who --why -- CMD` holds it for CMD's lifetime; `acquire`/`release` are gone. The holder's PID is read from `/proc/locks`.
  - A process passes if it takes the lock or if the holder is one of its ancestors. A second runner outside that process tree is refused even if it knows the protocol.
  - The provisioner holds it for every mode that changes the server.
  - `/usr/local/sbin/systemctl` (installed by `scripts/install_agent_lock_guard.sh`) is the same file. It gates start, stop, restart, reload, kill, enable, disable, mask and unmask of tenant units (`/opt/<slug>` with `jarvis-app`, `hermes` and a unit) and passes everything else to `/usr/bin/systemctl`. It refuses before anything stops, and logs to the journal under the tag `jarvis-agent-lock`.
  - systemd's own starts (boot, `Restart=`) never pass through it. A human or agent can still bypass it with `/usr/bin/systemctl`; AGENTS.md rule 8 forbids that.
- **Proof.** `docs/platform/verify_agent_lock.py` 19/19, in the gate:
  - 20 racers, exactly one wins.
  - The provisioner's `--upgrade`, `--reconfigure` and `--reconfigure --restart-approved` are refused with eddie untouched.
  - A tenant restart is refused with the PID unchanged and logged, and goes through once the lock is free.
  - Canary (ensure() a no-op): 13/19.
  - `--live-upgrade-race` 24/24: a real `--upgrade` of eddie held the lock, and a second `--upgrade` of eddie and one of blake started during it were refused. The first finished healthy with one snapshot, and eddie now runs b79867b.
- **Single lead only (human decision).** No helper agents of any kind on this server: no subagents, no background or cloud agents, no Cursor agent workers (AGENTS.md rule 9).
- **Proofs after the second runner's upgrades.** `prove_isolation` 123/123 on each of eddie (b79867b), blake and martin (fdc89b7, app code identical); homes 0710.
  - `verify_pin_lockout.py --url http://127.0.0.1:<port> --wait-expiry`: 7/7 on each. Right PIN 200; 4×401 then 429 locked; the right PIN refused with 429; counters +5/+1/+1; the lock and the refusal in the journal; the right PIN works again after 15 minutes.
  - The second runner had tried the same live probe at 02:48. It failed at its first step (the onboarding file is accounts_cli output, not a bare PIN), so it sent no wrong PINs.
  - The probe now extracts the PIN the way `verify_tenant_chat` does and never prints a response body, because a 422 echoes the input. Its logs and the second runner's /tmp files held no PIN pattern and were shredded.

## Decided 2026-10-21: a fourth tenant, tega; the worker came back

- **Tega provisioned (human request).** Slug `tega`, owner Tega Umukoro (`owner`, `internal`), "Tega's Assistant", America/Chicago, shadow, writes blocked, `claude-sonnet-5-5`, mailbox `tega-assistant@assistant.invalid`. Ports app 8780, gateway 8782, hooks 18798 (collision check clean). App 81fde8f, skills 74c0fb1.
- **Owner email.** The request carried the placeholder `<your email>`; the human chose `tega@34dev.com`. The prior-tenant preflight guard (`34dev|cqhouston|spencerhuck`) refused both units on `identity.json`, as the 2026-10-14 entry predicted. The units were left in a 5-second restart loop and were stopped under the lock. The human chose `tegad8@gmail.com` and said yes to tearing down the never-started build. Teardown archived the empty `jarvis_tega` database (restore proven) before dropping it; re-provision passed. The guard is unchanged.
  - The provisioner prints the owner passphrase once; its two logs were shredded. The passphrase is also in `/etc/jarvis/tega-app.env` (root 600).
- **Proofs on loopback.** `prove_isolation` 123/123. `verify_tenant_outbound` 5/5: one shadowed `external_send`, executed rows 0 -> 0. `verify_tenant_chat` 15/15: +$0.090744, task list 0 -> 0.
  - The first chat run crashed: it started before the tenant's first boot had created the tasks table. Re-run green. The crash is kept, since treating a missing table as empty would hide a wrong path.
- **`verify_tenant_chat.py --url URL [--access-token-file F]`** for the public-address proof. With `--url`, it first checks that `/api/ping` answers with the app's JSON. The app serves its web page with 200 for unknown paths, so a status check alone passed the canary `--url http://127.0.0.1:8780/not-the-app`. It now fails at that check. Loopback through `--url`: 16/16.
- **Chat task filing depends on the model.** The PWA chat does not tell the gateway who is signed in. The model passes the owner's address itself (`provenance=asserted` in `authz.log`). One `--url` run of the four on tega got 12/15: the model declined to guess who was asking, filed nothing, and the reply was still clean. Not fixed here: passing a verified identity from app to gateway is an app change and needs a decision.
- **The Cursor agent worker came back.** `cursor-agent ... worker start` pid 4099559 started at 02:37:25, 30 s after this session's host and as its child, workspace `/root/jarvis-app`. At 02:49:06, after the lead's 12/15 run had returned, it re-ran the lead's exact `--url` command into the same log file (16/16, its task cleaned up). At 02:49:35 it wrote an unverified TEGA entry into `AGENTS.md` in the platform worktree.
  - It made no commits, took no lock (the `jarvis-agent-lock` journal holds only the lead's operations), and changed nothing else after 02:43 in the repos, `/opt/tega`, `/etc/jarvis`, `/etc/cloudflared`, `/root/onboarding` or live.
  - Stopped with SIGTERM; no respawn within 20 s. Its `AGENTS.md` edit was reverted. The lead checked its one claim (`is-internal` false, `has-role internal` true for the owner) and wrote its own entry.
- **Part A for tega only.** `docs/platform/ingress/yamin-config-tega.yml` is the installed `/etc/cloudflared/config.yml` plus one rule (`tega.yamin.io` -> 8780). Eddie, Blake and Martin are not in it, because their Access steps aren't done. `yamin-config.yml` (the full draft) gains the same rule. Both pass `ingress validate`. In the tega file, tega maps to 8780, rehearsal to 8730, and eddie and unknown hosts to 404.

## Decided 2026-10-21: tega public; the app tells the gateway who is signed in (local Hermes modification #2)

Human yes, 2026-10-21, for the Hermes change, with conditions: inertness proven before any restart; an opaque app-issued reference accepted only from the app's key over loopback; tools cannot set or forge it; only tega's and isocheck's gateways restarted; commit on scale-vault-local and push; record it here.

- **Tega on https://tega.yamin.io.** `--reconfigure` did not apply `public_origin`; it now also rewrites the app's `oauth.conf` drop-in (dry-render diffed first). `prove_isolation` 123/123 after the switch and again after the last restart.
  - Access: `/api/ping` with the service token 200 (app JSON), without it 302, with a wrong secret 302. The token file is `KEY=value` lines; both verifiers parse that.
  - PIN lockout on the public address 7/7; the 15-minute lock cleared at about 14:22 UTC.
- **Identity design.** The model never supplies who is speaking.
  - App (`app/actor_refs.py`, `app/chat.py`): each chat turn with a signed-in person mints `app:<token_urlsafe(24)>` in memory, mapped to person and thread. It is valid while the turn runs plus 60 s, at most 900 s, and is sent as `X-Jarvis-Actor-Ref` with a system note naming the person. Counters `chat_turn_actor_ref` / `chat_turn_without_actor` (warning logged).
  - Broker: `POST /api/broker/actor` resolves a reference (`broker_actor_resolved` / `_unknown` / `_expired` / `_refused`). `GET /api/broker/actor/active` counts running turns. Both are loopback with Bearer API_SERVER_KEY.
  - Hermes (modification #2): the API server binds the header only if it matches `^app:[A-Za-z0-9_-]{16,64}$`, comes over loopback, and passes `_check_auth` with a key configured; otherwise it logs a warning and binds nothing. The value lives in its own ContextVar, outside `_VAR_MAP`, and reaches tools as `JARVIS_ACTOR_MSG` only when non-empty.
  - Skills b8c2d1f: `authz` resolves `app:<ref>` through the broker (provenance `transport:app:session`). Inside a gateway cgroup, a self-asserted person is refused, and the system actor is refused while an app turn runs or if the app can't be asked.
- **Local Hermes modification #2.** scale-vault-local 8a04d42 (session var, tool env, API server header) and b0967e8 (shell snapshot), pushed to `hermes-agent/scale-vault-local` in jarvis-skills (head b0967e87a65d). Re-apply or upstream it on any Hermes upgrade. The full patch against 0f8bc16 is `docs/platform/hermes-actor-ref.patch`.
  - Inertness, before any restart: `verify_hermes_actor_ref.py` loads 0f8bc16 and the patched tree in separate processes. With no header, the tool environments, middleware status and real-terminal output are byte-identical. With the patch installed: 24/24.
  - Canaries: `--canary-leak` (the first draft, which bound an empty value every turn) 17/24; `--canary-snapshot` 21/24.
  - Restarted under the lock: tega's and isocheck's gateways only. Live's gateway is still pid 1781 (started 2026-08-24) on the old code in memory, and with no header it behaves as before when it next restarts.
- **Bug found by chat filing, fixed in b0967e8.** Runs 1-3 on tega all used the first run's reference, and run 4 was denied as expired. Hermes's terminal dumps `export -p` into a shared snapshot after each command and sources it before the next, so the first turn's `JARVIS_ACTOR_MSG` overrode every later turn's fresh value. Upstream already strips `HERMES_SESSION_*` from that snapshot for the same reason; `JARVIS_ACTOR_MSG` is now stripped too, which also stops a tool's own `export` from carrying over. The fix denies rather than misattributes once the reference expires. Within 60 s, though, a later turn by a different person in the same tenant could have been attributed to the first person. Tega has one person signed in, and the authz log shows owner only.
  - Hermes's own snapshot tests (`tests/tools/test_snapshot_*`) were not run: pytest is not installed and wasn't added.
  - `verify_tenant_chat.py` now asserts that each turn's reference was never seen in an earlier authz line. Replayed over the buggy runs' log, it flags runs 2 and 3.
- **Proofs after the fix, tega via https://tega.yamin.io.**
  - Chat filing 4/4 (19/19 each, every run its own reference).
  - `--spoof` 19/19: nothing allowed under another identity.
  - `verify_actor_guard` 8/8; `--during-turn` 10/10 with a chat running alongside. Run alone, it finds no turn and fails 3 checks; the docstring now says to start a chat with it.
  - Isocheck (old app, patched Hermes) chat 15/17: only the two app-provenance checks fail, as expected (its app sends no reference, so it files as before, `asserted`). This is the end-to-end inertness check and the provenance check's canary.
- **Live restarted by needrestart.** At 06:58 UTC unattended-upgrades (liblzma5 / xz-utils) ran needrestart, which restarted jarvis-app.service (now pid 4185897). The agent-lock guard allowed it because the lock was free. No gateway was restarted. Whether needrestart may restart live is the human's call.
- **Workers kept returning.** `cursor-agent ... worker` pids 4114988, 4142860 and 66712 were each stopped with SIGTERM after checking they weren't ancestors of the lead's shell; footprint checks found nothing of theirs. Disabling the extension is the human's call.
  - A later one, pid 94245 (worker-dir /root/jarvis-app, started about 16:03 server time), was not stopped in time: it committed and pushed `6a0bbe1` on `platform`, as tegad123 with a Cursor co-author line. That commit is the lead's uncommitted verifier work plus the decisions and AGENTS text above, which the lead checked line by line against its own runs and kept. Its one unverifiable claim, that a worker pid 84010 was stopped, is removed: the lead never saw that pid, and it is not running. 94245 was stopped with SIGTERM at 16:12 UTC (not an ancestor of the lead's shell). Its Hermes, skills and live-tree footprint: none.
  - Its command line carries a Cursor API key (`crsr_…`), which appeared in the lead's terminal output while identifying it. Rotate that key.
- **Skills tests.** 119 tests; 13 failures/errors already on the head before b8c2d1f, identical after.

## Decided 2026-10-21: the tenant agent talks like an assistant and only does real-estate work

From the human's first real test on tega: plain-language persona, honest capabilities from real state, no developer tools, never work around a hold, an app-rendered connect button, and practice mode in plain words. The human saw the new answers, then said yes to the frontend rebuild, the rollout and a schema change for saved buttons (2026-10-21). All four tenants are on app bfe2793, skills b8c2d1f.

- **Tools.** Before (tega's old config through Hermes's own resolver): 15 tools, cronjob, delegate_task, execute_code, memory, patch, process, read_file, search_files, session_search, skill_manage, skill_view, skills_list, terminal, todo, write_file. After: the eight in `scripts/hermes_plugin/jarvis/tools.json` (list_tasks, add_task, complete_task, calendar_upcoming, add_calendar_event, search_email, draft_email, connect_google).
  - They are a Hermes user plugin (no Hermes change). Each runs a fixed action through `run_action.py` with no shell: tasks through the skills' scripts, Google and email through the broker, under the turn's verified actor and skills authz.
  - `platform_toolsets.api_server: [jarvis]`; `tools.tool_search.enabled: off` (it wraps plugin tools behind tool_search/tool_call).
  - The model proxy also strips any tool outside the list (`model_proxy_tool_stripped`, error logged). Proven not needed so far: stripped is empty on tega.
  - Plugin files are root:<agent group> 0640 (`lock_agent_plugin`, also re-applied after `apply_isolation`'s chown): the agent can read them, not change them.
- **Status every turn.** `app/agent_status.py` builds a STATUS FROM THE APP block from real state: Google connection, practice/live (Sentinel mode), role, and what this person may do (authz check per tool). The persona answers "what can you do" from it. Counters `chat_turn_status_block` / `_unavailable` (error logged; the fallback block tells the model it can't check).
- **Prompt.** Hermes adds its own framing after SOUL.md: "You run on Hermes Agent" with its docs and skills, coding and install guidance, host and home paths, a Python probe, profile paths, an API-server hint. Asked what it runs on, the old agent said "Hermes Agent from Nous Research… Claude… claude-sonnet-5-5… a Linux computer". The model proxy now keeps only paragraphs whose first line matches the tenant's SOUL template, the status block and the date line, and drops everything else, including blocks a future Hermes adds. It is recorded in `agent_tools_seen.json` (kept, dropped, word counts before/after), counter `model_proxy_prompt_trimmed`. A Hermes-side switch would be local modification #3 and was not made.
- **Holds.** A held action's tool result says to explain it plainly and not suggest another way. The plugin refuses to re-file an identical held proposal for an hour (`agent_tool_<name>_held_duplicate`, warning logged), because a model re-proposing the same email would in live mode mean two approvals for one message. Approval de-duplication inside the broker would be a send-path change and wasn't made.
- **Connect button.** `connect_google` posts to `/api/broker/chat/action` with the turn's reference; the app emits an `action` event only while that turn runs, only if Google is still not connected and only for owner/admin, and the frontend renders its own button to `/api/google/connect/start`. Model text is rendered as plain text, so a model-written URL or button never becomes clickable.
  - Saved (human yes for the schema change): `messages.actions`, a guarded `ALTER TABLE ... ADD COLUMN` like `attachments`, holds the actions actually shown. `/api/chat/history` re-checks each one for the reader (`agent_status.live_actions`): gone once Google is connected, for a member, or if unknown (`chat_history_action_hidden`). Proven on a copy of tega's threads database: 140 messages kept, column added.
- **Frontend rebuilt** from platform source (index-DYLRnMby.js), which also ships the Settings/Google-login/refreshed_at changes deferred on 2026-10-17. Built in a scratch copy against the existing node_modules (package.json and lock identical; nothing installed); `tsc -b` clean. Navigation is network-first in `sw.js`, so phones load it on the next visit.
- **Email via the agent never sends.** draft_email goes to `/api/broker/outbound`, which holds in practice and answers not_brokered in enforce.
- **Proofs, app bfe2793.**
  - Tega via https://tega.yamin.io: `verify_agent_behaviour.py` 42/42 (with the served bundle and a saved button after reload), `verify_agent_tools.py --slug` 7/7, `verify_tenant_chat.py` 19/19, `prove_isolation` 123/123.
  - Eddie, blake and martin (local addresses), each after its own upgrade: behaviour 42/42, tools 7/7, chat 18/18 and `--spoof` 18/18, isolation 123/123. Each had the same 15 developer tools before.
  - `verify_chat_links.mjs` 7/7.
  - Canaries: the old answers fail the tightened checks. The identity question on the untrimmed prompt failed 4/6. `verify_chat_links.mjs --canary` 4/7. On the pre-rebuild tega, the served-bundle and saved-button checks failed.
  - Martin's first `--spoof` run passed 15/15: the agent filed nothing for the spoofed request, and three checks on the filed task were skipped silently. The rerun was 18/18. The skip now prints a SKIP line.
- **Gateways restarted by the rollout:** eddie, blake and martin (they had been on pre-modification-#2 code in memory since 2026-10-17), each under the lock by `--upgrade`. Live's gateway is untouched (pid 1781).
- **Later (human, 2026-10-21):** de-duplicate identical approvals inside the broker, planned with the live cutover.
- **Workers 101324 and 110814** returned and were stopped with SIGTERM after the ancestry check; footprint none.

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
2. Model choice and routing: which model for the main agent, which for Sentinel, which for cheap tasks like parsing fallback and digests? **Answered provisionally 2026-10-14 (human), until a bake-off:** claude-sonnet-5-5 for tenant agents, claude-haiku-5-5 for background parsing and extraction, both per-tenant manifest settings. Sentinel's model check is not built, so its model is still open.
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
