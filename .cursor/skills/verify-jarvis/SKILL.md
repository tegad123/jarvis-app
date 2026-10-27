---
name: verify-jarvis
description: >-
  Prove a Jarvis change works in the fixture-backed verify sandbox
  (127.0.0.1:8711, /opt/jarvis-verify-fixture) using docs/ verify scripts,
  then show the SQLite/counter ledger delta. Use when verifying a change,
  running verify_*.py / verify*.mjs / mutate_*.py, checking feed, ask,
  channels, leads, tasks, approvals, sweeps, meetings, metrics, or health,
  or when the user mentions the sandbox, the fixture, PROMPT 1-6, or the ledger.
---

# Verify Jarvis in the sandbox

A change is not done until a script that exercises the changed path has been
run against the **verify sandbox**, and the **ledger** (SQLite rows or
counters in the sandbox box) shows the before/after delta. The script's own
`PASS` line is not proof.

## 1. Targets

| Port | What it is | Role |
|------|------------|------|
| **8711** | `docs/tasks/tasks_sandbox.sh` — code under test + fixture config | **The verification target** |
| 8701 | `docs/w3_sandbox.sh` | Isolated W3 scoping box |
| 8730 / 8672 | rehearsal app / gateway | **A tenant (live posture, real mailbox, writes on). Never a test target.** |
| **8700** | LIVE tenant (`/root/jarvis-app`) | **Never verify here. Never write here.** |
| ~~8710 / 8652~~ | testco | **Retired 2026-10-13.** Scripts that needed it exit with `RETIRED`. |

**The fixture** is `/opt/jarvis-verify-fixture` (root, 700, `chattr +i`): testco's frozen
`.example` config and stores in testco's layout (`hermes/state`,
`hermes/outbox`, `skills/openclaw-imports/_shared`, `fixtures/`,
`SEEDED_PINS.json`). Its `hermes/.env` holds **throwaway** values generated
at retirement, not testco's secrets. `runtime.json` points the gateway at the
dead port `127.0.0.1:9`. It is immutable, so a script that tries to write
into it fails with `Operation not permitted`; point that script at the
sandbox's copy (`$SB/hermes`, `VERIFY_SANDBOX_HERMES`) or a scratch home.

Outside the gate: `verify_showingsmart.py` 66/66, `verify_outbox.py` 21/21,
`verify_fullbody_fixture.py` 7/7. Known red because of fixture data (dana's
account email is a real address): `verify_auth_google.py`, `verify_email_column.py`.

