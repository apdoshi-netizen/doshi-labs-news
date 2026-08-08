# Multi-Source Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second email section listing everything Goldman Sachs, J.P. Morgan, and Morgan Stanley published in the 24 hours before the digest generates.

**Architecture:** A `wsjdaily/sources/` package where each adapter exposes one function, `fetch(now) -> list[Item]`. Four podcast shows share a single Apple-lookup adapter differing only by ID; one scrape adapter covers JPM Top Market Takeaways. Section 2 is built deterministically — no model call, no curation. `generate.py` drops to orchestration only.

**Tech Stack:** Python 3.11 stdlib only, `curl` via `subprocess` for all HTTP, pytest for tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-08-multi-source-expansion-design.md`

**Branch:** `feat/multi-source`, branched from merged `main` (`99be319`).

## Global Constraints

- **No new runtime dependencies.** stdlib + the `curl` binary only. pytest is dev-only and must never be imported by runtime code.
- **Python 3.11** (pinned in `.github/workflows/daily.yml`). This box may run a newer Python — do not rely on version-specific parsing behaviour.
- **All HTTP goes through the shared `curl()` helper.** Never `urllib`/`requests`.
- **Section 2 must never break section 1.** Every adapter failure is caught per-adapter and yields `[]`. Section 2 must never raise out of `main()` and never trigger `sys.exit(1)`.
- **The zero-slots-resolved guard stays scoped to WSJ picks**: write nothing, `exit 1`. An empty `research` list is a normal Saturday.
- **`picks.json` is additive only.** `picks` keeps its exact shape; `mailer.gs` reads `label`, `title`, `url`, `summary`.
- **`Item.published` must be timezone-aware.** Validated at construction.
- Type annotations on all function signatures; lines ≤ 100 chars; files under 400 lines.
- Immutability: adapters return new lists and never mutate their inputs.

## File structure

```
wsjdaily/
├── slots.py, filters.py            # unchanged
├── history.py                      # Task 4: _window date guard + research URLs
├── http.py                         # Task 1: shared curl() helper
└── sources/
    ├── __init__.py                 # Task 1: Item dataclass
    ├── apple.py                    # Task 2: four podcast shows
    ├── jpm_web.py                  # Task 3: Top Market Takeaways scrape
    └── wsj.py                      # Task 5: moved from generate.py
generate.py                         # Task 6: orchestration + research pipeline
mailer.gs                           # Task 7: second email section
tests/fixtures/                     # captured JSON + HTML, committed
```

---

### Task 1: Shared HTTP helper and the Item type

**Files:**
- Create: `wsjdaily/http.py`
- Create: `wsjdaily/sources/__init__.py`
- Create: `tests/test_item.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `wsjdaily.http.curl(args: list[str]) -> str`; `wsjdaily.sources.Item` frozen dataclass with fields `firm: str`, `show: str | None`, `title: str`, `url: str`, `published: datetime.datetime`, `kind: str`, `duration: str | None = None`, `summary: str = ""`.

**Context:** `curl()` currently lives in `generate.py`. The adapters need it, and importing from `generate` would create a cycle (`generate` imports `sources`, `sources` imports `generate`). Moving it to `wsjdaily/http.py` breaks that. `generate.py` keeps working by importing the moved helper — Task 5 removes its local copy.

- [ ] **Step 1: Write the failing test**

Create `tests/test_item.py`:

```python
"""Item construction invariants."""
import datetime

import pytest

from wsjdaily.sources import Item

UTC = datetime.timezone.utc


def make(**kw) -> Item:
    base = dict(
        firm="Morgan Stanley",
        show="Thoughts on the Market",
        title="AI's New Rules of Engagement",
        url="https://podcasts.apple.com/us/podcast/x/id1466686717?i=1",
        published=datetime.datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        kind="podcast",
    )
    base.update(kw)
    return Item(**base)


def test_constructs_with_defaults() -> None:
    item = make()
    assert item.duration is None
    assert item.summary == ""


def test_rejects_naive_published() -> None:
    """The GS and MS RSS feeds stamp -0000, which yields a NAIVE datetime.
    Subtracting that from an aware `now` raises TypeError deep in the pipeline;
    failing loudly at construction localises the bug to its source."""
    with pytest.raises(ValueError, match="timezone-aware"):
        make(published=datetime.datetime(2026, 8, 7, 20, 0))


def test_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        make(kind="video")


def test_is_frozen() -> None:
    item = make()
    with pytest.raises(Exception):
        item.title = "changed"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_item.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wsjdaily.sources'`

- [ ] **Step 3: Write the implementation**

Create `wsjdaily/http.py`:

```python
"""Shared HTTP helper.

Every network call in this project goes through curl. The invocation is
load-bearing: -L follows Google's consent redirect (without it the resolver
gets an empty 302 body), and the browser User-Agent avoids bot walls.
Lives here rather than in generate.py so source adapters can use it without
importing generate, which would create a circular import.
"""
import subprocess

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def curl(args: list[str]) -> str:
    """Run curl with the project's standard flags and return stdout."""
    return subprocess.run(
        ["curl", "-sL", "--max-time", "30", "-A", UA] + args,
        capture_output=True, text=True,
    ).stdout
```

Create `wsjdaily/sources/__init__.py`:

