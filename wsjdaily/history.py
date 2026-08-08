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
from typing import Iterator

from wsjdaily import jsonio

HISTORY_DAYS = 21             # literal title/URL repeat window
STORY_HARD_BLOCK_DAYS = 2     # storyKey match here is auto-rejected
STORY_SOFT_WINDOW_DAYS = 7    # storyKey match here is shown to the model
MAX_STORY_TOKENS = 4

RESEARCH_KEY = "_research"          # top-level bookkeeping key, not a date
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def norm_title(t: str) -> str:
    """Normalize a headline for identity matching (ignores prefixes/punctuation)."""
    t = re.sub(
        r"^\s*(exclusive|opinion|analysis|review|live|updated)\s*\|\s*",
        "",
        t.strip(),
        flags=re.I,
    )
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


def _window(hist: dict, today: str, days: int) -> Iterator[dict]:
    """Yield entries from the `days` days before `today`, excluding today.

    Cutoff is `today - days`, so days=2 covers the two full prior days
    (yesterday and the day before), days=7 covers the seven prior days, etc.
    """
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days)).isoformat()
    for day, items in hist.items():
        # Skip bookkeeping keys such as RESEARCH_KEY. Name-based ordering is
        # NOT enough: "_research" > "2026-.." because "_" is 0x5F, so the key
        # would otherwise pass the cutoff test and be iterated as if it were a
        # list of picks.
        if not _DATE_RE.match(day):
            continue
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


def save(
    hist: dict,
    today: str,
    picks: list[dict],
    path: str = "history.json",
    research_urls: list[str] | None = None,
) -> None:
    """Record today's picks and prune anything past the 21-day window."""
    hist = dict(hist)
    hist[today] = [
        {"title": p["title"], "url": p["url"], "storyKey": p.get("storyKey")}
        for p in picks
        if p.get("url")
    ]
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=HISTORY_DAYS)).isoformat()
    if research_urls is not None:
        # Defensive coercion, mirroring the try/except the READ side already has
        # in main(): a malformed history must not fail the run. A non-mapping
        # value here would either raise (int -> TypeError, str -> ValueError) or,
        # worse, silently corrupt further (dict(["u1", "u2"]) == {"u": "2"}).
        # The workflow's commit step now carries `if: always()`, so a raise here
        # no longer costs the whole digest -- picks.json is written before this
        # and would still ship. It would still cost a day of dedup, so the
        # coercion stays.
        raw_research = hist.get(RESEARCH_KEY)
        research = dict(raw_research) if isinstance(raw_research, dict) else {}
        research[today] = list(research_urls)
        hist[RESEARCH_KEY] = {
            d: v for d, v in sorted(research.items()) if d >= cutoff
        }
    hist = {d: v for d, v in hist.items() if d >= cutoff}
    # Atomic: the workflow commits whatever is on disk. A truncated history
    # would silently disable dedup rather than fail loudly.
    jsonio.write_json(path, dict(sorted(hist.items())))


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


def research_urls(hist: dict, exclude: str | None = None) -> set[str]:
    """Every research URL previously emitted, across all retained days.

    `exclude` drops one date -- always today's -- from the result, mirroring
    `_window`'s today-skip and for the same reason. The workflow fires two full
    runs before the 09:00 ET send, and each run REGENERATES picks.json from
    scratch rather than appending to it. Counting today's own entries as "seen"
    would therefore make the later run suppress everything the earlier run
    emitted, and the later run's picks.json is the one the mailer reads --
    shipping an empty research section on a normal day. Cross-day dedup, the
    case the overlapping-window rule actually exists for, is unaffected.
    """
    out: set[str] = set()
    for day, urls in (hist.get(RESEARCH_KEY) or {}).items():
        if exclude is not None and day == exclude:
            continue
        out.update(u for u in (urls or []) if u)
    return out


def covered_story_keys(hist: dict, today: str) -> list[str]:
    """Storylines covered in the last 7 days -- shown to the model as context."""
    seen: list[str] = []
    for item in _window(hist, today, STORY_SOFT_WINDOW_DAYS):
        key = norm_story_key(item.get("storyKey"))
        if key and key not in seen:
            seen.append(key)
    return sorted(seen)