**The sandbox** runs `VERIFY_APP_ROOT` (default: this checkout) with skills
code from `VERIFY_SKILLS_ROOT` (default: the fixture's copy), mock Google
OAuth (`APP_GOOGLE_OAUTH_MOCK=1`), a Fernet token key generated per start
(`$SB/token_key`, 600), a loopback origin, and a box `runtime.json` whose
`paths.app_root` is the code under test (skills find the app only there).

## 2. Run the gate (PROMPT 1-6 plus the auth suites)

Start from a clean shell: stray `JARVIS_*` / `HERMES_HOME` exports silently
redirect suites. Python is the venv (`httpx` etc.); never `pip install`.

```bash
cd /root/jarvis-platform    # the tree under test
unset $(env | sed -n 's/^\(JARVIS_[A-Z_]*\|HERMES_HOME\|TASKS_[A-Z_]*\|AUTH_BASE\|VERIFY_BASE\)=.*/\1/p')
export PYTHONDONTWRITEBYTECODE=1 VERIFY_APP_ROOT=$PWD VERIFY_SKILLS_ROOT=/root/jarvis-skills-platform
export VERIFY_OPERATOR_PASSPHRASE="$(sed -n 's/^APP_PASSPHRASE=//p' /opt/jarvis-verify-fixture/hermes/.env)"  # throwaway; never print
PY=/root/jarvis-app/.venv/bin/python; SB=/tmp/claude-0/-root/tasks-sandbox
bash docs/tasks/tasks_sandbox.sh start

env JARVIS_ACCOUNTS_DB=$SB/accounts.db JARVIS_THREADS_DB=$SB/threads.db VERIFY_APP_LOG=$SB/app.log \
    $PY docs/auth/verify_google_connection.py                                   # 72/72
env VERIFY_HTTP_ACCOUNTS_DB=$SB/accounts.db JARVIS_THREADS_DB=$SB/threads.db VERIFY_APP_LOG=$SB/app.log \
    $PY docs/auth/verify_creds_cutover.py                                       # 42/42
$PY docs/tasks/verify_tasks_api.py                                              # PROMPT 1: 65/65
$PY docs/tasks/verify_tasks_chat.py --stub                                      # PROMPT 2: 20/20, 1 skipped
$PY docs/tasks/verify_tasks_digest.py                                           # PROMPT 3: 32/32
$PY docs/tasks/verify_chat_honesty.py                                           # PROMPT 3b: 39/39
$PY docs/sweeps/verify_sweeps.py                                                # PROMPT 4: 119/119
$PY $VERIFY_SKILLS_ROOT/meeting-intake/tests/verify_meeting_intake.py --stub-extract --stub   # PROMPT 5: 69/69, 9 skipped
$PY docs/metrics/verify_metrics.py                                              # PROMPT 6: 62/62
bash docs/tasks/tasks_sandbox.sh stop
```

Counts are as of 2026-10-13 on `platform`. A different count is a finding.
The PROMPT numbering is inferred (the repo never names them); keep this map.

**Side-effect proof after the gate:** hash the fixture's non-sidecar files
before and after (must be identical), confirm `/opt/testco` does not exist,
and confirm live's `MainPID` and `git -C /root/jarvis-app status` are unchanged.

## 3. Scripts that WRITE — never point at :8700

Several refuse `:8700` in code; several do not. Treat the list as the rule.

- `docs/verify_w3_auth.py` — **hardcoded `:8700`**, promotes/disables live accounts. **Do not run.**
- `docs/verify_w3_sheet_leads.py` — **hardcoded `:8700`** GET. Do not run.
- `docs/phaseA-shots/verifyA.mjs`, `docs/phaseB-shots/verifyB.mjs`, `docs/step2-shots/verify.mjs`,
  `docs/step3-shots/verify3.mjs`, `docs/step4-shots/verify4.mjs` — **stop/restart `jarvis-app`**. Do not run.
- `docs/mutate_w3.py`, `docs/w1-shots/mutate_leadgate.py` — **restart `jarvis-app`**. Do not run.
- Older Playwright suites read the passphrase from **live** `/root/.hermes/.env`
  (`docs/*-shots/*.mjs`, `docs/visual-polish*/`). They are live-era artifacts; do not run.
- Mutation harnesses `docs/tasks/mutate_tasks.py`, `docs/sweeps/mutate_sweeps.py`,
  `docs/metrics/mutate_metrics.py` patch source; run only when asked.

**Retired with testco** (exit `RETIRED`): `verify_approval_executor.py`,
`operator-inject/verify_inject.py`, `operator-inject/verify_named_operator.py`,
`auth/mutate_google_connection.py`, and everything under `docs/testco-rehearsal/`
except the data module `seed/fixture_cast.py`.

## 4. Authenticate

`POST /api/login` (`app/main.py`, `app/auth.py`), cookie jar, no `Authorization` header.

| Path | Body |
|------|------|
| Person | `{"person_key": "<key>", "pin": "<pin>"}` — pins in `/opt/jarvis-verify-fixture/SEEDED_PINS.json` |
| Operator | `{"passphrase": "<secret>"}` — `APP_PASSPHRASE` from the fixture `.env` (or `VERIFY_OPERATOR_PASSPHRASE`, which the sandbox passes through) |

People: **dana** (owner), **priya** (admin), **omar** (member), **tester**
(member). Named operator **bao** is in `APP_OPERATOR_PASSPHRASES`. Never print
a pin or passphrase, even throwaway ones; read key names only.

## 5. Feature map

Tabs `feed | ask | approvals | leads | tasks` (`frontend/src/components/TabBar.tsx`).

| Surface | App | Verify scripts | Ledger (in `$SB`) |
|---------|-----|----------------|-------------------|
| Feed | `app/feed.py` → `_shared/notify` | `verify_tasks_chat.py`, `verify_chat_honesty.py` | `feed.jsonl` (not SQLite) |
| Ask / chat | `app/chat.py`, `POST /api/chat` | `verify_tasks_chat.py`, `verify_chat_honesty.py` | `threads.db` `messages`; chat tasks in `tasks.db` (`source='chat'`) |
| Channels / members | `app/threads.py` | `verify_w3_scope.py` (8701) | `threads.db` `threads`, `thread_members` |
| Team channels (people talk, @jarvis, unread, Settings → Channels) | `app/channels.py`, `app/chat.py` `stream_channel_post` | `docs/channels/verify_channels.py` (own sandbox :8713 + stub gateway), `docs/channels/verify_channels_ui.mjs` (:8714, two browsers) | `threads.db` `messages.author`, `thread_reads`, `channel_settings`, counters `channel_*` |
| Leads | `app/leads_source.py` → `leads_store.py` | `docs/w1-shots/verify_leads_*.py`, `docs/lead-capture/verify_lead_capture.py` | `leads.db` `leads` |
| Tasks | `app/tasks_source.py` → `tasks_store.py` | PROMPT 1, 2, 3, 3b | `tasks.db` `tasks`, `digest_map`, `digest_replies` |
| Approvals | `app/approvals.py` | `verify_fullbody_fixture.py`, `phase5-shots/verify_config_apply.py` | `$SB/hermes/state/approvals*.jsonl`, `$SB/hermes/outbox/captured_mail.jsonl` |
| Sweeps | skills `sweeps/scripts/` | PROMPT 4 | boxed `sweeps.db` (`nudges`, `runs`) |
| Meetings | skills `meeting-intake/` | PROMPT 5 | `tasks.db` (`source='meeting'`), feed |
| Metrics / health | `app/metrics.py`, `app/health.py` | PROMPT 6 | `threads.db` `counters`, `counter_days`, `measurements` |
| Google connection | `app/google_connection.py`, `app/google_oauth.py` | auth suites | `accounts.db` `google_connection` |

Boxed suites (`docs/tasks/env.py`) write under their `BOX`
(`/tmp/claude-0/-root/tasks-box`); `assert_isolated()` refuses to run if a
store resolves outside it.

## 6. Ledger rule

1. **Before**: count the rows the change should touch (in `$SB` or the box).
2. **Run** one script.
3. **After**: same query. Assert the **delta**, not a threshold. Paste both.

The `sqlite3` CLI is not installed; use the venv. To read a WAL database
consistently, copy `db` + `-wal` + `-shm` to a temp dir and open the copy.

```bash
$PY - <<'PY'
import sqlite3
SB = "/tmp/claude-0/-root/tasks-sandbox"
def q(db, sql):
    c = sqlite3.connect(f"{SB}/{db}"); print(db, c.execute(sql).fetchall()); c.close()
q("tasks.db", "SELECT status, COUNT(*) FROM tasks GROUP BY status")
q("threads.db", "SELECT name, value FROM counters")
q("accounts.db", "SELECT COUNT(*) FROM google_connection")
PY
```

### Definition of done (`AGENTS.md`)

In the sandbox (8711): the relevant `docs/` verify script(s) ran (paste
output); PROMPT 1-6 are green; the ledger delta is shown; the fixture is
unchanged. Not done: "the code looks right", "tests should pass".
