"""Item construction invariants."""
import datetime

import pytest

from wsjdaily.sources import Item

UTC = datetime.timezone.utc


def make(**kw) -> Item:
    base = dict(
        firm="Morgan Stanley",
        show="Thoughts on the Market",
        title="AI's New Rules of Engagement",
        url="https://podcasts.apple.com/us/podcast/x/id1466686717?i=1",
        published=datetime.datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        kind="podcast",
    )
    base.update(kw)
    return Item(**base)


def test_constructs_with_defaults() -> None:
    item = make()
    assert item.duration is None
    assert item.summary == ""


def test_rejects_naive_published() -> None:
    """The GS and MS RSS feeds stamp -0000, which yields a NAIVE datetime.
    Subtracting that from an aware `now` raises TypeError deep in the pipeline;
    failing loudly at construction localises the bug to its source."""
    with pytest.raises(ValueError, match="timezone-aware"):
        make(published=datetime.datetime(2026, 8, 7, 20, 0))


def test_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        make(kind="video")


def test_is_frozen() -> None:
    item = make()
    with pytest.raises(Exception):
        item.title = "changed"  # type: ignore[misc]