```python
"""Content source adapters.

Each adapter module exposes one function, `fetch(now) -> list[Item]`. Adapters
never write files, never call the model, and never import one another.
"""
import datetime
from dataclasses import dataclass

KINDS = ("podcast", "article")


@dataclass(frozen=True)
class Item:
    """One publication from a non-WSJ source."""

    firm: str                        # "Goldman Sachs" | "J.P. Morgan" | "Morgan Stanley"
    show: str | None                 # show name; None for written research
    title: str
    url: str
    published: datetime.datetime     # MUST be timezone-aware
    kind: str                        # one of KINDS
    duration: str | None = None      # e.g. "5 min"; None for articles
    summary: str = ""                # publisher-written, truncated

    def __post_init__(self) -> None:
        if self.published.tzinfo is None or self.published.utcoffset() is None:
            raise ValueError(
                "Item.published must be timezone-aware; got naive datetime for "
                + repr(self.title[:60])
            )
        if self.kind not in KINDS:
            raise ValueError("Item.kind must be one of %r; got %r" % (KINDS, self.kind))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_item.py -v`
Expected: 4 passed

Run: `python3 -m pytest tests/ -q`
Expected: 89 passed (85 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add wsjdaily/http.py wsjdaily/sources/__init__.py tests/test_item.py
git commit -m "feat: add shared curl helper and the Item source type"
```

---

### Task 2: Apple podcast adapter

**Files:**
- Create: `wsjdaily/sources/apple.py`
- Create: `tests/fixtures/apple_thoughts.json`
- Create: `tests/test_source_apple.py`

**Interfaces:**
- Consumes: `Item` from Task 1, `wsjdaily.http.curl`.
- Produces: `wsjdaily.sources.apple.SHOWS: tuple[Show, ...]`, `Show` frozen dataclass (`firm: str`, `name: str`, `itunes_id: int`), `parse(payload: dict, show: Show) -> list[Item]`, `fetch(now: datetime.datetime) -> list[Item]`.

**Context:** The RSS feeds are deliberately NOT used. GS (megaphone) and MS (art19) feeds contain no `<link>` element — only `.mp3` enclosures — and firm-site URLs cannot be derived from titles (verified: every constructed slug 404s). Apple's lookup API supplies both a stable per-episode URL and a clean ISO date.

The first element of `results` is the collection itself, not an episode. Filter on `wrapperType == "podcastEpisode"`.

- [ ] **Step 1: Capture the fixture**

```bash
mkdir -p tests/fixtures
curl -s "https://itunes.apple.com/lookup?id=1466686717&entity=podcastEpisode&limit=10" \
  > tests/fixtures/apple_thoughts.json
python3 -c "
import json; d=json.load(open('tests/fixtures/apple_thoughts.json'))
eps=[r for r in d['results'] if r.get('wrapperType')=='podcastEpisode']
print('episodes captured:', len(eps))
print('sample:', eps[0]['releaseDate'], '|', eps[0]['trackName'][:40])
"
```

Expected: at least 5 episodes. If the capture returns 0, the API shape changed — stop and report rather than inventing a fixture.

- [ ] **Step 2: Write the failing test**

Create `tests/test_source_apple.py`:

```python
"""Apple podcast adapter: parsing only, no network."""
import datetime
import json
import pathlib

import pytest

from wsjdaily.sources import Item
from wsjdaily.sources.apple import SHOWS, Show, parse

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "apple_thoughts.json"
SHOW = Show(firm="Morgan Stanley", name="Thoughts on the Market", itunes_id=1466686717)


def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parses_every_episode_in_the_fixture() -> None:
    items = parse(payload(), SHOW)
    assert len(items) >= 5
    assert all(isinstance(i, Item) for i in items)


def test_skips_the_collection_wrapper() -> None:
    """results[0] is the podcast itself, not an episode."""
    raw = payload()
    collections = [r for r in raw["results"] if r.get("wrapperType") != "podcastEpisode"]
    assert collections, "fixture should contain the collection wrapper"
    titles = {i.title for i in parse(raw, SHOW)}
    assert collections[0].get("collectionName") not in titles


def test_every_published_is_timezone_aware() -> None:
    for item in parse(payload(), SHOW):
        assert item.published.tzinfo is not None
        assert item.published.utcoffset() is not None


def test_carries_show_and_firm_metadata() -> None:
    item = parse(payload(), SHOW)[0]
    assert item.firm == "Morgan Stanley"
    assert item.show == "Thoughts on the Market"
    assert item.kind == "podcast"


def test_url_is_the_apple_episode_page() -> None:
    for item in parse(payload(), SHOW):
        assert item.url.startswith("https://podcasts.apple.com/")


def test_duration_renders_as_whole_minutes() -> None:
    items = [i for i in parse(payload(), SHOW) if i.duration]
    assert items, "fixture should have at least one episode with a duration"
    assert items[0].duration.endswith(" min")


def test_episode_missing_a_url_is_skipped_not_crashed() -> None:
    raw = payload()
    for r in raw["results"]:
        if r.get("wrapperType") == "podcastEpisode":
            r.pop("trackViewUrl", None)
            break
    before = len(parse(payload(), SHOW))
    assert len(parse(raw, SHOW)) == before - 1


def test_episode_missing_a_date_is_skipped_not_crashed() -> None:
    raw = payload()
    for r in raw["results"]:
        if r.get("wrapperType") == "podcastEpisode":
            r.pop("releaseDate", None)
            break
    before = len(parse(payload(), SHOW))
    assert len(parse(raw, SHOW)) == before - 1


def test_malformed_payload_yields_no_items() -> None:
    assert parse({}, SHOW) == []
    assert parse({"results": None}, SHOW) == []


def test_all_four_shows_are_configured() -> None:
    assert len(SHOWS) == 4
    ids = {s.itunes_id for s in SHOWS}
    assert ids == {1466686717, 948913991, 1456184829, 1367963156}
    assert {s.firm for s in SHOWS} == {"Morgan Stanley", "Goldman Sachs", "J.P. Morgan"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_source_apple.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wsjdaily.sources.apple'`

- [ ] **Step 4: Write the implementation**

Create `wsjdaily/sources/apple.py`:

```python
"""Podcast adapter backed by Apple's public lookup API.

Chosen over the shows' own RSS feeds because the GS (megaphone) and MS (art19)
feeds contain NO <link> element -- only .mp3 enclosures -- and firm-site episode
URLs cannot be derived from titles (verified: every constructed slug 404s).
Apple supplies a stable per-episode URL and a clean ISO date in one call.

The feeds also stamp dates as -0000, which makes email.utils.parsedate_to_datetime
return a NAIVE datetime; Apple's ISO releaseDate sidesteps that entirely.
"""
import datetime
import json
from dataclasses import dataclass

from wsjdaily.http import curl
from wsjdaily.sources import Item

LOOKUP = "https://itunes.apple.com/lookup?id={id}&entity=podcastEpisode&limit=10"
SUMMARY_CHARS = 240


@dataclass(frozen=True)
class Show:
    """One podcast, identified by its Apple collection id."""

    firm: str
    name: str
    itunes_id: int


SHOWS: tuple[Show, ...] = (
    Show("Morgan Stanley", "Thoughts on the Market", 1466686717),
    Show("Goldman Sachs", "Exchanges", 948913991),
    Show("J.P. Morgan", "Making Sense", 1456184829),
    Show("J.P. Morgan", "Eye on the Market", 1367963156),
)


def _parse_date(raw: str | None) -> datetime.datetime | None:
    """Parse Apple's ISO releaseDate into an aware datetime, or None."""
    if not raw:
        return None
    try:
        # Normalise the trailing Z explicitly rather than relying on the
        # running Python version's fromisoformat behaviour (CI pins 3.11).
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration(millis: object) -> str | None:
    """Render trackTimeMillis as whole minutes, e.g. '5 min'."""
    if not isinstance(millis, (int, float)) or millis <= 0:
        return None
    return "%d min" % max(1, round(millis / 60000))


def parse(payload: dict, show: Show) -> list[Item]:
    """Convert one lookup response into Items. Malformed input yields []."""
    results = (payload or {}).get("results") or []
    if not isinstance(results, list):
        return []
    items: list[Item] = []
    for r in results:
        if not isinstance(r, dict) or r.get("wrapperType") != "podcastEpisode":
            continue
        published = _parse_date(r.get("releaseDate"))
        url = r.get("trackViewUrl")
        title = (r.get("trackName") or "").strip()
        if not (published and url and title):
            continue
        items.append(Item(
            firm=show.firm,
            show=show.name,
            title=title,
            url=url,
            published=published,
            kind="podcast",
            duration=_duration(r.get("trackTimeMillis")),
            summary=(r.get("description") or "").strip()[:SUMMARY_CHARS],
        ))
    return items


def fetch(now: datetime.datetime) -> list[Item]:
    """Fetch every configured show. A failing show yields no items, never raises.

    `now` is accepted for interface symmetry with the other adapters; Apple
    returns absolute dates, so no relative-time arithmetic is needed here.
    """
    out: list[Item] = []
    for show in SHOWS:
        try:
            out.extend(parse(json.loads(curl([LOOKUP.format(id=show.itunes_id)])), show))
        except Exception as e:                       # noqa: BLE001 - isolation is the point
            print("apple: %s failed: %s" % (show.name, str(e)[:120]))
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_source_apple.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add wsjdaily/sources/apple.py tests/test_source_apple.py tests/fixtures/apple_thoughts.json
git commit -m "feat: add Apple podcast adapter for the four GS/JPM/MS shows"
```

---

### Task 3: JPM Top Market Takeaways adapter

**Files:**
- Create: `wsjdaily/sources/jpm_web.py`
- Create: `tests/fixtures/jpm_listing.html`, `tests/fixtures/jpm_article.html`
- Create: `tests/test_source_jpm.py`

**Interfaces:**
- Consumes: `Item` from Task 1, `wsjdaily.http.curl`.
- Produces: `parse_listing(html: str) -> list[str]` (absolute URLs), `parse_article(html: str, url: str) -> Item | None`, `fetch(now: datetime.datetime) -> list[Item]`.

**Context:** This is the only HTML scrape and the most fragile source. The listing page is server-rendered but carries NO dates; each article page carries `<meta name="publishDate">`, `<title>`, and `<meta name="description">` (verified — `og:description` and `twitter:description` are absent, so `description` is the only summary source).

It must **fail closed**: an article with no parseable date is skipped, never emitted with a guessed date. If JPM restructures, this source quietly returns nothing while everything else keeps working.

- [ ] **Step 1: Capture the fixtures**

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
curl -sL -A "$UA" "https://www.jpmorgan.com/insights/markets-and-economy/top-market-takeaways" \
  > tests/fixtures/jpm_listing.html
curl -sL -A "$UA" "https://www.jpmorgan.com/insights/markets-and-economy/markets/etfs-trading-institutional-liquidity" \
  > tests/fixtures/jpm_article.html
grep -c 'href="/insights/markets-and-economy/' tests/fixtures/jpm_listing.html
grep -o '<meta name="publishDate" content="[^"]*"' tests/fixtures/jpm_article.html
```

Expected: several listing links, and exactly one `publishDate` meta tag. If the `publishDate` tag is absent, JPM has changed their markup — stop and report; do not invent a fixture.

- [ ] **Step 2: Write the failing test**

Create `tests/test_source_jpm.py`:

```python
"""JPM Top Market Takeaways adapter: parsing only, no network."""
import pathlib

from wsjdaily.sources.jpm_web import parse_article, parse_listing

FIX = pathlib.Path(__file__).parent / "fixtures"
LISTING = (FIX / "jpm_listing.html").read_text(encoding="utf-8", errors="ignore")
ARTICLE = (FIX / "jpm_article.html").read_text(encoding="utf-8", errors="ignore")
URL = "https://www.jpmorgan.com/insights/markets-and-economy/markets/etfs-trading"


def test_listing_yields_absolute_deduped_urls() -> None:
    urls = parse_listing(LISTING)
    assert urls, "listing fixture should contain article links"
    assert len(urls) == len(set(urls)), "must be deduped"
    assert all(u.startswith("https://www.jpmorgan.com/insights/") for u in urls)


def test_listing_excludes_the_section_landing_page() -> None:
    """Shallow paths are sections, not articles."""
    urls = parse_listing(LISTING)
    assert "https://www.jpmorgan.com/insights/markets-and-economy" not in urls


def test_article_parses_title_date_and_summary() -> None:
    item = parse_article(ARTICLE, URL)
    assert item is not None
    assert item.firm == "J.P. Morgan"
    assert item.show is None
    assert item.kind == "article"
    assert item.duration is None
    assert item.title and "&amp;" not in item.title, "HTML entities must be unescaped"
    assert item.summary
    assert item.published.tzinfo is not None


def test_article_without_a_publish_date_is_skipped() -> None:
    """Fails CLOSED. Emitting a guessed date would corrupt the recency window."""
    stripped = ARTICLE.replace('name="publishDate"', 'name="somethingElse"')
    assert parse_article(stripped, URL) is None


def test_article_with_an_unparseable_date_is_skipped() -> None:
    import re
    broken = re.sub(r'(<meta name="publishDate" content=")[^"]*', r"\1not-a-date", ARTICLE)
    assert parse_article(broken, URL) is None


def test_article_without_a_title_is_skipped() -> None:
    import re
    untitled = re.sub(r"<title>.*?</title>", "", ARTICLE, flags=re.S)
    assert parse_article(untitled, URL) is None


def test_empty_html_yields_nothing() -> None:
    assert parse_listing("") == []
    assert parse_article("", URL) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_source_jpm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wsjdaily.sources.jpm_web'`

- [ ] **Step 4: Write the implementation**

Create `wsjdaily/sources/jpm_web.py`:

```python
"""J.P. Morgan Top Market Takeaways -- the one written-research source.

No feed exists. The listing page is server-rendered but carries no dates; each
article page carries <meta name="publishDate">, <title>, and
<meta name="description">. So the listing gives us URLs and each article page
gives us everything else, at one fetch per listed article (8-9 per run).

FAILS CLOSED by design: an article with no parseable date is skipped rather
than emitted with a guessed one, because a wrong date would silently corrupt
the trailing-24h window. If JPM restructures their markup this source returns
nothing and every other source keeps working.
"""
import datetime
import html
import re

from wsjdaily.http import curl
from wsjdaily.sources import Item

LISTING_URL = "https://www.jpmorgan.com/insights/markets-and-economy/top-market-takeaways"
BASE = "https://www.jpmorgan.com"
SUMMARY_CHARS = 240

_LINK = re.compile(r'href="(/insights/markets-and-economy/[^"#?]+)"')
_DATE = re.compile(r'<meta[^>]+name="publishDate"[^>]+content="([^"]+)"')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_DESC = re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]*)"')
_MIN_PATH_DEPTH = 3          # /insights/markets-and-economy/<section>/<slug>


def parse_listing(page: str) -> list[str]:
    """Absolute article URLs from the listing, deduped, order preserved."""
    seen, out = set(), []
    for path in _LINK.findall(page or ""):
        if path.strip("/").count("/") < _MIN_PATH_DEPTH - 1:
            continue                                  # section page, not an article
        url = BASE + path
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _clean_title(raw: str) -> str:
    """Unescape entities and drop any ' | J.P. Morgan' style suffix."""
    title = html.unescape(re.sub(r"\s+", " ", raw)).strip()
    return title.split(" | ")[0].strip()


def parse_article(page: str, url: str) -> Item | None:
    """Build an Item from an article page, or None if anything required is absent."""
    page = page or ""
    dm, tm = _DATE.search(page), _TITLE.search(page)
    if not (dm and tm):
        return None
    try:
        published = datetime.datetime.fromisoformat(dm.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=datetime.timezone.utc)
    title = _clean_title(tm.group(1))
    if not title:
        return None
    desc = _DESC.search(page)
    return Item(
        firm="J.P. Morgan",
        show=None,
        title=title,
        url=url,
        published=published,
        kind="article",
        duration=None,
        summary=html.unescape(desc.group(1)).strip()[:SUMMARY_CHARS] if desc else "",
    )


def fetch(now: datetime.datetime) -> list[Item]:
    """Fetch the listing, then each article page. Never raises.

    `now` is accepted for interface symmetry; article pages carry absolute
    dates, so no relative-time arithmetic happens here.
    """
    out: list[Item] = []
    try:
        urls = parse_listing(curl([LISTING_URL]))
    except Exception as e:                            # noqa: BLE001
        print("jpm_web: listing failed: %s" % str(e)[:120])
        return out
    for url in urls:
        try:
            item = parse_article(curl([url]), url)
        except Exception as e:                        # noqa: BLE001
            print("jpm_web: %s failed: %s" % (url[-48:], str(e)[:80]))
            continue
        if item:
            out.append(item)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_source_jpm.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add wsjdaily/sources/jpm_web.py tests/test_source_jpm.py tests/fixtures/jpm_listing.html tests/fixtures/jpm_article.html
git commit -m "feat: add JPM Top Market Takeaways scrape adapter"
```

---

### Task 4: History support for research URLs

**Files:**
- Modify: `wsjdaily/history.py`
- Modify: `tests/test_history.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `RESEARCH_KEY = "_research"`, `research_urls(hist: dict) -> set[str]`, and `save(hist, today, picks, path="history.json", research_urls=None)` with `research_urls: list[str] | None`.

**Context — this is the trap this task exists to defuse.** Research URLs are stored under a `_research` top-level key in `history.json`. Naming alone is NOT sufficient protection: `"_research" < "2026-07-17"` evaluates to **`False`**, because `_` is `0x5F` and sorts after the digits. So the key passes `_window`'s cutoff test, lands inside the window, and the loop then iterates the inner dict — yielding strings, and raising `AttributeError: 'str' object has no attribute 'get'` in both `prior_keys` and `blocked_story_keys`. That would take down the **WSJ** section, which the spec forbids.

`_window` must therefore skip any key that is not date-shaped.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_history.py`:

```python
def test_non_date_keys_do_not_break_the_window() -> None:
    """Regression: '_research' does NOT sort before the cutoff ('_' is 0x5F,
    after the digits), so it lands inside the window. Without a date-shape
    guard, iterating its inner dict yields strings and raises AttributeError
    in prior_keys and blocked_story_keys -- taking down the WSJ section."""
    from wsjdaily.history import RESEARCH_KEY, blocked_story_keys, prior_keys

    hist = {
        "2026-08-06": [{"title": "A", "url": "u", "storyKey": "a+b"}],
        RESEARCH_KEY: {"2026-08-06": ["https://podcasts.apple.com/x"]},
    }
    # The premise: the key does NOT sort before a date cutoff, so it cannot be
    # excluded by ordering alone and the guard is genuinely required.
    assert not (RESEARCH_KEY < "2026-07-17")
    titles, urls = prior_keys(hist, "2026-08-07")
    assert "u" in urls
    assert blocked_story_keys(hist, "2026-08-07") == {"a+b"}


def test_research_urls_collects_across_days() -> None:
    from wsjdaily.history import RESEARCH_KEY, research_urls

    hist = {RESEARCH_KEY: {"2026-08-06": ["u1", "u2"], "2026-08-07": ["u2", "u3"]}}
    assert research_urls(hist) == {"u1", "u2", "u3"}


def test_research_urls_on_a_legacy_history_is_empty() -> None:
    from wsjdaily.history import research_urls

    assert research_urls({"2026-08-06": [{"title": "A", "url": "u"}]}) == set()


def test_save_records_research_urls_and_prunes_them(tmp_path) -> None:
    import json

    from wsjdaily.history import RESEARCH_KEY, save

    path = str(tmp_path / "history.json")
    hist = {RESEARCH_KEY: {"2026-01-01": ["ancient"]}}
    save(hist, "2026-08-07", [], path, research_urls=["https://podcasts.apple.com/new"])
    written = json.loads(open(path).read())
    assert written[RESEARCH_KEY] == {"2026-08-07": ["https://podcasts.apple.com/new"]}
    assert "2026-01-01" not in written[RESEARCH_KEY], "pruned on the same 21-day cutoff"


def test_save_without_research_leaves_the_key_absent(tmp_path) -> None:
    import json

    from wsjdaily.history import RESEARCH_KEY, save

    path = str(tmp_path / "history.json")
    save({}, "2026-08-07", [{"title": "T", "url": "https://wsj.com/a", "storyKey": None}], path)
    assert RESEARCH_KEY not in json.loads(open(path).read())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_history.py -v`
Expected: FAIL with `ImportError: cannot import name 'RESEARCH_KEY'`

- [ ] **Step 3: Add the date guard to `_window`**

In `wsjdaily/history.py`, add near the other module constants:

```python
RESEARCH_KEY = "_research"          # top-level bookkeeping key, not a date
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
```

Then change the loop inside `_window` to skip non-date keys:

```python
    for day, items in hist.items():
        # Skip bookkeeping keys such as RESEARCH_KEY. Name-based ordering is
        # NOT enough: "_research" > "2026-.." because "_" is 0x5F, so the key
        # would otherwise pass the cutoff test and be iterated as if it were a
        # list of picks.
        if not _DATE_RE.match(day):
            continue
        if day == today or day < cutoff:
            continue
```

- [ ] **Step 4: Add `research_urls` and extend `save`**

```python
def research_urls(hist: dict) -> set[str]:
    """Every research URL previously emitted, across all retained days."""
    out: set[str] = set()
    for urls in (hist.get(RESEARCH_KEY) or {}).values():
        out.update(u for u in (urls or []) if u)
    return out
```

In `save`, after the existing pick assignment and before writing, add the
research bookkeeping (keeping the existing 21-day cutoff variable):

```python
    if research_urls is not None:
        research = dict(hist.get(RESEARCH_KEY) or {})
        research[today] = list(research_urls)
        hist[RESEARCH_KEY] = {d: v for d, v in sorted(research.items()) if d >= cutoff}
```

Update the signature to
`def save(hist: dict, today: str, picks: list[dict], path: str = "history.json",
research_urls: list[str] | None = None) -> None:` and make sure the existing
date-pruning comprehension does not discard `RESEARCH_KEY` — it filters on
`d >= cutoff`, which `_research` passes, so it survives; confirm this with the
new tests rather than by inspection.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_history.py -v`
Expected: all pass, including the 5 new cases

Run: `python3 -m pytest tests/ -q`
Expected: 111 passed (85 base + 4 Item + 10 Apple + 7 JPM + 5 history)

- [ ] **Step 6: Commit**

```bash
git add wsjdaily/history.py tests/test_history.py
git commit -m "feat: store research URLs in history behind a date-shape guard"
```

---

### Task 5: Move the WSJ pipeline into a source adapter

**Files:**
- Create: `wsjdaily/sources/wsj.py`
- Modify: `generate.py`

**Interfaces:**
- Consumes: `wsjdaily.http.curl`, `wsjdaily.slots`, `wsjdaily.filters`.
- Produces: `wsjdaily.sources.wsj.fetch_candidates_unfiltered() -> dict`, `fetch_candidates() -> dict`, `resolve_one(gn_url: str) -> str | None`, `url_section(url: str) -> str`, `BLOCKED_SECTIONS: set[str]`, `RAW_POOL_CAP: int`.

**Context:** A pure relocation — no behaviour changes. This is the split deferred from step B, now justified by a second and third source existing. `generate.py` keeps `curate_with_claude`, `parse_selections`, `heuristic`, `is_claimable`, `dry_run`, and `main`.

**Do not alter** the resolver: `curl -L` plus `Cookie: CONSENT=YES+` is load-bearing (without `-L` the response is an empty 302 body with no signature). Do not alter `RAW_POOL_CAP = 60` or the post-filter `[:15]`.

- [ ] **Step 1: Move the code**

Cut `fetch_candidates_unfiltered`, `fetch_candidates`, `resolve_one`, `url_section`, `BLOCKED_SECTIONS`, and `RAW_POOL_CAP` from `generate.py` into a new `wsjdaily/sources/wsj.py`, with this module docstring:

```python
"""WSJ candidate fetching and direct-URL resolution.

Moved out of generate.py when non-WSJ sources arrived. Behaviour is unchanged.

Two things here are load-bearing and must not be "cleaned up":
  * resolve_one uses an unofficial Google endpoint (batchexecute) and needs
    curl -L plus `Cookie: CONSENT=YES+`. Without -L the response is an empty
    302 body carrying no signature.
  * Only a NON-Google IP can turn Google News links into direct wsj.com URLs;
    Google CAPTCHA-blocks its own datacenter ranges. That is why resolution
    runs in GitHub Actions rather than in Apps Script.
"""
```

Add the imports it needs (`datetime`, `email.utils`, `re`, `urllib.parse`, `xml.etree.ElementTree as ET`, `from wsjdaily import filters`, `from wsjdaily.slots import SLOTS, by_key`, `from wsjdaily.http import curl`).

- [ ] **Step 2: Update `generate.py` to import from the new home**

Replace its local `curl` definition and the moved functions with:

```python
from wsjdaily.http import curl
from wsjdaily.sources import wsj
from wsjdaily.sources.wsj import BLOCKED_SECTIONS, url_section
```

Update the call sites: `fetch_candidates()` → `wsj.fetch_candidates()`,
`fetch_candidates_unfiltered()` → `wsj.fetch_candidates_unfiltered()`,
`resolve_one(...)` → `wsj.resolve_one(...)`. Keep `is_claimable` in
`generate.py` — it is selection policy, not fetching.

- [ ] **Step 3: Verify nothing changed behaviourally**

Run: `python3 -m pytest tests/ -q`
Expected: 111 passed, unchanged — a pure move adds no tests. `tests/test_sections.py` imports `url_section` and
`is_claimable` from `generate`, and both must still be importable from there.

Run: `python3 -c "import generate; print(len(generate.SLOTS), 'slots')"`
Expected: `5 slots`

Run: `grep -c "def fetch_candidates\|def resolve_one" generate.py`
Expected: `0`

- [ ] **Step 4: Commit**

```bash
git add generate.py wsjdaily/sources/wsj.py
git commit -m "refactor: move WSJ fetch and resolve into a source adapter"
```

---

### Task 6: Build the research section

**Files:**
- Modify: `generate.py`
- Create: `tests/test_research.py`

**Interfaces:**
- Consumes: all adapters, `history.research_urls`.
- Produces: `WINDOW_HOURS = 24`, `RESEARCH_SOURCES` tuple, `collect_research(now, seen_urls) -> list[Item]`, `research_payload(items) -> list[dict]`.

**Context:** Section 2 makes **no model call**. The window is the trailing 24 hours from generation, not the calendar day — Morgan Stanley publishes at 16:00–17:30 ET, hours after the 09:00 ET send, so a same-day filter could never include the only daily publisher (measured: same-day yields 0.4 items/weekday and is empty on 7 of 11 weekdays; trailing-24h yields 1.5 and is empty on 2 of 11).

- [ ] **Step 1: Write the failing test**

Create `tests/test_research.py`:

```python
"""Research section assembly: windowing, dedup, ordering, isolation."""
import datetime

from wsjdaily.sources import Item

import generate

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 8, 11, 17, tzinfo=UTC)


def item(hours_ago: float, title: str = "T", url: str | None = None) -> Item:
    return Item(
        firm="Morgan Stanley", show="Thoughts on the Market", title=title,
        url=url or ("https://podcasts.apple.com/%s" % title),
        published=NOW - datetime.timedelta(hours=hours_ago), kind="podcast",
    )


def test_keeps_items_inside_the_24h_window(monkeypatch) -> None:
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(1, "fresh"), item(23.9, "edge")],))
    got = generate.collect_research(NOW, set())
    assert {i.title for i in got} == {"fresh", "edge"}


