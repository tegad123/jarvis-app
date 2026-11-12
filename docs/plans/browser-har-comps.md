# Plan: a real browser for Ask Jarvis, and HAR comps

Status: plan only. Nothing in this document is built, deployed or switched on.
Written 2026-11-12 against `platform` at `e017f36`.

This plan is for a second run that will implement it. Each step in section 13
is sized to be done, tested and committed on its own, in order.

## Contents

1. What is being built
2. Rules that apply to the build
3. What exists today
4. Architecture
5. Files to add or change
6. Database changes
7. Settings and switches
8. The Ask tool contract
9. HAR page flow and how pages are read
10. Caps and rate limits
11. Security
12. Deploy runbook (for later, not part of the build)
13. Implementation steps
14. Test plan
15. Open risks and decisions
16. HAR terms and MLS access: what Tega should confirm
17. Work in progress that already exists

---

## 1. What is being built

Six things, in the order they depend on each other.

1. **A headed browser.** Each person's Chromium runs as a normal desktop
   browser inside a virtual display (Xvfb) instead of as a headless shell. Same
   one-browser-per-person rule, same on-demand start through the tenant's
   socket, same egress proxy.
2. **A live Take over view.** While a person has taken over, they see and
   drive their own browser live through noVNC inside the app. They sign in to
   HAR themselves and do any 2FA or captcha themselves. Nothing in this build
   solves captchas or bypasses 2FA.
3. **Connected sites.** A list in Settings of the sites a person has signed in
   to, with a sign-out button. No credentials are shown. The existing saved
   logins (`browser_logins`) stay as they are.
4. **A standing read allowance.** A person can allow Jarvis to keep reading a
   named site (follow links, page through results, use search filters) without
   a fresh approval every 30 minutes. Typing into or submitting any other form
   still needs approval, exactly as today.
5. **HAR comps.** An Ask tool that pulls sold comparables for an address
   through the requesting person's own HAR session, read-only and low-volume,
   and returns a comps table and a short summary to that person only.
6. **Two things that are built but left off.** A stubbed licensed data source
   (CoreLogic Trestle, RESO Web API) behind the same interface as the browser
   source, and an optional outbound proxy setting for the browser's egress.

## 2. Rules that apply to the build

From `AGENTS.md` and the brief. The implementing run must hold to these.

- Code on a branch off `platform`, draft PR only. No deploys, no restarts, no
  config or switch changes on any tenant (dev34, eddie, blake, martin, tega,
  port 8700). No merges. `main` is not touched.
- No live HAR sign-in and no live HAR session by an agent, at any point in the
  build. Tests use made-up fixtures only.
- Never `git add -A`. Paste `git log --oneline -1` after each commit.
- Never print env files, secrets, credentials, cookies or message bodies.
- `AGENTS.md` rule 6 needs an explicit human yes for new dependencies and
  schema changes. This build needs the following, and the PR must list each
  one with where the yes was given. The 21:02 brief asked for noVNC, Xvfb and
  the comps storage by name, which covers the intent; section 15 asks Tega to
  confirm the exact list.

  | Needs a yes | What |
  |---|---|
  | Frontend dependency | `@novnc/novnc`, pinned to an exact version |
  | Server package | `x11vnc` (apt). `xvfb` is already installed |
  | Browser runtime | the full Chromium build, copied from the Playwright cache already on the box |
  | SQLite tables | `browser_sites`, `comps_settings`, `comps_ack`, `comps_runs` (section 6) |
  | Shared service change | WebSocket forwarding in `front/main.py`, which every tenant's traffic passes through |

  No Postgres migration is needed (section 6). No Python dependency is added:
  `websockets==17.0.1` is already in `requirements.txt`.

## 3. What exists today

Facts read from `platform` at `e017f36`, so the implementing run does not have
to rediscover them. Line numbers are approximate.

**The browser.**

- `app/browser.py` is the app's side: the gate the agent goes through
  (`gate()`), the approval hooks, and the signed-in person's routes under
  `/api/browser/*` (`status`, `screen`, `takeover`, `input`, `close`,
  `logins`).
- `app/browser_worker.py` runs one Chromium per person, driven over
  `--remote-debugging-pipe`. It is standard library only and is started as
  `/usr/bin/python3 -I browser_worker.py` by `<slug>-browser.service`, user
  `jarvis-<slug>-web`, with `PrivateNetwork=yes`. It is asyncio; each request
  is one JSON line in, one JSON line out, on a unix socket owned by the app
  user (mode 0600, socket-activated).
- Chromium is launched `--headless --no-sandbox` from
  `/opt/jarvis/browser/chrome-headless-shell-1234`. `_attach()` forces the
  page size with `Emulation.setDeviceMetricsOverride`.
- Profiles already persist per person at
  `/var/lib/jarvis-browser/<slug>/people/<person>`. On exit only
  `Default/Cache` is deleted. An idle browser is closed after 600 seconds; the
  worker exits after 1800 idle seconds. At most `MAX_OPEN` (default 2)
  browsers run at once; the idlest is closed to make room.
- All page traffic goes through `app/browser_egress.py`, an HTTP CONNECT proxy
  in its own unit that reaches public addresses only.
- `scripts/provision_tenant.py` copies only `browser_worker.py` and
  `browser_egress.py` (`BROWSER_CODE_FILES`) into `/opt/jarvis/browser-code`.
  The manifest key `browser: enabled | off` turns the units on;
  `browser_limits` carries `max_open` and `memory_max` (default `1G`).

**The gate.**

- Reading needs no approval: `open`, `read`, `scroll`, `back` run straight
  away. One exception: a run that has read outside content and then wants to
  open a new site with data in the address is held (`_exfil_risk`).
- Acting (`click`, `type`, `press`, `login`) needs a per-site grant. The agent
  asks with `request_access`; the person approves; `execute_approved` creates
  a grant that lasts `TASK_MINUTES = 30`. Risky gestures (payments, deleting,
  and so on) are held one at a time even with a grant.
- Grants live in Postgres (`grants`, migration `0001`). The table already
  allows `scope = 'always'` and has `revoked_at`. `platform_store.py` has
  `active_grants`, `create_grant` and `consume_once_grant`, and no revoke
  function.
- While a person has taken over (`taken_over(person)`), every agent operation
  except `status` is refused.
- Only the person whose browser it is can decide a browser approval.

**Take over today.** `frontend/src/components/BrowserWindow.tsx` polls
`/api/browser/screen` for a still image and sends clicks and typed text to
`/api/browser/input`. It is not live and cannot show anything outside the
page (a browser dialog, a pop-up window).

**Feature switches.** `accounts.FEATURES` is
`("ask","leads","tasks","send","browser","imessage")`. Every feature is on
unless a row in `person_features` says otherwise, the owner's are always on,
and `feature_on(None, ...)` returns `True`. `docs/team/verify_team.py` asserts
the session's features are exactly those six, all on.

**Ask tools.** Declared in `scripts/hermes_plugin/jarvis/tools.json`, run by
`run_action.py`, which calls the app's broker (`app_broker.call(...)`). A tool
with `"browser"` in `needs` is only offered when the browser is on
(`agent_status.BROWSER_TOOLS`). The gateway gives a tool 150 seconds; the
browser broker call allows 120.

