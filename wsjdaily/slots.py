"""Slot definitions for the daily digest.

Configuration data only, no logic. `filters` and `generate` consume these.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    """One section of the daily email."""

    key: str                            # canonical name; the model's slot name
    query: str                          # Google News RSS search query
    max_age_hrs: int                    # hard cutoff for candidate age
    keywords: tuple[str, ...] | None    # title filter; None accepts anything
    reject_market_wraps: bool = False   # drop daily price-move wire copy
    keyword_fallback: bool = False      # rank matches first, keep the rest
    # What the EMAIL shows. Kept separate from `key` on purpose: `key` is the
    # name the model must echo back, the dedup history is written against it,
    # and RESOLVE_ORDER/CANONICAL_ORDER are built from it. Renaming a heading
    # should not ripple into any of that. Defaults to `key`.
    display: str | None = None

    @property
    def label(self) -> str:
        """Heading shown in the email."""
        return self.display or self.key
    allow_opinion: bool = False         # keep "Opinion | ..." titles


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

# Op-Ed RELEVANCE terms: economics, business, markets, and policy argument.
# These RANK rather than filter (keyword_fallback=True) -- matching columns sort
# to the top and the rest stay behind them, so the [:15] cap does the gating.
# Ordering was chosen over filtering because a hard filter drops good columns
# whose relevance is not lexical: measured against the 20 real Op-Ed picks in
# history, a filter kept 18/20 -- correctly dropping "America Needs the
# Filibuster" (political process) but also dropping "Milton Friedman Was Right",
# which is squarely economics. Ranking keeps both reachable.
OPED_KEYWORDS = (
    "tariff", "tax", "trade", "fed", "inflation", "econom", "market", "invest",
    "capital", "growth", "deficit", "debt", "regulat", "antitrust", "energy",
    "oil", "labor", "wage", "job", "housing", "bank", "monetary", "fiscal",
    "budget", "spend", "price", "industr", "manufactur", "supply chain",
    "productiv", "competit", "merger", "business", "financ", "treasury",
    "dollar", "socialism", "capitalism", "wealth", "profit", "subsid",
    "health", "insur", "billing", "fda", "ai", "tech", "china", "monopol",
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
        display="Micro",   # email heading; `key` stays the model-facing name
    ),
    Slot(
        key="Op-Ed",
        query="site:wsj.com/opinion when:4d",
        max_age_hrs=96,
        keywords=OPED_KEYWORDS,
        keyword_fallback=True,
        allow_opinion=True,
    ),
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
