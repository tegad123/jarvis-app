# Jarvis / Coagent — Agent Operating Rules

## What this is
Multi-tenant real estate AI agent platform (FastAPI backend + React frontend,
Hermes agent gateway). Tenant zero is a live business. Mistakes here send
emails and texts to real clients. Act accordingly.

## Topology — memorize before touching anything
- LIVE app:    port 8700, runs from /root/jarvis-app. NEVER modify, restart,
  or test against live. It serves a real user.
- VERIFY SANDBOX: port 8711 (suites also use 8712-8719), `docs/tasks/tasks_sandbox.sh start`, runs the
  code under test (VERIFY_APP_ROOT / VERIFY_SKILLS_ROOT) against the frozen
  verify fixture at /opt/jarvis-verify-fixture (testco's .example config and
  stores, throwaway secrets, mock Google OAuth, dead gateway port). All
  runtime verification happens here.
- REHEARSAL: app 8730, gateway 8672, /opt/rehearsal. A real mailbox, writes
  BLOCKED since 2026-10-13. The app runs as jarvis-rehearsal, the gateway as
  jarvis-rehearsal-agent; app secrets are in /etc/jarvis/rehearsal-app.env and
  /opt/rehearsal/app-private (docs/platform/decisions.md). Since 2026-10-14
  it runs the current layout (broker, Sentinel shadow, model proxy, Postgres
  jarvis_rehearsal) at app 6d847b0 / skills 74c0fb1, upgraded in place with
  `--upgrade`, then restarted once more (human yes) for JARVIS_APP_ROOT, and
  again 2026-10-15 (human yes) to load the model key, and 2026-10-16 (human
  yes, `--reconfigure --restart-approved`) for the owner's internal role and
  the patched Hermes.
  It is a tenant, NOT a test target: never point verify scripts or sandboxes
  at its config or stores.
- ISOCHECK: app 8740, gateway 8682, /opt/isocheck. A scratch tenant made by
  `scripts/provision_tenant.py` (isolation: two_user, writes blocked, no
  mailbox, `.invalid` addresses). It is the on-host test target for platform
  work; the lead agent may restart or re-provision it under the agent lock.
  `scripts/prove_isolation.py --slug isocheck` proves its isolation.
- EDDIE, BLAKE, MARTIN: apps 8750 / 8760 / 8770, gateways 8752 / 8762 / 8772,
  /opt/<slug>, manifests docs/tenants/<slug>.yaml. Each person's own Jarvis in
  practice mode (two-user, sentinel shadow, writes blocked), no Google
  connected, placeholder mailboxes `<slug>-assistant@assistant.invalid`.
  App bfd501b since 2026-10-16 (`--upgrade`, proofs 123/123).
  Their owners carry roles owner + internal (task.add needs internal or
  assistant; roles are literal). The provisioner refuses an owner without
  internal, and `--reconfigure` re-applies the manifest's roles.
  Connecting a real mailbox is a human STOP gate; after that, restarting
  the tenant is too (`--reconfigure` refuses without `--restart-approved`).
  The lead may restart them under the agent lock while no mailbox is connected.
  Their agents answer through the model proxy (key installed 2026-10-15).
  Each has one owner account since 2026-10-15 (human yes); the PIN is in
  /root/onboarding/<slug>-owner-pin.txt (root 600), never printed. More
  PINs are minted with `app.accounts_cli set-pin` (as the app user) only
  with a human yes. `docs/model/verify_tenant_chat.py --slug <slug>
  --pin-file ...` is their real chat test (costs a model round trip). It
  deletes the task it creates and checks the list ends as it began. Their
  task lists hold real tasks only (proof tasks cleared 2026-10-16). Don't
  file test tasks on them by hand; use isocheck.
- Two-user tenants run the agent in broker mode: Google calls, counters and
  channel creation go through the app's loopback /api/broker; the threads and
  accounts databases are in app-private and the agent cannot open them.
- POSTGRES 16 (cluster 16/main): unix socket only (no TCP listener), peer
  auth only, configured by scripts/setup_postgres.py. Each two-user tenant
  has database jarvis_<slug>; only its app OS user can connect, as
  jarvis_<slug>_app. Agent users have no pg_hba rule and are refused.
  Approvals, grants, the append-only audit log (`actions`), held proposals
  and model usage live there (scripts/pg_tenant.py; schema via Alembic in
  app/migrations). Verify sandboxes use jarvis_verify_* databases as root.
  Live keeps its SQLite and never connects.
