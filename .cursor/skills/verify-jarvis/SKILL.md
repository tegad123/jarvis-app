---
name: verify-jarvis
description: >-
  Prove a Jarvis change works on testco (127.0.0.1:8710) using docs/ verify
  scripts, then show the SQLite/counter ledger delta. Use when verifying a
  change, running verify_*.py / verify*.mjs / mutate_*.py, checking feed,
  ask, channels, leads, tasks, approvals, sweeps, meetings, metrics, or
  health, or when the user mentions testco, 8710, or the ledger.
---

# Verify Jarvis on testco

A change is not done until a script that exercises the changed path has been
run against **testco**, and the **ledger** (SQLite rows or counters) shows
the before/after delta. The script's own `PASS` line is not proof.

## 1. Target rule

Verification runs against **testco `127.0.0.1:8710` ONLY**. Never `:8700`.

| Port | What it is | Role |
|------|------------|------|
| **8710** | testco app (`/opt/testco/jarvis-app`) | **The only verification target** |
| 8652 | testco gateway | Used by Ask/chat scripts that need a model |
| 8711 | `docs/tasks/tasks_sandbox.sh` | Isolated tasks API box; not live, not testco |
| 8701 | `docs/w3_sandbox.sh` | Isolated W3 scoping box; not testco |
| **8700** | LIVE tenant (`/root/jarvis-app`) | **Never verify here. Never write here.** |

`HERMES_HOME` for testco is `/opt/testco/hermes` (`docs/tasks/env.py`). App
SQLite files resolve through `config.HERMES_HOME / "state"`
(`app/config.py`, `app/accounts.py`, `app/leads_store.py`,
`app/tasks_store.py`, `app/threads.py`). They are **not** under `data/`.
`data/` holds attachments (`app/attachments.py`) and the transcribe spool
(`app/transcribe.py`).

Never restart `jarvis-app.service`. Do not change testco's running build
without an explicit human yes (`AGENTS.md`).

### Scripts that WRITE — never point at :8700

These mutate a running app, a tenant store, or both. If they take `--base`
or a URL argv, that URL must not end in `:8700`. Several already refuse
`:8700` in code; several do not. Treat the list as the rule, not the
guard.

**HTTP writers (create / archive / inject / approve / chat / members):**

- `docs/operator-inject/verify_inject.py` — `POST /api/operator/inject`, owner approve
- `docs/operator-inject/verify_named_operator.py` — injects on 8710 (also probes 8700 for 401)
- `docs/tasks/verify_tasks_chat.py` — `POST /api/chat` writes `jarvis_tasks.db`
- `docs/tasks/verify_tasks_api.py` — create/edit/assign/archive (defaults to **8711**)
- `docs/tasks/verifyTasksUI.mjs` — creates, status-flips, archives a task
- `docs/approvals/verify_approval_executor.py` — injects a showing, `POST /api/approvals/{id}`
- `docs/approvals/verify_fullbody_fixture.py` — appends testco `approvals.jsonl`, then approves
- `docs/verify_w3_scope.py` — add/remove thread members (defaults to **8701**)
- `docs/verify_w3_auth.py` — **hardcoded `:8700`**, promotes/disables live accounts. **Do not run.**
- `docs/w1-shots/verifyLeadsUI.mjs` — `--store` writes leads (example port 8701)
- `docs/phaseA-shots/verifyA.mjs` — create/rename/archive channel; **restarts `jarvis-app`**
- `docs/phaseB-shots/verifyB.mjs` — uploads under `data/attachments/`; **restarts `jarvis-app`**
- `docs/phaseC1-shots/verifyC1.mjs` — transcribe + `POST /api/chat`
- `docs/phaseC1-shots/verifyC1b.mjs` — chat + TTS counters
- `docs/step2-shots/verify.mjs` — seeds a feed event; **stops `jarvis-app`**
- `docs/step3-shots/verify3.mjs` — sends chat; **stops `jarvis-app`**
- `docs/step4-shots/verify4.mjs` — Approve click; **restarts `jarvis-app`**
- `docs/testco-rehearsal/verify_boundary.py` — `gated_send` appends testco feed + outbox

