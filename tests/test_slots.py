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


def test_ranking_slots_are_exactly_sports_and_op_ed() -> None:
    """Both prefer a topic but must never discard the remainder.

    Sports prefers business stories and falls back to the top headline; Op-Ed
    ranks economics/policy columns above the rest. Neither drops candidates.
    """
    assert by_key("Sports").keyword_fallback is True
    assert by_key("Op-Ed").keyword_fallback is True
    assert [s.key for s in SLOTS if s.keyword_fallback] == ["Op-Ed", "Sports"]


def test_op_ed_is_the_only_slot_that_allows_opinion_titles() -> None:
    """Every Op-Ed headline starts with "Opinion |".

    This flag is what lets Op-Ed carry relevance keywords at all: `reject()`
    used to key opinion-dropping off `slot.keywords`, so giving Op-Ed keywords
    without this flag would empty the slot.
    """
    assert by_key("Op-Ed").allow_opinion is True
    assert [s.key for s in SLOTS if s.allow_opinion] == ["Op-Ed"]


def test_op_ed_has_relevance_keywords() -> None:
    kw = by_key("Op-Ed").keywords
    assert kw is not None
    for expected in ("tariff", "econom", "market", "fed"):
        assert expected in kw


def test_by_key_raises_on_unknown_slot() -> None:
    with pytest.raises(KeyError):
        by_key("Weather")
