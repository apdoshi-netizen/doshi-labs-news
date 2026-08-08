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


# --- summary cleaning -------------------------------------------------------


def test_clean_summary_keeps_only_the_first_paragraph() -> None:
    """Podcast descriptions bundle the blurb with a full transcript and legal
    boilerplate. Only the opening paragraph is the actual summary."""
    from wsjdaily.sources import clean_summary

    raw = ("The real summary sentence.\n\n"
           "----- Transcript -----\n\n"
           "Speaker: a very long transcript that must not reach the email.\n\n"
           "Copyright 2026. All rights reserved.")
    assert clean_summary(raw) == "The real summary sentence."


def test_clean_summary_normalises_whitespace_and_nbsp() -> None:
    from wsjdaily.sources import clean_summary

    assert clean_summary("In this episode of\xa0Making Sense,\n Lauren  Brice") == (
        "In this episode of Making Sense, Lauren Brice")


def test_clean_summary_returns_empty_for_missing_input() -> None:
    from wsjdaily.sources import clean_summary

    assert clean_summary(None) == ""
    assert clean_summary("   ") == ""


def test_clean_summary_truncates_on_a_word_boundary() -> None:
    """The bug this replaced cut mid-word ('...Read more insights from Mor')."""
    from wsjdaily.sources import SUMMARY_CHARS, clean_summary

    out = clean_summary("word " * 400)
    assert len(out) <= SUMMARY_CHARS + 1          # +1 for the ellipsis
    assert out.endswith("…")
    assert "wor…" not in out, "must not cut mid-word"


def test_clean_summary_leaves_a_realistic_blurb_whole() -> None:
    """Measured range across the four feeds is 252-759 chars; none should be cut."""
    from wsjdaily.sources import clean_summary

    blurb = ("Our Head of U.S. Public Policy Research explains how tensions, export "
             "controls and domestic regulation are reshaping where AI is built. " * 4)
    assert clean_summary(blurb).endswith("built.")