**Chat rendering.** Chat bubbles render plain text only, never markdown from
the model. The app draws its own controls under a reply from
`messages.actions` (a JSON array; today only `connect_google`).

**The front.** `front/main.py` forwards each tenant's traffic with `httpx`
and handles HTTP methods only. It does not forward WebSockets, and it strips
the `upgrade` header. The app itself has no WebSocket route today.

**The box.** `xvfb` is installed; `x11vnc`, `novnc` and `websockify` are not.
The full Chromium (`Google Chrome for Testing 151.0.7922.34`) is in
`/root/.cache/ms-playwright/chromium-1234/chrome-linux64/` and `ldd` reports
no missing libraries. The kernel restricts unprivileged user namespaces, which
is why Chromium runs with `--no-sandbox`.

**The written position on HAR.** `docs/SPEC.md` section 8.6 says no HAR or MLS
logins by the bot until HAR's rules are confirmed. This build changes that
position and must update that sentence (step 17).

## 4. Architecture

### 4.1 Headed browser in a virtual display

One Xvfb display per running browser, started and stopped by the worker
alongside that person's Chromium, inside the same systemd unit.

```
<slug>-browser.service  (user jarvis-<slug>-web, PrivateNetwork, PrivateTmp)
└─ browser_worker.py
   ├─ person A:  Xvfb :N  →  chrome (headed, profile A)  →  x11vnc (only during Take over)
   └─ person B:  Xvfb :M  →  chrome (headed, profile B)
```

- **Display numbers.** Start Xvfb with `-displayfd` so it picks a free display
  and reports the number, rather than deriving one from the person's name. The
  saved work in progress hashes the name into 200 slots, which can give two
  people the same display; do not carry that over.
- **Xvfb arguments.** `-screen 0 1440x900x24 -nolisten tcp`. Its unix socket
  lands in the unit's private `/tmp`.
- **Chromium arguments.** Today's list with these differences in headed mode:
  no `--headless`, no `--hide-scrollbars`, `--window-position=0,0`,
  `--window-size=1440,900`, binary is the full `chrome`. `DISPLAY` is set in
  the child's environment. `--remote-debugging-pipe`, `--no-sandbox` and the
  proxy arguments stay.
- **No user-agent override.** Headed Chromium already reports a normal,
  non-headless user agent that matches its real version. The saved work in
  progress hard-codes a `Chrome/131` string, while the binary on the box is
  151; a mismatched version is itself a tell. Drop the override and assert in
  a test that the reported user agent does not contain `Headless`.
- **No forced page size.** Skip `Emulation.setDeviceMetricsOverride` in headed
  mode; the window size does the job.
- **`navigator.webdriver`.** Measure it on a fixture page in step 3. The brief
  asks for no automation tells beyond the unavoidable. If the debugging pipe
  sets it, `--disable-blink-features=AutomationControlled` clears it. That
  flag exists only to avoid detection, so record in the PR risk note that it
  is passed.
- **Profile preferences.** Before the first launch of a profile, write
  `Default/Preferences` with: restore the previous session on start (so
  session-only cookies survive the browser being closed when idle), password
  saving off, and autofill off. Passwords must not end up in Chromium's own
  store on disk. Step 3 tests that a session cookie survives a close and
  reopen; if the preference does not achieve that, stop and report.
- **No silent fallback.** If headed mode is asked for and Xvfb or the full
  binary is missing, the launch fails with a clear error, a log line and a
  counter. It must not quietly start headless: a headless start against a
  signed-in profile changes how the browser looks to the site.
- **Mode switch.** Manifest key `browser_mode: headless | headed`, absent
  means `headless`. Existing tenants do not change until someone sets it.
  `status` reports the mode.
- **Memory.** A headed Chromium plus Xvfb needs more than the headless shell.
  Measure two running at once in step 3 and set the headed default for
  `browser_limits.memory_max` from the measurement (expect about `2G`).

### 4.2 Take over view

```
person's browser tab
   │  wss://<tenant host>/api/browser/view      (session cookie)
   ▼
jarvis-front            relays this one WebSocket path to the tenant's app
   ▼
tenant app              checks the session, the person, the Origin and that
   │                    Take over is active; then relays bytes
   ▼  unix socket (the existing worker socket, app user only)
browser_worker          "view" request names the person; worker connects to
   │                    that person's VNC socket and relays bytes
   ▼  unix socket inside the unit's private directory
x11vnc → Xvfb display of that person
```

- **Only during Take over.** The VNC server for a display is started when its
  person takes over and stopped when they hand back, the Take over idle limit
  (900 seconds) passes, or the browser closes. Outside Take over the app keeps
  using the existing still image, which also keeps headless tenants working.
- **VNC on a unix socket.** x11vnc listens on a unix socket in a 0700
  directory under the unit's private `/tmp`, one socket per person. It has no
  TCP listener, so a web page inside the same network namespace cannot reach
  it. No VNC password: the socket is reachable only by the worker, and access
  control is the app's job. Confirm the exact x11vnc options for "unix socket
  only", "no clipboard sent to the viewer", "one viewer", "no file transfer"
  against `x11vnc -help` on the box once the package is approved; they have
  not been verified because the package is not installed.
- **The worker relays.** A new `view` request on the existing worker socket:
  the worker answers one JSON line, then that connection becomes a raw byte
  pipe to the named person's VNC socket. It does not hold the per-browser
  lock (status requests must still work), and it keeps `last_used` fresh so
  the idle timer does not close a browser someone is looking at. No separate
  websockify process and no new listening socket.
- **The app's WebSocket route.** `/api/browser/view`. Section 11.2 lists its
  checks. The person always comes from the session; there is no parameter
  that names whose browser to show.
- **The front.** Add one WebSocket route to `front/main.py` that resolves the
  tenant from the Host header the same way `entry()` does and relays to the
  tenant's port. Limit it to the exact path `/api/browser/view`. Use the
  `websockets` client already pinned in `requirements.txt`. Add counters for
  opened, closed and refused.
- **The frontend.** `BrowserWindow.tsx` shows noVNC's `RFB` view while Take
  over is active and the tenant is in headed mode, and the existing still
  image otherwise. The import path for `RFB` differs between noVNC releases;
  confirm it against the pinned version and that Vite 5 builds it.
- **If the front change is refused.** The fallback is to keep Take over on
  the existing HTTP routes with a faster image refresh. It cannot show
  anything outside the page, so some sign-in pop-ups would not be usable. Do
  not build the fallback unless the front change is refused.

### 4.3 Connected sites

A small table records, per person and site, that the person signed in there
(section 6). A row is written when a person hands back from Take over on a
site and confirms "I'm signed in". Comps marks a row `needs_signin` when it
meets a sign-in wall.

Settings gets a "Connected sites" section listing site, state ("Signed in" or
"Needs sign-in"), when it was connected and last used, whether a standing read
allowance exists, and whether a saved login exists. It never shows a username,
password or cookie. Two buttons per row:

- **Sign out** clears that site's cookies and storage in the person's profile
  (new worker request `forget_site`), deletes the row, and revokes the read
  allowance for that site.
- **Stop always allowing** revokes only the read allowance.

The existing "Website logins" section and its routes are unchanged.

There is no background keep-alive. When a site's session expires, the next
use finds the sign-in wall and asks the person to take over. Extra automated
traffic to keep a session warm is exactly what this build should not add.