- Every broker write and every outbound post, channel or message goes through
  Sentinel (app/sentinel.py) and is recorded in the tenant's `actions` table.
  Two-user manifests set sentinel_mode; shadow (ISOCHECK, and every
  checked-in manifest) holds each such action, keeps the full proposal for
  the owner and writes a "would have" feed line. The audit log is the
  evidence for what a tenant tried. Approvals are app-only: the agent can
  propose through /api/broker/approvals and never decide.
- Per-piece switches on two-user tenants: JARVIS_SWITCH_BROKER, _SENTINEL,
  _MODEL_PROXY (manifest `switches`, rendered into the drop-ins). Off fails
  closed. To turn one off on a running tenant: edit the manifest, then
  `provision_tenant.py --manifest M --reconfigure` (the same path changes
  display names, timezone and models without re-provisioning).
- Moving an existing two-user tenant to new commits or a newer layout:
  `provision_tenant.py --manifest M --upgrade [--restart-approved]`. It keeps
  data, secrets and the OAuth client, snapshots /opt/<slug> and its units to
  /root/backups/<slug>/upgrade-<stamp>/, and restores that snapshot by itself
  if app or gateway is not healthy within 120 s. A tenant that already has
  jarvis_<slug> keeps it: the database is dumped into the snapshot, grants
  are re-applied, it is never dropped on rollback, and a schema version the
  new code does not expect is refused before anything stops (a migration is
  a human yes). Prove it first with docs/platform/verify_upgrade.py: sections
  A-D need an old-layout source tenant and none is left, so use
  `--existing-db` (replica provisioned from eddie's manifest at eddie's
  commits, never its data; section E with two canaries).
- Tenant agents reach Claude only through their app's model proxy
  (/api/model/anthropic). The platform key belongs in
  /etc/jarvis/anthropic.env (root 600, installed by a human); the agent
  holds a stand-in. Never copy a model key from /root/.hermes.
- Two-user tenants get their OAuth client from /etc/jarvis/oauth_client.json
  (root 600). Never read /root/.hermes for provisioning.
- TESTCO (8710/8652, /opt/testco) was RETIRED 2026-10-13. Archive and rollback:
  docs/platform/decisions.md. Scripts that needed it refuse with RETIRED.
- Hermes gateways: /usr/local/lib/hermes-agent, branch scale-vault-local.
- Live runs from the venv at /root/jarvis-app/.venv; rehearsal and every
  two-user tenant from its copy at /opt/jarvis/venv (2026-10-13). A pip
  install into either changes a running tenant. Do not install packages
  without flagging it first. Install into the platform venv only as
  `/opt/jarvis/venv/bin/python -m pip`, never `/opt/jarvis/venv/bin/pip`
  (a copied venv's scripts can still point at live's interpreter; one did,
  decisions.md 2026-10-14). The provisioner and prove_isolation refuse any
  platform venv script whose interpreter is under /root. Live's venv must
  never get psycopg. Manifests say `venv: platform`; `venv: shared` (live's
  interpreter) is retired and refused.

## Hard rules
1. Work on a branch off main. Never commit directly to main.
2. Never use git add -A. Stage specific files you changed and can name.
3. Never touch: /root/.hermes/ (secrets), /opt/rehearsal/hermes/ (runtime
   state: auth, credentials, kanban.db, caches), any .env or .env.bak file,
   /root/.claude/, systemd units, cloudflared tunnels. The verify fixture is
   read-only to scripts: sandboxes copy from it, nothing writes into it.
4. Never print, log, or quote the contents of env files or anything matching
   sk-ant-*. If you need to know whether a variable exists, test for its
   presence, never its value.
5. Never restart jarvis-app.service. Restarting rehearsal-app or
   rehearsal-gateway needs an explicit human yes (a real mailbox is
   connected), as does restarting any tenant with a real mailbox.
   The verify sandbox may be started and stopped freely.
6. No new dependencies, schema migrations, or send-path changes without an
   explicit human yes in the conversation.
7. After any commit, merge, or branch operation, run `git log --oneline -1`
   (or `git branch -v` for branch ops) and paste the actual output in your
   report. A report claiming a commit without its hash from the log is
   unverified. Do not report a task complete on the strength of having
   run `git commit` — report it complete on the strength of the log.
8. One agent changes the server at a time. Only the lead agent makes server
   changes (restarts, users, groups, permissions, services, provisioning,
   anything under /opt or /etc), and only while holding the lock:
   `scripts/agent_lock.py acquire --who NAME --why TEXT` before, and
   `scripts/agent_lock.py release --who NAME` after. If acquire returns 2,
   someone else holds it: stop and ask the human. Never delete a lock you
   did not take. Helper agents never take the lock and never touch the
   server; they work in their own git worktree on a feature branch.

