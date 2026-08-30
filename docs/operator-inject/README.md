# Operator fixture injection — 2026-08-30

The way an inbound email reaches testco, which has no Gmail poller or webhook
(push is deferred to cutover). One engine, `app/inject.py`, behind two doors:

| door | who | takes |
|---|---|---|
| `POST /api/operator/inject` `{"kind","fixture"}` | operator session only (owner/admin/member → 403) | a fixture **name** from the whitelist enumerated from `/opt/testco/fixtures` at app start — never a path; anything else → 400 |
| `/opt/testco/bin/testco-inject <kind> <fixture\|path.eml> [--fresh] [--list]` | Bao at a shell | a name or an arbitrary `.eml` path; `--fresh` (showing only) forgets the once-per-showing key |

Kinds → handler (same script, same env, the poller would call):
- `showing` → `showingsmart-autoreply/scripts/handle_showing_request.py --eml`
- `meeting` → `meeting-intake/scripts/handle_meeting.py --eml --origin email`
- `reply`   → `gmail-push-trigger/scripts/poll_gmail_history.py --reply-eml` (new seam;
  `service=None`, confirmation through `outbound_boundary.guard` → outbox;
  `In-Reply-To: <LATEST_DIGEST>` resolves to the newest digest in `digest_map`)

Every run: fresh `Message-ID` (`<inj-<run_id>@…>`), staged copy under
`state/inject/` (0600), one journal row in `state/operator_inject.jsonl`
(ts, run_id, kind, fixture, by, origin, rc, status, handler result), one feed
line `Added a test <kind> — <fixture>` (kind `operator.inject`, no addresses).
Refuses (409) unless the outbound boundary is active. Refused/400/403 calls
write no journal row (nothing ran).

`GET /api/operator/inject` (operator) returns the whitelist and boundary state.

## Fixtures (seeder §7b, survive re-seed)
`digest_reply_done1.eml` (`done 1`), `digest_reply_done_two.eml` (`done 2, 3`),
`digest_reply_priority.eml` (`priority 1 high`), `digest_reply_nonsense.eml`
(no command). Showing and meeting fixtures were already there.

## Env the wrapper and the unit carry
The five operator-CLI vars + `HERMES_API_URL=:8652` (meetings) +
**`JARVIS_SHEET_FIXTURE=/opt/testco/fixtures/sheet_fixture.json`** — the
showing handler reads the property sheet through the fixture transport (the
suite set it; the app unit did not, so the first injection died on "no Google
credential"). Added to `testco-app.service` and the wrapper.

## Findings
- **Approved `send_email` has no executor.** `approvals.decide()` records the
  decision and stops; nothing sends. On testco (no `standing_authorization`
  for showingsmart, by design) an injected showing ends as a pending approval;
  the owner's Approve is recorded and the mail is never captured. The showing
  suite proves the send only under `standing_authorization=True`. Smallest
  fix: in `decide()`, when `item.payload.action == "send_email"` and the
  decision is approve, call `send_gate.gated_send` with the payload (boundary
  captures it on testco) and store the result on the decision record.
- Same showing fixture twice = `duplicate` (key is address+time, the product's
  rule). `--fresh` on the CLI resets it; the API takes no flags.
- Same meeting fixture twice = `duplicate` (content-keyed) with the
  "Already filed…" note — also the product's rule.

## Proof
`verify_inject.py` (52/52 against :8710): 10 whitelist/shape rejections → 400;
tester/priya/dana → 403 on POST and GET, no session → 401, no journal rows;
reply happy path (acted on the latest digest, position 1 done, confirmation
captured, feed line, journal row, re-inject is a fresh reply); showing happy
path (handled, pending approval, owner approves, decision recorded, duplicate
on repeat, no second approval); meeting happy path (filed/duplicate note,
sends nothing, journal); CLI wrapper (reply acted, origin=cli, `--list`,
refuses a non-.eml path).

**Update 2026-08-30:** the approved-`send_email` executor gap is closed — see `docs/approvals/README.md`; `verify_inject.py` now prints `captured … YES` for the showing path after the owner approves.