### 4.4 Standing read allowance

Today reading pages needs no approval, and any click or typing needs the
30-minute site grant. The allowance lets a person approve once, per site, the
narrow set of gestures that only read:

- clicking a link, tab, pagination or sort control;
- typing into a search or filter field and submitting that search.

Everything else on the site keeps today's rules.

- **Classifying a gesture.** Extend the worker's `describe` answer with the
  element's role, tag, input type, link target, and the method and target of
  the form it sits in. `app/browser_policy.py` decides `read_only_gesture`
  from those facts. The rule is default-deny: a gesture that is not clearly
  reading falls through to the existing grant path. A password, email, text
  area or file field is never reading. A form that posts is never reading
  unless the field is a recognised search or filter field. The existing
  `risky()` check runs first and still holds risky gestures one at a time.
- **Storing it.** A row in the existing `grants` table with
  `scope = 'always'`, `action_class = 'external_send'`, destination `@<site>`,
  and a **different connector value** (`browser.read`) from the acting grant
  (`browser`). This matters: `_active_grant()` looks up grants by connector,
  so an `always` grant under the `browser` connector would unlock all clicking
  and typing on that site for good. Step 10 includes a canary test that a
  read allowance does not satisfy `_active_grant()`.
- **Asking for it.** When the agent attempts a read-only gesture on a site
  with neither an allowance nor a live site grant, the gate files a new
  approval of type `browser_read`. The card offers "Allow for 30 minutes" and
  "Always allow reading on this site". `approvals._scopes_for` returns both
  scopes for this type. Only the person whose browser it is can decide it, as
  with every browser approval.
- **Removing it.** Add `revoke_grant` to `platform_store.py` (sets
  `revoked_at`; the column exists). Used by Connected sites.
- **Recording it.** Every gesture run under the allowance writes a ledger row
  with the grant id, as acting gestures do today.

### 4.5 Comps

```
Ask turn ─ find_comps tool ─ run_action.py ─ POST /api/broker/comps
                                                   │
                         app/comps.py: switches, direct-line check, caps
                                                   │ starts a run (comps_runs row)
                         app/comps_sources.py: picks the source
                                ├─ HarBrowserSource  (app/comps_har.py)
                                │     every page load goes through browser.gate()
                                └─ TrestleSource     (stub, off)
                                                   │
                         result stored on the run; card drawn in the chat
```

- A comps request is a **run**: one row in `comps_runs` holding who asked,
  what they asked for, how many pages were loaded, how it ended, and the
  result.
- Runs take longer than one tool call allows (ten page loads with pauses).
  The run executes in the app in the background. `find_comps` waits up to 100
  seconds for it; if it is still going, the tool returns the run id and the
  agent calls `comps_result` to wait again.
- Every page the run loads goes through `browser.gate()` as that person, so
  each load is a ledger row and the Take over rule, the egress rule and the
  feature switch all apply without being re-implemented.
- The run stops at the first sign-in wall, human check, 2FA prompt or block.
  It marks the site `needs_signin`, asks the person to take over (the existing
  `_ask_person` path), and returns `needs_person`. It never retries on its
  own.
- The result is shown as a card the app draws, read from the stored run, not
  as text the model wrote. The model also receives the rows so the person can
  ask follow-up questions (section 15 flags this for a decision).

### 4.6 Data-source interface

`app/comps_sources.py` defines the shapes both sources share, so Ask's tool
does not change when the source does.

```python
@dataclass(frozen=True)
class CompsQuery:
    address: str
    radius_miles: float
    months_back: int
    beds_min: Optional[int]; beds_max: Optional[int]
    baths_min: Optional[float]; baths_max: Optional[float]
    sqft_min: Optional[int]; sqft_max: Optional[int]
    max_results: int                      # never above 100

@dataclass(frozen=True)
class Comp:
    address: str
    sold_date: str                        # YYYY-MM-DD
    sold_price: int
    sqft: Optional[int]
    beds: Optional[int]; baths: Optional[float]
    year_built: Optional[int]
    days_on_market: Optional[int]
    distance_miles: Optional[float]       # None when the source does not give it

@dataclass
class SourceResult:
    state: str        # done | needs_person | blocked | unreadable | ambiguous | failed | not_configured
    comps: List[Comp]
    page_loads: int
    detail: str       # plain words for the person; never page content
    choices: List[str]            # for "ambiguous"

class Source(Protocol):
    name: str
    def available(self, person: str) -> Tuple[bool, str]: ...
    def search(self, query: CompsQuery, person: str, budget: "Budget", ctx: dict) -> SourceResult: ...
```

- `HarBrowserSource` is section 9.
- `TrestleSource` is a stub: `available()` returns `(False, "not configured")`
  and `search()` returns `not_configured`. Its docstring describes the request
  it would make (a RESO Web API `Property` query for closed listings by close
  date and distance) and says the exact fields have not been checked against
  Trestle. Its credentials would be read from the environment by name; none
  are wired.
- Setting `comps.source`: `har_browser` (default) or `trestle`.
- `distance`, `$ per sqft`, the filters and the summary are computed or
  re-checked in `app/comps.py`, after the source returns, so both sources are
  held to the same rules.

### 4.7 Optional outbound proxy

Off by default, no provider wired. When a manifest block `browser_proxy`
(`host`, `port`) is present, `browser_egress.py` sends each connection through
that upstream proxy instead of connecting directly.

- The egress still resolves the destination itself and refuses anything that
  is not a public address before using the upstream.
- If the upstream is set and unreachable the request fails with a counter and
  a log line. It never falls back to a direct connection.
- Upstream credentials, if any, come from a systemd credential file
  (`LoadCredential=`), not from the unit's `Environment=` and not from the
  manifest. Logs name the upstream host and port and nothing else.
- The upstream must be a public address; the egress unit's `IPAddressDeny`
  already blocks private ranges.
- It applies to the whole tenant: everyone on the tenant leaves from the same
  address, as they do today.

## 5. Files to add or change

