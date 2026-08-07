# Pick algorithm refinement — design

**Date:** 2026-08-07
**Status:** Implemented on branch `feat/pick-algorithm`, pending a dry-run
sign-off before merge. See "As-built deltas" at the end of this document — that
section is the authority where it disagrees with what follows.
**Scope:** Step B of a four-step roadmap (A: sender change — done; **B: pick
algorithm**; C: expand beyond WSJ to Goldman Sachs / JP Morgan / Morgan Stanley
content and podcasts; D: Google Sheets consumption catalog).

## Problem

The digest has been good but not consistently *recent and relevant*. Five
failure modes were identified by classifying all 80 picks in `history.json`
(2026-07-19 through 2026-08-07) by WSJ URL section.

1. **Macro is dominated by daily market-wrap wire copy.** ~8 of 20 Macro picks
   were interchangeable roundups: "Treasury Yields Fall as U.S.-Iran Hostilities
   Take a Break" (7/27), "U.S. Treasury Yields Fall Amid Mideast Hopes; Dollar
   Rises Ahead of Fed" (7/28), "U.S. Treasury Yields Rise, Dollar Firm as Oil
   Prices Increase" (8/04), "Chip Stocks Weaken, Oil Steady as Investors Await
   Hormuz Progress" (8/06). Maximally fresh, minimally informative. The rubric
   asks for "big rate/FX/oil moves" and the model correctly picks the freshest
   match. The existing `NOISE` regex catches `Roundup: Market Talk` but not
   these.
2. **Story-level repeats slip through.** Dedup matches normalized title or exact
   URL only. KKR/Integer Holdings appeared 8/02 ("Near Deal to Buy") and 8/03
   ("to Buy … for $4.3 Billion"). Prologis/Segro 7/22 and 8/04. Paramount/Warner
   7/26 and 8/01. Warsh three times.
3. **Tech and Industry/Transaction converge** on AI-financing stories (Etched
   $20B, AMD–Anthropic, Nvidia–OpenAI $250B, Anthropic $15B data-center loan).
   Two slots deliver one genre.
4. **Slot leakage into off-sections.** One Macro pick resolved to `/podcasts/`
   (an audio page, not an article), one to `/pro/` (a separate paid tier), and
   today's Macro pick is `/politics/policy/` — Fed palace intrigue, not macro.
5. **Op-Ed is healthy** — 20/20 genuinely opinion, mostly economics and policy.

Separately, a fifth daily slot is being added: **WSJ Sports**.

## Decisions

| Area | Decision |
|---|---|
| Market wraps | Hard filter, treated as noise |
| Recency | Tier: prefer ≤24h clearing a quality bar; reach 48–72h only when nothing fresh qualifies |
| Repeats | Storyline-level block ~7d, material milestones allowed through |
| Tech/Deals overlap | Cross-slot diversity check; the Tech rubric itself is unchanged |
| Op-Ed | Unchanged |
| URL sections | Block `/pro/` and `/podcasts/`; leave bare `/articles/` URLs alone |
| Sports slot | New, fifth, rendered last |
| Slot precedence | Sports outranks Industry on a shared story |
| Validation | Local A/B dry run against one live candidate pool |

### The Sports slot

Prefer sports **business / finance / investing / economics**: franchise sales and
valuations, media-rights deals, private-equity and sovereign money in leagues,
stadium financing, league and CBA economics, the betting industry, athlete
investing.

If the day has no such story, fall back to **the top sports headline** rather
than leaving the slot empty or forcing a poor fit. This deliberately inverts the
"topical fit is the first filter" rule that governs every other slot, so it must
be stated explicitly in both the keyword filter and the model rubric. The
existing filter keeps a keyword-matching subset only when at least 3 items
survive; applied naively to Sports that rule would starve the slot on light days.

Slot config: query `site:wsj.com/sports when:3d`, max age **72h** — matching
Macro and Industry, and wide enough that the recency tier still has something to
fall back to on a quiet weekend.

## Approach

