# Multi-source expansion (GS / JPM / MS) — design

**Date:** 2026-08-08
**Status:** Approved, not yet implemented
**Scope:** Step C of a four-step roadmap (A: sender change — merged;
B: pick algorithm + Sports slot — implemented on `feat/pick-algorithm`;
**C: expand beyond WSJ**; D: Google Sheets consumption catalog).

**Depends on:** B being merged to `main` first. C moves `generate.py` code that
B is still amending; branching C from an unmerged B guarantees conflicts in the
one file both touch.

## Goal

Add a second section to the daily email listing everything published by Goldman
Sachs, J.P. Morgan, and Morgan Stanley since the previous digest. Exhaustive,
not curated: the reader wants the full list, not an editor's pick.

## What the sources actually support

Every claim below was verified by probing the live sources on 2026-08-07, not
assumed. The findings materially shaped the design.

### Podcasts: available, but not from the obvious place

All four shows publish public RSS, but **the GS and MS feeds contain no `<link>`
element at all** — only `.mp3` enclosure URLs. The requirement is "provide the
link", so the feed alone cannot satisfy it. Constructing firm-site URLs from
episode titles was tested and fails: every candidate URL 404s, and the MS site's
own slugs embed guest names and years that titles do not contain.

**Apple's lookup API solves both problems at once.** It returns a clean ISO
`releaseDate` and a stable per-episode `trackViewUrl`, over unauthenticated
HTTPS JSON, for every show:

| Firm | Show | iTunes ID | Cadence |
|---|---|---|---|
| Morgan Stanley | Thoughts on the Market | `1466686717` | daily |
| Goldman Sachs | Exchanges | `948913991` | ~2×/week |
| J.P. Morgan | Making Sense | `1456184829` | ~weekly |
| J.P. Morgan | Eye on the Market | `1367963156` | ~monthly |

Endpoint: `https://itunes.apple.com/lookup?id=<id>&entity=podcastEpisode&limit=10`.
Four calls per run, far inside the ~20 req/min rate limit. `limit=10` gives ample
headroom over the trailing-24h window even for the daily show, while keeping
responses small; the first result is the collection itself and is discarded
(filter on `wrapperType == "podcastEpisode"`).

Podcast `summary` comes from the episode `description` field, `duration` from
`trackTimeMillis` rendered as whole minutes.

### Written research: one source, not three

- **JPM Top Market Takeaways** — no feed, but the listing is server-rendered
  (8–9 links) and each article page carries `<meta name="publishDate">`. Usable
  at the cost of one fetch per previously-unseen URL, typically 0–1 per day.
  The same page supplies the title via `<title>` and the summary via
  `<meta name="description">` (verified present; `og:description` and
  `twitter:description` are absent, so `description` is the only source).
  `duration` is `None` for written items.
- **GS Insights** — listing pages are fully JavaScript-hydrated and serve
  **zero** links. Reaching them needs a headless browser, which would break the
  stdlib-plus-curl property that keeps the daily job reliable and add ~300MB of
  Chromium to CI. **Excluded.** GS is represented by its podcast only.
- **MS Ideas** — no feed; the podcast covers MS adequately. **Excluded.**

### Volume is the binding constraint

Measured across three weeks of real feed data: **1.6 items per weekday, maximum
3, zero on weekends.** The requested "3–5 items" is not achievable — it is a
supply limit, not an engineering one. Adding Eye on the Market and Top Market
Takeaways lifts the expectation to roughly 2.3 per weekday.

### The window cannot be "that day"

Publish times, in ET:

| Show | Publishes | vs the 09:00 ET send |
|---|---|---|
| MS Thoughts on the Market | **16:00–17:30** | 7–8 hours *after* |
| GS Exchanges | 00:00–10:39 | partly after generation |
| JPM Making Sense | 01:00–12:46 | mixed |

Generation runs 04:17–07:17 ET. A strict same-day filter evaluated then can
**never** include Morgan Stanley, the only daily publisher. Measured at the
generation moment: same-day yields 0.4 items/weekday and is empty on 7 of 11
weekdays; a trailing 24h window yields 1.5 and is empty on 2 of 11.