| File | Change |
|---|---|
| `app/browser_worker.py` | Load helper modules from its own directory under `-I`; headed launch; Xvfb and x11vnc lifecycle; `view`, `forget_site`, `tables` requests; richer `describe`; refuse non-web addresses |
| `app/browser_launch.py` (new) | Pure functions: launch arguments for each mode, profile path, first-run preferences |
| `app/browser_policy.py` (new) | Pure functions: `view_allowed`, `read_only_gesture` |
| `app/browser.py` | WebSocket route `/api/browser/view`; `browser_sites` table and routes; read-allowance path in `_act`; `browser_read` approvals; revoke |
| `app/browser_egress.py` | Optional upstream proxy |
| `app/platform_store.py` | `revoke_grant` |
| `app/approvals.py` | Scopes and display for `browser_read` |
| `app/comps.py` (new) | Switches, acknowledgement, caps, runs, summary, broker endpoint, person routes |
| `app/comps_sources.py` (new) | Shapes in 4.6, source selection, `TrestleSource` stub |
| `app/comps_har.py` (new) | `HarBrowserSource`: page classifier, parsers, page flow |
| `app/accounts.py` | `comps` feature, off by default, including for the owner |
| `app/team.py` | Accept `comps` in the feature patch; allow it for the owner |
| `app/agent_status.py` | `COMPS_TOOLS`; status line for comps; chat card for comps |
| `app/chat.py`, `app/threads.py` | Emit and persist the comps card reference |
| `app/tenant_delete.py` | Count the new tables; remove browser profiles |
| `app/main.py` | Register the comps routers |
| `front/main.py` | WebSocket forwarding for `/api/browser/view` |
| `scripts/hermes_plugin/jarvis/tools.json` | `find_comps`, `comps_result` |
| `scripts/hermes_plugin/jarvis/run_action.py` | The two tool functions and their plain-words replies |
| `scripts/provision_tenant.py` | `BROWSER_CODE_FILES`; `browser_mode`; `comps`; `browser_proxy`; headed binary path; unit checks |
| `scripts/install_browser_runtime.py` | Copy the full Chromium as well as the headless shell |
| `scripts/templates/browser.service` | Mode, Xvfb and x11vnc paths, memory |
| `scripts/templates/egress.service` | Upstream proxy settings and credential |
| `frontend/package.json` | `@novnc/novnc` |
| `frontend/src/components/BrowserWindow.tsx` | Live view during Take over |
| `frontend/src/components/CompsCard.tsx` (new) | The comps table and summary |
| `frontend/src/screens/Settings.tsx` | Connected sites; comps switch and notice; `comps` chip in Team |
| `frontend/src/lib/api.ts`, `lib/types.ts` | New calls and types |
| `docs/browser/verify_browser.py` | Headed, view, sites, allowance checks |
| `docs/comps/verify_comps.py` (new) | Comps checks |
| `docs/comps/fixtures/` (new) | Made-up HAR-like pages |
| `docs/team/verify_team.py` | Feature list now includes `comps: False` |
| `docs/accounts/verify_tenant_delete.py` | New tables and profile removal |
| `docs/front/verify_front.mjs` | WebSocket forwarding |
| `docs/SPEC.md` | Section 8.6 and "Not in v1" |

The skills repo's `identity.py` drops feature names it does not know, so the
`comps` switch is enforced in the app only. Check that `write_team_file()`
does not start listing `comps` as switched off for everyone.

## 6. Database changes

**Postgres: none.** The read allowance uses the existing `grants` table
(`scope = 'always'` and `revoked_at` already exist; `connector` is free text).
`SCHEMA_HEAD` stays `"0001"`. The new approval type `browser_read` goes in the
approval's action JSON, as `browser_task` does today; the `approvals.kind`
column stays `action`, which is one of the two values its check allows.

**SQLite, accounts database.** Four additive tables, created with
`CREATE TABLE IF NOT EXISTS` in the same lazy way `browser_logins` is. No
existing table changes. Each is a schema change under `AGENTS.md` rule 6.

```sql
-- A site a person signed in to in their own browser. No credentials.
CREATE TABLE IF NOT EXISTS browser_sites (
    person_key    TEXT NOT NULL,
    site          TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN ('connected','needs_signin')),
    connected_at  TEXT NOT NULL,
    last_used_at  TEXT,
    PRIMARY KEY (person_key, site)
);

-- The tenant's comps switch. One row, id = 1.
CREATE TABLE IF NOT EXISTS comps_settings (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER NOT NULL DEFAULT 0,
    changed_by  TEXT NOT NULL,
    changed_at  TEXT NOT NULL
);

-- Who acknowledged the HAR notice, and which wording. Rows are never updated.
CREATE TABLE IF NOT EXISTS comps_ack (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_version  TEXT NOT NULL,
    notice_sha256   TEXT NOT NULL,
    person_key      TEXT NOT NULL,
    acked_at        TEXT NOT NULL
);

-- One comps request. Also the counter for the daily caps.
CREATE TABLE IF NOT EXISTS comps_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_key    TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    source        TEXT NOT NULL,
    query_json    TEXT NOT NULL,
    state         TEXT NOT NULL,      -- running | done | needs_person | blocked | unreadable | ambiguous | failed
    stop_kind     TEXT,               -- login | captcha | two_factor | blocked
    page_loads    INTEGER NOT NULL DEFAULT 0,
    result_count  INTEGER NOT NULL DEFAULT 0,
    result_json   TEXT,               -- rows and summary; cleared after comps.keep_days
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS comps_runs_person ON comps_runs (person_key, started_at);
```

- `person_features` is unchanged. For `comps` a missing row means off; for the
  other six a missing row still means on.
- `messages` is unchanged. The comps card is stored in the existing
  `messages.actions` JSON array as the string `comps:<run id>`.
- `result_json` holds sold prices. It is cleared (set to NULL, the row kept)
  once older than `comps.keep_days`. The clearing runs when a new run starts
  and logs how many rows it cleared.
- `tenant_delete.py` deletes SQLite tables by listing them, so the data goes
  with the tenant; its verify script counts tables and must be updated.
- The profile directories on disk are a separate matter. Operator teardown in
  `provision_tenant.py` removes `/var/lib/jarvis-browser/<slug>`. In-app
  deletion (`tenant_delete.py`) only clears the in-memory "needs you" list
  and does not touch profiles, and nothing in `accounts.py` or `team.py`
  mentions the browser when a person is removed. Step 17 closes both, so a
  signed-in HAR session does not outlive a deleted tenant or a removed
  person.

## 7. Settings and switches

Comps runs for a person only when **every** line below is true. Each refusal
names the line that failed and bumps its own counter.

| # | Switch | Where it lives | Who sets it | Default |
|---|---|---|---|---|
| 1 | Browser on for the tenant | manifest `browser: enabled` | operator | off |
| 2 | Browser mode | manifest `browser_mode: headed` | operator | `headless` |
| 3 | Comps available to the tenant | manifest `comps: available`, read as `app.comps.available` | operator | off |
| 4 | Comps on for the tenant | `comps_settings.enabled` | owner, in Settings | off |
| 5 | HAR notice acknowledged | a `comps_ack` row for the current notice version | owner | none |
| 6 | Browser on for the person | `person_features` | owner or admin | on |
| 7 | Comps on for the person | `person_features` | owner or admin | **off, including the owner** |

Line 4 cannot be turned on without line 5 in the same request. If the notice
wording changes (new `notice_version`), line 5 fails until the owner
acknowledges the new wording. Comps requires headed mode: a headless browser
has no way for the person to complete a sign-in challenge.

Other settings, all read through `tenant_config` under `app.comps.*` and
rendered from a validated manifest block the way `lead_followup` is:

| Setting | Default | Hard limit in code |
|---|---|---|
| `source` | `har_browser` | `har_browser` or `trestle` |
| `site` | `har.com` | one site per tenant |
| `max_page_loads_per_request` | 10 | 10 |
| `max_requests_per_person_per_day` | 5 | 20 |
| `max_page_loads_per_person_per_day` | 40 | 100 |
| `max_results` | 25 | 100 |
| `pause_seconds` | `[4, 9]` (random between) | minimum 3 |
| `cooldown_hours_after_block` | 24 | minimum 1 |
| `keep_days` | 30 | maximum 90 |
| `allow_in_channels` | false | |

A setting can lower a hard limit and never raise it. A value outside the
limit is refused when the manifest is validated, not clamped silently.

Browser settings:

| Setting | Default |
|---|---|
| `browser_mode` | `headless` |
| `browser_limits.memory_max` | `1G` headless; set from measurement for headed |
| `browser_proxy` | absent (off) |