**In-process / boxed writers (no `:8700` HTTP, still mutate disk):**

- `docs/sweeps/verify_sweeps.py`, `docs/tasks/verify_chat_honesty.py`, `docs/tasks/verify_tasks_digest.py`
- `docs/metrics/verify_metrics.py` (boxed writes; optional live **GET** role check on 8710)
- `docs/lead-capture/verify_lead_capture.py`, `docs/lead-capture/verify_failure_event.py`
- `docs/w1-shots/verify_leads_store.py`, `docs/w1-shots/verify_leads_export.py`, `docs/w1-shots/verify_leads_contract.py`
- `docs/phase5-shots/verify_config_apply.py`
- `docs/w3_sandbox.sh`, `docs/tasks/tasks_sandbox.sh`

**Mutation harnesses (patch source, expect a verify script to go red):**

- `docs/mutate_w3.py`, `docs/w1-shots/mutate_leadgate.py` — both **restart `jarvis-app`**; do not run
- `docs/tasks/mutate_tasks.py`, `docs/sweeps/mutate_sweeps.py`, `docs/metrics/mutate_metrics.py`
- `docs/lead-capture/mutate_dedupe.py`, `docs/phase5-shots/mutate_config_apply.py`

**Read-only HTTP (still never write; two of these are live-only):**

- `docs/operator-inject/verify_outbox.py` — 8710 GET
- `docs/testco-rehearsal/verify_testco_service.py` — 8710 login + GET; pings 8700
- `docs/verify_w3_sheet_leads.py` — **hardcoded `:8700`**, GET `/api/leads` only. Do not run for testco.
- Playwright visual/read: `docs/testco-rehearsal/seed/verifyTestcoSeeded.mjs` (8710), `docs/desktop-layout/verify.mjs`, `docs/verifyW3ui.mjs` (8701), `docs/hero-summary-shots/verifyHero.mjs`, `docs/feed-humanization-shots/verifyFeed.mjs`, `docs/visual-polish/verifyVisual.mjs`, `docs/visual-polish-r2/verifyResponsive.mjs`, `docs/phase5-shots/verifyApprovals.mjs`
- `docs/testco-rehearsal/verifyTestco.mjs` — **unrunnable**. Bare `import … from 'playwright'` (ESM cannot see the hermes-agent install; `NODE_PATH` does not help) and a missing scratchpad `.env`. Do not spend a session rediscovering this (`docs/testco-baseline/BASELINE-REPORT.md`). It is also the empty-tenant / legacy-Passphrase gate, wrong for seeded PIN testco.

## 2. How to authenticate

Two doors, one cookie. `POST /api/login` in `app/main.py` (`LoginRequest` in
that file; `app/auth.py` issues the session):

| Path | Body | Session |
|------|------|---------|
| Person | `{"person_key": "<key>", "pin": "<pin>"}` | role from `accounts` (`owner` / `admin` / `member`) |
| Operator | `{"passphrase": "<secret>"}` | `role=operator`, `person_key=null`; optional `operator` label |

Scripts persist the cookie with `curl -c jar -b jar` or Playwright
`storageState`. They do **not** send `Authorization` headers.

**Testco pins** live at `/opt/testco/SEEDED_PINS.json` (file exists; do not
print or quote its contents). Scripts that read it:

- Default `--pins /opt/testco/SEEDED_PINS.json`: `verify_inject.py`, `verify_tasks_chat.py`, `verify_tasks_api.py`, `verifyTasksUI.mjs`
- Hardcoded path: `verify_outbox.py`, `verify_approval_executor.py`, `verify_fullbody_fixture.py`, `verify_metrics.py` (live section), `verify_testco_service.py`, `verifyTestcoSeeded.mjs`, `docs/desktop-layout/verify.mjs`, `docs/testco-rehearsal/roles.py`

