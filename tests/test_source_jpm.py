"""JPM Top Market Takeaways adapter: parsing only, no network."""
import pathlib

from wsjdaily.sources.jpm_web import parse_article, parse_listing

FIX = pathlib.Path(__file__).parent / "fixtures"
LISTING = (FIX / "jpm_listing.html").read_text(encoding="utf-8", errors="ignore")
ARTICLE = (FIX / "jpm_article.html").read_text(encoding="utf-8", errors="ignore")
URL = "https://www.jpmorgan.com/insights/markets-and-economy/markets/etfs-trading"


def test_listing_yields_absolute_deduped_urls() -> None:
    urls = parse_listing(LISTING)
    assert urls, "listing fixture should contain article links"
    assert len(urls) == len(set(urls)), "must be deduped"
    assert all(u.startswith("https://www.jpmorgan.com/insights/") for u in urls)


def test_listing_excludes_the_section_landing_page() -> None:
    """Shallow paths are sections, not articles."""
    urls = parse_listing(LISTING)
    assert "https://www.jpmorgan.com/insights/markets-and-economy" not in urls


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
