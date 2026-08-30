# Vibrancy pass — temperature, not layout

2026-08-21 · frontend only · no backend, no behaviour · W3 suites re-run green (54/54, 21/21)

The app was token-faithful and read administrative. Layout and components are unchanged; this
pass changes temperature. **Gate: 31/31** (`vibgate.mjs`), side-by-sides in `SBS-dark.png` /
`SBS-light.png`.

---

## Two things about the sources, before anything else

**There is only ONE mockup on this box, and it is dark.** `docs/design/` holds three PNGs; the
worklist already records that only `Screenshot…7.27.55 PM.png` (the dark conversations screen) is
design. The other two are a Supabase usage page and a Claude Console cost page. **No light-mode
conversations/channel mockup exists here.** So `SBS-dark.png` is a true side-by-side against the
design; `SBS-light.png` puts the light app beside the *dark* mockup, which shows that structure,
rhythm and chip language match but cannot settle light-mode colour. Light was built from the
tokens' light palette and the same structural decisions. If a light mockup exists elsewhere,
that comparison is still owed.

**`docs/design/Screenshot 2026-08-17 at 7.05.30 PM.png` has an API key in the URL bar** —
`platform.claude.com/cost?api_key=apikey_01QAS4v…[redacted] (id truncated deliberately — reporting an exposed credential by reproducing it in full spreads it further; the prefix is enough to identify the key at the console)`. It is a real-looking
Anthropic key sitting in a docs directory that ships with the repo. It is not design and not
referenced by anything. **Rotate it and delete the screenshot.**

## The five gaps

**1 · INK PRESENCE.** Kind words were already coloured; what was missing was *area*. Six
coloured glyphs on a white card do not register — the colour has to have surface. The kind word
is now a tinted chip (below), the pinned direct-line card carries an ink wash and an
accent-tinted hairline, the "Channels" section label is ink rather than a third grey, and the
`JARVIS` label above every assistant turn is ink — the cheapest repeated accent on a screen that
is otherwise white text and one bubble. User bubbles and the send button were already solid ink.

**Spark marks were hardcoded `text-ink-300` in both modes.** ink-300 is the *dark*-mode accent
tint; on white paper it is a pale smudge. That single class was most of why the light theme had
almost no accent on the conversations screen. Now `text-light-accent dark:text-ink-300`. The
hero keeps ink-300 in both modes, correctly — it is an ink ground either way.

**2 · CHIPS NOT LABELS.** The card header was two mono labels of identical weight side by side,
so a row opened with a pair of grey words and the reader had to work out which mattered. Now:
kind as a tinted chip, skill as quieter mono *after* it. The hierarchy is the point, not the
colour. Applied to feed rows, lead status, approval state, and channel badges.

> **There are TWO chip registers, because the mockup has two.** A COUNT ("2 UPDATES") is
> metadata: mono, uppercase, tracked — digits stay aligned down a list. A STATE ("Going quiet",
> "Hot") is the app talking: sentence-case sans 12px/600, as the mockup draws it. Flattening
> them into one was the first attempt and it made "Going quiet" shout in uppercase mono, louder
> than the channel name above it. The tint is shared; the type is not.

Chip tints derive from the same semantic tokens as the dot and word beside them, so a warning
chip can never disagree with a warning dot. They live in `index.css` as `.chip-*`, not as
Tailwind colours, because background, text and border must move together.

**3 · WARM GROUND + LIFT.** `light.bg` #F2EEE6 → **#F3EDE2**: the red/blue spread goes 12 → 17.
Warmth is what makes it read as paper; the r2 depth change is what makes cards read as cards.
New `lift` / `lift-dark` shadows — heavier and lower than `card`, offset does the floating, blur
does the softness, and a tight 1px layer keeps the card seated. Verified by the r2 alpha-parsing
check, not by "contains rgb twice": Tailwind always emits two fully transparent ring
placeholders first, so the naive check passes on a card with **no** shadow.

**4 · ATMOSPHERE.** The hero was a single 160deg ramp between two close navies, which reads as
flat fill at card size — the eye needs a light *source*, not a gradient. Two radial washes now
sit over the ramp: a warm ink-400 bloom off the top-left, a cooler ink-300 lift at the lower
right. Both under 15%. A `.page-glow` radial sits behind the top of the feed. Thinking panel and
login keep their existing glass bloom, untouched.

**5 · TYPE CONFIDENCE.** Card headlines 500 → **600** at −0.006em; at 500 they weighed the same
as the body under them and the card had no entry point. Channel names were already `row-title`
(600) — verified, not changed.

## Deliberate deviations from the mockup

* **"Going quiet" is a tinted chip here; the mockup draws it as dot + coloured text.** The brief
  asked for it tinted, explicitly. It keeps the mockup's *type* register (sentence-case sans) so
  it does not shout — but this is a knowing difference, not a miss.
* **"Channels" label and the pinned card are inked**; the mockup has them neutral. Same trade:
  gap 1 asked for accent on every screen, and the conversations screen had almost none.
* Carried forward from r2, unchanged: the `Hi, <name>` chrome header stays; count chips
  ("2 UPDATES", "1 ALERT") and "Hot" have **no producer** and so never render in production —
  the mockup shows them, the app cannot; the Approvals tab count badge is still unimplemented.

## Gate — 31/31

Four viewports (390/1280 × dark/light) × four tabs.

| Check | What it actually asserts |
|---|---|
| Ink presence | ≥4 saturated elements of ≥2 kinds per screen — the rule "no screen is grayscale-plus-one-chip", counted |
| Lift | ≥2 shadow layers with **alpha > 0**, parsed per layer |
| Hero | ≥2 radial gradients *plus* the linear ramp |
| Page glow | radial present, `z-index < 0`, not focusable |
| Overflow | no element past the viewport at either width |
| DOM jargon | no raw dotted kind string; humanised words still render; **canary proves the matcher fires** |

### Two harness bugs found and fixed, both mine

**The ink metric was a bad proxy.** The first version summed *weighted coloured area* and
returned an identical **7167** for the Leads screen at three different viewport widths — an
impossible result for a real area measurement, and precisely the "identical failures mean the
harness broke, not the code" tell. The screenshots showed a screen full of amber and green
chips. Counting distinct saturated elements is stable and is what the rule says.

**The page glow caused the overflow it was meant to be invisible for.** `inset: -80px -20%` made
it 140% of the viewport and pushed the document to 440px at 390px wide. The glow itself is
invisible, so the only symptom was sideways scroll. Any decorative layer that exceeds the
viewport needs a clipping parent, and this one has none by design — clamped to `inset: … 0 … 0`
with wider gradient stops, which costs nothing visually.

**And one that was not a bug:** Leads kept failing at 7167 because the sheet-backed list was
hitting Google's per-minute read quota and rendering its error state — four filter chips and
nothing else, which looks exactly like a screen that lost its colour. Verified in isolation
(200, full list). The gate now retries with backoff and **skips loudly** if the list never
loads; an un-run check must never look like a passing one.

## Token file note

`tailwind.config.cjs` claimed to be a verbatim copy of `~/jarvis-tokens_tailwind_config.js` and
had not been one since r2. Re-copying over it, as that header instructed, would have silently
reverted three deliberate decisions. The header is now a divergence log instead.
