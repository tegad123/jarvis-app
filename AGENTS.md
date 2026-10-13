# Jarvis / Coagent — Agent Operating Rules

## What this is
Multi-tenant real estate AI agent platform (FastAPI backend + React frontend,
Hermes agent gateway). Tenant zero is a live business. Mistakes here send
emails and texts to real clients. Act accordingly.

## Topology — memorize before touching anything
- LIVE app:    port 8700, runs from /root/jarvis-app. NEVER modify, restart,
  or test against live. It serves a real user.
- VERIFY SANDBOX: port 8711, `docs/tasks/tasks_sandbox.sh start`, runs the
  code under test (VERIFY_APP_ROOT / VERIFY_SKILLS_ROOT) against the frozen
  verify fixture at /opt/jarvis-verify-fixture (testco's .example config and
  stores, throwaway secrets, mock Google OAuth, dead gateway port). All
  runtime verification happens here.
- REHEARSAL: app 8730, gateway 8672, /opt/rehearsal. Live posture with a real
  mailbox and writes on. It is a tenant, NOT a test target: never point verify
  scripts or sandboxes at its config or stores.
- TESTCO (8710/8652, /opt/testco) was RETIRED 2026-10-13. Archive and rollback:
  docs/platform/decisions.md. Scripts that needed it refuse with RETIRED.
- Hermes gateways: /usr/local/lib/hermes-agent, branch scale-vault-local.
- Live and rehearsal share the venv at /root/jarvis-app/.venv. A pip install
  affects BOTH. Do not install packages without flagging it first.

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
   rehearsal-gateway needs an explicit human yes (it is a live-posture tenant).
   The verify sandbox may be started and stopped freely.
6. No new dependencies, schema migrations, or send-path changes without an
   explicit human yes in the conversation.
7. After any commit, merge, or branch operation, run `git log --oneline -1`
   (or `git branch -v` for branch ops) and paste the actual output in your
   report. A report claiming a commit without its hash from the log is
   unverified. Do not report a task complete on the strength of having
   run `git commit` — report it complete on the strength of the log.

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
The exact commands are in .cursor/skills/verify-jarvis/SKILL.md.

## Current known state (2026-10-13)
- testco is retired: units, tunnel hostnames (app/gateway/hooks.yamin.io) and
  /opt/testco are gone. Its baseline branch is on GitHub as
  archive/testco-baseline (48346d2). The full tree is in a root-only tarball
  until 2026-11-12 (docs/platform/decisions.md).
- The verify sandbox runs with `JARVIS_ALLOW_WRITES=1` deliberately: writes are
  enabled behind the `.example` recipient boundary so action claims are
  testable inside the sandbox.
- No external tester is active.