Chosen: **deterministic pre-filter, then one enriched model call.**

Python handles what is mechanical and testable — wrap detection, section
blocking, recency tiering, storyline-key extraction. The model receives a
pre-cleaned, pre-tiered pool plus an explicit already-covered list, and returns
all five picks in one call under a stated diversity constraint. API cost is
unchanged at one call per run.

Rejected alternatives:

- **Two calls (score, then select).** Inspectable per-candidate scores that would
  generalize well to step C, but double the cost and latency and more machinery
  than ~15 candidates × 5 slots warrants.
- **Deterministic scoring, model writes summaries only.** Cheapest and fully
  testable, but discards the editorial judgment that makes the picks good.
  Keyword scoring cannot separate consequential from loud — it is precisely how
  market wraps win, since they match every macro keyword.

## Architecture

`generate.py` is 322 lines; this change adds a fifth slot, wrap filtering,
tiering, storyline dedup, and a diversity pass. Extract only the new, testable
logic; leave fetch/curate/resolve in place until step C forces a source-adapter
split.

```
wsj-ft-daily/
├── generate.py        # orchestration, fetch, curate, resolve
├── wsjdaily/
│   ├── slots.py       # the 5 slot definitions — config data, no logic
│   ├── filters.py     # pure: wrap detection, keyword filter, recency tiering
│   └── history.py     # pure: storyline keys, prior-coverage lookup
└── tests/             # pytest, no API key or network required
```

CI continues to run `python3 generate.py`; a package directory requires no
install step. The source abstraction step C will need is deliberately *not*
pre-built, because the GS/JPM/MS requirements are not yet known.

## Pipeline

1. **Fetch** — unchanged. Google News RSS per slot, WSJ-source rows only.
2. **Reject** — existing `NOISE`, plus a new `MARKET_WRAP` matcher, plus the
   per-slot keyword filter with the Sports exception described above.

   `MARKET_WRAP` targets the wire-roundup *sentence shape*, not its subject
   matter, so that substantive rate and oil stories survive. The shape is a
   market-state subject (yields / stocks / oil / dollar / bonds / futures /
   shares) followed by a price-move verb (rise / fall / slip / climb / ease /
   steady / firm / weaken / jump / surge / edge), typically joined by "as",
   "amid", "ahead of", or a semicolon. "U.S. Treasury Yields Rise, Dollar Firm as
   Oil Prices Increase" matches; "U.S. Economic Growth Slowed to 1.5% in Second
   Quarter" and "Three Fed Officials Say Inflation Should Have Prompted Higher
   Rates" do not. The regex is tuned against the real corpus in tests, and false
   positives are the failure mode to watch — a banned wrap costs nothing, a
   banned real story costs an article.
3. **Tier** — partition each slot into FRESH (≤24h) and FALLBACK (>24h up to
   that slot's existing max age: 48h Tech, 72h Macro / Industry / Sports, 96h
   Op-Ed). The model selects from FRESH unless it is empty or every option in it
   fits the slot poorly; "fits poorly" is the model's judgment against the
   existing per-slot rubric, not a numeric threshold.
4. **Prior coverage** — load storyline keys from the last 7 days.
5. **Curate** — one API call. Reply format extends to
   `slot|id|storykey|summary`, where storykey is 2–4 lowercase entity tokens
   joined by `+` (e.g. `kkr+integer`). Still pipe-delimited and line-based, for
   the reasons in HANDOFF gotcha #5.
6. **Select & resolve** — model's pick first, then fallbacks.

### Section blocking happens after resolution

Candidates carry Google News URLs; `/pro/` and `/podcasts/` are visible only in
the resolved wsj.com URL. The blocklist therefore lives inside the resolve loop —
resolve, inspect the section, reject and advance — mirroring the existing
duplicate-URL rejection.

### Storyline dedup is a hybrid

An exact storyKey match within **2 days** is a hard deterministic block; that is
the KKR/Integer consecutive-day case, near-certainly a rehash. Days **3–7** are
left to the model, which sees the covered list and judges whether the development
is materially new. This guarantees the worst observed failure cannot recur
without amputating legitimate milestone coverage.

