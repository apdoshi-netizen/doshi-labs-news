"""Research section assembly: windowing, dedup, ordering, isolation."""
import datetime

from wsjdaily.sources import Item

import generate

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 8, 8, 11, 17, tzinfo=UTC)


def item(hours_ago: float, title: str = "T", url: str | None = None) -> Item:
    return Item(
        firm="Morgan Stanley", show="Thoughts on the Market", title=title,
        url=url or ("https://podcasts.apple.com/%s" % title),
        published=NOW - datetime.timedelta(hours=hours_ago), kind="podcast",
    )


def test_keeps_items_inside_the_24h_window(monkeypatch) -> None:
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(1, "fresh"), item(23.9, "edge")],))
    got = generate.collect_research(NOW, set())
    assert {i.title for i in got} == {"fresh", "edge"}


def test_drops_items_older_than_the_window(monkeypatch) -> None:
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(24.1, "stale")],))
    assert generate.collect_research(NOW, set()) == []


def test_drops_items_published_in_the_future(monkeypatch) -> None:
    """Clock skew on a source must not surface tomorrow's episode today."""
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(-2, "future")],))
    assert generate.collect_research(NOW, set()) == []


def test_drops_urls_already_emitted(monkeypatch) -> None:
    """Consecutive runs' 24h windows overlap when GitHub's scheduler drifts."""
    seen = {"https://podcasts.apple.com/dupe"}
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(2, "dupe"), item(3, "new")],))
    got = generate.collect_research(NOW, seen)
    assert [i.title for i in got] == ["new"]


def test_sorts_newest_first(monkeypatch) -> None:
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [item(10, "older"), item(1, "newer")],))
    assert [i.title for i in generate.collect_research(NOW, set())] == ["newer", "older"]


def test_a_failing_adapter_does_not_stop_the_others(monkeypatch) -> None:
    """THE governing rule: section 2 must never break section 1."""
    def boom(now):
        raise RuntimeError("network down")

    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (boom, lambda now: [item(1, "ok")]))
    assert [i.title for i in generate.collect_research(NOW, set())] == ["ok"]


def test_all_adapters_failing_yields_an_empty_list(monkeypatch) -> None:
    def boom(now):
        raise RuntimeError("down")

    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (boom, boom))
    assert generate.collect_research(NOW, set()) == []


def test_payload_is_json_serialisable_with_iso_dates() -> None:
    import json

    payload = generate.research_payload([item(1, "x")])
    json.dumps(payload)
    row = payload[0]
    assert set(row) == {"firm", "show", "title", "url", "published", "kind",
                        "duration", "summary"}
    assert row["published"].startswith("2026-08-08T")
