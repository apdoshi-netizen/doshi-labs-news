"""Podcast adapter backed by Apple's public lookup API.

Chosen over the shows' own RSS feeds because the GS (megaphone) and MS (art19)
feeds contain NO <link> element -- only .mp3 enclosures -- and firm-site episode
URLs cannot be derived from titles (verified: every constructed slug 404s).
Apple supplies a stable per-episode URL and a clean ISO date in one call.

The feeds also stamp dates as -0000, which makes email.utils.parsedate_to_datetime
return a NAIVE datetime; Apple's ISO releaseDate sidesteps that entirely.
"""
import datetime
import json
from dataclasses import dataclass

from wsjdaily.http import curl
from wsjdaily.sources import Item

LOOKUP = "https://itunes.apple.com/lookup?id={id}&entity=podcastEpisode&limit=10"
SUMMARY_CHARS = 240


@dataclass(frozen=True)
class Show:
    """One podcast, identified by its Apple collection id."""

    firm: str
    name: str
    itunes_id: int


SHOWS: tuple[Show, ...] = (
    Show("Morgan Stanley", "Thoughts on the Market", 1466686717),
    Show("Goldman Sachs", "Exchanges", 948913991),
    # Goldman publishes under TWO podcast brands, both surfacing on
    # goldmansachs.com/insights. Exchanges alone misses roughly half of it --
    # the 2026-08-07 "Why US Stocks May Grind Higher" episode lives under
    # /insights/the-markets/ and never reached the digest until this was added.
    Show("Goldman Sachs", "The Markets", 1683802600),
    Show("Goldman Sachs", "Talks at GS", 1373320104),
    Show("J.P. Morgan", "Making Sense", 1456184829),
    Show("J.P. Morgan", "Eye on the Market", 1367963156),
)


def _parse_date(raw: str | None) -> datetime.datetime | None:
    """Parse Apple's ISO releaseDate into an aware datetime, or None."""
    if not raw:
        return None
    try:
        # Normalise the trailing Z explicitly rather than relying on the
        # running Python version's fromisoformat behaviour (CI pins 3.11).
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration(millis: object) -> str | None:
    """Render trackTimeMillis as whole minutes, e.g. '5 min'."""
    if not isinstance(millis, (int, float)) or millis <= 0:
        return None
    return "%d min" % max(1, round(millis / 60000))


def parse(payload: dict, show: Show) -> list[Item]:
    """Convert one lookup response into Items. Malformed input yields []."""
    results = (payload or {}).get("results") or []
    if not isinstance(results, list):
        return []
    items: list[Item] = []
    for r in results:
        if not isinstance(r, dict) or r.get("wrapperType") != "podcastEpisode":
            continue
        published = _parse_date(r.get("releaseDate"))
        url = r.get("trackViewUrl")
        title = (r.get("trackName") or "").strip()
        if not (published and url and title):
            continue
        items.append(Item(
            firm=show.firm,
            show=show.name,
            title=title,
            url=url,
            published=published,
            kind="podcast",
            duration=_duration(r.get("trackTimeMillis")),
            summary=(r.get("description") or "").strip()[:SUMMARY_CHARS],
        ))
    return items


def fetch(now: datetime.datetime) -> list[Item]:
    """Fetch every configured show. A failing show yields no items, never raises.

    `now` is accepted for interface symmetry with the other adapters; Apple
    returns absolute dates, so no relative-time arithmetic is needed here.
    """
    out: list[Item] = []
    for show in SHOWS:
        try:
            out.extend(parse(json.loads(curl([LOOKUP.format(id=show.itunes_id)])), show))
        except Exception as e:                       # noqa: BLE001 - isolation is the point
            print("apple: %s failed: %s" % (show.name, str(e)[:120]))
    return out
