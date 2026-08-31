# Testco health baseline — 2026-08-30

Read-only pass against `127.0.0.1:8710` using
`.cursor/skills/verify-jarvis/SKILL.md`. No product fixes, no restarts, no
writes outside this directory. Branch: `docs/testco-baseline`.

`sqlite3` CLI is not installed. Snapshots used the venv
(` /root/jarvis-app/.venv/bin/python`) and
`file:…?mode=ro&immutable=1`. That reads the checkpointed file only; WAL
is reported separately below.

## What the skill allowed this run

**Ran** — skill “Read-only HTTP”, target 8710, and not in the stale-will-fail
table, plus the one user-named exception:

| Script | Why the skill says it is safe |
|--------|-------------------------------|
| `docs/testco-rehearsal/verify_testco_service.py` | Listed as Read-only HTTP: 8710 login + GET, ping of 8700. In the stale-will-fail table (dana PIN) — **ran anyway** because this task named it as the service-level check. |
| `docs/testco-rehearsal/verifyTestco.mjs` | Listed as Playwright visual/read, hardcoded `http://127.0.0.1:8710`. Does not mutate tenant stores. Not in the stale-will-fail table (it is the empty-tenant / legacy-Passphrase gate). |

**Not run** (and why):

| Script | Filter that dropped it |
|--------|------------------------|
| `docs/operator-inject/verify_outbox.py` | Read-only 8710 GET, but **in the stale-will-fail table** (per-user auth). |
| `docs/testco-rehearsal/seed/verifyTestcoSeeded.mjs` | 8710, but **stale-will-fail** (PIN + members). |
| `docs/desktop-layout/verify.mjs` | **stale-will-fail** (PIN UI). No default URL. |
| `docs/testco-rehearsal/roles.py` | **stale-will-fail**. Not a `verify_*` script. |
| `docs/verify_w3_sheet_leads.py` | Hardcoded **:8700**. |
| `docs/verifyW3ui.mjs` | Hardcoded **:8701**. |
| `verifyHero.mjs`, `verifyFeed.mjs`, `verifyVisual.mjs`, `verifyResponsive.mjs`, `verifyApprovals.mjs` | No default URL; live `/root/.hermes/.env` Passphrase. Skill: “not testco suites.” |
| Every writer / mutate / sandbox | Skill write list. Not this pass. |

## Results

### `verify_testco_service.py` — 21/25, exit 1

Full output: `verify_testco_service.out`.

**Passed (the isolation story holds):** both `testco-*.service` units are
active, enabled, `Restart=on-failure`; `systemctl list-units 'testco-*'`
is the two testco units only; `/proc` `HERMES_HOME=/opt/testco/hermes`;
feeds redirected under `/opt/testco/`; no fds into
`/root/.hermes/state` or `/root/jarvis-app/data`; live feed still 82
lines; live `:8700` `/api/ping` healthy.

**Failed — classified:**

| Assertion | Got | Class | Why |
|-----------|-----|-------|-----|
| `JARVIS_ALLOW_WRITES==0` on gateway and app | `1` | **environment** | Runtime env on the running units, not a main-branch product assertion. The suite expects the write guard armed; testco is running with writes allowed. |
| leads `source==store` and `count==40` | `source=store` `count=41` | **environment** (seed / concurrent use) | Store branch is correct. Count is +1 vs the suite’s seeded 40. Ledger `leads` is 41 active + 6 archived. Not a missing feature on stale testco. Do not attribute to Bao by investigation. |
| feed `total==90` | `996` | **environment** (seed / concurrent use) | Ledger `activity_feed.jsonl` is 996 lines — same number. The suite’s “90 seeded events” is stale relative to the current file. Not a main product bug. |

Dana PIN login **worked** (the leads/feed GETs returned JSON). So the
stale-will-fail warning for “per-user auth absent” did **not** fire on
this path. The running testco build answers person+PIN for dana.

### `verifyTestco.mjs` — did not execute against 8710, exit 1

Full output: `verifyTestco.mjs.out`.

| Failure | Class | Why |
|---------|-------|-----|
| `Cannot find package 'playwright'` (`import { chromium } from 'playwright'`) | **environment** | Script does not use the skill’s Playwright path (`/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs`). `NODE_PATH` retry still failed (ESM). No browser, no login, no tab walk. |
| Scratchpad `…/testco-clone/hermes/.env` **missing** | **environment** | Would have been the next crash (`readFileSync` of that path). Presence checked; contents not read. |
| (not reached) empty-tenant + `Passphrase` login | would have been **environment** / wrong-suite | Skill already says this file expects a fresh empty tenant and legacy Passphrase — wrong for today’s seeded PIN testco. |

No `docs/testco-rehearsal/vis/` screenshots were created.

## Ledger delta

**Checkpointed files (`immutable=1`) — near zero.**
`ledger-diff.txt` is header timestamps only. Tasks, leads, threads,
counters, `counter_days`, and all four JSONL line counts are identical
between `ledger-before.txt` (19:38:08Z) and `ledger-after.txt`
(19:40:33Z).

| Surface | Before = after |
|---------|----------------|
| tasks by status | archived 6, done 3, in_progress 1, open 19 |
| leads archived | 0→41, 1→6 |
| active threads | 9 |
| `counters` | unchanged (see before file) |
| `activity_feed.jsonl` | 996 |
| `approvals.jsonl` | 25 |
| `approvals_decisions.jsonl` | 10 |
| `captured_mail.jsonl` | 177 |

A WAL-inclusive copy (`ledger-after-wal.txt`) of `jarvis_threads.db`
is **ahead of the checkpoint**: e.g. `sweep_runs` 522 vs 443,
`nudges_sent.quiet_lead` 29 vs 24, extra keys
`digest_replies_resolved`, `tasks_completed_late`,
`tasks_completed_no_deadline`. A WAL copy taken at 19:38 vs 19:40
showed **no further counter/task/lead movement** during this run.

**Finding, not a silent-writer in these scripts:** the two suites that
ran did not change the checkpointed ledger. The WAL/checkpoint gap
means something else (sweeps, digest, tester activity) has been writing
to testco and has not been checkpointed. JSONL line counts did not move
in this window. Per the task: if that is Bao, noted and not investigated.

## Surprises

1. **Stale-auth prediction was wrong for the service check.** Dana PIN
   login on 8710 succeeded. The skill’s “per-user auth absent on testco”
   does not match this path.
2. **`JARVIS_ALLOW_WRITES=1`** on both testco units. Isolation from live
   still holds (fds + feed path). The write *guard* the suite wants is
   off.
3. **Seeded-size assertions are dead.** Feed 996 vs 90, leads 41 vs 40.
   Isolation (store vs live 82-line feed) still holds.
4. **`sqlite3` CLI missing; WAL hides current counters** if you only
   open the main file immutable. Future ledger snapshots should copy
   `db`+`-wal`+`-shm` or the numbers lie low.
5. **`verifyTestco.mjs` is not runnable here** without a Playwright
   import rewrite and a missing scratchpad env. It never touched 8710.

## Files in this directory

- `ledger-before.txt` / `ledger-after.txt` / `ledger-diff.txt`
- `ledger-after-wal.txt` — WAL-inclusive threads/tasks/leads
- `verify_testco_service.out`
- `verifyTestco.mjs.out`
- `BASELINE-REPORT.md` (this file)
