"""Pure candidate filters: noise, market-wrap rejection, keyword gating, tiering.

Every function here is deterministic and side-effect free, so the whole module
is testable without an API key or network access.
"""
import re

from wsjdaily.slots import Slot

# Non-article junk the Google News feed returns.
NOISE = re.compile(
    r"(Print Edition|News Archive|Exchange Rate|Roundup: Market Talk|"
    r"What to Read|WSJ Dollar Index|Latest News and Forecasts)",
    re.I,
)

# Daily price-move wire copy. Matches the SHAPE of a wrap headline: a
# market-state subject within the first three words, then a price-move verb
# within ~40 characters. Deliberately excludes "prices" and "rates" as
# subjects -- each would swallow a verified must-survive headline such as
# "U.S. Import Prices Unexpectedly Rise in June".
#
# "markets" is PLURAL-ONLY on purpose. Singular "market" would swallow
# "Microsoft's One-Day Market-Cap Gain Makes History", since \b matches at the
# hyphen. The plural was added after a live 2026-08-07 run picked "Markets
# Rally on Surprise U.S. Job Losses, Airbnb Soars" for the Macro slot -- a wrap
# the pattern missed, though the same headline with "Stocks" was caught.
# Verified: adding it changes nothing across all 80 historical picks.
_SUBJ = (
    r"(?:treasur(?:y|ys|ies)|yields?|stocks?|shares|oil|crude|dollar|bonds?|"
    r"futures|gold|yen|euro|markets)"
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


MIN_KEYWORD_MATCHES = 3  # below this, a normal slot keeps its whole pool


def apply_keyword_filter(slot: Slot, rows: list[dict]) -> list[dict]:
    """Keep the keyword-matching subset of `rows`, or reorder, or keep all.

    Normal slots keep the subset only when at least MIN_KEYWORD_MATCHES survive,
    so a thin pool is not filtered down to nothing.

    Slots with `keyword_fallback` (Sports) ORDER rather than discard: matching
    rows first, then the rest, relative order preserved within each group. The
    intent for Sports is a PREFERENCE -- "prefer sports business/finance; if the
    day has none, take the top sports headline" -- and the model's rubric in
    generate.py already states it. Enforcing it a second time here by deletion
    threw away the very fallbacks the rubric exists to use: a measured day left
    Sports with one candidate against MAX_RESOLVE_TRIES resolve attempts, so a
    single resolver failure or /pro/ hit emptied the slot. Ordering keeps the
    preference intact (the business story is still what the model sees first)
    without starving the resolver.
    """
    if not slot.keywords:
        return list(rows)
    # Leading word-boundary only, not substring and not a trailing boundary.
    # A naive `in` check lets short keywords like "ai" false-positive
    # mid-word (e.g. "Sailing" contains "ai"); a *leading* \b already fixes
    # that, because "ai" in "Sailing" is not at the start of a word. A
    # *trailing* \b would also be wrong: several keyword-tuple entries in
    # slots.py are deliberate prefix stems, not whole words -- "acqui"
    # (acquire/acquisition), "econom" (economic/economy), "bankrupt"
    # (bankruptcy), "unemploy" (unemployment), "invest" (investor/investing)
    # -- and a trailing \b would stop all of them from matching their own
    # inflected forms. Leading-only accepts a residual: "ai" also matches
    # "Aid"/"Air"/"Aim". That trade-off is deliberate -- correctly matching
    # the prefix-stem keywords (which drive real slot categorization) is
    # worth more than eliminating a rare short-token false positive, and the
    # more specific "artificial intelligence" keyword already covers the
    # intended AI-story case.
    patterns = [re.compile(r"\b" + re.escape(k), re.I) for k in slot.keywords]
    # Track matched rows by INDEX, not by value. Rows are dicts, so a
    # `r not in matched` membership test would compare by equality -- both slow
    # and semantically wrong, since two distinct articles can compare equal.
    matched_idx = {i for i, r in enumerate(rows) if any(p.search(r["title"]) for p in patterns)}
    if slot.keyword_fallback:
        return ([r for i, r in enumerate(rows) if i in matched_idx]
                + [r for i, r in enumerate(rows) if i not in matched_idx])
    if len(matched_idx) >= MIN_KEYWORD_MATCHES:
        return [r for i, r in enumerate(rows) if i in matched_idx]
    return list(rows)


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
