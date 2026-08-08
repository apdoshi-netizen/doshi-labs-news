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