def test_drops_items_older_than_the_window(monkeypatch) -> None:
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(24.1, "stale")],))
    assert generate.collect_research(NOW, set()) == []


def test_drops_items_published_in_the_future(monkeypatch) -> None:
    """Clock skew on a source must not surface tomorrow's episode today."""
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(-2, "future")],))
    assert generate.collect_research(NOW, set()) == []


def test_drops_urls_already_emitted(monkeypatch) -> None:
    """Consecutive runs' 24h windows overlap when GitHub's scheduler drifts."""
    seen = {"https://podcasts.apple.com/dupe"}
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(2, "dupe"), item(3, "new")],))
    got = generate.collect_research(NOW, seen)
    assert [i.title for i in got] == ["new"]


def test_sorts_newest_first(monkeypatch) -> None:
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(10, "older"), item(1, "newer")],))
    assert [i.title for i in generate.collect_research(NOW, set())] == ["newer", "older"]


def test_a_failing_adapter_does_not_stop_the_others(monkeypatch) -> None:
    """THE governing rule: section 2 must never break section 1."""
    def boom(now):
        raise RuntimeError("network down")

    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (boom, lambda now: [item(1, "ok")]))
    assert [i.title for i in generate.collect_research(NOW, set())] == ["ok"]


def test_all_adapters_failing_yields_an_empty_list(monkeypatch) -> None:
    def boom(now):
        raise RuntimeError("down")

    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (boom, boom))
    assert generate.collect_research(NOW, set()) == []