The 21-day literal title/URL dedup is retained unchanged alongside it.

### Diversity precedence is processing order

Slots resolve in the order **Macro → Sports → Industry → Op-Ed → Tech**, sharing
one set of used storyKeys and URLs. First slot to claim a story keeps it, so
Sports outranking Industry falls out of the ordering rather than needing a
separate reconciliation pass. Output order in `picks.json` stays canonical, with
Sports appended last.

## Data changes

`history.json` entries gain `storyKey` alongside `title` and `url`. Written
going forward only — no migration and no backfill, since the 7-day storyline
window self-heals within a week. Pre-existing entries degrade to title/URL
matching.

`picks.json` gains `storyKey` as an additive field. This is the join key the
step-D Sheets catalog will want.

`mailer.gs` requires **no change**: it maps over `data.picks`, so the fifth row
renders automatically with no Apps Script redeploy.

## Error handling

Existing invariants are load-bearing and stay exactly as they are:

- **Zero slots resolved → write nothing, `exit 1`.** Still the blocked-runner-IP
  signal (HANDOFF gotcha #2). With five slots the threshold remains *zero*, not
  "fewer than five" — a partial run is a good run.
- **API failure or unparseable reply → heuristic fallback.** The heuristic must
  be extended to five slots and must now run *after* the same filter and tier
  stages the model sees. Today it selects from raw candidates, so an API outage
  would quietly reintroduce the market wraps this change bans.
- **Empty slot → `"No WSJ pick today."` row, non-fatal.** Unchanged.

New paths:

- **Storykey missing or malformed** → accept the pick, store `storyKey: null`. A
  missing dedup key must never cost an article; that entry degrades to
  title/URL matching.
- **Every candidate in a slot rejected** by section blocking or diversity → fall
  through to the empty-slot row rather than relaxing the rules.
  `MAX_RESOLVE_TRIES` rises from 4 to 6, since there are now more grounds for
  rejection.
- **Sports pool empty** → empty row. Not an error; WSJ has quiet sports days.

Bug fixed in passing: the heuristic fallback currently sets `summary` to the raw
headline, so an API outage sends emails with the title printed twice. It should
set `""`.

## Testing

pytest against the pure modules; no API key, no network.

- **Wrap detection** — the eight real wrap headlines from `history.json` as
  must-reject fixtures, plus genuine macro stories from the same 20 days ("U.S.
  Economic Growth Slowed to 1.5% in Second Quarter", "Three Fed Officials Say
  Inflation Should Have Prompted Higher Rates") as must-survive. Tuning against
  the real corpus rather than invented examples.
- **Tiering** — boundaries at 23.9h and 24.1h; an all-stale slot yields an empty
  FRESH tier without crashing.
- **Storyline dedup** — consecutive-day KKR/Integer blocks; the same key at 5
  days reaches the model instead of being auto-blocked; a null key degrades
  gracefully.
- **Sports keyword fallback** — a pool with zero business stories returns the
  full pool rather than empty.
- **Diversity precedence** — a story shared between Sports and Industry lands in
  Sports, and Industry advances to its next candidate.

Target 80% coverage on `wsjdaily/`. `generate.py`'s network paths are covered by
the dry run, not by unit tests.

## Rollout

1. Build behind `--dry-run`: fetches one live pool, runs legacy and new curation
   against it, prints both side by side, **writes nothing**. Two API calls.
2. Review the diff; tune the wrap regex and the sports query against what it
   shows.
3. Merge to `main` only after sign-off on a dry-run output. The daily job picks
   it up on the next tick.
4. The first live run is observable in the `picks.json` commit diff before the
   9 AM send. Rollback is a single `git revert`; the mailer continues from the
   previous file.

If a live run produces zero resolvable picks, the existing `exit 1` path leaves
yesterday's file in place and the mailer sends its "no digest" alert rather than
a broken email.

## Known risk

WSJ's sports section is small, and `site:wsj.com/sports` may return only a
handful of items per day — the Sports slot could come up empty more often than
the other four. If the dry run shows a thin pool, the remedy is a secondary query
on sports-business terms across all of `wsj.com` rather than the `/sports` path
alone. This is to be confirmed empirically in the A/B run rather than guessed at
now.

---

## As-built deltas (2026-08-07, post-implementation)

The branch `feat/pick-algorithm` implements this spec with the following
deviations, each found during implementation review and each deliberate. This
section is the authority where it disagrees with the sections above.

### 1. Sports uses preference ORDERING, not filtering

The spec described a keyword filter with a fallback. As implemented, that
discarded the non-matching candidates outright: a live pool measured
`raw=7 filtered=1`, leaving the slot one candidate against six resolve
attempts, so a single resolver failure or `/pro/` hit would empty it.

`apply_keyword_filter` now returns matched rows FIRST and keeps the remainder
behind them for `keyword_fallback` slots. The model still sees business stories
at the top; the slot retains full resolve depth. Non-fallback slots are
unchanged. Approved by the operator before merge.

### 2. Keyword matching is leading-word-boundary, not substring

Pre-existing production code matched keywords with plain substring containment.
Because `TECH_KEYWORDS` contains the two-letter token `ai`, that matched inside
unrelated words -- "S**ai**ling", "s**ai**d", "ch**ai**r", "camp**ai**gn" --
polluting the Tech pool and inflating the `>= 3` threshold that decides whether
the filter applies at all.

Matching now uses a LEADING word boundary (`\bkeyword`). A trailing boundary was
rejected: several keywords are deliberate prefix stems (`acqui`, `econom`,
`bankrupt`, `unemploy`, `invest`) that both-sided boundaries kill entirely.
Residual accepted: `ai` still matches "Aid"/"Air"/"Aim".

**Expect the Tech slot to become stricter than it was in production.**

### 3. Window cutoff is `today - days`

The plan's `_window` code and its own test disagreed. Resolved toward this
spec's "within 2 days" language: a 2-day block covers the two prior days, and
the 21-day literal window covers 21 days, matching `save`'s pruning. The
alternative (`days - 1`) silently narrowed the literal-repeat window to 20 days.

### 4. Raw pool cap is 60, not 20

The plan truncated the feed to 20 rows BEFORE filtering; pre-branch production
filtered the whole feed and then took 15. The plan's version was a strict
reduction that hit hardest on the slot with the most new rejection logic
(Macro measured 5 surviving candidates against `MAX_RESOLVE_TRIES=6`).
`RAW_POOL_CAP = 60` restores production-equivalent depth: Macro 5 -> 12,
Sports 1 -> 7. `dry_run` caps both arms at 15 so the A/B stays comparable.

### 5. In-run duplicate-story guard for fallback picks

The spec's cross-slot diversity covered the model's picks but not fallbacks:
a fallback carries no `storyKey`, so only URL matching applied, and two slots
could email the same event under different URLs. `is_claimable` now also
rejects a candidate whose normalized title was already claimed THIS run
(reason `dup-story`). History semantics are unchanged -- a `None` storyKey still
never blocks on history grounds, per this spec's error-handling section.

Catches the same headline at different URLs, which is the reproduced case. It
does not attempt semantic matching of the same story under different headlines.

### Known follow-ups, not addressed here

- **Op-Ed has no quality gate.** It has no keywords, so only noise and wrap
  rejection apply; with the wider pool it saturates and is purely
  recency-ranked. Pre-existing, surfaced by delta 4. Worth a rubric change.
- **The wrap regex fires on 11 of 80 historical picks**, vs the ~8 targeted.
  Two oil-geopolitics headlines are arguable false positives. A banned wrap
  costs nothing; a banned real story costs an article. Watch the first live runs.
- `generate.py` is 405 lines against a 400-line guideline. Splitting it now
  would create a circular import; the step-C source-adapter work restructures
  this file anyway.
