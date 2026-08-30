# Testco — seeded synthetic workload for the tester

2026-08-21 · seeded into the same-host testco clone · **live box untouched, asserted below**

The rehearsal proved a fresh tenant *provisions*. An empty app proves nothing about whether the
product is any good to use, so this fills that tenant with a working week: leads with histories,
a feed with several days behind it, deal channels mid-conversation, and decisions waiting.

Everything is fictional. Every address ends in `.example` — RFC 2606 reserved, so it can never
resolve, and a fixture that escapes into a send path fails closed instead of mailing a stranger.

---

## 1. What is in there

| | Count | Notes |
|---|---|---|
| Accounts | 3 | dana (owner), omar (member), priya (admin) — from the tenant's own registry |
| Leads | 40 | 4 statuses × 4 temperatures, spread across the 3 agents, ages 1–95 days |
| Feed events | 90 | 8 kinds, 21 May → today, **14 today** in the tenant's zone |
| Deal channels | 4 | multi-turn transcripts, scoped membership |
| Direct lines | 3 | one per person, each with a morning brief |
| Pending approvals | 3 | each names the channel it came from |
| Lead-sheet fixture | 5 tabs / 18 rows | for exercising the W1 importer |

Feed kinds: `email.classified` 20 · `lead.created` 40 · `task.created` 13 · `task.completed` 5 ·
`info` 5 · `warning` 4 · `alert` 1 · `deal.channel_created` 2.

That last one is unrecognised by `lib/kinds.ts` **on purpose** — it exercises `prettifyKind()`
on a real screen, which no hand-checked kind ever does.

## 2. How it was seeded

Through the production code paths, not by writing rows:

| Thing | Path |
|---|---|
| Leads | `leads_store.create()` — which emits its own `lead.created` event, as in production |
| Channels | `threads.create_thread()` / `add_member()` / `append_messages()` |
| Direct lines | `threads.ensure_direct()` |
| Accounts | `accounts.set_pin()` |
| Approvals | `approvals._append_jsonl()` — the same writer `approvals.seed()` uses |
| Feed | `notify.build_event()` + `notify._write_feed()` |

**The one place it bends, stated plainly.** `notify.notify()` stamps `now()` and takes no clock
argument. Sixty events sharing one timestamp exercises neither the day grouping, nor the TODAY
hero, nor relative times — so events are built by the real constructor (real schema, real demo
marker) and written by the real writer, with `ts` overwritten afterwards. Nothing else is
touched. The 40 store-emitted lead events are likewise moved back to each lead's own age,
matched on the `lead_id` in the attachment rather than on line position.

**Every event carries `demo-seeded`.** `notify.build_event()` applies the marker centrally when
`JARVIS_DEMO_SEED` is set, so nothing reading this feed later — a purge audit included — can
mistake a seeded event for a real one. Asserted: 90/90 marked.

### What is NOT seeded, and why

* **Thinking steps are not storable.** They are transient SSE frames (`hermes.tool.progress`)
  rendered while a reply streams; the message store has `role/content/ts/partial/attachments`
  and no step column. A finished transcript in production contains none either — so seeding
  them would be seeding something the product does not have. The channels instead carry
  assistant turns that narrate the work, which is what a transcript actually looks like once the
  stream ends. **A tester who wants to see thinking steps must ask a live question, and can —
  the gateway is wired.**
* **Inline approval cards do not exist in the transcript.** Approvals are their own screen. What
  is real is the COUPLING, and that IS seeded: each pending approval names its deal channel, and
  that channel's transcript contains the turn where Jarvis says it needs sign-off. That is the
  loop to walk — read the channel, go to Approvals, decide.

## 3. Isolation

`seed/testco_env.py` sets every path variable **before any app import**, then re-reads the
RESOLVED paths back out of the modules and **refuses to run** if any of them lands outside the
clone. Setting an env var proves nothing on its own; the modules are asked.

Nine paths are checked: `HERMES_HOME`, `SKILLS_ROOT`, the threads / leads / accounts DBs, both
approvals files, the attachments dir, and `notify.feed_path()`.

> Isolating the database is not isolating the side effects (standing PATTERN 3). It bit twice
> during this task, in code written the same night: the importer's `--store` flag moved the lead
> DB but not the feed, and eleven import events landed in the seeded workload's feed. `--store`
> now moves both, and refuses a target outside the clone.

**Live box, asserted after seeding:** app healthy on 8700 · feed **82** events · `JARVIS_ALLOW_WRITES`
unset · threads DB unchanged (4 channels, names intact).

## 4. The lead-sheet COPY fixture

`fixtures/testco-leads-sheet.xlsx` — uploadable to Drive for a real end-to-end run — plus
per-tab CSVs and `sheet_fixture.json`, which `seed/run_importer_fixture.py` replays.

**Only the gspread TRANSPORT is stubbed.** `google_creds.gspread_client()` is swapped for an
object serving the fixture through the same four calls the importer makes. Everything after that
is production code: `derive_map()`, header-TEXT matching, co-listing and collision logic,
`identity.resolve()`, `leads_store.upsert_external()`. Stubbing the parsing would test the
fixture; stubbing only the network tests the importer.

The fixture reproduces the shape of a real tracker, warts included, and every wart is a code
path that would otherwise go unexercised:

| Wart | What it proves |
|---|---|
| Row 0 is a merged banner | `HEADER_ROW_INDEX = 1` |
| Tab title `"Dana "` has a trailing space | title stripping |
| Headers worded differently per tab | matching on header TEXT, not position |
| A `Referral Src` column nothing knows | reported as unmatched, never mapped by position |
| `SEND LOG` tab | skipped by CONFIG (`exclude_tabs`) |
| `2025 ARCHIVE` tab | skipped by STRUCTURE (no lead-name column) — a tenant who names their log something else is still handled |
| `TC-2010` twice, SAME name | co-listing: merged, correctly |
| `TC-2010` a third time, DIFFERENT name | genuine collision: shouted about, because merging destroys a lead |
| Agent `Thaddeus Grimsby` | unresolvable owner left NULL and reported, never guessed |
| One blank row | skipped |

Verified: dry-run reports all ten; `--execute` writes 11 leads + 2 updates; re-running writes
0 and updates 13 (idempotent). The execute proof runs against `--store`, a throwaway DB **and
feed** inside the clone, so the seeded workload is untouched.

> **`Thread ID` is NOT an unmatched column.** An earlier draft of the fixture claimed it was;
> W1 added it to `HEADER_FIELDS` after finding the column, so it maps. The run corrected the
> fixture. `Referral Src` exists to cover the unmatched path for real.

## 5. The DOM-grep gate — 17/17

`seed/verifyTestcoSeeded.mjs`. Three roles × four tabs × three viewports, **every `Details`
disclosure expanded** — the raw `detail` string carries the from-address, subject and classifier
reasoning, and a collapsed panel renders none of it, so a gate that skips this greps only
headlines.

**73 forbidden terms, DERIVED not hand-written** (`seed/build_leak_terms.py`), per standing
PATTERN 1b: a hand list of tenant slugs and channel names, UNIONed with everything read from the
**live** registry on disk and the **live** feed — names, first names, addresses, local parts,
alias keys, internal domains. Read from disk, never via `identity.load()`, because this process
has `JARVIS_IDENTITY_CONFIG` pointed at the testco fixture registry and `load()` would hand back
Dana/Omar/Priya — the names that are supposed to be present.

**A canary runs first.** A gate that cannot fail proves nothing — Phase 3 shipped a green run
once because an unescaped paren made the pattern match nothing. The matcher is proved against a
planted term before its silence is believed.

Result: **zero** prior-tenant terms in ~81,000 rendered characters. All 12 visible addresses
`.example`. Zero horizontal overflow at 390 and 1280, both themes.

### Two terms had to be subtracted, and one of them is a finding

* `jarvis` — the live registry's `assistant` person is Jarvis at `jarvis@34dev.com`, so it derives
  as a real-person term. It is the product name and appears on every screen of every tenant.
* `priya` — **the 34dev demo cast and the testco cast collide on a first name.** 34dev's
  demo-seeded events name a title officer "Priya Raman"; Testco employs a "Priya". The gate could
  not tell them apart.
  > **Keep fixture casts disjoint on first names.** Two casts written months apart collided, and
  > the cost is a leak gate that either cries wolf or has to be weakened. Cheap to avoid up front,
  > awkward afterwards — the testco registry is the tenant's and is not ours to rename.

The subtraction is principled, not a shortening: a term naming a person in the RECEIVING tenant's
own registry cannot be evidence of the PREVIOUS tenant's data. Everything else still errs long.

## 6. Two product defects the seeding found

**1. The Leads screen showed raw person keys.** `_to_screen_row` put `owner_person` — a KEY — into
the `agent` column, while the sheet reader puts a DISPLAY name ("Martin") in the same field. So a
sheet-backed tenant read `· Martin` and an app-native one read `· omar`: same field, two
registers, decided by which backend the tenant happens to be on. Fixed —
`leads_source._display_name()` resolves through the registry, falling back to the key rather than
to blank, because "no owner" and "owner we cannot name" are different problems.
`tab` stays the key: grouping on a display name would merge two people who share one.

**2. A test compared a LOCAL date against a UTC timestamp.** `verify_w3_scope.py` asserted the
hero count by slicing `ts[:10]` and comparing it to the summary's local `date`. It passed for
weeks and failed the first time it ran between UTC midnight and Chicago midnight. Fixed to
compare in the summary's own timezone. Same root cause as the seeded feed reading "Nothing yet
today" in a headless UTC browser — **an assertion about "today" has to name the zone, exactly as
the endpoint it checks does.** The seed now also carries hours-old events, so the hero is
non-empty in any timezone, not just the tenant's.

## 7. Running it

```bash
cd docs/testco-rehearsal/seed
python3 seed_workload.py --wipe          # deterministic (seeded RNG); re-runnable
python3 build_sheet_fixture.py           # xlsx + CSVs + json
python3 run_importer_fixture.py          # dry-run against the fixture
python3 run_importer_fixture.py --execute --store <clone>/fixtures/_proof.db
./clone_app.sh start                     # testco app on 8702
node verifyTestcoSeeded.mjs              # the gate
./clone_app.sh stop
```

PINs are written once to `<clone>/SEEDED_PINS.json`, mode 600. The store prints a PIN exactly
once and cannot recover it, so that file is the only copy — it is a handoff artefact, and it does
not belong in a golden image.

## 8. For the tester — what to actually try

1. Sign in as **dana**. The hero should read "N things need you" over a week of activity.
2. Open **118 Alderwood Ln**, read to the bottom, then go to **Approvals** and act on the reply to
   Rowan Castellane. That is the product's central loop.
3. Sign in as **omar**. Copperbeam is gone from his list — he is not on it. Approvals refuse him
   in words. He is greeted by his own name.
4. Tap a status or temperature chip on **Leads** to change it.
5. Ask a live question in a direct line — that is the only way to see thinking steps, and they are
   real, not seeded.