## 8. The Ask tool contract

Two tools in `tools.json`, both with `"needs": ["browser", "comps"]`. They are
offered to the agent only when all seven lines in section 7 hold for the
person; otherwise they are absent from the allowlist and the status block
says, in one line, why comps is not available.

**`find_comps`**

| Parameter | Type | Default | Range |
|---|---|---|---|
| `address` | string, required | | 5 to 200 characters |
| `radius_miles` | number | 1.0 | 0.25 to 5 |
| `months_back` | integer | 6 | 1 to 24 |
| `beds_min`, `beds_max` | integer | none | 0 to 12 |
| `baths_min`, `baths_max` | number | none | 0 to 12 |
| `sqft_min`, `sqft_max` | integer | none | 200 to 20000 |
| `max_results` | integer | 25 | 1 to 100 |

**`comps_result`**: `run_id` (integer, required). Waits up to 100 seconds for
a run that `find_comps` reported as still running. Only the person who started
the run can read it.

**Broker endpoint.** `POST /api/broker/comps`, body
`{"ref": <turn ref>, "op": "find" | "result", ...parameters}`. Same shape as
`lead_tools.py`: `broker._authorize`, the person from `broker._turn_person`,
then the checks in this order: switches (section 7), direct line, caps.

**Success.**

```json
{
  "ok": true, "state": "done", "run_id": 41, "source": "har_browser",
  "subject": {"address": "..."},
  "count": 18, "truncated": false, "page_loads": 6,
  "summary": {
    "used": 16,
    "median_price_per_sqft": 212,
    "price_per_sqft_low": 188, "price_per_sqft_high": 241,
    "suggested_low": 398000, "suggested_high": 452000,
    "note": "Based on 16 sales. Not an appraisal."
  },
  "comps": [
    {"address": "...", "sold_date": "2026-08-14", "sold_price": 415000,
     "price_per_sqft": 207, "beds": 3, "baths": 2.0, "sqft": 2004,
     "year_built": 1998, "days_on_market": 21, "distance_miles": 0.4}
  ]
}
```

Still running: `{"ok": true, "state": "running", "run_id": 41}`.

**Summary rules.** `price_per_sqft` is sold price divided by sqft, rounded.
The median uses only rows that have both. Low and high are the 25th and 75th
percentiles. `suggested_low` and `suggested_high` are those multiplied by the
subject's sqft, and are present only when the subject's sqft is known and at
least three rows were used. With fewer than three, the summary says there are
too few sales to suggest a range. `truncated` is true when the source had more
rows than `max_results`.

**Refusals.** Each has a fixed plain-words line in `run_action.py` (the
pattern `LEADS_SAY` uses), so the agent tells the person the right thing.

| HTTP | `error` | Meaning |
|---|---|---|
| 403 | `feature_off` + `layer` | Which line of section 7 failed |
| 403 | `direct_only` | Asked in a shared channel; comps are shown in the person's own direct line |
| 409 | `needs_person` + `kind` | Sign-in, human check, 2FA or block; the person must take over |
| 409 | `busy` | That person already has a run going |
| 409 | `cooling_off` + `until` | A block was met recently |
| 429 | `daily_cap` + `resets_at` | Daily cap reached |
| 409 | `ambiguous` + `choices` | More than one property matches the address |
| 502 | `unreadable` | The page was not one the reader recognises; nothing was guessed |
| 503 | `source_unavailable` | Browser not running, or the source is not configured |
| 400 | `bad_request` | A parameter is out of range |

**Where the result appears.** The direct-line check is: the turn's thread has
`direct_for` equal to the requesting person. When the run ends `done`, the
turn requests the chat card (the mechanism `connect_google` uses through
`actor_refs.request_action`), the stream emits
`{"type": "comps", "run_id": 41}`, and the reply row stores `comps:41` in
`messages.actions`. The frontend fetches `GET /api/comps/runs/41` and draws
the table. That route requires a signed-in person, refuses the break-glass
operator, and returns 404 unless the run belongs to the caller.

## 9. HAR page flow and how pages are read

**What is not known.** No HAR or Matrix page was opened during planning, and
none may be opened by an agent during the build. The page structure, the
addresses of the search pages, and which surface shows sold prices to a
signed-in agent are unknown. The saved work in progress contains a HAR search
address that is a guess; do not use it.

**How the build proceeds without that knowledge.**

1. The implementing run writes its own made-up pages that look like a listing
   site (`docs/comps/fixtures/`): a search form, a results table, a results
   page of cards, a "no results" page, a sign-in wall, a human-check page, a
   2FA page, a "too many requests" page, and a page of an unknown shape. All
   names, addresses and prices are invented and use `.example` where an
   address is needed.
2. The reader is built and tested against those.
3. A person (Spencer or Tega), signed in on their own machine, saves the real
   pages by hand and passes them through a sanitising script that replaces
   every address, price, name, MLS number, phone, email and image before the
   file is committed. That is a human step after the PR, listed in section 15.
4. The first run against the real site happens on a test tenant with a person
   watching, after deploy, with the caps set to their lowest. That is also
   after the PR.

**Reading a page.** All parsing is Python in `app/comps_har.py`, so it is
testable without a browser. The worker gets one new request, `tables`, which
runs a fixed script shipped in the worker (never one supplied by the model or
by the app at run time) and returns, capped in size:

- each HTML table as header cells plus rows of cell text;
- repeated cards as lists of label and value text;
- JSON data blocks embedded in the page (for example JSON-LD).

The parser tries, in order: an embedded data block; a table whose headers map
to known column names; labelled cards. Columns are found by header text
through one mapping table (for example "Sold Price", "Close Price" →
`sold_price`), never by CSS class names, which change often. The mapping
table is the single place to edit when the real pages are seen.

**Classifying a page before reading it.** `classify(page)` returns one of:

| Class | Signals | What the run does |
|---|---|---|
| `results` | a table or cards the parser recognises with at least one row | read it |
| `empty` | an explicit "no results" marker | finish with zero rows |
| `login` | the worker's existing `login` flag, a password field, or a sign-in address | stop, `needs_person` |
| `captcha` | a known human-check frame or wording | stop, `needs_person` |
| `two_factor` | a one-time-code field or wording | stop, `needs_person` |
| `blocked` | HTTP 403 or 429, or "access denied" / "unusual activity" wording | stop, start the cooldown |
| `unknown` | none of the above | stop, `unreadable` |

`empty` and `unknown` are deliberately different. A page the reader does not
understand must never be reported as "no sales found".

**The flow.**

1. Check switches, direct line and caps; create the run row.
2. Open the search starting page for the configured site. If the site lets a
   search be expressed in the address, use that. Otherwise fill the search
   filters with the read-allowance gestures: address, radius, sold status,
   date range, beds, baths, sqft.
3. Classify. Stop on anything but `results` or `empty`.
4. If more than one property matches the address, return `ambiguous` with the
   choices as the site shows them.
5. Read the rows. Page forward only while rows are needed and the page budget
   allows. Pause a random time within `pause_seconds` before every load.
6. Stop reading at `max_results` rows.
7. Re-check every row in the app against the request (sold date within
   `months_back`, beds, baths, sqft, radius when the source gives distance)
   and drop rows that fail, counting how many were dropped. Do not trust the
   site's filters to have applied.
8. Compute the summary, store the result, finish the run.