Values are either a string pin or `{pin: ...}`. Normalize like
`verify_inject.py` / `verifyTasksUI.mjs` (`pinOf`). Person keys those
scripts actually use: **dana** (owner), **priya** (admin), **omar**
(member), **tester** (member). Confirm keys from the JSON **key names
only**.

**Operator passphrase** is not in `SEEDED_PINS.json`. Scripts parse
`APP_PASSPHRASE=` from `/opt/testco/hermes/.env` (never print the value).
Named operator `bao` is `APP_OPERATOR_PASSPHRASES` (`verify_named_operator.py`,
`app/auth.py` `operator_passphrases()`).

**UI:** `frontend/src/screens/Login.tsx` — person tile + `PIN` placeholder,
or "Operator access" + `Operator passphrase` / `Passphrase`. Older
Playwright suites still fill a single `Passphrase` from **live**
`/root/.hermes/.env`. Those are not testco suites.

**Never** log pin or passphrase values. Presence of the env var is enough.

```bash
# Person (cookie jar). Do not echo the pin.
python3 -c 'import json; p=json.load(open("/opt/testco/SEEDED_PINS.json")); print("keys:", sorted(p))'
curl -s -c /tmp/dana.jar -X POST http://127.0.0.1:8710/api/login \
  -H 'Content-Type: application/json' \
  -d '{"person_key":"dana","pin":"<from file, not from chat>"}'
```

## 3. Feature map

Tabs are `feed | ask | approvals | leads | tasks`
(`frontend/src/components/TabBar.tsx`). Ask is also channels + members.
Sweeps, meetings, metrics, and health have no tab.

**Testco ledger root:** `/opt/testco/hermes/state/` (and
`/opt/testco/hermes/outbox/captured_mail.jsonl`).