def test_payload_is_json_serialisable_with_iso_dates() -> None:
    import json

    payload = generate.research_payload([item(1, "x")])
    json.dumps(payload)
    row = payload[0]
    assert set(row) == {"firm", "show", "title", "url", "published", "kind",
                        "duration", "summary"}
    assert row["published"].startswith("2026-08-08T")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_research.py -v`
Expected: FAIL with `AttributeError: module 'generate' has no attribute 'RESEARCH_SOURCES'`

- [ ] **Step 3: Write the implementation**

Add to `generate.py`:

```python
from wsjdaily.sources import Item, apple, jpm_web

WINDOW_HOURS = 24            # trailing window; see the spec's timing analysis
RESEARCH_SOURCES = (apple.fetch, jpm_web.fetch)


def collect_research(now: datetime.datetime, seen_urls: set) -> list[Item]:
    """Everything published in the trailing WINDOW_HOURS, newest first.

    No model call: the reader asked for an exhaustive listing, not a curated
    pick. Each adapter is isolated -- a failure costs its own items and never
    the run, which is the rule that keeps section 2 from breaking section 1.
    """
    window_start = now - datetime.timedelta(hours=WINDOW_HOURS)
    items: list[Item] = []
    for fetch in RESEARCH_SOURCES:
        try:
            items.extend(fetch(now))
        except Exception as e:                        # noqa: BLE001
            print("research: %s failed: %s" % (getattr(fetch, "__module__", "?"),
                                               str(e)[:120]), file=sys.stderr)
    fresh = [i for i in items
             if window_start < i.published <= now and i.url not in seen_urls]
    return sorted(fresh, key=lambda i: i.published, reverse=True)