**Controls the run may use.** Only controls whose names match a short
allowlist: search, apply filters, next page, sort. It never uses a control
whose name matches save, contact, email, message, share, schedule, favourite,
print, export or download. That deny list is checked in `comps_har.py` in
addition to the gate's own rules, and downloads stay denied at the browser.

**Failure handling.**

- Any stop leaves the browser on the page where it stopped, so the person
  sees the same thing when they take over.
- No automatic retry of any kind. A person asks again.
- A worker failure or timeout ends the run as `failed` with the page count so
  far; the pages already loaded still count against the daily cap.
- A run still `running` after ten minutes is marked `failed` by the next
  request, with a counter.
- After `blocked`, that person's comps are refused until the cooldown ends.

## 10. Caps and rate limits

| Cap | Default | Enforced where |
|---|---|---|
| Page loads per request | 10 | a `Budget` object the source must draw from before each load; the eleventh draw raises |
| Requests per person per day | 5 | counted from `comps_runs` before a run starts |
| Page loads per person per day | 40 | summed from `comps_runs.page_loads` |
| Sold rows per request | 25, never above 100 | in `app/comps.py`, after the source returns, whatever the source did |
| Pause between page loads | random 4 to 9 seconds | in the source, before each load |
| Runs at once | one per person, one per tenant | a lock; a second request gets `busy` |
| Cooldown after a block | 24 hours | from the latest `blocked` run |

- "Day" is the tenant's local day, using the timezone the app already uses
  for dates. Find that helper rather than adding a second notion of local
  time.
- The run rows are the counter. There is no separate counter that could
  disagree with them.
- A run counts against the daily caps from the moment it starts, however it
  ends.
- Health counters, each bumped at the point of refusal: `comps_started`,
  `comps_done`, `comps_needs_person`, `comps_blocked`, `comps_unreadable`,
  `comps_refused_<layer>`, `comps_refused_daily_cap`, `comps_refused_busy`,
  `comps_refused_cooldown`, `comps_refused_channel`, `comps_rows_dropped`.

## 11. Security

### 11.1 One person's browser stays that person's

- Every route takes the person from the signed-in session
  (`browser._person(sess)`), never from a parameter. The break-glass operator
  has no browser and no comps.
- Comps runs under the requesting person's profile only. There is no code
  path that names another person's profile.
- The owner cannot view a member's browser, read a member's comps run, or
  decide a member's browser approvals.
- **A new risk that headed mode creates, and its required fix.** Every
  person's profile in a tenant is owned by the same unix user. Today that is
  contained because the agent can only open `http` and `https` addresses and a
  person can only click inside a page. A live view of a real browser window
  gives the person an address bar. Without a fix, a team member could type a
  `file://` address and read another member's profile directory, cookies
  included. Required, all three:
  1. Block non-web addresses before they load, with Chromium's managed policy
     (`URLBlocklist` for `file://`, `chrome://`, `view-source:` and the like).
     The policy directory for this Chromium build needs confirming on the box.
  2. The worker watches every tab; if a top-level address is not `http`,
     `https` or `about:blank` it navigates away at once, logs, and bumps a
     counter. This is the announced backstop, not the main defence.
  3. A test: with person A's profile present, person B's browser cannot
     display `file:///<A's profile>` or `file:///etc/hostname`.
  If the test cannot be made to pass, stop and report. The alternative is one
  systemd unit instance per person with only that person's profile mounted,
  which is a larger change to the worker and the units.

### 11.2 The Take over view

The WebSocket route accepts a connection only when all of these hold, checked
in this order, each refusal with its own counter:

1. The browser is on for the tenant and in headed mode.
2. The session cookie is a signed-in person, not the operator.
3. The `Origin` header matches the tenant's own host. WebSockets are not
   covered by same-origin rules, so this check is what stops another website
   from opening the view with the person's cookie.
4. The browser feature is on for the person.
5. That person has Take over active.
6. No other view is open for that person (the newer one is refused).

While open: the session is re-checked every 30 seconds and the connection is
closed if the person signed out, lost the feature, or handed back. It closes
after the Take over idle limit.

What is logged: open and close, the person key, byte counts, the reason for
closing. Never frame contents or keystrokes. The person's HAR password passes
through this channel as keystrokes; it is held only in transit, in the same
processes that already carry typed text for `/api/browser/input`.

The remote clipboard is not sent to the viewer. Pasting from the person's
machine into the remote browser is allowed, so a password manager works.

### 11.3 Secrets

- Passwords: the build stores none. Chromium's password saving is switched
  off in the profile. `browser_logins` keeps its existing encryption and is
  not used by comps; comps relies on the session the person created.
- Session cookies live in the person's profile directory (0700, the tenant's
  browser user). They are never read by the app, never returned by a worker
  request, and never logged. `forget_site` deletes them by site.
- Logs carry sites, never full addresses with query strings from a signed-in
  site, never page text, never sold prices. The worker's existing redaction
  stays in the path.
- Proxy credentials: section 4.7.
- Trestle credentials: not wired. When they are, by environment name only.

### 11.4 Sold prices

- Returned only in the requesting person's direct line; refused in shared
  channels unless `allow_in_channels` is set.
- Stored only in `comps_runs.result_json`, readable only by that person,
  cleared after `keep_days`.
- The agent's reply text in that direct line may quote prices and is stored
  with the thread like any reply, visible to that person.
- Not written to logs, counters, the ledger's `actions` rows, or the activity
  feed. The ledger records that a page on the site was read, not what it said.

### 11.5 What stays as it is

The egress proxy still reaches public addresses only. The unit keeps
`PrivateNetwork`, its own user, and no view of the tenant's files. Downloads
stay denied. Agent operations stay refused during Take over. Risky gestures
stay held one at a time.

## 12. Deploy runbook (for later, not part of the build)

Nothing here is run by the implementing agent. Each numbered step needs a
person's go-ahead at the time, and every restart is named.

**Once per server.**

1. `apt-get install x11vnc`. Confirm `xvfb`, `xauth` and `fonts-liberation`
   are present (they are today).
2. Run `scripts/install_browser_runtime.py` with its new option to copy the
   full Chromium from the Playwright cache to
   `/opt/jarvis/browser/chrome-1234/`, root-owned, read-only. It downloads
   nothing. Check with `chrome --version` and `ldd`.
3. Install the managed policy file from section 11.1 and confirm Chromium
   picks it up.
4. Deploy the new `front/main.py` and restart `jarvis-front.service`. **This
   restart interrupts every tenant for a moment.** Do it in a quiet window.
   Check: an ordinary page still loads for two tenants; a WebSocket to
   `/api/browser/view` without a session is refused; a WebSocket to any other
   path is refused.
5. Confirm the public tunnel passes WebSockets to the front.

**Per tenant** (test tenant first, then dev34 when Tega says so).

1. Add to the manifest: `browser_mode: headed`, `browser_limits.memory_max`
   at the measured value, and, only if comps is wanted, `comps: available`
   with any cap overrides.
2. Run the existing upgrade path in `provision_tenant.py` (`upgrade()`); check
   its options with `--help`. It copies the worker files, re-renders the units
   and reports unit problems. It restarts the tenant's app, gateway and
   browser units, which needs the restart go-ahead.