| Surface | App / UI | Verify scripts | Ledger (from app/ or the script) |
|---------|----------|----------------|----------------------------------|
| **Feed** | `app/feed.py` → `_shared/notify.read_feed()`; `GET /api/feed`, `/api/feed/summary` (`app/main.py`); `frontend/src/screens/Feed.tsx` (also mounts `OutboxList.tsx` for the operator), `HeroCard.tsx`, `FeedRow.tsx` | `verifyFeed.mjs`, `verifyHero.mjs`, `verifyTestcoSeeded.mjs`, `verify_w3_scope.py` (feed scope), `verify_testco_service.py` | **Not SQLite.** `activity_feed.jsonl` via `app/feed.py` `feed_path()` → notify. Testco path used by scripts: `/opt/testco/hermes/state/activity_feed.jsonl` (`verify_inject.py`) |
| **Ask / chat** | `app/chat.py` (BFF to gateway); `POST /api/chat`, `GET /api/chat/history` (`app/main.py`); `frontend/src/screens/Ask.tsx`; voice `app/transcribe.py`, `MicButton.tsx` | `verify_tasks_chat.py` (a), `verify_chat_honesty.py`, `verifyC1.mjs`, `verifyC1b.mjs`, `verify3.mjs`, `verifyB.mjs` | `jarvis_threads.db` tables `messages`, `attachments` (`app/threads.py`). Gateway `state.db` is unused (`app/chat.py`). Chat-created tasks: `jarvis_tasks.db` `tasks` (`source='chat'`, `app/tasks_store.py`) |
| **Channels** | `app/threads.py`; `/api/threads` CRUD + archive (`app/main.py`); `ConversationsList.tsx`, `NewChannelSheet.tsx` | `verifyA.mjs` (writes + live DB path — do not run on 8700), `verify_w3_scope.py`, `verifyW3ui.mjs`, `verifyTestcoSeeded.mjs` | `jarvis_threads.db`: `threads`, `messages`, `thread_members` (`app/threads.py`). Archive is a flag, never DELETE |
| **Members** | `thread_members` + `/api/threads/{id}/members` (`app/threads.py`, `app/main.py`); `frontend/src/components/MembersSheet.tsx` | `verify_w3_scope.py`, `verifyW3ui.mjs`, `verifyTestcoSeeded.mjs`, `docs/testco-rehearsal/roles.py` | `jarvis_threads.db` `thread_members`; accounts in `jarvis_accounts.db` `accounts` (`app/accounts.py`) |
| **Leads** | Seam `app/leads_source.py` (`store` vs `sheet`). Store: `app/leads_store.py`. Sheet: `app/leads.py`. Routes `/api/leads*` (`app/main.py`). UI `frontend/src/screens/Leads.tsx` | Store: `verify_leads_store.py`, `verify_leads_contract.py`, `verifyLeadsUI.mjs --store`, `verify_lead_capture.py`, `mutate_dedupe.py`. Sheet (live, read-only): `verify_w3_sheet_leads.py`, `verifyLeadsUI.mjs --sheet`. Export: `verify_leads_export.py` | Store: `jarvis_leads.db` table `leads` (`app/leads_store.py`). Sheet: no SQLite. Testco is store (`verify_testco_service.py` asserts `source=="store"`) |
| **Tasks** | Seam `app/tasks_source.py` (`store` vs `legacy`). Store: `app/tasks_store.py`. Routes `/api/tasks*` (`app/main.py`). UI `frontend/src/screens/Tasks.tsx` | `verify_tasks_api.py` (8711 sandbox), `verifyTasksUI.mjs` (8710), `verify_tasks_chat.py`, `verify_chat_honesty.py`, `verify_tasks_digest.py`, `mutate_tasks.py` | `jarvis_tasks.db`: `tasks`, `digest_map`, `digest_replies` (`app/tasks_store.py`). Legacy skill DB `/opt/testco/skills/openclaw-imports/task-manager/data/tasks.db` must not change (`verify_tasks_chat.py`) |
| **Approvals** | `app/approvals.py`; `GET/POST /api/approvals*` (`app/main.py`); `frontend/src/screens/Approvals.tsx`. Config apply: `app/config_apply.py` | `verify_approval_executor.py`, `verify_fullbody_fixture.py`, `verifyApprovals.mjs`, `verify_config_apply.py`, `mutate_config_apply.py`, `verify4.mjs` (live — do not run) | **JSONL, not SQLite.** `approvals.jsonl`, `approvals_decisions.jsonl` (`app/approvals.py`). Execute path writes `outbox/captured_mail.jsonl` |
| **Sweeps** | Skill scripts under `/opt/testco/skills/openclaw-imports/sweeps/scripts/` (`stale_email`, `task_followup`, `quiet_lead`). No app/ module, no tab | `docs/sweeps/verify_sweeps.py`, `docs/sweeps/mutate_sweeps.py`; seams also in `verify_metrics.py` | Boxed `sweeps.db` (`nudges`, `runs` — named in `verify_sweeps.py` / `docs/tasks/env.py`). Plus boxed `tasks` / `leads` / `feed.jsonl` / outbox. **Not defined in `app/`** |
| **Meetings** | Inject kind `meeting` → `meeting-intake/scripts/handle_meeting.py` (`app/inject.py`). Chat path `_run_meeting_intake` (`app/chat.py`). Tasks may have `source='meeting'` (`app/tasks_store.py`) | `verify_inject.py` (kind `meeting`), `verify_metrics.py` (showing/digest seams) | Feed line + `operator_inject.jsonl` (`app/inject.py`). No meetings table in `app/` |
| **Metrics** | `app/metrics.py`; `GET /api/counters`, `POST /api/counters/{name}` (`app/main.py`) | `docs/metrics/verify_metrics.py`, `docs/metrics/mutate_metrics.py`; TTS counters in `verifyC1b.mjs` | Same file as threads: `jarvis_threads.db` tables `counters` (`app/threads.py`), `counter_days`, `measurements` (`app/metrics.py`) |
| **Health** | `app/health.py` (`gateway`, `feed`, `sheets`); `GET /api/health`, `GET /api/health/summary` (`app/main.py`). Summary is `metrics.summary()` — owner/admin/operator only. **No UI screen** (`getHealth` in `frontend/src/lib/api.ts` is unused by a tab) | `verify_metrics.py` (live `/api/health/summary` roles), `verify_testco_service.py` (`/api/ping`) | Summary reads `counter_days` / `measurements`. `/api/health` is live I/O, not a table |

