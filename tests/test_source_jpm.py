"""JPM Top Market Takeaways adapter: parsing (no network) and fetch() isolation."""
import datetime
import pathlib

import pytest

from wsjdaily.sources import jpm_web
from wsjdaily.sources.jpm_web import LISTING_URL, fetch, parse_article, parse_listing

FIX = pathlib.Path(__file__).parent / "fixtures"
LISTING = (FIX / "jpm_listing.html").read_text(encoding="utf-8", errors="ignore")
ARTICLE = (FIX / "jpm_article.html").read_text(encoding="utf-8", errors="ignore")
URL = "https://www.jpmorgan.com/insights/markets-and-economy/markets/etfs-trading"
NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.timezone.utc)


def test_listing_yields_absolute_deduped_urls() -> None:
    urls = parse_listing(LISTING)
    assert urls, "listing fixture should contain article links"
    assert len(urls) == len(set(urls)), "must be deduped"
    assert all(u.startswith("https://www.jpmorgan.com/insights/") for u in urls)


def test_listing_excludes_the_section_landing_page() -> None:
    """Shallow paths are sections, not articles."""
    urls = parse_listing(LISTING)
    assert "https://www.jpmorgan.com/insights/markets-and-economy" not in urls


def test_listing_filters_out_a_shallow_section_href() -> None:
    """Real guard on the depth filter: a 2-slash section href (e.g. a bare
    '/markets' category page) must be dropped while a 3-slash article href
    from the same fragment survives. Verified to fail if _MIN_PATH_DEPTH's
    check is removed from parse_listing -- see fix-round notes in the report.
    """
    fragment = (
        '<a href="/insights/markets-and-economy/markets">Markets section</a>'
        '<a href="/insights/markets-and-economy/markets/'
        'etfs-trading-institutional-liquidity">ETFs article</a>'
    )
    urls = parse_listing(fragment)
    assert urls == [
        "https://www.jpmorgan.com/insights/markets-and-economy/markets/"
        "etfs-trading-institutional-liquidity"
    ]


def test_article_parses_title_date_and_summary() -> None:
    item = parse_article(ARTICLE, URL)
    assert item is not None
    assert item.firm == "J.P. Morgan"
    assert item.show is None
    assert item.kind == "article"
    assert item.duration is None
    assert item.title and "&amp;" not in item.title, "HTML entities must be unescaped"
    assert item.summary
    assert item.published.tzinfo is not None


def test_article_without_a_publish_date_is_skipped() -> None:
    """Fails CLOSED. Emitting a guessed date would corrupt the recency window."""
    stripped = ARTICLE.replace('name="publishDate"', 'name="somethingElse"')
    assert parse_article(stripped, URL) is None


def test_article_with_an_unparseable_date_is_skipped() -> None:
    import re
    broken = re.sub(r'(<meta name="publishDate" content=")[^"]*', r"\1not-a-date", ARTICLE)
    assert parse_article(broken, URL) is None


def test_article_without_a_title_is_skipped() -> None:
    import re
    untitled = re.sub(r"<title>.*?</title>", "", ARTICLE, flags=re.S)
    assert parse_article(untitled, URL) is None


def test_empty_html_yields_nothing() -> None:
    assert parse_listing("") == []
    assert parse_article("", URL) is None


def test_listing_failure_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_curl(args: list[str]) -> str:
        (url,) = args
        if url == LISTING_URL:
            raise RuntimeError("simulated listing failure")
        raise AssertionError("no article should be requested if the listing fails")

    monkeypatch.setattr(jpm_web, "curl", fake_curl)
    assert fetch(NOW) == []


@pytest.mark.parametrize("bad_listing", ["", "<html>nope</html>"])
def test_garbage_listing_response_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch, bad_listing: str
) -> None:
    """Pins the curl()-never-raises chain: a garbage/empty listing yields no
    urls to parse_listing, so no article is ever requested."""

    def fake_curl(args: list[str]) -> str:
        (url,) = args
        assert url == LISTING_URL, "no article should be requested from a garbage listing"
        return bad_listing

    monkeypatch.setattr(jpm_web, "curl", fake_curl)
    assert fetch(NOW) == []


def test_one_bad_article_does_not_abort_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The governing rule: one dead article must never take down the rest."""
    urls = parse_listing(LISTING)
    assert len(urls) >= 2, "fixture should list multiple articles"
    broken_url = urls[0]
    calls: list[str] = []

    def fake_curl(args: list[str]) -> str:
        (url,) = args
        calls.append(url)
        if url == LISTING_URL:
            return LISTING
        if url == broken_url:
            raise RuntimeError("simulated article failure")
        return ARTICLE

    monkeypatch.setattr(jpm_web, "curl", fake_curl)
    items = fetch(NOW)

    assert calls == [LISTING_URL] + urls, "every article must still be attempted"
    assert items, "the surviving articles should still produce items"
    assert len(items) == len(urls) - 1
    assert all(i.url != broken_url for i in items)


def test_requests_listing_first_then_each_article_in_listing_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this, a typo dropping or reordering a request is invisible."""
    urls = parse_listing(LISTING)
    calls: list[str] = []

    def fake_curl(args: list[str]) -> str:
        (url,) = args
        calls.append(url)
        return LISTING if url == LISTING_URL else ARTICLE

    monkeypatch.setattr(jpm_web, "curl", fake_curl)
    fetch(NOW)

    assert calls == [LISTING_URL] + urls
