"""Market-wrap detection, tuned against the real 2026-07-19..2026-08-07 corpus."""
import pytest

from wsjdaily.filters import is_market_wrap, is_noise

# Every daily price-move roundup picked for Macro in the last 20 days.
WRAPS = [
    "Oil Eases as Mediators Push for New U.S.-Iran Ceasefire",
    "Global Bond Yields Jump as Oil Prices Surge, Inflation Fears Mount",
    "Treasury Yields Fall as U.S.-Iran Hostilities Take a Break",
    "U.S. Treasury Yields Fall Amid Mideast Hopes; Dollar Rises Ahead of Fed",
    "Oil Surges as Fresh Middle East Strikes Threaten Fragile Diplomacy",
    "U.S. Treasury Yields Soar as Market Struggles to Interpret Fed",
    "Treasury Yields Rise in Month That Saw Inflation Fears Revive",
    "Treasury Yields, Dollar Fall as Talks to Reopen Hormuz Are Set to Restart",
    "U.S. Treasury Yields Rise, Dollar Firm as Oil Prices Increase",
    "Chip Stocks Weaken, Oil Steady as Investors Await Hormuz Progress",
    # Picked live for the Macro slot on 2026-08-07 by the pre-fix pattern.
    # "Markets" (plural) was missing from the subject list; the same headline
    # with "Stocks" was already caught.
    "Markets Rally on Surprise U.S. Job Losses, Airbnb Soars",
]

# Substantive Macro stories from the same window. A false positive here costs
# a real article, which is the expensive failure mode.
KEEPERS = [
    "U.S. Economic Growth Slowed to 1.5% in Second Quarter",
    "Three Fed Officials Say Inflation Should Have Prompted Higher Rates",
    "Trump’s Tariffs Enter New Phase, Ending Months of Calm",
    "U.S. Import Prices Unexpectedly Rise in June",
    "Why Bessent Is Leaning on the Fed to Help Prop Up Japan’s Currency",
    "What Trump’s Latest Tariffs Mean for the American Economy",
    "Trump Unveils New Tariffs Designed to Withstand Legal Scrutiny",
    "ECB to Hold Rates Steady as Rebound in Energy Prices Threatens to Revive Inflation",
    "Exclusive | Trump Has Called Warsh Repeatedly Since He Became Fed Chair",
    # Guards the plural-only "markets" subject. Singular "market" would match
    # here, because \b matches at the hyphen in "Market-Cap". Without this
    # fixture, changing _SUBJ's "markets" to "markets?" passes the whole suite
    # while silently dropping real articles.
    "Microsoft’s One-Day Market-Cap Gain Makes History",
]


@pytest.mark.parametrize("title", WRAPS)
def test_rejects_daily_market_wraps(title: str) -> None:
    assert is_market_wrap(title) is True


@pytest.mark.parametrize("title", KEEPERS)
def test_keeps_substantive_macro_stories(title: str) -> None:
    assert is_market_wrap(title) is False


def test_deal_story_matches_the_wrap_shape_hence_macro_only_scoping() -> None:
    """A real Industry pick that matches the wrap SHAPE.

    This is exactly why wrap rejection is gated behind Slot.reject_market_wraps
    instead of being applied to every slot: globally, this filter would discard
    a legitimate $4.2B takeover story. Asserted here so the coupling is
    deliberate and visible rather than an accident.
    """
    assert is_market_wrap("Mitie Shares Soar on $4.2 Billion Takeover by OCS") is True


def test_noise_matches_existing_junk_categories() -> None:
    assert is_noise("Roundup: Market Talk") is True
    assert is_noise("WSJ Dollar Index") is True
    assert is_noise("Apollo to Buy European Budget Airline easyJet for $7.7 Billion") is False
