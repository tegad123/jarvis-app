# Capturing real Matrix pages as test fixtures

For Tega. MLS search (`docs/plans/browser-har-comps.md`) was built and tested
against made-up pages in `docs/mls/fixtures/`, because nobody on the build had
the real HAR MLS Matrix in front of them. Before the first real search, the
reader in `app/mls_matrix.py` has to be proven on the real thing. This guide
turns a few pages you save from your own Matrix session into fixtures with
nothing real left in them, and runs the tests on them.

Nothing here logs in anywhere or sends a request. You save the pages
yourself, in your own browser, signed in as yourself.

## 1. What to save

Save each page once, in your own browser, while signed in to Matrix as
yourself. Use a search you would run anyway; prefer an area with a mix of
statuses.

| Save as | What the page is | Needed |
|---|---|---|
| `real_search.html` | The search form Jarvis will open (the page `mls.start_url` points to), with nothing typed in | Yes |
| `real_results.html` | A results grid (the table view) with active, pending and sold rows | Yes |
| `real_results_p2.html` | Page 2 of the same results (press Next first) | Yes |
| `real_sold.html` | A results grid of sold rows only (a comps search) | Yes |
| `real_empty.html` | A search with no results | Yes |
| `real_login.html` | The Matrix (or HAR) sign-in page, signed out | Yes |
| `real_ambiguous.html` | The page Matrix shows when an area or address matches more than one thing, if it has one | If it exists |
| `real_two_factor.html` | A one-time-code prompt, if Matrix ever shows you one | If you see one |
| `real_captcha.html` | A "prove you are human" page, if you ever see one | If you see one |
| `real_blocked.html` | An "access denied" / "too many requests" page, if you ever see one | If you see one |
| `real_cards.html` | Results in any other view you use (photo cards, single line) | Optional |

Also write down the full address (URL) of the search form page. That is
`mls.start_url` for the deploy, and its host is the site Jarvis reads.

How to save, in Chrome: wait until the page has finished loading (the table
is showing), then **File → Save Page As… → Format: "Webpage, HTML Only"**.
Not "Webpage, Complete": that also saves images and scripts.

Keep the saved files **outside the repository** (for example `~/matrix-raw/`).
They contain real listings, your name and session tokens. Never commit them,
attach them to a PR or paste them into a chat. Delete them when you are done.

Do not save pages that show a client's personal details (contacts, CRM or
showing-feedback screens). They are not needed.

## 2. Sanitize each page

From the repository root:

```bash
python3 docs/mls/sanitize_fixture.py ~/matrix-raw/results.html docs/mls/fixtures/real/real_results.html \
  --name "Your Full Name" --name "Your Office Name"
```

Add one `--name` for every real name you can see anywhere on the page:
yours, your office's, and any agent or client named in a header, footer or
message. The tool:

- drops every script, frame, image, style sheet and hidden form field (that
  is where session tokens live), and every value typed into a field;
- replaces, in result rows, the address (`102 Fixture St`…), the MLS number
  (`9000…`) and the subdivision, and moves prices and square feet by up to
  10% and every date by the same random number of days;
- replaces agent, office, phone, e-mail and remarks columns with `Redacted`,
  and any e-mail address, phone number or long number anywhere else;
- keeps what the reader needs to recognise the page: column headers, field
  labels, buttons, status and type words, beds, baths, year built, zip.

It then checks what it wrote and **refuses to save** (exit code 1) if it still
sees an e-mail, a phone number, a `--name`, a hidden field, a script or a full
URL. If it refuses, find that text in the saved page, run it again with
another `--name`, or delete the text by hand from the saved page and run it
again.

**Then read the output yourself** in a text editor before committing it. The
tool removes what it knows to look for, nothing more. Search it for anything
real: street names, names of people or companies, numbers you recognise.

## 3. Say what each page should read as

Next to each sanitized page, write `<same name>.expect.json`:

```json
{"class": "results", "rows": 25}
```

- `class` is what the page is: `search`, `results`, `empty`, `login`,
  `captcha`, `two_factor`, `blocked`, `ambiguous` or `unknown`.
- `rows` is how many listing rows you counted on the page (for a results
  page; leave it out for the others).

## 4. Run the tests

On a dev machine (never on a live tenant), with the verify sandbox started:

```bash
export TASKS_SANDBOX=/tmp/mls-sandbox
TASKS_PORT=8741 docs/tasks/tasks_sandbox.sh start
/opt/jarvis/venv/bin/python docs/mls/verify_mls.py
```

Step 13 opens every page in `docs/mls/fixtures/real/` through the real
headless browser worker, from a local stand-in site, and prints one line per
page:

```
PASS  captured page real_results.html: reads as results with 25 rows (its .expect.json)
```

If a page fails:

- **reads as `unknown`**: the reader does not recognise the page. The words
  it looks for (status names, the sign-in and "no results" wording, column
  headers) are in `app/mls_matrix.py` (`COLUMNS`, `STATUS_WORDS`,
  `TYPE_WORDS`, the `*_TEXT` patterns and `LOGIN_URL`). Hand the failing line and the
  sanitized page to whoever picks this up; that is the change to make.
- **the right class, the wrong row count**: a column header is not mapped
  (`COLUMNS`), or the grid is not a plain table. Same hand-off.
- **the search form**: step 14 types each filter into the field whose label
  it knows (`FILTER_LABELS`). If the real form names a field differently,
  that is the place.

Stop the sandbox when done: `docs/tasks/tasks_sandbox.sh stop`.

## 5. Commit

Commit only the sanitized pages and their `.expect.json` files, in
`docs/mls/fixtures/real/`, on a branch, in a PR. Say in the PR which Matrix
host they came from and the `mls.start_url` you wrote down. Delete
`~/matrix-raw/`.

No real search should be turned on for anyone (`mls.start_url` stays empty)
until every page above reads as expected.