**Decision: the window is the trailing 24 hours from generation time** —
"everything published since the last digest", which is what a morning digest
means to its reader.

## Decisions

| Area | Decision |
|---|---|
| Sources | 4 podcasts via Apple lookup + JPM Top Market Takeaways via scrape |
| GS written insights | Excluded — JS-hydrated, needs a headless browser |
| Window | Trailing 24h from generation, not calendar day |
| Curation | None. Exhaustive listing, no model call for section 2 |
| Summaries | Publisher's own episode description, truncated |
| Podcast links | Apple Podcasts episode page |
| Ordering | Reverse-chronological, newest first |
| Empty days | Render the header plus "No GS/JPM/MS publications today" |
| Email | WSJ five unchanged, then the new section |
| Section heading | **"Street Research"** — placeholder pending the operator's preference; change is one string in `mailer.gs` |

## Architecture

This is where `generate.py` finally splits — against a real seam (a second and
third I/O protocol) rather than a guessed one.

```
wsjdaily/
├── slots.py, filters.py, history.py    # unchanged from step B
└── sources/
    ├── __init__.py      # Item dataclass + the SOURCES registry
    ├── wsj.py           # MOVED from generate.py: fetch + batchexecute resolver
    ├── apple.py         # one generic adapter; four shows are config
    └── jpm_web.py       # Top Market Takeaways listing + per-article date
generate.py              # orchestration only; ~405 lines today, ~150 after
```

Every adapter exposes one function, `fetch(now: datetime) -> list[Item]`.
Adapters never write files, never call the model, and never import each other.
Four podcast shows share **one** adapter differing only by ID, so adding a show
later is a one-line config entry.

```python
@dataclass(frozen=True)
class Item:
    firm: str          # "Goldman Sachs" | "J.P. Morgan" | "Morgan Stanley"
    show: str | None   # show name; None for written research
    title: str
    url: str
    published: datetime.datetime   # MUST be timezone-aware
    kind: str          # "podcast" | "article"
    duration: str | None
    summary: str       # publisher-written, truncated
```

## Pipeline

Section 1 (WSJ) is untouched: same five slots, same filters, same single
curated API call, same resolver.

Section 2 never calls the model:

1. `window_start = generation_time - 24h`
2. Each adapter fetches; failures are caught per-adapter and yield `[]`
3. Keep items where `window_start < published <= now`
4. Drop URLs already emitted on a previous run
5. Sort reverse-chronologically
6. Truncate publisher summaries

### Why overlap dedup is required

Consecutive runs' 24h windows can overlap by several hours when GitHub's
scheduler drifts (observed lag: 45–110 min). Without a URL-seen check the same
episode appears on two consecutive days.

Emitted `research` URLs are recorded in `history.json` under a separate
top-level key, so the existing per-date pick structure is untouched:

```json
{
  "2026-08-07": [ {"title": "…", "url": "…", "storyKey": "…"} ],
  "_research":  { "2026-08-07": ["https://podcasts.apple.com/…", "…"] }
}
```

**This requires a one-line guard in `history._window`, and the naming alone is
not sufficient.** Verified: `"_research" < "2026-07-17"` evaluates to `False`
because `_` (0x5F) sorts after the digits, so the key passes the cutoff test,
lands inside the window, and the loop then iterates the inner dict — yielding
strings, and raising `AttributeError: 'str' object has no attribute 'get'` in
both `prior_keys` and `blocked_story_keys`. That would take down the WSJ
section, which this design forbids.

`_window` must therefore skip any key that is not date-shaped:

```python
for day, items in hist.items():
    if not _DATE_RE.match(day):        # new: ignore non-date bookkeeping keys
        continue
    if day == today or day < cutoff:
        continue
```

with `_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")`. This also hardens the
function against any future top-level key. A regression test must assert that
a history containing `_research` leaves `prior_keys` and `blocked_story_keys`
working.

`history.save` prunes `_research` on the same 21-day cutoff as the pick history.
Existing history files without the key degrade to an empty dict.

