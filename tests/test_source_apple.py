"""Apple podcast adapter: parsing only, no network."""
import datetime
import json
import pathlib

import pytest

from wsjdaily.sources import Item
from wsjdaily.sources import apple
from wsjdaily.sources.apple import SHOWS, Show, fetch, parse

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "apple_thoughts.json"
SHOW = Show(firm="Morgan Stanley", name="Thoughts on the Market", itunes_id=1466686717)
NOW = datetime.datetime(2026, 8, 7, 12, 0, tzinfo=datetime.timezone.utc)


def payload() -> dict:
    return json.loads(FIXTURE.read_text())


def raw_fixture_text() -> str:
    return FIXTURE.read_text()


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
    # Pins the floor-vs-round behaviour against the known fixture episode:
    # trackTimeMillis 308000 -> 308000 / 60000 = 5.13(3)... -> round() == 5.
    raw = payload()
    first_episode = next(
        r for r in raw["results"] if r.get("wrapperType") == "podcastEpisode"
    )
    assert first_episode["trackTimeMillis"] == 308000
    assert items[0].duration == "5 min"


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


def test_episode_missing_a_title_is_skipped_not_crashed() -> None:
    raw = payload()
    for r in raw["results"]:
        if r.get("wrapperType") == "podcastEpisode":
            r.pop("trackName", None)
            break
    before = len(parse(payload(), SHOW))
    assert len(parse(raw, SHOW)) == before - 1


def test_malformed_payload_yields_no_items() -> None:
    assert parse({}, SHOW) == []
    assert parse({"results": None}, SHOW) == []


def test_all_configured_shows_are_present() -> None:
    assert len(SHOWS) == 6
    ids = {s.itunes_id for s in SHOWS}
    assert ids == {1466686717, 948913991, 1683802600, 1373320104,
                   1456184829, 1367963156}
    assert {s.firm for s in SHOWS} == {"Morgan Stanley", "Goldman Sachs", "J.P. Morgan"}


def test_goldman_has_both_of_its_podcast_brands() -> None:
    """Regression: only Exchanges was configured, so the 2026-08-07 episode
    published under /insights/the-markets/ never reached the digest at all."""
    gs = {s.name for s in SHOWS if s.firm == "Goldman Sachs"}
    assert gs == {"Exchanges", "The Markets", "Talks at GS"}


def _id_in_url(args: list[str]) -> int:
    """Pull the itunes id back out of the lookup URL curl() was called with."""
    (url,) = args
    marker = "id="
    start = url.index(marker) + len(marker)
    end = url.index("&", start)
    return int(url[start:end])


def test_one_failing_show_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """The governing rule: a dead show must never take down the other three."""
    broken_id = SHOWS[0].itunes_id
    calls: list[int] = []

    def fake_curl(args: list[str]) -> str:
        sid = _id_in_url(args)
        calls.append(sid)
        if sid == broken_id:
            raise RuntimeError("simulated curl failure for this show")
        return raw_fixture_text()

    monkeypatch.setattr(apple, "curl", fake_curl)
    items = fetch(NOW)

    assert calls == [s.itunes_id for s in SHOWS], "every show must still be attempted"
    surviving_firms = {s.firm for s in SHOWS if s.itunes_id != broken_id}
    assert items, "the three surviving shows should still produce items"
    # Every returned item's firm must belong to a surviving show. The Item.firm
    # is stamped from the requesting Show, so this proves the broken show's
    # own parse never contributed to the output.
    assert {i.firm for i in items} <= surviving_firms


@pytest.mark.parametrize("bad_response", ["", "not json"])
def test_empty_or_garbage_curl_response_is_survived(
    monkeypatch: pytest.MonkeyPatch, bad_response: str
) -> None:
    """Pins the json.loads("") chain: curl() never raises, it returns "" on
    failure, so isolation depends on json.loads raising *inside* the try."""

    def fake_curl(args: list[str]) -> str:
        return bad_response

    monkeypatch.setattr(apple, "curl", fake_curl)
    assert fetch(NOW) == []


def test_all_four_shows_are_actually_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this, a typo dropping a show from the loop is invisible."""
    requested_ids: list[int] = []

    def fake_curl(args: list[str]) -> str:
        requested_ids.append(_id_in_url(args))
        return raw_fixture_text()

    monkeypatch.setattr(apple, "curl", fake_curl)
    fetch(NOW)
    assert set(requested_ids) == {s.itunes_id for s in SHOWS}
