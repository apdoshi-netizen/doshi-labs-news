# Pick Algorithm Refinement Implementation Plan

> **STATUS: EXECUTED.** This plan was implemented on branch `feat/pick-algorithm`
> (17 commits, 81 tests). **Do not re-execute it, and do not copy code from it.**
> Four snippets below are known-defective — they were caught in review and fixed
> during implementation, but the text here was left as written so the review
> history makes sense. The shipped code is the authority; the "As-built deltas"
> section of `docs/superpowers/specs/2026-08-07-pick-algorithm-design.md` records
> every divergence.
>
> Known-defective snippets in this document:
> - **Task 3**, `apply_keyword_filter`: uses substring containment (`k in title`).
>   Ships as a leading word-boundary regex — substring lets `ai` match "Sailing".
> - **Task 5**, `_window`: its cutoff and its own test contradict each other.
>   Ships as `cutoff = today - days`.
> - **Task 6**, `fetch_candidates`: caps the feed at `rows[:20]` before filtering.
>   Ships as `RAW_POOL_CAP = 60`, filtering the full feed as production did.
> - **Task 8**, `dry_run`: its reindex loop aliases shared dicts and produces
>   duplicate ids. Ships with independent per-pool copies.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily digest reliably recent *and* relevant by filtering out market-wrap wire copy, tiering candidates by freshness, blocking storyline repeats, enforcing cross-slot diversity, and adding a fifth WSJ Sports slot.

**Architecture:** Deterministic pre-filter feeding one enriched model call. Pure, testable Python (`wsjdaily/`) handles wrap detection, keyword gating, recency tiering, and storyline identity; the model receives a pre-cleaned, pre-tiered pool plus an already-covered list and returns all five picks in a single API call. Fetch, curate, and resolve stay in `generate.py`.

**Tech Stack:** Python 3.11 stdlib only (no runtime dependencies — `curl` via `subprocess` for all HTTP), pytest for tests, GitHub Actions for CI.

**Spec:** `docs/superpowers/specs/2026-08-07-pick-algorithm-design.md`

## Global Constraints