def research_payload(items: list[Item]) -> list[dict]:
    """Serialise Items for picks.json."""
    return [{"firm": i.firm, "show": i.show, "title": i.title, "url": i.url,
             "published": i.published.isoformat(), "kind": i.kind,
             "duration": i.duration, "summary": i.summary} for i in items]
```

- [ ] **Step 4: Wire it into `main()`**

After `picks` is assembled and **after** the zero-resolved `sys.exit(1)` guard —
so a blocked-runner run still exits without doing research work — add:

```python
    now = datetime.datetime.now(datetime.timezone.utc)
    research = collect_research(now, history.research_urls(hist))
    print("research: %d item(s) in the last %dh" % (len(research), WINDOW_HOURS),
          file=sys.stderr)
```

Extend the result dict with `"research": research_payload(research)` and change
the history call to:

```python
    history.save(hist, date, picks, research_urls=[i.url for i in research])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: 119 passed (111 + 8 research)

- [ ] **Step 6: Commit**

```bash
git add generate.py tests/test_research.py
git commit -m "feat: assemble the trailing-24h research section"
```

---

### Task 7: Email rendering, dry-run preview, and docs

**Files:**
- Modify: `mailer.gs`
- Modify: `generate.py` (dry-run preview)
- Modify: `HANDOFF.md`, `SETUP.md`