3. Check, as a signed-in person on that tenant: the browser opens a public
   page; `status` reports `headed`; Take over shows a live view; a second
   person cannot see the first person's view; handing back closes the view.
4. Comps stays off until the owner turns it on in Settings and acknowledges
   the notice, and turns it on for each person.
5. First real comps request: one person, signed in to HAR by their own hand
   through Take over, with someone watching the browser and the counters.

**Rolling back.** Set `browser_mode: headless` and run the upgrade again. Both
Chromium builds are the same revision, so a profile works in either. Setting
`comps: off` in the manifest disables comps regardless of the owner's switch.
The new tables are additive and can stay.

## 13. Implementation steps

One commit per step at least. Each step lists what proves it. Run the proof,
paste the output and the `git log --oneline -1` line. Do not start a step
until the one before it is proven.

**Before step 1.** Create a branch off `platform`. Decide whether to start
from the saved work in progress (section 17); if so, cherry-pick only the
pieces named there. Confirm the yes list in section 2 with Tega.

### A. Groundwork, no change in behaviour

**Step 1. Let the worker use helper modules.**
`browser_worker.py` runs under `python3 -I`, which drops the script's own
directory from the import path, and provisioning copies only two files.
Insert the worker's directory on `sys.path` explicitly, and add the new
helper files to `BROWSER_CODE_FILES`.
Proof: a test copies the files to a temp directory the way
`install_browser_code()` does and starts the worker with `/usr/bin/python3 -I`;
it reaches `ready`. Canary: with a helper file left out of the copy, the test
fails.

**Step 2. Launch arguments as a pure function.**
Create `app/browser_launch.py` with `chrome_args(profile, proxy_port, mode)`
and move the argument list out of `Browser.ensure()`.
Proof: for `headless` the list equals today's list exactly (compare against a
copy of the current list in the test). `docs/browser/verify_browser.py` still
passes unchanged.

### B. Headed browser

**Step 3. Headed launch in the worker.**
Xvfb per person with `-displayfd`; headed arguments; first-run preferences;
no device-metrics override in headed mode; Xvfb stops when its browser stops;
`status` reports the mode; missing Xvfb or binary fails loudly.
Proof, against the fixture site the verify script already serves: the user
agent has no `Headless`; the window is 1440 by 900; two people get two
different displays; a session-only cookie set by the fixture survives a close
and reopen; after closing there is no leftover Xvfb process; with the Xvfb
path pointed at nothing the launch fails with the counter bumped and no
headless browser started. Record `navigator.webdriver` and peak memory for
two browsers in the step's notes.

**Step 4. Runtime and provisioning.**
`install_browser_runtime.py` copies the full build; the manifest gains
`browser_mode`; `browser.service` gains the mode and binary paths;
`browser_unit_problems` reports a headed tenant whose binary or Xvfb is
missing.
Proof: render the unit for a headed and a headless manifest and compare to
expected text; an invalid `browser_mode` is refused; the headless render is
byte-identical to today's.

### C. Take over view

**Step 5. The worker's `view` request and the address guard.**
Start x11vnc for a display on a unix socket when asked; relay bytes; stop it
on hand-back. Add the watcher that refuses non-web addresses, and the managed
policy file.
Proof: a test client receives the RFB greeting through the worker for its own
person; no TCP listener exists in the unit; the `file://` test in section
11.1 passes. Where x11vnc is not installed, the relay is tested against a
small fake server in the test and the step is reported as partly proven, not
proven.

**Step 6. The app's WebSocket route.**
`/api/browser/view` with the six checks in section 11.2.
Proof, one check per refusal plus the presence case: no session; operator
session; wrong `Origin`; feature off; Take over not active; second viewer;
and a person with Take over active gets their own stream. Scoping: with
members A and B both running browsers, B's connection reaches B's display and
a `person=A` parameter makes no difference. Each refusal's counter moves by
exactly one.

**Step 7. The front forwards that one path.**
Proof: `docs/front/verify_front.mjs` gains checks that the path is relayed to
the right tenant, any other WebSocket path is refused, an unknown host is
refused, and ordinary HTTP forwarding is unchanged.

**Step 8. The live view in the app.**
Add `@novnc/novnc`; `BrowserWindow.tsx` shows it during Take over in headed
mode and the still image otherwise.
Proof: the frontend builds; a screenshot of the live view on a local
instance; the headless tenant path still shows the still image.

### D. Connected sites and the read allowance

**Step 9. Connected sites.**
`browser_sites` table; routes to list, confirm "I'm signed in" on hand-back,
and sign out; worker `forget_site`; the Settings section.
Proof: the list shows no username, password or cookie field (assert on the
response keys); sign-out removes the fixture site's cookie from the profile
and the row; one person's list never contains another's rows.

**Step 10. The read allowance.**
Richer `describe`; `read_only_gesture`; `browser_read` approvals with both
scopes; the `browser.read` grant; `revoke_grant`; the Settings control.
Proof on the fixture site: with an allowance, following a link and submitting
a search filter run with no new approval and write ledger rows carrying the
grant id; typing into a non-search form and clicking a risky button are still
held. Canary: an `always` read grant alone does not satisfy `_active_grant()`.
After revoking, the same link click asks again.

### E. Comps

**Step 11. Switches and the notice.**
`comps` in `FEATURES`, off by default for everyone including the owner;
`feature_on(None, "comps")` is false; `comps_settings` and `comps_ack`; owner
routes to read the notice, acknowledge and switch on, and switch off; the
Settings notice and switch; the Team chip.
Proof: a fresh tenant has comps off at every layer; a member or admin cannot
acknowledge or switch the tenant on; switching on without acknowledging is
refused; acknowledging stores who and when; a new notice version turns comps
off again until acknowledged; `docs/team/verify_team.py` updated and passing.

**Step 12. Shapes, summary, caps, runs.**
`comps_sources.py` with the shapes and the Trestle stub; `comps_runs`; the
`Budget`; the cap checks; the summary; the re-check filter; retention.
Proof with a fake source: the tenth page load is allowed and the eleventh is
refused; the fifth request of the day is allowed and the sixth is refused, and
allowed again on the next local day; 100 rows come back from a source that
offers 150, with `truncated` true; the re-check drops a row outside the date
range and counts it; the summary matches hand-worked numbers; fewer than three
rows gives no suggested range; `trestle` selected gives `source_unavailable`.

**Step 13. Reading pages.**
The worker's `tables` request; the fixtures; `classify` and the parsers.
Proof: every fixture page gets the expected class; the table and the card
fixtures parse to the same rows; the unknown page gives `unreadable` and
never an empty result; the "no results" page gives zero rows.

**Step 14. The run and the tool.**
`HarBrowserSource` flow through `browser.gate()`; pauses; stops and handoff;
the broker endpoint; `tools.json`; `run_action.py`; `agent_status.py`.
Proof on the fixture site standing in for the listing site: a full run
returns the expected rows and writes one ledger row per page load; a sign-in
wall stops the run, marks the site `needs_signin`, asks the person, and loads
no further page; a block starts the cooldown; a request in a channel is
refused; a person with comps off does not have the tools in their allowlist;
a second run while one is going gets `busy`; pauses are honoured (use a short
test range and assert the elapsed time).