Storyline keys from step B are deliberately **not** reused here. These are five
known publishers, not a fuzzy news feed; two firms will not publish the same
episode. A URL check is sufficient, and storyline machinery would be complexity
without a matching problem.

## Data changes

`picks.json` gains one additive key:

```json
{ "date": "…", "generatedAt": "…", "picks": [ … ], "research": [ … ] }
```

`picks` keeps its exact existing shape, so an un-updated `mailer.gs` continues
sending the WSJ five and silently ignores `research`. The Apps Script redeploy
is therefore non-urgent and rollback is trivial.

`mailer.gs` gains a second section. **This requires the operator to re-paste it
into Apps Script and re-run `sendTestNow`** — the first mailer change since the
sender migration.

## Error handling

**The governing rule: section 2 must never break section 1.** Five new network
dependencies now touch a job that emails daily; none may jeopardize the WSJ
digest that already works.

- **Every adapter is wrapped individually.** A failure logs and yields `[]`.
  One dead source costs its own items, never the run.
- **Section 2 never triggers `sys.exit(1)`.** That guard stays scoped to WSJ
  picks, where it signals a CAPTCHA-blocked runner and forces a retry. An empty
  `research` list is a normal Saturday, and must never suppress a good digest.
- **Apple rate limiting** (~20 req/min) is treated as an empty fetch, not an
  exception. Four calls per run is far inside the limit.
- **`jpm_web.py` fails closed.** It is the only HTML scrape and depends on a
  `<meta name="publishDate">` tag JPM can remove in any redesign. No parseable
  date means the item is skipped — never emitted with a guessed date. If JPM
  restructures, that one source quietly returns nothing and everything else
  keeps working.
- **Timezone discipline.** `Item.published` is validated timezone-aware at
  construction. This is a class of bug, not a one-off: the GS and MS RSS feeds
  stamp `-0000`, which makes `email.utils.parsedate_to_datetime` return a
  *naive* datetime, and the existing `(now - dt)` arithmetic in `generate.py`
  raises `TypeError` on naive input. Using Apple's ISO `releaseDate` avoids it
  for podcasts; the validation catches any future source that reintroduces it.

## Testing

Adapters are pure parsers over fixtures — real captured Apple JSON and real JPM
HTML committed to `tests/fixtures/`. No network in the suite, matching step B.

- **Parser tests** — each adapter against its fixture: correct item count,
  titles, timezone-aware dates, non-empty URLs.
- **The `-0000` regression** — a naive RSS-style date must not crash and must
  not be silently treated as local time.
- **Window boundaries** — items at 23h59m and 24h01m before generation land on
  the correct sides.
- **Overlap dedup** — the same episode across two runs with overlapping windows
  appears exactly once.
- **Failure isolation** — an adapter that raises yields `[]` while the other
  four still return. This encodes the governing rule and is the most important
  test in the suite.
- **Weekend case** — all adapters empty produces a valid `picks.json` with
  `research: []` and exit code 0.

Target 80% coverage on `wsjdaily/sources/`.

## Rollout

1. Merge B to `main` first. Branch C from merged `main`.
2. Build adapters bottom-up, each with fixtures, before wiring into
   `generate.py`.
3. Verify with `python3 generate.py --dry-run`, which gains a section-2 preview
   showing what `research` would contain and which adapters failed.
4. Merge, then update `mailer.gs` and re-run `sendTestNow`.

Rollback is a `git revert` plus leaving `mailer.gs` alone — the additive schema
means the old mailer keeps working against new `picks.json`.

## Known limitations

- **~2.3 items per weekday, zero on weekends.** A supply limit of what these
  firms publish, not something the implementation can improve.
- **Podcast links point to Apple Podcasts**, not the firms' own sites. No
  reliable path to firm-hosted episode pages exists; both URL construction and
  slug matching were tested and failed.
- **Goldman contributes only its podcast.** Its written insights need a
  headless browser to reach.
- **JPM Top Market Takeaways has no listing dates** — freshness requires
  fetching each new article page.