**Interfaces:**
- Consumes: the `research` key in `picks.json`.
- Produces: no new Python interfaces.

**Context:** `mailer.gs` changes for the first time since the sender migration. The schema is additive, so an un-updated mailer keeps sending the WSJ five and ignores `research` — deploy order is forgiving, and rollback is leaving `mailer.gs` alone.

- [ ] **Step 1: Add the research section to `buildEmail` in `mailer.gs`**

After the existing `rows` assignment, add:

```javascript
  var research = (data.research || []).map(function (r) {
    var meta = [r.firm, r.show, r.duration].filter(function (x) { return x; }).join(' · ');
    var sum = r.summary
      ? '<div style="color:#555;font-size:14px;margin-top:2px;">' + escapeHtml(r.summary) + '</div>'
      : '';
    return '<p style="margin:0 0 20px 0;">' +
      '<span style="color:#888;font-size:13px;">' + escapeHtml(meta) + '</span><br>' +
      '<a href="' + r.url + '" style="color:#0b57d0;text-decoration:none;">' +
      escapeHtml(r.title) + '</a>' + sum + '</p>';
  }).join('\n');

  var researchBody = research ||
    '<p style="margin:0;color:#888;">No GS/JPM/MS publications today.</p>';
```

Then extend `htmlBody` so the new section follows the WSJ rows:

```javascript
  var htmlBody =
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#111;line-height:1.4;">' +
      rows +
      '<h3 style="font-size:15px;text-transform:uppercase;letter-spacing:.05em;' +
        'color:#444;border-top:1px solid #ddd;padding-top:16px;margin:28px 0 14px 0;">' +
        'Street Research</h3>' +
      researchBody +
    '</div>';
```

And extend `textBody`:

```javascript
  var researchText = (data.research || []).map(function (r) {
    var meta = [r.firm, r.show, r.duration].filter(function (x) { return x; }).join(' · ');
    return meta + '\n' + r.title + ' — ' + r.url + (r.summary ? '\n' + r.summary : '');
  }).join('\n\n') || 'No GS/JPM/MS publications today.';

  var textBody = data.picks.map(function (p) {
    var line = p.label + ': ' + (p.url ? p.title + ' — ' + p.url : 'No WSJ pick today.');
    if (p.summary && p.url) line += '\n' + p.summary;
    return line;
  }).join('\n\n') + '\n\nSTREET RESEARCH\n\n' + researchText;
```

- [ ] **Step 2: Add a research preview to `dry_run`**

At the end of `dry_run` in `generate.py`, after the pool-size table:

```python
    now = datetime.datetime.now(datetime.timezone.utc)
    research = collect_research(now, history.research_urls(hist))
    print("\nSTREET RESEARCH (last %dh): %d item(s)" % (WINDOW_HOURS, len(research)))
    for i in research:
        print("  %s  %-16s %s" % (i.published.strftime("%Y-%m-%d %H:%M"),
                                  i.firm[:16], i.title[:56]))
```