**Step 15. The comps card.**
The stream event, `messages.actions`, `GET /api/comps/runs/{id}`,
`CompsCard.tsx`.
Proof: the card survives a reload; another member, the owner and the operator
each get 404 for someone else's run; a cleared run shows "these results have
expired" rather than an empty table.

### F. The rest

**Step 16. Outbound proxy.**
Proof with a local stand-in upstream: traffic goes through it when set;
nothing changes when unset; an unreachable upstream fails with its counter
and no direct connection; credentials do not appear in the logs.

**Step 17. Deletion, documents, full run.**
Tenant deletion and person removal cover the new tables, profiles and grants;
`docs/SPEC.md` updated; the PR description carries section 16 in plain words.
Run every suite in section 14 and paste the output.

## 14. Test plan

**Where tests run.** On a throwaway local instance, the way
`docs/browser/verify_browser.py` already works (its own temp data, its own
fixture site, `JARVIS_BROWSER_TEST=1`). Never against 8700, dev34 or any
running tenant. Testco's running build is not changed without approval.

**Fixtures.** Made-up pages only. No page fetched from HAR, by anyone, is
committed unsanitised.

**New checks**, each named in the step that adds it. Every refusal check has
a matching check that the allowed case works, and every "nothing happened"
check asserts a counter or row count before and after.

| Area | Script |
|---|---|
| Headed launch, profile persistence, view relay, sites, allowance | `docs/browser/verify_browser.py` |
| Take over scoping (a member cannot view another's browser) | `docs/browser/verify_browser.py` |
| Comps parsing, caps, switches, direct-line rule, run scoping | `docs/comps/verify_comps.py` |
| Front WebSocket forwarding | `docs/front/verify_front.mjs` |
| Feature list | `docs/team/verify_team.py` |
| Deletion | `docs/accounts/verify_tenant_delete.py` |

**Existing suites to run at the end**, with their output pasted:

- leads: `docs/w1-shots/verify_leads_store.py`, `verify_leads_contract.py`,
  `verify_leads_export.py` (108 checks at the last merge)
- deletion: `docs/accounts/verify_tenant_delete.py`
- calendar dates: `docs/imessage/verify_calendar_dates.py`
- sentinel: `docs/sentinel/verify_sentinel_gate.py`,
  `verify_sentinel_broker.py`
- Swift: `swift test` in `ios/JarvisKit`

**What tests cannot show.** That the reader works on the real HAR pages, that
HAR's own bot checks tolerate the browser, and how long a HAR session lasts.
Those are only learned in the supervised first run after deploy.

## 15. Open risks and decisions

**Decisions for Tega before the build starts.**

1. Confirm the yes list in section 2 (noVNC package, `x11vnc`, the full
   Chromium, four SQLite tables, the change to the shared front).
2. Which surface comps should read: HAR.com's member pages or Matrix. The plan
   assumes one site per tenant, default `har.com`. Ask Spencer which page he
   uses for comps today. Matrix searches are form posts, which sit closer to
   the "no form submits beyond search filters" line.
3. Whether the comps rows go to the model. The plan sends them so the agent
   can answer follow-ups, which means sold prices are part of the text sent to
   the model provider for that turn. The alternative is to send the model the
   summary only and show the rows in the card alone.
4. Whether to pass the flag that clears `navigator.webdriver`, if step 3 finds
   it set.
5. Who saves and sanitises the real pages (section 9), and when.

**Risks.**

| Risk | What the plan does about it |
|---|---|
| The real pages do not match the made-up fixtures | One mapping table to edit; unknown pages stop as `unreadable`; supervised first run |
| HAR blocks or challenges the browser anyway | Stop, hand to the person, cooldown; no retry, no solving |
| An account is flagged or fined | Low caps, off by default, owner's acknowledgement on record; the risk itself is accepted by the team, not removed |
| Cross-person file access through the address bar | Section 11.1; the build stops if its test cannot pass |
| Shared front change breaks other tenants | One path only, verify script, quiet-window restart in the runbook |
| Memory: headed browsers are heavier | Measured in step 3; limit raised from the measurement |
| noVNC does not build under Vite 5 at the newest version | Pin the newest version that builds; report the version chosen |
| x11vnc cannot do unix-socket-only | Fall back to TigerVNC's `Xvnc` (one process instead of Xvfb plus x11vnc); a different package to approve |
| `--no-sandbox` shows a warning bar in the live view | Cosmetic; say so in the PR; do not hide it with test-only flags without asking |
| The read classifier calls an acting gesture "reading" | Default-deny, risky check first, POST forms excluded, tests for both directions |
| A session-only HAR cookie is lost when the idle browser closes | Restore-session preference, tested in step 3 |
| All profiles in a tenant share one unix user | Existing design; a browser exploit could cross people. Per-person units are the stronger fix and are out of scope here |
| Distance is not shown by the site | The column is left empty; no geocoding service is added |

## 16. HAR terms and MLS access: what Tega should confirm

In plain words, for the PR description.

This feature signs nobody in. Each agent signs in to HAR themselves, in their
own browser session on the server, and Jarvis then reads sold listings through
that session on the agent's request.

The brief records that the team has been told of, and accepts, these points.
They are repeated here as the brief stated them; HAR's current documents were
not re-read during planning.

- HAR's terms of use ban scraping.
- MLS Rule 6.1 bans sharing MLS passwords, including with technology vendors,
  with fines of $500, then $1,000, then loss of access.
- Rule 12 covers third-party access; Rule 12.2 limits sold listings to 100 per
  request.
- Rule 13.2 concerns who may be shown sold prices.

How the build responds: no password is stored or typed by Jarvis; each
session is one agent's own; requests are few and slow, capped per request and
per day; no more than 100 sold rows per request; nothing is saved, sent or
submitted on the site beyond search filters; sold prices go only to the agent
who asked; the feature is off until the owner reads the notice and turns it
on. None of that makes automated access permitted if HAR's terms forbid it.
It lowers the volume and keeps each agent inside their own login.

What to confirm with HAR and the brokerage:

1. Whether an agent's own signed-in session being driven by software on a
   server counts as sharing access under Rule 6.1, even though no password is
   handed over.
2. Whether HAR offers an approved route for this use, and whether the
   brokerage can get a licensed feed (Trestle). The stub is there so the
   switch is a setting, not a rebuild.
3. Whether sold prices may be stored for 30 days, and whether that period
   should be shorter.
4. Who at the brokerage acknowledges the notice, since their name is what is
   recorded.

## 17. Work in progress that already exists

Branch `cursor/real-browser-comps-3d4d`, commit `8c75839`, pushed. It was
written before the switch to planning. It is not tested, not wired into the
app, and must not be deployed.

| File | What it is | Use it? |
|---|---|---|
| `app/browser_launch.py` | Launch arguments, display number, profile path | Keep the argument builder. Drop the hard-coded user agent and the display number derived from the person's name |
| `app/browser_policy.py` | `view_allowed`, `read_only_gesture` by wording | Keep as a starting point; step 10 needs the element facts, not wording alone |
| `app/comps.py` | Row parser for a made-up line format, stop detection, median | Keep the stop wording and the median as a start. Its caps are older and lower than this plan's, its range is a flat 5 percent, and its HAR search address is a guess that must not be used |
| `app/browser_worker.py` | Adds an import of `browser_launch` and nothing else | Do not use as is: the import fails under `python3 -I`, which is how the unit starts the worker. Step 1 fixes the cause |
