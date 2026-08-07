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