- [ ] **Step 3: Verify end to end without writing anything**

Run: `python3 -m pytest tests/ -q`
Expected: 119 passed

Run: `python3 generate.py --dry-run`
(no API key needed — both curation arms fail gracefully; the research section
is what you are checking)

Confirm: the research preview prints, and `git status --short` shows
`picks.json` and `history.json` **unmodified**. Report the item count and the
firms represented — if it is 0, check whether the window genuinely contains
nothing rather than assuming a bug; weekends legitimately produce 0.

- [ ] **Step 4: Update the docs**

In `HANDOFF.md`:
- Opening line: the email is now 5 WSJ articles **plus a Street Research section**.
- **Files** table: add `wsjdaily/sources/` ("source adapters: WSJ, Apple podcasts, JPM web").
- Add to **Key gotchas**:

```markdown
12. **GS and MS podcast RSS feeds have no `<link>` element** -- only .mp3
    enclosures -- and firm-site episode URLs cannot be built from titles (every
    constructed slug 404s). Apple's lookup API is the only source of a stable
    per-episode link, and it supplies clean ISO dates too.
13. **The research window is trailing 24h, not the calendar day.** Morgan
    Stanley publishes 16:00-17:30 ET, hours AFTER the 09:00 ET send, so a
    same-day filter can never include the only daily publisher.
14. **`_research` in history.json is not date-shaped and must be skipped by
    `_window`.** "_research" > "2026-.." because "_" is 0x5F, so it passes the
    cutoff test and would be iterated as if it were a list of picks.
```

- Under **Handy commands**, note that `--dry-run` now also previews the research section.

In `SETUP.md`: mention the second email section in the opening description, and add a step after the mailer paste reminding the operator to re-run `sendTestNow` after any `mailer.gs` change.

- [ ] **Step 5: Commit**

```bash
git add mailer.gs generate.py HANDOFF.md SETUP.md
git commit -m "feat: render the Street Research section and preview it in dry-run"
```

- [ ] **Step 6: Hand off — do NOT merge**

Report to the operator:
1. The dry-run research preview output (item count, firms, titles).
2. That merging needs their go-ahead.
3. That **after** merge they must re-paste `mailer.gs` into Apps Script and run
   `sendTestNow`, or the email keeps showing only the WSJ five.

---

## Verification checklist

- [ ] All four Apple shows parse from a real captured fixture, with tz-aware dates and `podcasts.apple.com` URLs
- [ ] An episode missing a URL or a date is skipped, not crashed on
- [ ] JPM articles without a parseable `publishDate` are skipped — never emitted with a guessed date
- [ ] `_research` in `history.json` does not break `prior_keys` or `blocked_story_keys`
- [ ] Items at 23h59m are kept, 24h01m dropped, future-dated dropped
- [ ] A URL emitted on a previous run is not emitted again
- [ ] One adapter raising does not prevent the others from returning
- [ ] All adapters failing yields `research: []`, exit code 0, and a valid `picks.json`
- [ ] `picks` in `picks.json` is unchanged in shape; `mailer.gs` renders 5 WSJ rows plus the new section
- [ ] Zero WSJ slots resolved still writes nothing and exits 1
- [ ] `python3 -m pytest tests/ -q` passes with ≥80% coverage on `wsjdaily/sources/`
