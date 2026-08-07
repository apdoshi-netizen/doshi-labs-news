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


def test_sports_orders_business_stories_first_without_dropping_the_rest() -> None:
    """Sports PREFERS business stories -- it ranks them first, it does not
    discard the remainder. Dropping them left the slot with too few fallbacks
    for the resolver, so one resolver failure emptied it entirely."""
    rows = [
        row("Knicks Beat Celtics in Overtime Thriller"),
        row("Silver Lake Buys Stake in Serie A for $2 Billion"),
        row("Marathon Runner Sets Course Record"),
    ]
    kept = apply_keyword_filter(by_key("Sports"), rows)
    assert [r["title"] for r in kept] == [
        "Silver Lake Buys Stake in Serie A for $2 Billion",   # matched, hoisted
        "Knicks Beat Celtics in Overtime Thriller",           # retained, in order
        "Marathon Runner Sets Course Record",
    ]


def test_sports_preserves_relative_order_within_each_group() -> None:
    rows = [
        row("Knicks Beat Celtics in Overtime Thriller"),
        row("Silver Lake Buys Stake in Serie A for $2 Billion"),
        row("Marathon Runner Sets Course Record"),
        row("NFL Signs $10 Billion Media Rights Deal"),
    ]
    kept = apply_keyword_filter(by_key("Sports"), rows)
    assert [r["title"] for r in kept] == [
        "Silver Lake Buys Stake in Serie A for $2 Billion",
        "NFL Signs $10 Billion Media Rights Deal",
        "Knicks Beat Celtics in Overtime Thriller",
        "Marathon Runner Sets Course Record",
    ]


def test_sports_never_loses_a_candidate() -> None:
    """Ordering is a permutation: the pool size must be unchanged."""
    rows = [
        row("Knicks Beat Celtics in Overtime Thriller"),
        row("Silver Lake Buys Stake in Serie A for $2 Billion"),
        row("Marathon Runner Sets Course Record"),
    ]
    kept = apply_keyword_filter(by_key("Sports"), rows)
    assert len(kept) == len(rows)
    assert all(r in kept for r in rows)


def test_sports_falls_back_to_whole_pool_when_no_business_story_exists() -> None:
    """The top-headline fallback. Must not starve the slot."""
    rows = [row("Knicks Beat Celtics in Overtime Thriller"), row("Marathon Runner Sets Record")]
    assert apply_keyword_filter(by_key("Sports"), rows) == rows


def test_non_fallback_slots_still_discard_the_unmatched_remainder() -> None:
    """Fix 3 is scoped to keyword_fallback slots only. Macro/Industry/Tech keep
    their existing subset-only behaviour above MIN_KEYWORD_MATCHES."""
    rows = [
        row("Meta Releases Coding Agent to Compete With OpenAI"),
        row("SK Hynix to Invest $38 Billion on Chip Production"),
        row("Google Fined $1 Billion Under EU Antitrust Rules"),
        row("A Fine Day for Sailing on the Chesapeake"),
    ]
    kept = apply_keyword_filter(by_key("Tech"), rows)
    assert len(kept) == 3
    assert "A Fine Day for Sailing on the Chesapeake" not in [r["title"] for r in kept]


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


def test_ai_does_not_match_sailing() -> None:
    """Regression: the false positive found in fix round 1."""
    rows = [
        row("A Fine Day for Sailing on the Chesapeake"),
        row("Meta Releases Coding Agent to Compete With OpenAI"),
        row("SK Hynix to Invest $38 Billion on Chip Production"),
        row("Google Fined $1 Billion Under EU Antitrust Rules"),
    ]
    kept = apply_keyword_filter(by_key("Tech"), rows)
    assert all("Sailing" not in r["title"] for r in kept)


# Non-fallback slots only keep the matched subset once >= MIN_KEYWORD_MATCHES
# rows match (see test_tech_keeps_full_pool_when_too_few_match), so each stem
# regression below pads the pool with two unrelated matching filler rows to
# clear that threshold, plus one row that matches nothing at all -- proving
# both that the stem matched (the target row and the filler rows survive)
# and that the threshold logic still runs (the non-matching row is dropped).

_UNEMPLOYMENT_NON_MATCH = row("Marathon Runner Sets Course Record")


def test_acqui_matches_acquire_as_a_prefix_stem() -> None:
    """Regression: 'acqui' must match inflected forms, not just the literal stem."""
    target = row("Apollo to Acquire easyJet for $7.7 Billion")
    filler_1 = row("Investor Group Buys Stake in Retailer")
    filler_2 = row("Company Raises $10 Million in Funding Round")
    rows = [target, filler_1, filler_2, _UNEMPLOYMENT_NON_MATCH]
    kept = apply_keyword_filter(by_key("Industry / Company / Transaction"), rows)
    assert target in kept
    assert _UNEMPLOYMENT_NON_MATCH not in kept


def test_econom_matches_economic_as_a_prefix_stem() -> None:
    target = row("U.S. Economic Growth Slowed to 1.5% in Second Quarter")
    filler_1 = row("Three Fed Officials Say Inflation Should Have Prompted Higher Rates")
    filler_2 = row("Trump's Tariffs Enter New Phase, Ending Months of Calm")
    rows = [target, filler_1, filler_2, _UNEMPLOYMENT_NON_MATCH]
    kept = apply_keyword_filter(by_key("Macro"), rows)
    assert target in kept
    assert _UNEMPLOYMENT_NON_MATCH not in kept


def test_bankrupt_matches_bankruptcy_as_a_prefix_stem() -> None:
    target = row("Retailer Files for Bankruptcy Protection")
    filler_1 = row("Company Raises $10 Million in Funding Round")
    filler_2 = row("Investor Group Buys Stake in Retailer")
    rows = [target, filler_1, filler_2, _UNEMPLOYMENT_NON_MATCH]
    kept = apply_keyword_filter(by_key("Industry / Company / Transaction"), rows)
    assert target in kept
    assert _UNEMPLOYMENT_NON_MATCH not in kept


def test_unemploy_matches_unemployment_as_a_prefix_stem() -> None:
    target = row("Unemployment Rate Ticks Up")
    filler_1 = row("Three Fed Officials Say Inflation Should Have Prompted Higher Rates")
    filler_2 = row("Trump's Tariffs Enter New Phase, Ending Months of Calm")
    rows = [target, filler_1, filler_2, row("Marathon Runner Sets Course Record")]
    kept = apply_keyword_filter(by_key("Macro"), rows)
    assert target in kept
    assert row("Marathon Runner Sets Course Record") not in kept


def test_invest_matches_investors_as_a_prefix_stem() -> None:
    target = row("SoftBank Investors Push for Buyback")
    filler_1 = row("Apollo to Acquire easyJet for $7.7 Billion")
    filler_2 = row("Company Raises $10 Million in Funding Round")
    rows = [target, filler_1, filler_2, _UNEMPLOYMENT_NON_MATCH]
    kept = apply_keyword_filter(by_key("Industry / Company / Transaction"), rows)
    assert target in kept
    assert _UNEMPLOYMENT_NON_MATCH not in kept
