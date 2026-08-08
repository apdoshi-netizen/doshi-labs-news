"""J.P. Morgan Top Market Takeaways -- the one written-research source.

No feed exists. The listing page is server-rendered but carries no dates; each
article page carries <meta name="publishDate">, <title>, and
<meta name="description">. So the listing gives us URLs and each article page
gives us everything else, at one fetch per listed article (8-9 per run).

FAILS CLOSED by design: an article with no parseable date is skipped rather
than emitted with a guessed one, because a wrong date would silently corrupt
the trailing-24h window. If JPM restructures their markup this source returns
nothing and every other source keeps working.
"""
import datetime
import html
import re

from wsjdaily.http import curl
from wsjdaily.sources import Item

LISTING_URL = "https://www.jpmorgan.com/insights/markets-and-economy/top-market-takeaways"
BASE = "https://www.jpmorgan.com"
SUMMARY_CHARS = 240

_LINK = re.compile(r'href="(/insights/markets-and-economy/[^"#?]+)"')
_DATE = re.compile(r'<meta[^>]+name="publishDate"[^>]+content="([^"]+)"')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_DESC = re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]*)"')
_MIN_PATH_DEPTH = 3          # /insights/markets-and-economy/<section>/<slug>


def parse_listing(page: str) -> list[str]:
    """Absolute article URLs from the listing, deduped, order preserved."""
    seen, out = set(), []
    for path in _LINK.findall(page or ""):
        if path.strip("/").count("/") < _MIN_PATH_DEPTH - 1:
            continue                                  # section page, not an article
        url = BASE + path
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _clean_title(raw: str) -> str:
    """Unescape entities and drop any ' | J.P. Morgan' style suffix."""
    title = html.unescape(re.sub(r"\s+", " ", raw)).strip()
    return title.split(" | ")[0].strip()


def parse_article(page: str, url: str) -> Item | None:
    """Build an Item from an article page, or None if anything required is absent."""
    page = page or ""
    dm, tm = _DATE.search(page), _TITLE.search(page)
    if not (dm and tm):
        return None
    try:
        published = datetime.datetime.fromisoformat(dm.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=datetime.timezone.utc)
    title = _clean_title(tm.group(1))
    if not title:
        return None
    desc = _DESC.search(page)
    return Item(
        firm="J.P. Morgan",
        show=None,
        title=title,
        url=url,
        published=published,
        kind="article",
        duration=None,
        summary=html.unescape(desc.group(1)).strip()[:SUMMARY_CHARS] if desc else "",
    )


def fetch(now: datetime.datetime) -> list[Item]:
    """Fetch the listing, then each article page. Never raises.

    `now` is accepted for interface symmetry; article pages carry absolute
    dates, so no relative-time arithmetic happens here.
    """
    out: list[Item] = []
    try:
        urls = parse_listing(curl([LISTING_URL]))
    except Exception as e:                            # noqa: BLE001
        print("jpm_web: listing failed: %s" % str(e)[:120])
        return out
    for url in urls:
        try:
            item = parse_article(curl([url]), url)
        except Exception as e:                        # noqa: BLE001
            print("jpm_web: %s failed: %s" % (url[-48:], str(e)[:80]))
            continue
        if item:
            out.append(item)
    return out
