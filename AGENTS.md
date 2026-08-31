# Jarvis / Coagent — Agent Operating Rules

## What this is
Multi-tenant real estate AI agent platform (FastAPI backend + React frontend,
Hermes agent gateway). Tenant zero is a live business. Mistakes here send
emails and texts to real clients. Act accordingly.

## Topology — memorize before touching anything
- LIVE app:    port 8700, runs from /root/jarvis-app. NEVER modify, restart,
  or test against live. It serves a real user.
- TESTCO app:  port 8710, runs from /opt/testco/jarvis-app. All runtime
  verification happens here.
- TESTCO gateway: port 8652 (hermes agent framework,
  /usr/local/lib/hermes-agent, branch scale-vault-local).
- Both apps currently share the venv at /root/jarvis-app/.venv. A pip install
  affects BOTH. Do not install packages without flagging it first.

## Hard rules
1. Work on a branch off main. Never commit directly to main.
2. Never use git add -A. Stage specific files you changed and can name.
3. Never touch: /root/.hermes/ (secrets), /opt/testco/hermes/ (runtime state:
   auth, credentials, kanban.db, caches), any .env or .env.bak file,
   /root/.claude/, systemd units, cloudflared tunnels.
4. Never print, log, or quote the contents of env files or anything matching
   sk-ant-*. If you need to know whether a variable exists, test for its
   presence, never its value.
5. Never restart jarvis-app.service. testco-app and testco-gateway may be
   restarted when verification requires it.
6. No new dependencies, schema migrations, or send-path changes without an
   explicit human yes in the conversation.

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

## Multi-tenant invariants
- Every tenant-scoped query carries tenant identity. No exceptions.
- gated_send is enforced at the send layer, not by callers remembering.
- tenant_config is the single read path for settings. No hardcoded values
  that shadow it.
- Role gates (owner/admin/member/operator) are enforced by the API;
  the UI only reflects them. Every hidden control has a server-side twin.
- Operator (break-glass) sessions see structure, never bodies.

## Definition of done
A change is done when, on testco (8710):
1. The relevant verify script(s) under docs/ pass — run them, paste output.
2. PROMPT 1-6 regression checks are green.
3. You have shown the before/after ledger delta for anything that writes.
Not done: "the code looks right", "tests should pass", a passing build alone.

## Current known state (2026-08-30)
- /opt/testco/jarvis-app is STALE relative to main. It **has** per-user auth
  (dana PIN login on :8710 verified 2026-08-30; source:
  `docs/testco-baseline/BASELINE-REPORT.md`). It **lacks** the Tasks tab/API,
  channel members, operator-role frontend, and later design passes. Branch
  testco documents that snapshot. Reconciling it up to main is a planned,
  human-supervised task — do not "helpfully" sync it.
- `JARVIS_ALLOW_WRITES=1` on the running testco units is **deliberate**:
  writes are enabled behind the `.example` recipient boundary so action
  claims are testable. A verify assertion expecting `0` is stale, not a
  misconfiguration (`docs/testco-baseline/BASELINE-REPORT.md`).
- External tester (Bao) is actively testing against testco. Do not change
  testco's running build without human approval.
