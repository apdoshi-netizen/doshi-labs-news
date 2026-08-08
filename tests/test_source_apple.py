"""Apple podcast adapter: parsing only, no network."""
import datetime
import json
import pathlib

import pytest

from wsjdaily.sources import Item
from wsjdaily.sources.apple import SHOWS, Show, parse

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "apple_thoughts.json"
SHOW = Show(firm="Morgan Stanley", name="Thoughts on the Market", itunes_id=1466686717)


def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parses_every_episode_in_the_fixture() -> None:
    items = parse(payload(), SHOW)
    assert len(items) >= 5
    assert all(isinstance(i, Item) for i in items)


def test_skips_the_collection_wrapper() -> None:
    """results[0] is the podcast itself, not an episode."""
    raw = payload()
    collections = [r for r in raw["results"] if r.get("wrapperType") != "podcastEpisode"]
    assert collections, "fixture should contain the collection wrapper"
    titles = {i.title for i in parse(raw, SHOW)}
    assert collections[0].get("collectionName") not in titles


def test_every_published_is_timezone_aware() -> None:
    for item in parse(payload(), SHOW):
        assert item.published.tzinfo is not None
        assert item.published.utcoffset() is not None


def test_carries_show_and_firm_metadata() -> None:
    item = parse(payload(), SHOW)[0]
    assert item.firm == "Morgan Stanley"
    assert item.show == "Thoughts on the Market"
    assert item.kind == "podcast"


def test_url_is_the_apple_episode_page() -> None:
    for item in parse(payload(), SHOW):
        assert item.url.startswith("https://podcasts.apple.com/")


def test_duration_renders_as_whole_minutes() -> None:
    items = [i for i in parse(payload(), SHOW) if i.duration]
    assert items, "fixture should have at least one episode with a duration"
    assert items[0].duration.endswith(" min")


def test_episode_missing_a_url_is_skipped_not_crashed() -> None:
    raw = payload()
    for r in raw["results"]:
        if r.get("wrapperType") == "podcastEpisode":
            r.pop("trackViewUrl", None)
            break
    before = len(parse(payload(), SHOW))
    assert len(parse(raw, SHOW)) == before - 1


def test_episode_missing_a_date_is_skipped_not_crashed() -> None:
    raw = payload()
    for r in raw["results"]:
        if r.get("wrapperType") == "podcastEpisode":
            r.pop("releaseDate", None)
            break
    before = len(parse(payload(), SHOW))
    assert len(parse(raw, SHOW)) == before - 1


def test_malformed_payload_yields_no_items() -> None:
    assert parse({}, SHOW) == []
    assert parse({"results": None}, SHOW) == []


def test_all_four_shows_are_configured() -> None:
    assert len(SHOWS) == 4
    ids = {s.itunes_id for s in SHOWS}
    assert ids == {1466686717, 948913991, 1456184829, 1367963156}
    assert {s.firm for s in SHOWS} == {"Morgan Stanley", "Goldman Sachs", "J.P. Morgan"}