**Auth / accounts** (every scoped surface): `app/auth.py`, `app/accounts.py`,
`app/accounts_cli.py`, `frontend/src/screens/Login.tsx`. DB:
`jarvis_accounts.db` table `accounts`.

**Operator inject / outbox:** `app/inject.py`; `GET/POST /api/operator/inject`,
`GET /api/operator/outbox` (`app/main.py`); `frontend/src/components/OutboxList.tsx`.
Journal `state/operator_inject.jsonl`; outbox
`/opt/testco/hermes/outbox/captured_mail.jsonl`. Scripts: `verify_inject.py`,
`verify_named_operator.py`, `verify_outbox.py`.

## 4. Ledger rule

After any write, show the before/after delta from the SQLite rows or
counters. The script's own `PASS` output alone is not proof.

1. **Before** — count or snapshot the rows the change should touch.
2. **Run** the verify script (or the API call).
3. **After** — same query. Assert the **delta**, not a threshold.
4. Paste both numbers. A skipped assertion is a hole.

The `sqlite3` CLI is **not installed**. `file:…?mode=ro&immutable=1` reads
only the checkpointed file and reports **stale-low** counters (WAL on
`jarvis_threads.db` was ~80 sweep_runs ahead on 2026-08-30). Copy
`db`+`-wal`+`-shm` to a temp dir, then open the copy
(`docs/testco-baseline/BASELINE-REPORT.md`).

```bash
# Testco stores. Never query /root/.hermes/state/ as the testco ledger.
# Never install sqlite3. WAL-inclusive snapshot via the venv:
/root/jarvis-app/.venv/bin/python - <<'PY'
import sqlite3, shutil
from pathlib import Path
T = Path("/opt/testco/hermes/state")
S = Path("/tmp/testco-ledger-snap")
shutil.rmtree(S, ignore_errors=True); S.mkdir()
for name in ("jarvis_tasks.db", "jarvis_leads.db", "jarvis_threads.db"):
    src = T / name
    if src.exists():
        shutil.copy2(src, S / name)
    for suf in ("-wal", "-shm"):
        w = T / (name + suf)
        if w.exists():
            shutil.copy2(w, S / (name + suf))
def q(db, sql):
    p = S / db
    if not p.exists():
        print(f"MISSING {db}"); return
    con = sqlite3.connect(str(p))
    rows = con.execute(sql).fetchall(); con.close()
    if not rows: print("(0 rows)")
    for r in rows: print("|".join("" if x is None else str(x) for x in r))
print("--- tasks ---"); q("jarvis_tasks.db", "SELECT status, COUNT(*) FROM tasks GROUP BY status;")
print("--- leads ---"); q("jarvis_leads.db", "SELECT archived, COUNT(*) FROM leads GROUP BY archived;")
print("--- threads ---"); q("jarvis_threads.db", "SELECT COUNT(*) FROM threads WHERE archived=0;")
print("--- counters ---"); q("jarvis_threads.db", "SELECT name, value FROM counters;")
print("--- counter_days ---"); q("jarvis_threads.db", "SELECT name, day, value FROM counter_days ORDER BY day DESC LIMIT 20;")
PY
T=/opt/testco/hermes/state
wc -l "$T/activity_feed.jsonl" "$T/approvals.jsonl" "$T/approvals_decisions.jsonl" \
    /opt/testco/hermes/outbox/captured_mail.jsonl
```

`GET /api/health/summary` and `GET /api/counters` are the HTTP face of the
same `jarvis_threads.db` tables. Use them when you need the API, the file
when you need the ledger.

Boxed suites (`docs/tasks/env.py`, `docs/lead-capture/env.py`) write under
their `BOX`, not testco state. Ledger those box files. Confirm
`assert_isolated()` would pass — isolating the database is not isolating
the side effects.

## 5. Known gap — stale testco