## Engineering principles (these are law, learned from real failures here)
- A fallback that does not announce itself is a false green. Every fallback
  must log/counter when it fires.
- The model's own report is not evidence. Only the ledger is: verify against
  the SQLite rows, the counters, /api/health/summary — not your own output.
- A broken filter looks identical to a clean dataset. Prove filters filter.
- Assert the delta, not the threshold. A skipped assertion is a hole.
- Guard tests are claims until a canary proves they can fire. Absence
  assertions need a companion presence assertion.
- Invoke what you changed. Grep is not execution.
- Exit code 0 is not proof of success. The worst bugs exit cleanly.
- Isolating the database is not isolating the side effects.
- UI sentences are backend specifications. If the screen says "the sheet
  stays the place you edit", the API must enforce exactly that.
- A claimed commit without its hash in the log is the false-green pattern
  applied to git. The report is not the ledger; git log is the ledger.

## Multi-tenant invariants
- Every tenant-scoped query carries tenant identity. No exceptions.
- gated_send is enforced at the send layer, not by callers remembering.
- tenant_config is the single read path for settings. No hardcoded values
  that shadow it.
- Role gates (owner/admin/member/operator) are enforced by the API;
  the UI only reflects them. Every hidden control has a server-side twin.
- Operator (break-glass) sessions see structure, never bodies.
- A mock may only return what the real dependency returns. Mock-supplied
  convenience data is a false green — the connect email and PKCE verifier
  were both hidden this way.

## Definition of done
A change is done when, in the verify sandbox (8711, fixture-backed):
1. The relevant verify script(s) under docs/ pass — run them, paste output.
2. PROMPT 1-6 regression checks are green.
3. You have shown the before/after ledger delta for anything that writes
   (the sandbox's box files, never the fixture).
Not done: "the code looks right", "tests should pass", a passing build alone.
The exact commands are in .cursor/skills/verify-jarvis/SKILL.md. The full gate
is `bash docs/platform/gate.sh`: one line per suite, then the fixture hash,
live's PID and the number of files written into live's tree (must be 0).
- No script may work on live: import /root/jarvis-app, bind a working path to
  it or /root/.hermes, or run live's venv. Use VERIFY_APP_ROOT /
  VERIFY_SKILLS_ROOT (the platform trees) and /opt/jarvis/venv.
  docs/platform/verify_no_live_targets.py enforces this; an unported legacy
  script must refuse to start (`sys.exit("REFUSING: ...")`).

## Current known state (2026-10-13)
- testco is retired: units, tunnel hostnames (app/gateway/hooks.yamin.io) and
  /opt/testco are gone. Its baseline branch is on GitHub as
  archive/testco-baseline (48346d2). The full tree is in a root-only tarball
  until 2026-11-12 (docs/platform/decisions.md).
- The verify sandbox runs with `JARVIS_ALLOW_WRITES=1` deliberately: writes are
  enabled behind the `.example` recipient boundary so action claims are
  testable inside the sandbox.
- No external tester is active.
- A Hermes-home watcher runs on this host. REMOVE BY 2026-10-29. It exists
  because isocheck's /opt/isocheck/hermes once flipped from 0710 to 0700, which
  locks the app out of shared state. On 2026-10-16 it caught the cause: the
  gateway's auth-store save (`hermes_cli/auth.py` `_save_auth_store` ->
  `hermes_constants.secure_parent_dir`) hardcodes 0700 on the home and
  ignores `HERMES_HOME_MODE`. Hermes scale-vault-local 0f8bc16 makes it keep
  `HERMES_HOME_MODE` for the home only (human yes). The install's origin is
  upstream NousResearch, never pushed to; the patched branch is kept as
  `hermes-agent/scale-vault-local` in github.com/tegad123/jarvis-skills. The
  install is a shallow clone, so push from a full clone of upstream. Live
  sets no `HERMES_HOME_MODE`, so its behaviour is unchanged. Every tenant
  gateway, rehearsal included, has run the patched code since 2026-10-16.
  The watcher now confirms the fix holds.
  It is a root bpftrace program, `/root/archives/hermes-home-watch.bt`,
  attached to chmod, fchmodat and fchmodat2. It logs every mode change other
  than 0710 on a path under /opt/ ending in `/hermes`, with process, parent and
  uid, to `/root/archives/hermes-home-watch.log`. It changes nothing, and
  prove_isolation's 0710 check stays the gate. Its canary is a chmod on a
  throwaway /opt/watchcanary/hermes (two lines, labelled watchcanary, are
  already in the log). Stop it with `pkill -f hermes-home-watch.bt`. If it has
  not fired by the remove-by date, stop it, delete the .bt file and this bullet,
  and record the outcome in decisions.md.
