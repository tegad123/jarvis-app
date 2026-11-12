# Plan: a real browser for Ask Jarvis, and HAR MLS search

Status: approved (PR #4), being implemented on `cursor/browser-har-comps-build-3d4d`.
Nothing is deployed or switched on. Written 2026-11-12 against `platform` at `e017f36`.

**Decisions recorded 2026-11-12 (Tega).** These override anything below that
says otherwise.

1. Approved under `AGENTS.md` rule 6: `@novnc/novnc`, `x11vnc`, the full
   Chromium from the Playwright cache, the four SQLite tables, and the shared
   front WebSocket change (code and runbook only; not applied to the live front).
2. Comps reads **HAR MLS Matrix**, not HAR.com member pages.
3. The sold rows go to the model so Ask can answer follow-ups; the card and the
   rows are still shown only to the person who asked.
4. The Chromium automation marker is hidden (`--disable-blink-features=AutomationControlled`).
   Still no captcha solving and no 2FA bypass.
5. Tega saves and sanitises the real Matrix pages after the PR; the build uses
   made-up fixtures and leaves a capture guide (`docs/mls/FIXTURE-CAPTURE.md`).
6. **No licensed-feed source.** The Trestle stub, its settings and the
   pluggable data-source layer are dropped. The search reads Matrix through
   the asking person's own browser session only, in one module (`app/mls_matrix.py`).
7. **No comps switches and no notice acknowledgement.** The tenant switch,
   the per-person `comps` feature and the owner's HAR-notice acknowledgement
   are dropped. The search tool is offered to any person whenever the browser
   itself is available to them (tenant browser on, headed mode, that person's
   `browser` feature on) and the Matrix start page is configured.
8. **General MLS search, not only sold comps.** The tool takes a search in
   plain words ("active listings in the Heights under $600k", "what sold on
   my street this year") as filters: status (active, pending, sold), area or
   neighbourhood, zip, address and radius, price, beds, baths, sqft, property
   type, year built and a date range. Ask maps the request to those filters,
   the person's own Matrix session runs it, and the person gets a table and a
   short summary. Sold comps for a subject property are one case of this.
   Kept from before: own login only, read-only, stop at sign-in, captcha or
   2FA and hand off through Take over, the page and daily caps and pauses,
   results shown only to the person who asked, at most 100 rows per search.

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
5. **MLS search.** An Ask tool that runs a property search described in
   plain words (status, area, zip, price, beds, baths, sqft, type, dates)
   through the requesting person's own HAR MLS Matrix session, read-only and
   low-volume, and returns a table and a short summary to that person only.
   Sold comps for an address are one kind of search.
6. **One thing built but left off.** An optional outbound proxy setting for
   the browser's egress.

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
  the comps storage by name, which covers the intent; Tega confirmed the list
  on 2026-11-12 (decision 1), and decision 7 removed two of the four tables.

  | Needs a yes | What |
  |---|---|
  | Frontend dependency | `@novnc/novnc`, pinned to an exact version |
  | Server package | `x11vnc` (apt). `xvfb` is already installed |
  | Browser runtime | the full Chromium build, copied from the Playwright cache already on the box |
  | SQLite tables | `browser_sites`, `mls_runs` (section 6) |
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
site and confirms "I'm signed in". An MLS search marks a row `needs_signin` when it
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

### 4.5 MLS search

```
Ask turn ─ search_mls tool ─ run_action.py ─ POST /api/broker/mls
                                                   │
                         app/mls.py: availability, direct-line check, caps
                                                   │ starts a run (mls_runs row)
                         app/mls_matrix.py: the Matrix reader
                                      every page load goes through browser.gate()
                                                   │
                         result stored on the run; card drawn in the chat
```

- A search is a **run**: one row in `mls_runs` holding who asked, the
  filters, how many pages were loaded, how it ended, and the result.
- Runs take longer than one tool call allows (ten page loads with pauses).
  The run executes in the app in the background. `search_mls` waits up to 100
  seconds for it; if it is still going, the tool returns the run id and the
  agent calls `mls_result` to wait again.
- Every page the run loads goes through `browser.gate()` as that person, so
  each load is a ledger row and the Take over rule, the egress rule and the
  person's `browser` feature all apply without being re-implemented.
- The run stops at the first sign-in wall, human check, 2FA prompt or block.
  It marks the site `needs_signin`, asks the person to take over (the existing
  `_ask_person` path), and returns `needs_person`. It never retries on its
  own.
- The result is shown as a card the app draws, read from the stored run, not
  as text the model wrote. The model also receives the rows so the person can
  ask follow-up questions (decision 3).

### 4.6 Shapes

There is one source, Matrix through the person's own browser, so there is no
source interface and no source setting. `app/mls.py` holds three plain
dataclasses; the reader returns them and the summary consumes them:

```python
STATUSES = ("active", "pending", "sold")
PROPERTY_TYPES = ("single_family", "townhouse_condo", "multi_family", "lot_land")

@dataclass(frozen=True)
class Query:
    statuses: Tuple[str, ...]             # one or more of STATUSES; default ("active",)
    area: Optional[str]                   # neighbourhood, subdivision or market area, as the person said it
    zip_codes: Tuple[str, ...]            # up to 10 five-digit codes
    address: Optional[str]                # a street address: the centre for radius_miles, the subject for comps
    radius_miles: Optional[float]         # only with address
    price_min: Optional[int]; price_max: Optional[int]
    beds_min: Optional[int]; beds_max: Optional[int]
    baths_min: Optional[float]; baths_max: Optional[float]
    sqft_min: Optional[int]; sqft_max: Optional[int]
    year_built_min: Optional[int]; year_built_max: Optional[int]
    property_types: Tuple[str, ...]       # subset of PROPERTY_TYPES; empty = any
    date_from: Optional[str]; date_to: Optional[str]   # YYYY-MM-DD; see "Dates" below
    subject_sqft: Optional[int]           # comps only: the subject's size, for a suggested range
    max_results: int                      # never above 100

@dataclass(frozen=True)
class Listing:
    address: str
    status: str                           # one of STATUSES, as mapped from the page
    list_price: Optional[int]
    sold_price: Optional[int]
    list_date: Optional[str]; pending_date: Optional[str]; sold_date: Optional[str]
    beds: Optional[int]; baths: Optional[float]
    sqft: Optional[int]
    year_built: Optional[int]
    property_type: Optional[str]
    area: Optional[str]                   # subdivision or market area as the page shows it
    zip_code: Optional[str]
    days_on_market: Optional[int]
    mls_number: Optional[str]
    distance_miles: Optional[float]       # None when the page does not give it

@dataclass
class Result:
    state: str        # done | needs_person | blocked | unreadable | ambiguous | failed
    listings: List[Listing]
    page_loads: int
    truncated: bool
    detail: str       # plain words for the person; never page content
    choices: List[str]            # for "ambiguous"
```

- A query needs at least one place: `area`, `zip_codes` or `address`. A
  search of the whole MLS is refused as `bad_request`.
- **Dates.** `date_from`/`date_to` apply to the sold date for sold listings,
  the pending date for pending ones and the list date for active ones. Ask
  turns "the last six months" into dates itself; the tool takes dates only.
- Listing agent names, phone numbers, emails, showing instructions and
  remarks are not read into `Listing` at all.
- `$ per sqft`, the filters and the summary are computed or re-checked in
  `app/mls.py` after the reader returns; the site's own filters are not
  trusted (section 9, flow step 7).

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
| `app/mls.py` (new) | Shapes, availability check, caps, runs, summary, broker endpoint, person routes |
| `app/mls_limits.py` (new) | Settings defaults and hard limits; shared with `provision_tenant.py` |
| `app/mls_matrix.py` (new) | The Matrix reader: page classifier, parsers, page flow |
| `app/agent_status.py` | `MLS_TOOLS`; status line for MLS search; chat card |
| `app/chat.py`, `app/threads.py` | Emit and persist the MLS card reference |
| `app/tenant_delete.py` | Count the new tables; remove browser profiles |
| `app/main.py` | Register the MLS routers |
| `front/main.py` | WebSocket forwarding for `/api/browser/view` |
| `scripts/hermes_plugin/jarvis/tools.json` | `search_mls`, `mls_result` |
| `scripts/hermes_plugin/jarvis/run_action.py` | The two tool functions and their plain-words replies |
| `scripts/provision_tenant.py` | `BROWSER_CODE_FILES`; `browser_mode`; `mls`; `browser_proxy`; headed binary path; unit checks |
| `scripts/install_browser_runtime.py` | Copy the full Chromium as well as the headless shell |
| `scripts/templates/browser.service` | Mode, Xvfb and x11vnc paths, memory |
| `scripts/templates/egress.service` | Upstream proxy settings and credential |
| `frontend/package.json` | `@novnc/novnc` |
| `frontend/src/components/BrowserWindow.tsx` | Live view during Take over |
| `frontend/src/components/MlsCard.tsx` (new) | The search results table and summary |
| `frontend/src/screens/Settings.tsx` | Connected sites |
| `frontend/src/lib/api.ts`, `lib/types.ts` | New calls and types |
| `docs/browser/verify_browser.py` | Headed, view, sites, allowance checks |
| `docs/mls/verify_mls.py` (new) | MLS search checks |
| `docs/mls/fixtures/` (new) | Made-up Matrix-like pages |
| `docs/mls/FIXTURE-CAPTURE.md`, `docs/mls/sanitize_fixture.py` (new) | How Tega saves and sanitises real Matrix pages |
| `docs/accounts/verify_tenant_delete.py` | New tables and profile removal |
| `docs/front/verify_front.mjs` | WebSocket forwarding |
| `docs/SPEC.md` | Section 8.6 and "Not in v1" |

There is no new person feature: the search follows the existing `browser`
feature, so `accounts.FEATURES`, the Team screen and `write_team_file()` are
unchanged.

## 6. Database changes

**Postgres: none.** The read allowance uses the existing `grants` table
(`scope = 'always'` and `revoked_at` already exist; `connector` is free text).
`SCHEMA_HEAD` stays `"0001"`. The new approval type `browser_read` goes in the
approval's action JSON, as `browser_task` does today; the `approvals.kind`
column stays `action`, which is one of the two values its check allows.

**SQLite, accounts database.** Two additive tables (four were approved; the
comps switch and notice tables were dropped with decision 7), created with
`CREATE TABLE IF NOT EXISTS` in the same lazy way `browser_logins` is. No
existing table changes.

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

-- One MLS search. Also the counter for the daily caps.
CREATE TABLE IF NOT EXISTS mls_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_key    TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    query_json    TEXT NOT NULL,
    state         TEXT NOT NULL,      -- running | done | needs_person | blocked | unreadable | ambiguous | failed
    stop_kind     TEXT,               -- login | captcha | two_factor | allow_reading | taken_over | blocked | browser_unavailable
    page_loads    INTEGER NOT NULL DEFAULT 0,
    result_count  INTEGER NOT NULL DEFAULT 0,
    result_json   TEXT,               -- rows and summary; cleared after mls.keep_days
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS mls_runs_person ON mls_runs (person_key, started_at);
```

- `person_features` is unchanged.
- `messages` is unchanged. The MLS card is stored in the existing
  `messages.actions` JSON array as the string `mls:<run id>`.
- `result_json` holds listing prices. It is cleared (set to NULL, the row
  kept) once older than `mls.keep_days`. The clearing runs when a new run
  starts and logs how many rows it cleared.
- `tenant_delete.py` deletes SQLite tables by listing them, so the data goes
  with the tenant; its verify script counts tables and must be updated.
- The profile directories on disk are a separate matter. Operator teardown in
  `provision_tenant.py` removes `/var/lib/jarvis-browser/<slug>`. In-app
  deletion (`tenant_delete.py`) only clears the in-memory "needs you" list
  and does not touch profiles, and nothing in `accounts.py` or `team.py`
  mentions the browser when a person is removed. Step 17 closes both, so a
  signed-in HAR session does not outlive a deleted tenant or a removed
  person.

## 7. Settings and availability

There is no MLS switch (decision 7). The search is available to a person
exactly when the browser is, plus the one piece of configuration it cannot
run without. Each refusal names the line that failed and bumps
`mls_refused_<why>`.

| # | Condition | Where it lives | Who sets it | Default |
|---|---|---|---|---|
| 1 | Browser on for the tenant | manifest `browser: enabled` | operator | off |
| 2 | Browser mode | manifest `browser_mode: headed`, read as `app.browser.mode` | operator | `headless` |
| 3 | Matrix start page configured | manifest `mls: start_url`, read as `app.mls.start_url` | operator | none |
| 4 | A signed-in person | the session | | |
| 5 | Browser on for the person | `person_features` `browser` | owner or admin | on |

Line 3 is configuration, not a switch: the Matrix hostname and search page
are not known yet (section 9) and there is no guessed default. The search
needs headed mode because a headless browser gives the person no way to
complete a sign-in challenge.

Other settings, all read through `tenant_config` under `app.mls.*` and
rendered from a validated manifest block the way `lead_followup` is. Defaults
and limits live in `app/mls_limits.py`, which the provisioner imports too:

| Setting | Default | Hard limit in code |
|---|---|---|
| `site` | `harmls.com` | one site per tenant (the Matrix host's registrable name) |
| `start_url` | none: the search is unavailable until set | an `https` address on `site`, the Matrix search page Tega confirms |
| `max_page_loads_per_request` | 10 | 10 |
| `max_requests_per_person_per_day` | 5 | 20 |
| `max_page_loads_per_person_per_day` | 40 | 100 |
| `max_results` | 25 | 100 |
| `pause_seconds` | `[4, 9]` (random between) | 3 to 60 |
| `cooldown_hours_after_block` | 24 | 1 to 336 |
| `keep_days` | 30 | maximum 90 |
| (shared channels) | never | not a setting: results go only to the person's direct line |

A setting can lower a hard limit and never raise it. A value outside the
limit is refused when the manifest is validated, not clamped silently, and an
invalid `app.mls` read by the app makes the search unavailable with
`mls_config_invalid` rather than running with guessed values. A `start_url`
with a headless or disabled browser is refused at provisioning.

Browser settings:

| Setting | Default |
|---|---|
| `browser_mode` | `headless` |
| `browser_limits.memory_max` | `1G` headless; set from measurement for headed |
| `browser_proxy` | absent (off) |

## 8. The Ask tool contract

Two tools in `tools.json`, both with `"needs": ["browser"]`. They are offered
to the agent only when every line in section 7 holds for the person;
otherwise they are absent from the allowlist and the status block says, in
one line, why MLS search is not available.

**`search_mls`.** Ask maps the person's words to these filters. The tool
description tells the model to pass only what the person said or clearly
implied, never to invent a filter, and to ask the person when the place is
missing.

| Parameter | Type | Default | Range |
|---|---|---|---|
| `statuses` | list of `active`, `pending`, `sold` | `["active"]` | 1 to 3, no repeats |
| `area` | string | none | 2 to 80 characters: a neighbourhood, subdivision or market area ("the Heights") |
| `zip_codes` | list of strings | none | up to 10 five-digit codes |
| `address` | string | none | 5 to 120 characters |
| `radius_miles` | number | none; 1.0 when `address` is given | 0.25 to 5; only with `address` |
| `price_min`, `price_max` | integer | none | 0 to 100,000,000 |
| `beds_min`, `beds_max` | integer | none | 0 to 12 |
| `baths_min`, `baths_max` | number | none | 0 to 12 |
| `sqft_min`, `sqft_max` | integer | none | 200 to 20000 |
| `year_built_min`, `year_built_max` | integer | none | 1800 to next year |
| `property_types` | list of `single_family`, `townhouse_condo`, `multi_family`, `lot_land` | any | no repeats |
| `date_from`, `date_to` | `YYYY-MM-DD` | none | not in the future; at most 36 months back |
| `subject_sqft` | integer | none | 200 to 20000; comps only |
| `max_results` | integer | 25 | 1 to 100 |

At least one of `area`, `zip_codes` or `address` is required. Every min must
not exceed its max.

Examples of the mapping (in the tool description, for the model):

- "active listings in the Heights" → `statuses: [active], area: "Heights"`.
- "3-bed houses under 600k in 77008 that went pending this month" →
  `statuses: [pending], zip_codes: [77008], beds_min: 3,
  property_types: [single_family], price_max: 600000, date_from: <first of month>`.
- "comps for 123 Main St, 2,000 sqft, last 6 months" → `statuses: [sold],
  address: "123 Main St", radius_miles: 1, date_from: <today − 6 months>,
  subject_sqft: 2000`.

**`mls_result`**: `run_id` (integer, required). Waits up to 100 seconds for
a run that `search_mls` reported as still running. Only the person who
started the run can read it.

**Broker endpoint.** `POST /api/broker/mls`, body
`{"ref": <turn ref>, "op": "search" | "result", ...parameters}`. Same shape as
`lead_tools.py`: `broker._authorize`, the person from `broker._turn_person`,
then the checks in this order: availability (section 7), direct line,
parameters, caps.

**Success.**

```json
{
  "ok": true, "state": "done", "run_id": 41,
  "query": {"statuses": ["active"], "area": "Heights"},
  "count": 18, "truncated": false, "page_loads": 3,
  "dropped": 1, "unverified": 0,
  "summary": {
    "by_status": {"active": 18},
    "median_price": 549000, "price_low": 465000, "price_high": 689000,
    "median_price_per_sqft": 298,
    "median_days_on_market": 24,
    "note": "18 active listings. Prices are list prices."
  },
  "listings": [
    {"address": "...", "status": "active", "list_price": 549000, "sold_price": null,
     "list_date": "2026-10-02", "sold_date": null, "price_per_sqft": 298,
     "beds": 3, "baths": 2.0, "sqft": 1842, "year_built": 1925,
     "property_type": "single_family", "area": "Heights", "zip_code": "77008",
     "days_on_market": 41, "mls_number": "...", "distance_miles": null}
  ]
}
```

Still running: `{"ok": true, "state": "running", "run_id": 41}`.

**Summary rules.** The price of a row is its sold price when sold and its
list price otherwise; the note says which. `price_per_sqft` is that price
divided by sqft, rounded. Medians use only rows that have the field; low and
high are the 25th and 75th percentiles. When every row is sold, `address` and
`subject_sqft` are given and at least three rows have a price per sqft, the
summary adds `suggested_low` and `suggested_high` (the percentiles times
`subject_sqft`) and the note says "Not an appraisal". With fewer than three,
it says there are too few sales to suggest a range. `truncated` is true when
the page had more rows than `max_results`.

**Refusals.** Each has a fixed plain-words line in `run_action.py` (the
pattern `LEADS_SAY` uses), so the agent tells the person the right thing.

| HTTP | `error` | Meaning |
|---|---|---|
| 403 | `unavailable` + `why` | Which line of section 7 failed |
| 403 | `direct_only` | Asked in a shared channel; results are shown in the person's own direct line |
| 400 | `bad_request` + `detail` | A parameter is out of range, or no place was given |
| 409 | `needs_person` + `kind` | `login`, `captcha` or `two_factor`: the person must take over; `allow_reading`: the person has not allowed reading this site yet (one read card is filed); `taken_over`: the person took the browser mid-run |
| 409 | `taken_over` | The person is using the browser themselves; no run is started |
| 409 | `blocked` | The site answered 403/429 or "unusual activity"; the cooldown starts |
| 409 | `busy` | That person (or the tenant) already has a run going |
| 409 | `cooling_off` + `until` | A block was met recently |
| 429 | `daily_cap` + `resets_at` | Daily cap reached |
| 409 | `ambiguous` + `choices` | The area or address matches more than one thing on the site |
| 502 | `unreadable` | The page was not one the reader recognises; nothing was guessed |
| 502 | `failed` (`kind` `browser_unavailable`) | The browser could not be reached |

**Where the result appears.** The direct-line check is: the turn's thread has
`direct_for` equal to the requesting person. When the run ends `done`, the
turn requests the chat card (the mechanism `connect_google` uses through
`actor_refs.request_action`), the stream emits
`{"type": "mls", "run_id": 41}`, and the reply row stores `mls:41` in
`messages.actions`. The frontend fetches `GET /api/mls/runs/41` and draws
the table. That route requires a signed-in person, refuses the break-glass
operator, and returns 404 unless the run belongs to the caller.

## 9. Matrix page flow and how pages are read

**What is not known.** No HAR or Matrix page was opened during planning, and
none may be opened by an agent during the build. The page structure, the
addresses of the search pages, and which surface shows sold prices to a
signed-in agent are unknown. The saved work in progress contains a HAR search
address that is a guess; do not use it.

**How the build proceeds without that knowledge.**

1. The implementing run writes its own made-up pages that look like a listing
   site (`docs/mls/fixtures/`): a search form, a results table, a results
   page of cards, a "no results" page, a sign-in wall, a human-check page, a
   2FA page, a "too many requests" page, and a page of an unknown shape. All
   names, addresses and prices are invented and use `.example` where an
   address is needed.
2. The reader is built and tested against those.
3. Tega, signed in on their own machine, saves the real
   pages by hand and passes them through a sanitising script that replaces
   every address, price, name, MLS number, phone, email and image before the
   file is committed (`docs/mls/FIXTURE-CAPTURE.md`, `docs/mls/sanitize_fixture.py`).
   That is a human step after the PR.
4. The first run against the real site happens on a test tenant with a person
   watching, after deploy, with the caps set to their lowest. That is also
   after the PR.

**Reading a page.** All parsing is Python in `app/mls_matrix.py`, so it is
testable without a browser. The worker gets one new request, `tables`, which
runs a fixed script shipped in the worker (never one supplied by the model or
by the app at run time) and returns, capped in size:

- each HTML table as header cells plus rows of cell text;
- repeated cards as lists of label and value text;
- JSON data blocks embedded in the page (for example JSON-LD).

The parser tries, in order: an embedded data block; a table whose headers map
to known column names; labelled cards. Columns are found by header text
through one mapping table (for example "Sold Price", "Close Price" →
`sold_price`; "Status" values "A", "Active" → `active`), never by CSS class names, which change often. The mapping
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

**Ticking status and type boxes.** The read allowance (step 10) treats a
checkbox as reading only when it is in a form posting to the same site and
its label is exactly a status or property-type name (`FILTER_TOGGLE` in
`app/browser_policy.py`: Active, Pending, Sold, Single-Family,
Townhouse/Condo and the like). "Remember my criteria", "Email me new
listings" and any other box stay task-approval clicks, so a search can never
save a search or start an alert.

`empty` and `unknown` are deliberately different. A page the reader does not
understand must never be reported as "no sales found".

**The flow.**

1. Check availability, direct line, parameters and caps; create the run row.
2. Open `start_url`. If the site lets a search be expressed in the address,
   use that. Otherwise fill the search filters with the read-allowance
   gestures: status, area, zip, address and radius, price, beds, baths, sqft,
   year built, property type, date range. A filter the reader cannot find on
   the page ends the run as `unreadable` naming the filter; it is never
   silently left out.
3. Classify. Stop on anything but `results` or `empty`.
4. If the area or address matches more than one thing, return `ambiguous`
   with the choices as the site shows them.
5. Read the rows. Page forward only while rows are needed and the page budget
   allows. Pause a random time within `pause_seconds` before every load.
6. Stop reading at `max_results` rows.
7. Re-check every row in the app against every filter (status, zip, price,
   beds, baths, sqft, year built, type, the date for its status, radius when
   the page gives distance) and drop rows that fail, counting them as
   `dropped`. A row missing a field a filter needs is kept and counted as
   `unverified`, and the card says so. Do not trust the site's filters to
   have applied.
8. Compute the summary, store the result, finish the run.

**Controls the run may use.** Only controls whose names match a short
allowlist: search, apply filters, next page, sort. It never uses a control
whose name matches save, contact, email, message, share, schedule, favourite,
print, export or download. That deny list is checked in `mls_matrix.py` in
addition to the gate's own rules, and downloads stay denied at the browser.

**Failure handling.**

- Any stop leaves the browser on the page where it stopped, so the person
  sees the same thing when they take over.
- No automatic retry of any kind. A person asks again.
- A worker failure or timeout ends the run as `failed` with the page count so
  far; the pages already loaded still count against the daily cap.
- A run still `running` after ten minutes is marked `failed` by the next
  request, with a counter.
- After `blocked`, that person's searches are refused until the cooldown ends.

## 10. Caps and rate limits

| Cap | Default | Enforced where |
|---|---|---|
| Page loads per request | 10 | a `Budget` object the reader must draw from before each load; the eleventh draw raises |
| Requests per person per day | 5 | counted from `mls_runs` before a run starts |
| Page loads per person per day | 40 | summed from `mls_runs.page_loads` |
| Rows per request | 25, never above 100 | in `app/mls.py`, after the reader returns, whatever the reader did |
| Pause between page loads | random 4 to 9 seconds | in the reader, before each load |
| Runs at once | one per person, one per tenant | a lock; a second request gets `busy` |
| Cooldown after a block | 24 hours | from the latest `blocked` run |

- "Day" is the tenant's local day, using the timezone the app already uses
  for dates. Find that helper rather than adding a second notion of local
  time.
- The run rows are the counter. There is no separate counter that could
  disagree with them.
- A run counts against the daily caps from the moment it starts, however it
  ends.
- Health counters, each bumped at the point of refusal: `mls_started`,
  `mls_done`, `mls_needs_person`, `mls_blocked`, `mls_unreadable`,
  `mls_refused_<why>`, `mls_refused_daily_cap`, `mls_refused_busy`,
  `mls_refused_cooldown`, `mls_refused_channel`, `mls_rows_dropped`,
  `mls_rows_unverified`.

## 11. Security

### 11.1 One person's browser stays that person's

- Every route takes the person from the signed-in session
  (`browser._person(sess)`), never from a parameter. The break-glass operator
  has no browser and no MLS search.
- A search runs under the requesting person's profile only. There is no code
  path that names another person's profile.
- The owner cannot view a member's browser, read a member's search run, or
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
  not used by the search, which relies on the session the person created.
- Session cookies live in the person's profile directory (0700, the tenant's
  browser user). They are never read by the app, never returned by a worker
  request, and never logged. `forget_site` deletes them by site.
- Logs carry sites, never full addresses with query strings from a signed-in
  site, never page text, never prices. The worker's existing redaction
  stays in the path.
- Proxy credentials: section 4.7.

### 11.4 Search results

- Returned only in the requesting person's direct line; refused in shared
  channels, with no setting to allow them.
- Stored only in `mls_runs.result_json`, readable only by that person,
  cleared after `keep_days`.
- Sent to the model for that person's turn so it can answer follow-ups
  (decision 3). The agent's reply text in that direct line may quote prices
  and is stored with the thread like any reply, visible to that person.
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
   at the measured value, and, only if MLS search is wanted, an `mls:` block
   with `start_url` (the Matrix search page Tega confirms) and any cap
   overrides.
2. Run the existing upgrade path in `provision_tenant.py` (`upgrade()`); check
   its options with `--help`. It copies the worker files, re-renders the units
   and reports unit problems. It restarts the tenant's app, gateway and
   browser units, which needs the restart go-ahead.
3. Check, as a signed-in person on that tenant: the browser opens a public
   page; `status` reports `headed`; Take over shows a live view; a second
   person cannot see the first person's view; handing back closes the view.
4. There is no MLS switch (decision 7): once `start_url` is set, every person
   with the browser feature on can run searches. To limit who can, switch the
   `browser` feature off for those people in Team before the upgrade.
5. First real search: one person, signed in to Matrix by their own hand
   through Take over, with someone watching the browser and the counters.

**Rolling back.** Set `browser_mode: headless` and run the upgrade again. Both
Chromium builds are the same revision, so a profile works in either. Removing
`mls.start_url` from the manifest (and upgrading) makes MLS search
unavailable for everyone. The new tables are additive and can stay.

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

### E. MLS search

**Step 11. Availability and settings.**
`app/mls_limits.py`; the manifest `mls:` block validated in
`provision_tenant.py` and rendered as `app.mls`, plus `app.browser.mode`;
`mls.available_for(person)` walking section 7 in order. No new person
feature and no tenant switch.
Proof: each line of section 7 off in turn gives its own `why` and bumps its
own counter, and all on gives available (presence check); an out-of-range
setting is refused by the provisioner and, read by the app, makes the search
unavailable with `mls_config_invalid`; `start_url` with a headless browser is
refused at provisioning; `docs/team/verify_team.py` still passes with the
feature list unchanged.

**Step 12. Shapes, summary, caps, runs.**
The shapes and parameter checks in `mls.py`; `mls_runs`; the `Budget`; the
cap checks; the summary; the re-check filter; retention.
Proof with a fake reader: the tenth page load is allowed and the eleventh is
refused; the fifth request of the day is allowed and the sixth is refused, and
allowed again on the next local day; 100 rows come back from a reader that
offers 150, with `truncated` true; the re-check drops a row with the wrong
status, one outside the price range and one outside the date range for its
status, and counts each, and keeps a row missing sqft as unverified; a query
with no place is refused; the summary matches hand-worked numbers for an
active search and for a sold-comps search; fewer than three sold rows gives
no suggested range; no `start_url` gives `unavailable`.

**Step 13. Reading pages.**
The worker's `tables` request; the fixtures (search form with every filter,
results table with mixed statuses, results cards, "no results", an
ambiguous-area picker, sign-in wall, human check, 2FA, "too many requests",
unknown page); `classify` and the parsers.
Proof: every fixture page gets the expected class; the table and the card
fixtures parse to the same rows; status words map to `active`, `pending`,
`sold`; the unknown page gives `unreadable` and never an empty result; the
"no results" page gives zero rows; agent names, phones and emails on the
fixture never reach a `Listing`.

**Step 14. The run and the tool.**
The Matrix reader's flow through `browser.gate()`; filling each filter with
read gestures; pauses; stops and handoff; the broker endpoint; `tools.json`;
`run_action.py`; `agent_status.py`.
Proof on the fixture site standing in for Matrix: an "active in the Heights"
search and a sold-comps search each return the expected rows and write one
ledger row per page load; a filter missing from the form ends `unreadable`
naming it; a sign-in wall stops the run, marks the site `needs_signin`, asks
the person, and loads no further page; a block starts the cooldown; a request
in a channel is refused; a person with `browser` off does not have the tools
in their allowlist while a person with it on does; a second run while one is
going gets `busy`; pauses are honoured (use a short test range and assert the
elapsed time).

**Step 15. The results card.**
The stream event, `messages.actions`, `GET /api/mls/runs/{id}`,
`MlsCard.tsx`.
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
| MLS search parsing, caps, availability, direct-line rule, run scoping | `docs/mls/verify_mls.py` |
| Front WebSocket forwarding | `docs/front/verify_front.mjs` |
| Feature list unchanged | `docs/team/verify_team.py` |
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

**Decisions.** All settled on 2026-11-12; see the list at the top of this
document. They replace the five questions this section asked at planning time.

**Risks.**

| Risk | What the plan does about it |
|---|---|
| The real pages do not match the made-up fixtures | One mapping table to edit; unknown pages stop as `unreadable`; supervised first run |
| HAR blocks or challenges the browser anyway | Stop, hand to the person, cooldown; no retry, no solving |
| An account is flagged or fined | Low caps and pauses, unavailable until `start_url` is set; the risk itself is accepted by the team, not removed. With no switch or acknowledgement (decision 7), every person with the browser can search once it is configured |
| Ask maps words to the wrong filter | Only stated filters are passed; the card shows the filters used; every filter is re-checked on the rows; a filter the form lacks stops the run |
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
own browser session on the server, and Jarvis then runs MLS searches
(active, pending or sold listings) through that session on the agent's request.

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
per day; no more than 100 rows per request; nothing is saved, sent or
submitted on the site beyond search filters; results go only to the agent
who asked. There is no separate on switch or acknowledgement: it is available
to everyone with the browser once the operator sets the Matrix start page. None of that makes automated access permitted if HAR's terms forbid it.
It lowers the volume and keeps each agent inside their own login.

What to confirm with HAR and the brokerage:

1. Whether an agent's own signed-in session being driven by software on a
   server counts as sharing access under Rule 6.1, even though no password is
   handed over.
2. Whether HAR offers an approved route for this use. (A licensed feed is
   out of scope for this build; adding one later would be new work.)
3. Whether listing and sold prices may be stored for 30 days, and whether
   that period should be shorter.

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