`/opt/testco/jarvis-app` is **STALE vs main**. It **has** per-user auth:
dana PIN login on :8710 succeeded 2026-08-30
(`docs/testco-baseline/BASELINE-REPORT.md`). It **lacks** the Tasks
tab/API, channel members, operator-role frontend, and later design
passes (`AGENTS.md`). A fail on 8710 for those missing features means
the **deploy lacks the code**, not that main is broken. Do not "fix" by
syncing testco. Reconciling is a human-supervised task. Bao is testing
the running build; do not change it without approval.

`JARVIS_ALLOW_WRITES=1` on the running testco units is **deliberate**
(writes behind the `.example` boundary so action claims are testable).
`verify_testco_service.py` asserting `==0` is a stale assertion, not a
misconfiguration (`docs/testco-baseline/BASELINE-REPORT.md`).

**Will fail on running testco because the feature is absent (not because it is broken):**

| Feature | Scripts |
|---------|---------|
| Tasks | `verify_tasks_api.py`, `verify_tasks_chat.py` §(a), `verifyTasksUI.mjs`, `mutate_tasks.py` |
| Members / W3 scoping | `verify_w3_scope.py`, `verifyW3ui.mjs`, `verifyTestcoSeeded.mjs` (dana/omar/priya channel scope), `roles.py` |
| Operator-role frontend | `verify_outbox.py`, `verify_named_operator.py`, `verify_inject.py` (operator inject + role gates) |

Basic PIN login is **not** in this table. `verify_testco_service.py`
(dana login), `verify_approval_executor.py` part 2,
`verify_fullbody_fixture.py`, `verify_metrics.py` live role check, and
`docs/desktop-layout/verify.mjs` were listed here as “auth absent”;
the baseline proved person+PIN works on 8710, so a fail there is not a
stale-deploy gap.

In-process suites that import **this repo** (`/root/jarvis-app`) and a
scratch box do **not** hit the stale deploy: `verify_chat_honesty.py`,
`verify_tasks_digest.py`, `verify_sweeps.py`, `verify_leads_store.py`,
`verify_leads_export.py`, `verify_leads_contract.py`,
`verify_config_apply.py`, `verify_lead_capture.py`. They prove current
`app/` code, not `/opt/testco/jarvis-app`.

`docs/testco-rehearsal/verifyTestco.mjs` is **unrunnable** (bare
`playwright` import + missing scratchpad env) and also expects a fresh
empty tenant with legacy Passphrase — do not rediscover this
(`docs/testco-baseline/BASELINE-REPORT.md`).

`verify_w3_auth.py` / `verify_w3_sheet_leads.py` target **live 8700**, not
stale testco.

## How to pick and run

1. Map the change to a row in the feature map.
2. Prefer a script that already refuses `:8700` and defaults to 8710 or a sandbox.
3. If the script is in the stale table and you are hitting 8710, stop and say so.
4. Snapshot the ledger, run **one** script, snapshot again, paste the delta.
5. Python: `/root/jarvis-app/.venv/bin/python docs/…`. Do not `pip install`.
6. Playwright: import from
   `/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs`
   (as `verifyTasksUI.mjs` does). Many `verify*.mjs` take the URL as
   `argv[2]` with **no default** — pass `http://127.0.0.1:8710` explicitly
   or they will not run; never pass 8700. Skip
   `docs/testco-rehearsal/verifyTestco.mjs` — unrunnable (bare playwright
   import + missing scratchpad env;
   `docs/testco-baseline/BASELINE-REPORT.md`).
7. Tasks API role matrix: `docs/tasks/tasks_sandbox.sh start` then
   `verify_tasks_api.py` on **8711**. That sandbox copies testco
   accounts/threads and uses **current** `/root/jarvis-app` code — it is
   how you prove tasks on main without the stale deploy.
8. Do not run mutate harnesses or any script that `systemctl restart
   jarvis-app` unless the user explicitly asks.

### Definition of done (`AGENTS.md`)

On testco (8710): the relevant `docs/` verify script(s) ran (paste
output); PROMPT 1–6 regressions that apply are green; the ledger delta
is shown. Not done: "the code looks right", "tests should pass".
