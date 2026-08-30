# Approval executor — 2026-08-30

`app/approvals.decide()` now EXECUTES an approved `send_email` action through
`send_gate.gated_send` (the sole send path — `verify_send_paths.py` 12/12 on
both trees) and writes the outcome on the decision record:

```
approvals_decisions.jsonl  {id, decision, decided_by, ts, send: {ok, state, reason, to, subject, ts, …gate fields}}
  state: sent | captured | blocked | failed
```

- The decision is recorded as `approved` whether or not the send worked — the
  person did approve it; a failure is a fact beside it, never a rollback.
- Failure surfaces twice: `send.state == failed|blocked` on the record (and on
  `GET /api/approvals` via `load_all`), and an **ops ALERT** on the feed
  `Couldn't send the approved email — <summary>` with the reason. Success posts
  `Sent the approved email — <summary>` (kind `approval.sent`).
- Boundary tenants: the gate captures to the outbox; the row carries
  `run_id = <approval id>` — the linkage a test asserts on.
- Refused shapes (recorded as failed, nothing sent): missing `to`/`subject`;
  **preview-only payload** (`body_preview` with no `body`/`html_body` — how
  live's owner-email currently files its request; sending the preview would
  mail the customer 200 characters); the gate raising; a recipient the
  boundary refuses (`RecipientRefused`).
- Token: `create_approval_token(to, subject, body)` is minted at execution
  time — the human approval IS the authorisation; the token is the gate's
  materialisation of it.
- Non-send actions (`create_channel`, `create_event`) are unchanged: recorded,
  not executed. Config changes still apply before recording.

## Live audit (read-only, before any deploy)
`/root/.hermes/state/`: 4 requests, 1 decision. The only decided request is
`apr_ab25f539f8e4` (email-to-calendar `create_event`, approved by Spencer
2026-08-16). The one `send_email` request, `apr_7ae19ab8c49c` (owner-email,
"Send a follow-up to William Mchenry about 609 25th"), is still **pending** —
**no approved-but-unsent send_email decisions exist on live.** Note its payload
carries `body_preview` only; if approved after this change it will record a
failed send with that reason rather than mail a preview. The producer
(owner-email) should file the full body — follow-up.

## Proof
`verify_approval_executor.py` **31/31**: boxed (scratch HERMES_HOME + files,
testco runtime config) approve → captured + linkage + feed + overlay + second
approve refused; reject → nothing sent, no `send`; failures (gate raises,
boundary refuses, preview-only) → approved + failed state + alert; non-send
action untouched. Then testco: member 403, owner approves the pending showing
autoreply → 200 with `send.state=captured`, outbox row linked, decision record,
feed line, `GET /api/approvals` shows the state.

The legacy approvals suites (`docs/step4-shots/verify4.mjs`, `docs/verify_w3_*`)
are bound to the live tenant (:8700, live passphrase, live decisions file) and
were not run.
