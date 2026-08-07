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
