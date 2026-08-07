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


def test_sports_is_the_only_slot_with_keyword_fallback() -> None:
    """Sports prefers business stories but falls back to the top headline."""
    assert by_key("Sports").keyword_fallback is True
    assert [s.key for s in SLOTS if s.keyword_fallback] == ["Sports"]


def test_op_ed_accepts_any_title() -> None:
    assert by_key("Op-Ed").keywords is None


def test_by_key_raises_on_unknown_slot() -> None:
    with pytest.raises(KeyError):
        by_key("Weather")
