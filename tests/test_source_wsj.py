"""WSJ candidate fetching: a bad RSS response costs its own slot, never the run.

The branch's governing rule is that a network or markup failure costs at most
its own items. `root.find("channel")` used to sit OUTSIDE the try wrapping
ET.fromstring, so XML-valid RSS with no <channel> element returned None and
`.findall("item")` raised AttributeError -- escaping fetch_candidates and main()
and aborting all five slots. The workflow's commit step has no `if: always()`,
so that also destroys a digest already assembled on disk.
"""
import datetime
import email.utils

import pytest

from wsjdaily.slots import SLOTS
from wsjdaily.sources import wsj

NO_CHANNEL = '<?xml version="1.0"?><rss version="2.0"><notchannel/></rss>'


def _rss_with_one_wsj_item() -> str:
    """Well-formed RSS carrying a single fresh WSJ item."""
    recent = email.utils.format_datetime(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>A Real Headline - WSJ</title>"
        "<source>WSJ</source>"
        "<link>https://news.google.com/articles/abc</link>"
        "<pubDate>%s</pubDate></item>"
        "</channel></rss>" % recent
    )


def test_rss_without_a_channel_element_yields_an_empty_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No exception escapes; every slot is simply empty."""
    monkeypatch.setattr(wsj, "curl", lambda args: NO_CHANNEL)
    out = wsj.fetch_candidates_unfiltered()
    assert set(out) == {s.key for s in SLOTS}
    assert all(out[s.key] == [] for s in SLOTS)


def test_a_channelless_response_does_not_abort_the_other_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the fix: one bad response costs one slot, not all five."""
    broken_slot, good_slots = SLOTS[0], SLOTS[1:]
    assert good_slots, "fixture assumption: more than one slot exists"
    good_rss = _rss_with_one_wsj_item()
    calls: list[str] = []

    def fake_curl(args: list[str]) -> str:
        # Slots are fetched in SLOTS order, so the first call is broken_slot's.
        calls.append(args[-1])
        return NO_CHANNEL if len(calls) == 1 else good_rss

    monkeypatch.setattr(wsj, "curl", fake_curl)
    out = wsj.fetch_candidates_unfiltered()

    assert len(calls) == len(SLOTS), "every slot must still be attempted"
    assert out[broken_slot.key] == []
    assert all(out[s.key] for s in good_slots), "healthy slots must still produce rows"


def test_malformed_xml_still_yields_an_empty_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-existing behaviour, pinned so the fix did not narrow the except."""
    monkeypatch.setattr(wsj, "curl", lambda args: "<rss><unclosed>")
    out = wsj.fetch_candidates_unfiltered()
    assert all(out[s.key] == [] for s in SLOTS)
