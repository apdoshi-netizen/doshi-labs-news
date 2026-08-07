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

HISTORY_DAYS = 21             # literal title/URL repeat window
STORY_HARD_BLOCK_DAYS = 2     # storyKey match here is auto-rejected
STORY_SOFT_WINDOW_DAYS = 7    # storyKey match here is shown to the model
MAX_STORY_TOKENS = 4


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

    `days` counts the trailing days inclusive of the boundary day, so
    days=2 covers yesterday and the day before -- i.e. cutoff is
    `today - (days - 1)`, not `today - days`.
    """
    cutoff = (datetime.date.fromisoformat(today) - datetime.timedelta(days=days - 1)).isoformat()
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