- **No new runtime dependencies.** `generate.py` runs on a bare GitHub runner with `curl`. pytest is dev-only, never imported by runtime code.
- **Python 3.11** (pinned in `.github/workflows/daily.yml`).
- **All HTTP goes through the existing `curl()` helper.** Never `urllib`/`requests` — the curl invocation with `-L` and `Cookie: CONSENT=YES+` is load-bearing (HANDOFF gotcha #3).
- **Model reply stays pipe-delimited and line-based**, never JSON (HANDOFF gotcha #5).
- **Extract the first `type=="text"` content block**, never `content[0]` (HANDOFF gotcha #6).
- **Zero resolved slots → write nothing and `exit 1`.** Non-negotiable; it is the blocked-runner-IP signal (HANDOFF gotcha #2).
- **`picks.json` schema is additive only.** `mailer.gs` reads `label`, `title`, `url`, `summary`; never rename or remove those.
- **Model:** `claude-sonnet-5`. One API call per live run.
- **Immutability:** filters return new lists; never mutate the caller's rows in place.
- **Type annotations on all function signatures**; `ruff`-clean; lines ≤ 100 chars.
- Files stay under 400 lines.

---

### Task 1: Package scaffold, slot definitions, and the Sports slot

**Files:**
- Create: `wsjdaily/__init__.py`
- Create: `wsjdaily/slots.py`
- Create: `tests/__init__.py`
- Create: `tests/test_slots.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `Slot` frozen dataclass with fields `key: str`, `query: str`, `max_age_hrs: int`, `keywords: tuple[str, ...] | None`, `reject_market_wraps: bool`, `keyword_fallback: bool`. Module constants `SLOTS: tuple[Slot, ...]`, `CANONICAL_ORDER: tuple[str, ...]`, `RESOLVE_ORDER: tuple[str, ...]`, and `by_key(key: str) -> Slot`.

- [ ] **Step 1: Create the dev requirements file**

```
# requirements-dev.txt — test tooling only. Runtime has zero dependencies.
pytest>=8.0
```

Install it: `python3 -m pip install -r requirements-dev.txt`

- [ ] **Step 2: Write the failing test**

Create `tests/__init__.py` as an empty file, then `tests/test_slots.py`:

```python
"""Slot configuration invariants."""
import pytest

from wsjdaily.slots import CANONICAL_ORDER, RESOLVE_ORDER, SLOTS, by_key


def test_five_slots_in_canonical_email_order() -> None:
    assert CANONICAL_ORDER == (
        "Macro",
        "Industry / Company / Transaction",
        "Op-Ed",
        "Tech",
        "Sports",
    )


def test_resolve_order_puts_sports_before_industry() -> None:
    """Sports outranks Industry on a shared story, so it resolves first."""
    assert RESOLVE_ORDER.index("Sports") < RESOLVE_ORDER.index("Industry / Company / Transaction")
    assert sorted(RESOLVE_ORDER) == sorted(CANONICAL_ORDER)


def test_market_wrap_rejection_is_macro_only() -> None:
    """'Mitie Shares Soar on $4.2 Billion Takeover' is a legitimate Industry pick."""
    assert by_key("Macro").reject_market_wraps is True
    assert [s.key for s in SLOTS if s.reject_market_wraps] == ["Macro"]


def test_sports_is_the_only_slot_with_keyword_fallback() -> None:
    """Sports prefers business stories but falls back to the top headline."""
    assert by_key("Sports").keyword_fallback is True
    assert [s.key for s in SLOTS if s.keyword_fallback] == ["Sports"]


def test_op_ed_accepts_any_title() -> None:
    assert by_key("Op-Ed").keywords is None


def test_by_key_raises_on_unknown_slot() -> None:
    with pytest.raises(KeyError):
        by_key("Weather")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_slots.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wsjdaily'`

- [ ] **Step 4: Write the implementation**

Create `wsjdaily/__init__.py` as an empty file, then `wsjdaily/slots.py`:

```python
"""Slot definitions for the daily digest.

Configuration data only, no logic. `filters` and `generate` consume these.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    """One section of the daily email."""

    key: str                            # canonical name; also the email label
    query: str                          # Google News RSS search query
    max_age_hrs: int                    # hard cutoff for candidate age
    keywords: tuple[str, ...] | None    # title filter; None accepts anything
    reject_market_wraps: bool = False   # drop daily price-move wire copy
    keyword_fallback: bool = False      # keep whole pool when nothing matches


MACRO_KEYWORDS = (
    "econom", "inflation", "fed", "rate", "jobs", "unemploy", "gdp", "tariff",
    "trade", "treasury", "yield", "bond", "central bank", "dollar", "currency",
    "recession", "growth", "prices", "oil", "stimulus", "deficit",
)

INDUSTRY_KEYWORDS = (
    "merger", "acqui", "deal", "takeover", "ipo", "bankrupt", "buyout", "bid",
    "billion", "million", "stake", "shares", "earnings", "profit", "revenue",
    "invest", "fund", "raise", "spinoff", "sells", "buys", "to buy",
)

TECH_KEYWORDS = (
    "ai", "artificial intelligence", "chip", "semiconductor", "software",
    "tech", "nvidia", "apple", "google", "microsoft", "openai", "meta",
    "amazon", "tesla", "intel", "amd", "tsmc", "data center", "cloud", "cyber",
    "robot", "quantum", "startup", "app", "internet", "silicon",
)

# Sports BUSINESS terms. When none match, the Sports slot keeps its whole pool
# and falls back to the day's top headline (keyword_fallback=True).
SPORTS_KEYWORDS = (
    "valuation", "stake", "sale", "sells", "buys", "acqui", "investor",
    "private equity", "media rights", "broadcast", "streaming rights",
    "sponsorship", "revenue", "billion", "million", "franchise", "owner",
    "betting", "sportsbook", "salary cap", "collective bargaining", "lockout",
    "stadium", "arena", "expansion fee", "ipo", "fund", "deal", "contract",
)

SLOTS: tuple[Slot, ...] = (
    Slot(
        key="Macro",
        query=(
            '(economy OR inflation OR "Federal Reserve" OR "interest rates" OR jobs '
            'OR GDP OR tariffs OR Treasury OR "central bank") site:wsj.com when:3d'
        ),
        max_age_hrs=72,
        keywords=MACRO_KEYWORDS,
        reject_market_wraps=True,
    ),
    Slot(
        key="Industry / Company / Transaction",
        query=(
            "(merger OR acquisition OR deal OR earnings OR takeover OR IPO OR "
            "bankruptcy OR buyout) site:wsj.com when:3d"
        ),
        max_age_hrs=72,
        keywords=INDUSTRY_KEYWORDS,
    ),
    Slot(key="Op-Ed", query="site:wsj.com/opinion when:4d", max_age_hrs=96, keywords=None),
    Slot(key="Tech", query="site:wsj.com/tech when:2d", max_age_hrs=48, keywords=TECH_KEYWORDS),
    Slot(
        key="Sports",
        query="site:wsj.com/sports when:3d",
        max_age_hrs=72,
        keywords=SPORTS_KEYWORDS,
        keyword_fallback=True,
    ),
)

# Order the email renders in.
CANONICAL_ORDER: tuple[str, ...] = tuple(s.key for s in SLOTS)

# Order slots are RESOLVED in. The first slot to claim a story keeps it, so
# Sports precedes Industry: a sports-business deal lands in Sports and Industry
# advances to its next candidate.
RESOLVE_ORDER: tuple[str, ...] = (
    "Macro",
    "Sports",
    "Industry / Company / Transaction",
    "Op-Ed",
    "Tech",
)

_BY_KEY = {s.key: s for s in SLOTS}


def by_key(key: str) -> Slot:
    """Look up a slot by its canonical name. Raises KeyError if unknown."""
    return _BY_KEY[key]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_slots.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add wsjdaily/ tests/ requirements-dev.txt
git commit -m "feat: add wsjdaily package with 5-slot config incl. Sports"
```

---

### Task 2: Market-wrap rejection

**Files:**
- Create: `wsjdaily/filters.py`
- Create: `tests/test_filters_wrap.py`

**Interfaces:**
- Consumes: `wsjdaily.slots.Slot`.
- Produces: `is_market_wrap(title: str) -> bool`, `is_noise(title: str) -> bool`, and module constants `NOISE`, `MARKET_WRAP` (compiled patterns).

**Context:** The regex below was validated against the real corpus before this plan was written: all 10 wrap headlines in `history.json` reject, all 9 substantive Macro headlines survive. It matches the wire-roundup *sentence shape* (a market-state subject in the first three words, followed within ~40 characters by a price-move verb), not subject matter — so "U.S. Import Prices Unexpectedly Rise in June" survives because `prices` is deliberately **not** a subject token. Do not add `prices`, `rates`, or `market` to `_SUBJ`; each one breaks a verified must-survive case.

- [ ] **Step 1: Write the failing test**

```python
"""Market-wrap detection, tuned against the real 2026-07-19..2026-08-07 corpus."""
import pytest

from wsjdaily.filters import is_market_wrap, is_noise

# Every daily price-move roundup picked for Macro in the last 20 days.
WRAPS = [
    "Oil Eases as Mediators Push for New U.S.-Iran Ceasefire",
    "Global Bond Yields Jump as Oil Prices Surge, Inflation Fears Mount",
    "Treasury Yields Fall as U.S.-Iran Hostilities Take a Break",
    "U.S. Treasury Yields Fall Amid Mideast Hopes; Dollar Rises Ahead of Fed",
    "Oil Surges as Fresh Middle East Strikes Threaten Fragile Diplomacy",
    "U.S. Treasury Yields Soar as Market Struggles to Interpret Fed",
    "Treasury Yields Rise in Month That Saw Inflation Fears Revive",
    "Treasury Yields, Dollar Fall as Talks to Reopen Hormuz Are Set to Restart",
    "U.S. Treasury Yields Rise, Dollar Firm as Oil Prices Increase",
    "Chip Stocks Weaken, Oil Steady as Investors Await Hormuz Progress",
]

# Substantive Macro stories from the same window. A false positive here costs
# a real article, which is the expensive failure mode.
KEEPERS = [
    "U.S. Economic Growth Slowed to 1.5% in Second Quarter",
    "Three Fed Officials Say Inflation Should Have Prompted Higher Rates",
    "Trump’s Tariffs Enter New Phase, Ending Months of Calm",
    "U.S. Import Prices Unexpectedly Rise in June",
    "Why Bessent Is Leaning on the Fed to Help Prop Up Japan’s Currency",
    "What Trump’s Latest Tariffs Mean for the American Economy",
    "Trump Unveils New Tariffs Designed to Withstand Legal Scrutiny",
    "ECB to Hold Rates Steady as Rebound in Energy Prices Threatens to Revive Inflation",
    "Exclusive | Trump Has Called Warsh Repeatedly Since He Became Fed Chair",
]


@pytest.mark.parametrize("title", WRAPS)
def test_rejects_daily_market_wraps(title: str) -> None:
    assert is_market_wrap(title) is True


@pytest.mark.parametrize("title", KEEPERS)
def test_keeps_substantive_macro_stories(title: str) -> None:
    assert is_market_wrap(title) is False


def test_deal_story_matches_the_wrap_shape_hence_macro_only_scoping() -> None:
    """A real Industry pick that matches the wrap SHAPE.

    This is exactly why wrap rejection is gated behind Slot.reject_market_wraps
    instead of being applied to every slot: globally, this filter would discard
    a legitimate $4.2B takeover story. Asserted here so the coupling is
    deliberate and visible rather than an accident.
    """
    assert is_market_wrap("Mitie Shares Soar on $4.2 Billion Takeover by OCS") is True


def test_noise_matches_existing_junk_categories() -> None:
    assert is_noise("Roundup: Market Talk") is True
    assert is_noise("WSJ Dollar Index") is True
    assert is_noise("Apollo to Buy European Budget Airline easyJet for $7.7 Billion") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_filters_wrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wsjdaily.filters'`

- [ ] **Step 3: Write the implementation**

Create `wsjdaily/filters.py`:

```python
"""Pure candidate filters: noise, market-wrap rejection, keyword gating, tiering.

Every function here is deterministic and side-effect free, so the whole module
is testable without an API key or network access.
"""
import re

# Non-article junk the Google News feed returns.
NOISE = re.compile(
    r"(Print Edition|News Archive|Exchange Rate|Roundup: Market Talk|"
    r"What to Read|WSJ Dollar Index|Latest News and Forecasts)",
    re.I,
)

# Daily price-move wire copy. Matches the SHAPE of a wrap headline: a
# market-state subject within the first three words, then a price-move verb
# within ~40 characters. Deliberately excludes "prices", "rates", and "market"
# as subjects -- each would swallow a verified must-survive headline such as
# "U.S. Import Prices Unexpectedly Rise in June".
_SUBJ = (
    r"(?:treasur(?:y|ys|ies)|yields?|stocks?|shares|oil|crude|dollar|bonds?|"
    r"futures|gold|yen|euro)"
)
_VERB = (
    r"(?:rise|rises|rose|fall|falls|fell|slip|slips|climb|climbs|ease|eases|"
    r"steady|firm|firms|weaken|weakens|jump|jumps|surge|surges|soar|soars|"
    r"sink|sinks|tumble|tumbles|rally|rallies|slide|slides|edge|edges|gain|"
    r"gains|drop|drops|dip|dips|advance|advances|retreat|retreats|mixed|"
    r"higher|lower)"
)
MARKET_WRAP = re.compile(
    r"^(?:[\w.'’\-]+\s+){0,2}" + _SUBJ + r"\b.{0,40}?\b" + _VERB + r"\b",
    re.I,
)


def is_noise(title: str) -> bool:
    """True for non-article junk categories."""
    return bool(NOISE.search(title))


def is_market_wrap(title: str) -> bool:
    """True for daily price-move roundups.

    Only applied to slots with `reject_market_wraps=True` (Macro). Applied
    globally it would reject legitimate deal stories such as
    "Mitie Shares Soar on $4.2 Billion Takeover by OCS".
    """
    return bool(MARKET_WRAP.search(title))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_filters_wrap.py -v`
Expected: 21 passed (10 wraps + 9 keepers + 1 shape-collision + 1 noise)

- [ ] **Step 5: Commit**

```bash
git add wsjdaily/filters.py tests/test_filters_wrap.py
git commit -m "feat: reject daily market-wrap wire copy from the Macro slot"
```

---

### Task 3: Keyword gating with the Sports fallback

**Files:**
- Modify: `wsjdaily/filters.py` (append)
- Create: `tests/test_filters_keywords.py`

**Interfaces:**
- Consumes: `Slot` from Task 1, `is_noise`/`is_market_wrap` from Task 2.
- Produces: `apply_keyword_filter(slot: Slot, rows: list[dict]) -> list[dict]` and `reject(slot: Slot, rows: list[dict]) -> list[dict]`. A `row` is `{"title": str, "ageHrs": float, "url": str}`; both functions return new lists and never mutate input.

**Context:** The current rule keeps the keyword-matching subset only when at least 3 items survive, otherwise it discards the filter entirely. Sports needs the inverse: keep the matching subset whenever it is non-empty, and keep everything when it is empty. That is the "top sports headline" fallback from the spec, and the reason the naive rule would starve the slot on light days.

- [ ] **Step 1: Write the failing test**

```python
"""Keyword gating, including the Sports slot's deliberate fallback."""
from wsjdaily.filters import apply_keyword_filter, reject
from wsjdaily.slots import by_key


def row(title: str, age: float = 5.0) -> dict:
    return {"title": title, "ageHrs": age, "url": "https://news.google.com/x"}


def test_op_ed_accepts_everything_since_it_has_no_keywords() -> None:
    rows = [row("Opinion | Milton Friedman Was Right"), row("Opinion | The Everything Tax")]
    assert apply_keyword_filter(by_key("Op-Ed"), rows) == rows


def test_tech_keeps_matching_subset_when_at_least_three_match() -> None:
    rows = [
        row("Meta Releases Coding Agent to Compete With OpenAI"),
        row("SK Hynix to Invest $38 Billion on Chip Production"),
        row("Google Fined $1 Billion Under EU Antitrust Rules"),
        row("A Fine Day for Sailing on the Chesapeake"),
    ]
    kept = apply_keyword_filter(by_key("Tech"), rows)
    assert len(kept) == 3
    assert all("Sailing" not in r["title"] for r in kept)


def test_tech_keeps_full_pool_when_too_few_match() -> None:
    """Existing behaviour: don't over-filter a thin pool into nothing."""
    rows = [row("A Fine Day for Sailing"), row("Nvidia Ships New Chip")]
    assert apply_keyword_filter(by_key("Tech"), rows) == rows


def test_sports_keeps_business_stories_even_when_only_one_matches() -> None:
    """Sports prefers business; one match is enough to filter on."""
    rows = [
        row("Knicks Beat Celtics in Overtime Thriller"),
        row("Silver Lake Buys Stake in Serie A for $2 Billion"),
        row("Marathon Runner Sets Course Record"),
    ]
    kept = apply_keyword_filter(by_key("Sports"), rows)
    assert [r["title"] for r in kept] == ["Silver Lake Buys Stake in Serie A for $2 Billion"]


def test_sports_falls_back_to_whole_pool_when_no_business_story_exists() -> None:
    """The top-headline fallback. Must not starve the slot."""
    rows = [row("Knicks Beat Celtics in Overtime Thriller"), row("Marathon Runner Sets Record")]
    assert apply_keyword_filter(by_key("Sports"), rows) == rows


def test_reject_drops_wraps_only_for_macro() -> None:
    wrap = row("Treasury Yields Fall as U.S.-Iran Hostilities Take a Break")
    real = row("U.S. Economic Growth Slowed to 1.5% in Second Quarter")
    assert [r["title"] for r in reject(by_key("Macro"), [wrap, real])] == [real["title"]]


def test_reject_keeps_share_price_deal_stories_in_the_industry_slot() -> None:
    deal = row("Mitie Shares Soar on $4.2 Billion Takeover by OCS")
    assert reject(by_key("Industry / Company / Transaction"), [deal]) == [deal]


def test_reject_drops_noise_and_opinion_prefix_outside_op_ed() -> None:
    rows = [
        row("Roundup: Market Talk"),
        row("Opinion | The Everything Tax"),
        row("Apollo to Buy easyJet for $7.7 Billion"),
    ]
    kept = reject(by_key("Industry / Company / Transaction"), rows)
    assert [r["title"] for r in kept] == ["Apollo to Buy easyJet for $7.7 Billion"]


def test_reject_keeps_opinion_prefix_inside_op_ed() -> None:
    rows = [row("Opinion | The Everything Tax")]
    assert reject(by_key("Op-Ed"), rows) == rows


def test_reject_does_not_mutate_input() -> None:
    rows = [row("Roundup: Market Talk"), row("Apollo to Buy easyJet")]
    before = list(rows)
    reject(by_key("Industry / Company / Transaction"), rows)
    assert rows == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_filters_keywords.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_keyword_filter'`

- [ ] **Step 3: Write the implementation**

Append to `wsjdaily/filters.py`:

```python
MIN_KEYWORD_MATCHES = 3  # below this, a normal slot keeps its whole pool


def apply_keyword_filter(slot: Slot, rows: list[dict]) -> list[dict]:
    """Keep the keyword-matching subset of `rows`, or all of `rows`.

    Normal slots keep the subset only when at least MIN_KEYWORD_MATCHES survive,
    so a thin pool is not filtered down to nothing. Slots with
    `keyword_fallback` (Sports) keep the subset whenever it is non-empty and
    otherwise keep everything -- that is the top-headline fallback.
    """
    if not slot.keywords:
        return list(rows)
    matched = [r for r in rows if any(k in r["title"].lower() for k in slot.keywords)]
    if slot.keyword_fallback:
        return matched if matched else list(rows)
    return matched if len(matched) >= MIN_KEYWORD_MATCHES else list(rows)


def reject(slot: Slot, rows: list[dict]) -> list[dict]:
    """Drop noise, off-slot opinion pieces, and (for Macro) market wraps.

    Returns a new list; never mutates `rows`.
    """
    kept = []
    for r in rows:
        title = r["title"]
        if is_noise(title):
            continue
        # Opinion pieces belong only in the Op-Ed slot, which has no keywords.
        if slot.keywords and title.lower().startswith("opinion"):
            continue
        if slot.reject_market_wraps and is_market_wrap(title):
            continue
        kept.append(r)
    return apply_keyword_filter(slot, kept)
```

Add `from wsjdaily.slots import Slot` to the imports at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_filters_keywords.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add wsjdaily/filters.py tests/test_filters_keywords.py
git commit -m "feat: keyword gating with Sports top-headline fallback"
```

---

### Task 4: Recency tiering

**Files:**
- Modify: `wsjdaily/filters.py` (append)
- Create: `tests/test_filters_tier.py`

**Interfaces:**
- Consumes: rows from Task 3.
- Produces: `FRESH_MAX_HRS: int = 24` and `tier(rows: list[dict]) -> tuple[list[dict], list[dict]]` returning `(fresh, fallback)`.

- [ ] **Step 1: Write the failing test**

```python
"""Recency tiering: FRESH is <=24h, FALLBACK is everything older."""
from wsjdaily.filters import FRESH_MAX_HRS, tier


def row(title: str, age: float) -> dict:
    return {"title": title, "ageHrs": age, "url": "https://news.google.com/x"}


def test_splits_on_the_24_hour_boundary() -> None:
    fresh, fallback = tier([row("new", 23.9), row("old", 24.1)])
    assert [r["title"] for r in fresh] == ["new"]
    assert [r["title"] for r in fallback] == ["old"]


def test_exactly_24_hours_counts_as_fresh() -> None:
    fresh, fallback = tier([row("edge", float(FRESH_MAX_HRS))])
    assert [r["title"] for r in fresh] == ["edge"]
    assert fallback == []


def test_all_stale_slot_yields_empty_fresh_tier_without_crashing() -> None:
    fresh, fallback = tier([row("a", 50.0), row("b", 70.0)])
    assert fresh == []
    assert len(fallback) == 2


def test_empty_pool_yields_two_empty_tiers() -> None:
    assert tier([]) == ([], [])


def test_preserves_input_order_within_each_tier() -> None:
    fresh, _ = tier([row("first", 1.0), row("second", 2.0)])
    assert [r["title"] for r in fresh] == ["first", "second"]


def test_missing_age_is_treated_as_stale_not_fresh() -> None:
    """A malformed row must never be promoted into the fresh tier."""
    fresh, fallback = tier([{"title": "no age", "url": "u"}])
    assert fresh == []
    assert len(fallback) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_filters_tier.py -v`
Expected: FAIL with `ImportError: cannot import name 'FRESH_MAX_HRS'`

- [ ] **Step 3: Write the implementation**

Append to `wsjdaily/filters.py`:

```python
FRESH_MAX_HRS = 24  # candidates at or under this age are the preferred tier


def tier(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split candidates into (fresh, fallback) by age.

    The model is instructed to choose from `fresh` unless it is empty or every
    option in it fits the slot poorly. A row with no `ageHrs` is treated as
    stale so a malformed entry can never be promoted into the fresh tier.
    """
    fresh, fallback = [], []
    for r in rows:
        age = r.get("ageHrs")
        if age is not None and age <= FRESH_MAX_HRS:
            fresh.append(r)
        else:
            fallback.append(r)
    return fresh, fallback
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_filters_tier.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add wsjdaily/filters.py tests/test_filters_tier.py
git commit -m "feat: recency tiering with a 24h fresh threshold"
```

---

### Task 5: Storyline identity and coverage windows

**Files:**
- Create: `wsjdaily/history.py`
- Create: `tests/test_history.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: constants `HISTORY_DAYS = 21`, `STORY_HARD_BLOCK_DAYS = 2`, `STORY_SOFT_WINDOW_DAYS = 7`; functions `norm_title(t: str) -> str`, `norm_story_key(raw: str | None) -> str | None`, `load(path: str) -> dict`, `save(hist: dict, today: str, picks: list[dict], path: str) -> None`, `prior_keys(hist: dict, today: str) -> tuple[set[str], set[str]]`, `blocked_story_keys(hist: dict, today: str) -> set[str]`, `covered_story_keys(hist: dict, today: str) -> list[str]`.

**Context:** `norm_story_key` sorts its tokens so `"KKR Integer"` and `"integer, kkr"` produce the identical key `integer+kkr`. Hard block is ≤2 days (the observed KKR/Integer consecutive-day rehash); days 3–7 are surfaced to the model as context so it can judge whether a development is materially new. The 21-day literal title/URL dedup in `prior_keys` is unchanged from the current implementation.

- [ ] **Step 1: Write the failing test**

```python
"""Storyline identity, coverage windows, and history persistence."""
import json

from wsjdaily.history import (
    blocked_story_keys,
    covered_story_keys,
    load,
    norm_story_key,
    norm_title,
    prior_keys,
    save,
)


def test_norm_title_strips_prefixes_and_punctuation() -> None:
    assert norm_title("Exclusive | Trump Has Called Warsh") == norm_title("Trump Has Called Warsh")
    assert norm_title("Opinion | The Everything Tax") == norm_title("The Everything Tax")


def test_norm_story_key_is_order_independent() -> None:
    assert norm_story_key("KKR Integer") == "integer+kkr"
    assert norm_story_key("integer, KKR") == "integer+kkr"


def test_norm_story_key_deduplicates_and_caps_at_four_tokens() -> None:
    assert norm_story_key("kkr kkr integer") == "integer+kkr"
    assert len(norm_story_key("a b c d e f").split("+")) == 4


def test_norm_story_key_returns_none_for_empty_or_missing() -> None:
    assert norm_story_key(None) is None
    assert norm_story_key("") is None
    assert norm_story_key("   ") is None
    assert norm_story_key("!!! ???") is None


HIST = {
    "2026-08-05": [{"title": "Old Story", "url": "u1", "storyKey": "aaa+bbb"}],
    "2026-08-06": [{"title": "KKR Near Deal to Buy Integer", "url": "u2", "storyKey": "integer+kkr"}],
    "2026-08-07": [{"title": "Today Pick", "url": "u3", "storyKey": "zzz+yyy"}],
}


def test_hard_block_covers_two_days_and_ignores_today() -> None:
    blocked = blocked_story_keys(HIST, "2026-08-07")
    assert "integer+kkr" in blocked      # yesterday -> hard blocked
    assert "aaa+bbb" not in blocked      # 2 days back is outside the window
    assert "zzz+yyy" not in blocked      # today's own entry never blocks itself


def test_soft_window_surfaces_older_keys_for_model_judgment() -> None:
    covered = covered_story_keys(HIST, "2026-08-07")
    assert "aaa+bbb" in covered
    assert "integer+kkr" in covered
    assert "zzz+yyy" not in covered


def test_entries_without_a_story_key_are_skipped_not_crashed() -> None:
    hist = {"2026-08-06": [{"title": "Legacy Row", "url": "u"}]}
    assert blocked_story_keys(hist, "2026-08-07") == set()
    assert covered_story_keys(hist, "2026-08-07") == []


def test_prior_keys_ignores_today_and_respects_21_day_cutoff() -> None:
    titles, urls = prior_keys(HIST, "2026-08-07")
    assert norm_title("Old Story") in titles
    assert "u2" in urls
    assert "u3" not in urls  # today's own picks never exclude themselves


def test_save_writes_story_key_and_prunes_beyond_21_days(tmp_path) -> None:
    path = str(tmp_path / "history.json")
    hist = {"2026-01-01": [{"title": "Ancient", "url": "old", "storyKey": "x+y"}]}
    picks = [
        {"title": "New Pick", "url": "https://wsj.com/a", "storyKey": "apollo+easyjet"},
        {"title": "Empty Slot", "url": "", "storyKey": None},
    ]
    save(hist, "2026-08-07", picks, path)
    written = json.loads(open(path).read())
    assert "2026-01-01" not in written                       # pruned
    assert written["2026-08-07"] == [
        {"title": "New Pick", "url": "https://wsj.com/a", "storyKey": "apollo+easyjet"}
    ]                                                        # url-less pick omitted


def test_load_returns_empty_dict_when_file_is_missing(tmp_path) -> None:
    assert load(str(tmp_path / "nope.json")) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wsjdaily.history'`

- [ ] **Step 3: Write the implementation**

Create `wsjdaily/history.py`:

```python
"""History persistence and storyline identity.

`history.json` maps date -> [{title, url, storyKey}]. Two independent dedup
mechanisms read it:

  * literal repeats  -- normalized title or exact URL, 21-day window
  * storyline repeats -- storyKey, hard-blocked for 2 days and surfaced to the
    model for days 3-7 so it can judge whether a development is materially new
"""
import datetime
import json
import re

HISTORY_DAYS = 21             # literal title/URL repeat window
STORY_HARD_BLOCK_DAYS = 2     # storyKey match here is auto-rejected
STORY_SOFT_WINDOW_DAYS = 7    # storyKey match here is shown to the model
MAX_STORY_TOKENS = 4


def norm_title(t: str) -> str:
    """Normalize a headline for identity matching (ignores prefixes/punctuation)."""
    t = re.sub(r"^\s*(exclusive|opinion|analysis|review|live|updated)\s*\|\s*", "", t.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def norm_story_key(raw: str | None) -> str | None:
    """Canonicalize a storyline key to sorted, deduped, lowercase tokens.

    Sorting makes the key order-independent, so "KKR Integer" and "integer, kkr"
    both become "integer+kkr". Returns None when no usable token survives.
    """
    if not raw:
        return None
    tokens = sorted({t for t in re.split(r"[^a-z0-9]+", raw.lower()) if t})
    return "+".join(tokens[:MAX_STORY_TOKENS]) if tokens else None


def _window(hist: dict, today: str, days: int):
    """Yield entries from the `days` days before `today`, excluding today."""
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).isoformat()
    for day, items in hist.items():
        if day == today or day < cutoff:
            continue
        for item in items:
            yield item


def load(path: str = "history.json") -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save(hist: dict, today: str, picks: list[dict], path: str = "history.json") -> None:
    """Record today's picks and prune anything past the 21-day window."""
    hist = dict(hist)
    hist[today] = [
        {"title": p["title"], "url": p["url"], "storyKey": p.get("storyKey")}
        for p in picks
        if p.get("url")
    ]
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
    hist = {d: v for d, v in hist.items() if d >= cutoff}
    with open(path, "w") as f:
        json.dump(dict(sorted(hist.items())), f, indent=2, ensure_ascii=False)


def prior_keys(hist: dict, today: str) -> tuple[set[str], set[str]]:
    """Normalized titles and URLs picked on PREVIOUS days, 21-day window.

    Today's own entry is ignored so the day's later runs do not exclude their
    own earlier picks.
    """
    titles, urls = set(), set()
    for item in _window(hist, today, HISTORY_DAYS):
        titles.add(norm_title(item.get("title", "")))
        urls.add(item.get("url", ""))
    titles.discard("")
    urls.discard("")
    return titles, urls


def blocked_story_keys(hist: dict, today: str) -> set[str]:
    """Storylines covered in the last 2 days -- auto-rejected without asking."""
    return {
        key
        for item in _window(hist, today, STORY_HARD_BLOCK_DAYS)
        if (key := norm_story_key(item.get("storyKey")))
    }


def covered_story_keys(hist: dict, today: str) -> list[str]:
    """Storylines covered in the last 7 days -- shown to the model as context."""
    seen: list[str] = []
    for item in _window(hist, today, STORY_SOFT_WINDOW_DAYS):
        key = norm_story_key(item.get("storyKey"))
        if key and key not in seen:
            seen.append(key)
    return sorted(seen)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_history.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add wsjdaily/history.py tests/test_history.py
git commit -m "feat: storyline keys with 2-day hard block and 7-day soft window"
```

---

### Task 6: Wire filters and history into generation

**Files:**
- Modify: `generate.py` (replaces `SLOTS`, `NOISE`, `norm_title`, `load_history`, `prior_keys`, `save_history`, `fetch_candidates`, `curate_with_claude`, `parse_selections`, `heuristic`)

**Interfaces:**
- Consumes: everything produced by Tasks 1–5.
- Produces: `parse_selections(text: str) -> list[dict]` where each selection is `{"slot": str, "i": int, "storyKey": str | None, "summary": str}`.

**Context:** The model reply gains a third field, becoming `slot|id|storykey|summary`. Three-field replies are still accepted with `storyKey=None` so a format regression degrades to today's behaviour rather than failing the run. The heuristic fallback must now consume the *filtered, tiered* pool — today it selects from raw candidates, so an API outage would silently reintroduce the market wraps this change bans.

- [ ] **Step 1: Replace the module header and delete the migrated code**

In `generate.py`, delete the `SLOTS` tuple, the `NOISE` regex, and the functions `norm_title`, `load_history`, `prior_keys`, `save_history`. Replace the import block with:

```python
import os, sys, re, json, subprocess, urllib.parse, email.utils, datetime
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from wsjdaily import filters, history
from wsjdaily.slots import CANONICAL_ORDER, RESOLVE_ORDER, SLOTS, by_key

MODEL = "claude-sonnet-5"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MAX_RESOLVE_TRIES = 6          # more grounds for rejection than before
BLOCKED_SECTIONS = {"pro", "podcasts"}
```

- [ ] **Step 2: Rewrite `fetch_candidates` to use the slot objects and filters**

```python
def fetch_candidates() -> dict:
    """Fetch and clean the candidate pool for every slot."""
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {}
    for slot in SLOTS:
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(slot.query) + "&hl=en-US&gl=US&ceid=US:en")
        try:
            root = ET.fromstring(curl([url]))
        except Exception:
            out[slot.key] = []
            continue
        rows, seen = [], set()
        for it in root.find("channel").findall("item"):
            if (it.findtext("source") or "").strip() != "WSJ":
                continue
            title = re.sub(r"\s*-\s*WSJ\s*$", "", (it.findtext("title") or "").strip())
            if not title or title in seen:
                continue
            try:
                dt = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:
                continue
            if (now - dt).total_seconds() > slot.max_age_hrs * 3600:
                continue
            seen.add(title)
            rows.append((dt, title, it.findtext("link")))
        rows.sort(reverse=True)
        cands = [
            {"i": 0, "title": t, "ageHrs": round((now - dt).total_seconds() / 3600, 1), "url": u}
            for dt, t, u in rows[:20]
        ]
        kept = filters.reject(slot, cands)[:15]
        for i, c in enumerate(kept):
            c["i"] = i
        out[slot.key] = kept
    return out
```

- [ ] **Step 3: Rewrite the prompt to carry tiers and covered storylines**

```python
def curate_with_claude(cands: dict, covered: list[str]) -> list[dict]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    tiered = {}
    for slot in SLOTS:
        fresh, fallback = filters.tier(cands.get(slot.key, []))
        tiered[slot.key] = {
            "fresh": [{"i": c["i"], "title": c["title"], "ageHrs": c["ageHrs"]} for c in fresh],
            "older": [{"i": c["i"], "title": c["title"], "ageHrs": c["ageHrs"]} for c in fallback],
        }

    rubric = (
        "Macro: the story with the broadest cross-asset, market-moving significance "
        "(central banks, major data prints like CPI/jobs/GDP, fiscal/tariff/policy). "
        "NOT daily price roundups, voter sentiment, or political color. "
        "Industry/Company/Transaction: one concrete corporate story -- a NAMED deal/M&A, "
        "financing, IPO, material earnings, or major regulatory/legal event, ideally with a "
        "dollar figure or named parties. NOT box-office, sports, lifestyle, or human interest. "
        "Op-Ed: one substantive argument column (prefer economics/business/policy). "
        "Tech: one consequential tech-industry development (AI, chips, big-tech strategy, "
        "major product, regulation, notable research). NOT gadget reviews or lifestyle-tech. "
        "Sports: PREFER sports business/finance/economics -- franchise sales and valuations, "
        "media-rights deals, private-equity or sovereign money in leagues, stadium financing, "
        "league/CBA economics, betting, athlete investing. If and only if the candidates "
        "contain no such story, pick the single most important sports headline instead.")

    user = (
        "Candidate WSJ headlines by slot. Each slot has a 'fresh' list (<=24h old) and an "
        "'older' list. ageHrs = hours old.\n"
        + json.dumps(tiered, ensure_ascii=False)
        + "\n\nStorylines already emailed in the last 7 days (do NOT repeat one unless the "
          "candidate is a materially NEW development, e.g. a deal closing or collapsing, not "
          "a restatement):\n" + json.dumps(covered, ensure_ascii=False)
        + "\n\nFor EACH slot pick the ONE headline that best fits that slot per the rubric.\n"
          "RECENCY: choose from 'fresh' unless it is empty or every option in it fits the slot "
          "poorly; only then fall back to 'older'.\n"
          "TOPICAL FIT comes first for every slot except Sports, whose fallback rule is in the "
          "rubric. A fresh but off-topic headline must NOT be chosen.\n"
          "DIVERSITY: the 5 picks must be 5 distinct stories about distinct subjects. Do not "
          "let two slots cover the same underlying event.\n"
          "Reply with EXACTLY five lines and nothing else, one per slot, each formatted:\n"
          "slot name|chosen id|storykey|summary of <=25 words grounded only in the headline\n"
          "storykey = 2-4 lowercase entity words joined by '+' identifying the underlying "
          "story, e.g. kkr+integer or apollo+easyjet.\n"
          "Slot names exactly: " + ", ".join(CANONICAL_ORDER) + ". "
          "If a slot has no candidates use id -1, storykey -, and summary: No WSJ pick today.")

    payload = json.dumps({
        "model": MODEL, "max_tokens": 1024,
        "system": "You are a financial news editor. Follow the rubric exactly.\n\n" + rubric,
        "messages": [{"role": "user", "content": user}],
    })
    resp = curl(["-H", "x-api-key: " + key, "-H", "anthropic-version: 2023-06-01",
                 "-H", "content-type: application/json", "--data", payload,
                 "https://api.anthropic.com/v1/messages"])
    try:
        data = json.loads(resp)
    except Exception:
        raise RuntimeError("non-JSON API response: " + resp[:400])
    if "content" not in data:
        raise RuntimeError("API error response: " + json.dumps(data)[:400])
    text = next((b.get("text") for b in data["content"] if b.get("type") == "text"), None)
    if not text:
        raise RuntimeError("no text block; content=" + json.dumps(data["content"])[:400])
    return parse_selections(text)
```

- [ ] **Step 4: Extend `parse_selections` to four fields**

```python
def parse_selections(text: str) -> list[dict]:
    """Parse 'slot|id|storykey|summary' lines.

    Three-field lines are still accepted with storyKey=None, so a model format
    regression degrades to the previous behaviour instead of failing the run.
    """
    valid = set(CANONICAL_ORDER)
    sels: list[dict] = []
    for line in text.strip().splitlines():
        parts = [p.strip().strip("*`") for p in line.split("|", 3)]
        if len(parts) == 4:
            slot, idx, raw_key, summary = parts
        elif len(parts) == 3:
            slot, idx, summary = parts
            raw_key = None
        else:
            continue
        try:
            idx = int(idx)
        except ValueError:
            continue
        if slot in valid and slot not in {s["slot"] for s in sels}:
            sels.append({"slot": slot, "i": idx,
                         "storyKey": history.norm_story_key(raw_key), "summary": summary})
    if len(sels) != len(SLOTS):
        raise RuntimeError("parsed %d/%d selections; raw=%r" % (len(sels), len(SLOTS), text[:300]))
    return sels
```

- [ ] **Step 5: Fix the heuristic fallback**

```python
def heuristic(cands: dict) -> list[dict]:
    """Offline fallback. Consumes the SAME filtered, tiered pool the model sees,
    so an API outage cannot reintroduce the market wraps we filter out.

    Summary is left empty on purpose: using the headline as the summary printed
    the title twice in the email.
    """
    picks = []
    for slot in SLOTS:
        fresh, fallback = filters.tier(cands.get(slot.key, []))
        pool = fresh or fallback
        chosen = pool[0] if pool else None
        picks.append({"slot": slot.key, "i": chosen["i"] if chosen else -1,
                      "storyKey": None, "summary": ""})
    return picks
```

- [ ] **Step 6: Verify the module imports and the existing suite still passes**

Run: `python3 -c "import generate; print(len(generate.SLOTS), 'slots')"`
Expected: `5 slots`

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass (no regressions from the refactor)

- [ ] **Step 7: Commit**

```bash
git add generate.py
git commit -m "feat: wire filters, tiering, and storyline context into curation"
```

---

### Task 7: Resolve loop with section blocking and diversity precedence

**Files:**
- Modify: `generate.py` (the `main()` selection/resolve loop)
- Create: `tests/test_sections.py`

**Interfaces:**
- Consumes: `RESOLVE_ORDER`, `CANONICAL_ORDER`, `BLOCKED_SECTIONS` from earlier tasks.
- Produces: `url_section(url: str) -> str` and
  `is_claimable(url: str, story_key: str | None, blocked_keys: set[str], used_keys: set[str], used_urls: set[str], prior_urls: set[str]) -> str` — returns `""` when the candidate is usable, otherwise a short rejection reason (`"section"`, `"dup-url"`, `"storyline"`) used for both the log line and the tests.

- [ ] **Step 1: Write the failing test**

```python
"""WSJ URL section extraction and candidate claimability."""
from generate import BLOCKED_SECTIONS, is_claimable, url_section


def test_extracts_the_first_path_segment() -> None:
    assert url_section("https://www.wsj.com/tech/sk-hynix-abc123") == "tech"
    assert url_section("https://www.wsj.com/business/deals/easyjet-fbe3") == "business"


def test_identifies_the_blocked_sections_seen_in_history() -> None:
    assert url_section("https://www.wsj.com/pro/central-banking/ecb-xyz") in BLOCKED_SECTIONS
    assert url_section("https://www.wsj.com/podcasts/minute-briefing/abc") in BLOCKED_SECTIONS


def test_bare_article_urls_are_not_blocked() -> None:
    """These resolve and work; they are just non-canonical."""
    assert url_section("https://www.wsj.com/articles/microsoft-profit-abc") not in BLOCKED_SECTIONS


def test_handles_urls_with_query_strings_and_no_path() -> None:
    assert url_section("https://www.wsj.com/opinion/socialism-2b3f?mod=hp_lead") == "opinion"
    assert url_section("https://www.wsj.com/") == ""
    assert url_section("") == ""


OK_URL = "https://www.wsj.com/business/deals/serie-a-abc123"


def test_claimable_candidate_returns_empty_reason() -> None:
    assert is_claimable(OK_URL, "silverlake+seriea", set(), set(), set(), set()) == ""


def test_blocked_section_is_rejected() -> None:
    pro = "https://www.wsj.com/pro/central-banking/ecb-xyz"
    assert is_claimable(pro, "ecb+rates", set(), set(), set(), set()) == "section"


def test_url_already_used_today_or_on_a_prior_day_is_rejected() -> None:
    assert is_claimable(OK_URL, None, set(), set(), {OK_URL}, set()) == "dup-url"
    assert is_claimable(OK_URL, None, set(), set(), set(), {OK_URL}) == "dup-url"


def test_storyline_hard_blocked_in_the_last_two_days_is_rejected() -> None:
    assert is_claimable(OK_URL, "integer+kkr", {"integer+kkr"}, set(), set(), set()) == "storyline"


def test_sports_claims_a_story_and_industry_is_then_rejected_for_it() -> None:
    """Diversity precedence: Sports resolves first and claims the story, so the
    same story is no longer claimable when Industry is resolved."""
    used_keys: set[str] = set()
    assert is_claimable(OK_URL, "silverlake+seriea", set(), used_keys, set(), set()) == ""
    used_keys.add("silverlake+seriea")          # Sports claims it
    assert is_claimable(OK_URL, "silverlake+seriea", set(), used_keys, set(), set()) == "storyline"


def test_missing_story_key_never_blocks_on_storyline() -> None:
    """A null key degrades to URL matching; it must not cost an article."""
    assert is_claimable(OK_URL, None, {"integer+kkr"}, {"a+b"}, set(), set()) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sections.py -v`
Expected: FAIL with `ImportError: cannot import name 'url_section'`

- [ ] **Step 3: Add `url_section` and `is_claimable` to `generate.py`**

```python
def url_section(url: str) -> str:
    """First path segment of a wsj.com URL, e.g. 'tech' or 'pro'. '' if none."""
    m = re.match(r"https?://(?:www\.)?wsj\.com/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def is_claimable(url: str, story_key: str | None, blocked_keys: set, used_keys: set,
                 used_urls: set, prior_urls: set) -> str:
    """Return "" if this resolved candidate is usable, else a rejection reason.

    `used_keys` is shared across slots and grows as slots resolve in
    RESOLVE_ORDER, which is what makes Sports outrank Industry on a shared
    story. A None story_key never blocks -- a missing dedup key must not cost
    an article.
    """
    if url_section(url) in BLOCKED_SECTIONS:
        return "section"
    if url in used_urls or url in prior_urls:
        return "dup-url"
    if story_key and (story_key in blocked_keys or story_key in used_keys):
        return "storyline"
    return ""
```

- [ ] **Step 4: Update the pre-curation dedup block in `main()`**

`norm_title`, `load_history`, `prior_keys`, and `save_history` moved to
`wsjdaily.history` in Task 5, so the existing block that drops previously-sent
candidates must be repointed. Replace the `hist = load_history()` /
`prior_titles, prior_urls = prior_keys(hist, date)` lines and the reindex loop
below them with:

```python
    hist = history.load()
    prior_titles, prior_urls = history.prior_keys(hist, date)
    blocked_keys = history.blocked_story_keys(hist, date)
    covered = history.covered_story_keys(hist, date)

    cands = fetch_candidates()

    # Drop articles already sent on a previous day, BEFORE curation, so the
    # model cannot pick a repeat. Reindex so ids stay contiguous.
    dropped = 0
    for key, lst in cands.items():
        kept = [c for c in lst if history.norm_title(c["title"]) not in prior_titles]
        dropped += len(lst) - len(kept)
        for i, c in enumerate(kept):
            c["i"] = i
        cands[key] = kept
    print("dedup: dropped %d previously-sent candidate(s); history spans %d day(s)"
          % (dropped, len(hist)), file=sys.stderr)
```

Then update the curation call to pass the covered list:

```python
    try:
        selections = curate_with_claude(cands, covered)
        print("curation: Claude", file=sys.stderr)
    except Exception as e:
        print("curation: heuristic fallback (" + str(e)[:400] + ")", file=sys.stderr)
        selections = heuristic(cands)
```

- [ ] **Step 5: Rewrite the selection loop in `main()`**

Replace the loop that begins `for key, _q, _m, _kw in SLOTS:` with:

```python
    picks_by_slot: dict[str, dict] = {}
    used_urls: set[str] = set()
    used_story_keys: set[str] = set()

    # Resolve in precedence order so the first slot to claim a story keeps it
    # (Sports outranks Industry); the email still renders in CANONICAL_ORDER.
    for slot_key in RESOLVE_ORDER:
        sel = next((x for x in selections if x["slot"] == slot_key), None)
        lst = cands.get(slot_key, [])
        chosen = None
        if sel and sel.get("i", -1) >= 0:
            chosen = next((c for c in lst if c["i"] == sel["i"]), None)

        order = ([chosen] if chosen else []) + [c for c in lst if c is not chosen]
        picked = None
        for cand in order[:MAX_RESOLVE_TRIES]:
            direct = resolve_one(cand["url"])
            if not direct:
                continue
            # The storyKey describes the MODEL's pick, so it only applies when
            # this candidate is that pick; fallbacks carry no key.
            cand_key = sel.get("storyKey") if (sel and cand is chosen) else None
            reason = is_claimable(direct, cand_key, blocked_keys, used_story_keys,
                                  used_urls, prior_urls)
            if reason:
                print("  skip %s: %s" % (reason, cand["title"][:50]), file=sys.stderr)
                continue
            picked = (cand, direct)
            break

        if not picked:
            print("FAIL " + slot_key + ": no usable candidate", file=sys.stderr)
            picks_by_slot[slot_key] = {"slot": slot_key, "label": slot_key, "title": "",
                                       "url": "", "summary": "No WSJ pick today.",
                                       "storyKey": None, "source": "WSJ"}
            continue

        cand, direct = picked
        used_urls.add(direct)
        # Only carry the model's summary and storyKey if we used the model's
        # pick -- otherwise they describe a different article.
        is_model_pick = sel is not None and cand is chosen
        story_key = sel.get("storyKey") if is_model_pick else None
        if story_key:
            used_story_keys.add(story_key)
        summary = (sel.get("summary") or "")[:200] if is_model_pick else ""
        print("OK   " + slot_key + ": " + cand["title"][:55], file=sys.stderr)
        picks_by_slot[slot_key] = {"slot": slot_key, "label": slot_key, "title": cand["title"],
                                   "url": direct, "summary": summary,
                                   "storyKey": story_key, "source": "WSJ"}

    picks = [picks_by_slot[k] for k in CANONICAL_ORDER]
```

Finally, replace the `save_history(hist, date, picks)` call at the bottom of
`main()` with `history.save(hist, date, picks)`. Leave the zero-resolved
`sys.exit(1)` guard exactly as it is.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/ -v`
Expected: all pass, including the six new `is_claimable` cases

- [ ] **Step 7: Commit**

```bash
git add generate.py tests/test_sections.py
git commit -m "feat: block /pro/ and /podcasts/, enforce cross-slot diversity"
```

---

### Task 8: Dry-run A/B mode, CI, and documentation

**Files:**
- Modify: `generate.py` (add `--dry-run` handling to `main()`)
- Create: `.github/workflows/tests.yml`
- Modify: `HANDOFF.md`
- Modify: `SETUP.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `python3 generate.py --dry-run` — fetches one live pool, curates twice (filters off, then on), prints both side by side, writes nothing.

**Context:** The "filters off" arm approximates current behaviour by skipping `filters.reject` and passing an empty covered-storylines list. It is an approximation, not a bit-exact replay of the old prompt — the prompt itself has changed. That is deliberate: maintaining a second legacy prompt for one comparison run is not worth the code.

- [ ] **Step 1: Add the dry-run path to `main()`**

At the top of `main()`, after computing `date`:

```python
    if "--dry-run" in sys.argv:
        dry_run(date)
        return
```

Then add the function:

```python
def dry_run(date: str) -> None:
    """Fetch one live pool and curate it twice -- filters off, then on.

    Prints both pick sets side by side and writes NOTHING. Costs 2 API calls.
    The 'filters off' arm approximates the previous behaviour; it is not a
    bit-exact replay, since the prompt itself changed.
    """
    hist = history.load()
    raw = fetch_candidates_unfiltered()
    filtered = {s.key: filters.reject(by_key(s.key), raw.get(s.key, [])) for s in SLOTS}
    for pool in (raw, filtered):
        for rows in pool.values():
            for i, c in enumerate(rows):
                c["i"] = i

    print("=" * 78)
    for label, pool, covered in (
        ("BEFORE (filters off)", raw, []),
        ("AFTER  (filters on)", filtered, history.covered_story_keys(hist, date)),
    ):
        print(label)
        try:
            sels = curate_with_claude(pool, covered)
        except Exception as e:
            print("  curation failed: " + str(e)[:200])
            continue
        for slot_key in CANONICAL_ORDER:
            s = next((x for x in sels if x["slot"] == slot_key), None)
            chosen = next((c for c in pool.get(slot_key, []) if s and c["i"] == s["i"]), None)
            print("  %-34s %s" % (slot_key, chosen["title"][:60] if chosen else "(none)"))
        print("-" * 78)

    for slot in SLOTS:
        print("pool %-34s raw=%-3d filtered=%d"
              % (slot.key, len(raw.get(slot.key, [])), len(filtered.get(slot.key, []))))
```

Split the existing `fetch_candidates` into two functions so the dry run can see
both stages:

- `fetch_candidates_unfiltered() -> dict` — everything through `rows[:20]`,
  returning contiguously indexed rows with **no** `filters.reject` applied.
- `fetch_candidates() -> dict` — calls it, then applies
  `filters.reject(slot, rows)[:15]` and reindexes.

The `[:20]` cap belongs to the unfiltered function and the `[:15]` cap to the
filtered one, so filtering happens on the wider pool rather than on an
already-truncated one.

- [ ] **Step 2: Run the dry run and review**

Run: `ANTHROPIC_API_KEY=$YOUR_KEY python3 generate.py --dry-run`

Check three things and report them before merging:
1. Does the AFTER Macro pick avoid market wraps?
2. Is the Sports pool non-empty, and did it choose a business story? If `pool Sports raw=0`, apply the fallback from the spec's Known Risk section: widen the query to sports-business terms across all of `wsj.com`.
3. Did any slot's filtered pool collapse to 0 or 1? That signals an over-aggressive filter.

Confirm `git status` shows **no** modification to `picks.json` or `history.json`.

- [ ] **Step 3: Add the CI test workflow**

Create `.github/workflows/tests.yml`:

```yaml
name: tests

on:
  push:
    paths: ['**.py', 'requirements-dev.txt', '.github/workflows/tests.yml']
  pull_request:
  workflow_dispatch: {}

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: python3 -m pip install -r requirements-dev.txt
      - run: python3 -m pytest tests/ -v
```

The daily job stays untouched: it must not depend on pytest.

- [ ] **Step 4: Update the documentation**

In `HANDOFF.md`:
- Change the opening line from "4 curated" to "5 curated" and add Sports to the slot list.
- Under **Files**, add `wsjdaily/` ("pure filter + history logic, unit-tested") and `tests/`.
- Add to **Key gotchas**:

```markdown
9. **The market-wrap filter is Macro-only.** Applied globally it rejects real
   deal stories -- "Mitie Shares Soar on $4.2 Billion Takeover by OCS" matches
   the same shape. See `Slot.reject_market_wraps`.
10. **Storyline dedup is hybrid:** exact storyKey match within 2 days is a hard
    block; days 3-7 are shown to the model, which judges whether a development
    is materially new. Keys are sorted token sets, so order does not matter.
11. **Slots resolve in `RESOLVE_ORDER`, not `CANONICAL_ORDER`.** Sports resolves
    before Industry so a sports-business deal lands in Sports. The email still
    renders in canonical order.
```

- Under **Handy commands**, add:

```bash
# A/B the curation against one live pool without writing anything:
ANTHROPIC_API_KEY=sk-ant-... python3 generate.py --dry-run

# Run the unit tests (no API key needed):
python3 -m pytest tests/ -v
```

- Remove "No `sendNow`" from the backlog only if it has actually been built; otherwise leave it.

In `SETUP.md`, update the "4 curated" references to 5 and add Sports to the slot list in the opening description.

- [ ] **Step 5: Full verification**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

Run: `python3 -m pytest tests/ --cov=wsjdaily --cov-report=term-missing` (if `pytest-cov` is installed)
Expected: ≥80% on `wsjdaily/`

- [ ] **Step 6: Commit**

```bash
git add generate.py .github/workflows/tests.yml HANDOFF.md SETUP.md
git commit -m "feat: add --dry-run A/B mode, test CI, and docs for 5-slot digest"
```

- [ ] **Step 7: Push only after sign-off**

Do **not** push until the dry-run output from Step 2 has been reviewed and approved. Then:

```bash
git pull --rebase origin main && git push origin main
```

The next scheduled tick (08:17, 10:17, or 11:17 UTC) picks it up. Watch the `picks.json` commit diff before the 9 AM ET send. Rollback is `git revert <sha> && git push`; the mailer keeps working off the previous `picks.json`.

---

## Verification checklist

- [ ] All 10 historical market-wrap headlines are rejected; all 9 substantive Macro headlines survive
- [ ] Sports slot returns a business story when one exists, the top headline when none does
- [ ] A story shared between Sports and Industry lands in Sports; Industry advances
- [ ] `/pro/` and `/podcasts/` URLs are rejected after resolution
- [ ] Consecutive-day storyline repeats are hard-blocked; 5-day-old ones reach the model
- [ ] `picks.json` has 5 entries in canonical order with Sports last
- [ ] `mailer.gs` is unmodified and renders 5 rows
- [ ] Zero-resolved run still writes nothing and exits 1
- [ ] Heuristic fallback produces empty summaries, not duplicated titles
- [ ] `python3 -m pytest tests/ -v` passes with ≥80% coverage on `wsjdaily/`
