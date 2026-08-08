"""Research section assembly: windowing, dedup, ordering, isolation."""
import datetime

from wsjdaily.slots import CANONICAL_ORDER
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


def test_an_adapter_returning_non_items_does_not_raise(monkeypatch) -> None:
    """A bare dict has no `.published`; without the isinstance filter the
    comprehension raises AttributeError straight into main()."""
    monkeypatch.setattr(generate, "RESEARCH_SOURCES",
                        (lambda now: [{"title": "not an Item"}, item(1, "ok")],))
    assert [i.title for i in generate.collect_research(NOW, set())] == ["ok"]


def test_an_item_carrying_a_naive_datetime_does_not_raise(monkeypatch) -> None:
    """Comparing a naive datetime to an aware one raises TypeError, and that
    happens in the filter stage -- OUTSIDE the per-adapter try. Item's
    __post_init__ rejects naive datetimes at construction, so the only way in is
    post-construction mutation; the point is that the guard, not the validator,
    is what stops this reaching main(). Without the second try this test errors
    with `TypeError: can't compare offset-naive and offset-aware datetimes`.
    """
    bad = item(1, "naive")
    object.__setattr__(bad, "published", datetime.datetime(2026, 8, 8, 10, 0))

    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (lambda now: [bad],))
    assert generate.collect_research(NOW, set()) == []


def test_payload_is_json_serialisable_with_iso_dates() -> None:
    import json

    payload = generate.research_payload([item(1, "x")])
    json.dumps(payload)
    row = payload[0]
    assert set(row) == {"firm", "show", "title", "url", "published", "kind",
                        "duration", "summary"}
    assert row["published"].startswith("2026-08-08T")


# --- main()-level wiring -----------------------------------------------------
# collect_research being isolated is worth nothing if main() is wired to it
# wrongly. These run the REAL main() in a tmp cwd with the WSJ path stubbed.

_SLOT_URL = {k: "https://www.wsj.com/x/slot-%d" % n for n, k in enumerate(CANONICAL_ORDER)}


def _stub_wsj(monkeypatch, tmp_path) -> None:
    """Hermetic, minimally-sufficient WSJ path: one candidate per slot, all
    resolvable, all chosen. Mirrors how tests/test_resolve_loop.py stubs it."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESCUE_ONLY", raising=False)
    monkeypatch.setattr(generate.sys, "argv", ["generate.py"])
    monkeypatch.setattr(generate, "fetch_candidates", lambda: {
        k: [{"i": 0, "title": "Story %s" % k, "ageHrs": 5.0, "url": "gn://%s" % k}]
        for k in CANONICAL_ORDER})
    monkeypatch.setattr(generate, "resolve_one",
                        lambda gn: _SLOT_URL[gn.removeprefix("gn://")])
    monkeypatch.setattr(generate, "curate_with_claude", lambda cands, covered: [
        {"slot": k, "i": 0, "storyKey": None, "summary": "s"} for k in CANONICAL_ORDER])


def _run_main(tmp_path) -> dict:
    import json

    generate.main()
    return json.loads((tmp_path / "picks.json").read_text())


def test_main_survives_a_raising_adapter_and_still_writes_the_wsj_digest(
        tmp_path, monkeypatch, capsys) -> None:
    """THE governing rule, at the level that matters: a dead research source
    must not cost the WSJ digest, and an empty research list must not trip the
    zero-resolved sys.exit(1) guard."""
    def boom(now):
        raise RuntimeError("network down")

    _stub_wsj(monkeypatch, tmp_path)
    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (boom,))

    written = _run_main(tmp_path)          # no SystemExit, no exception
    capsys.readouterr()

    assert [p["slot"] for p in written["picks"]] == list(CANONICAL_ORDER)
    assert all(p["url"] for p in written["picks"]), "all 5 WSJ slots still resolved"
    assert written["research"] == []


def test_main_survives_a_broken_history_lookup(tmp_path, monkeypatch, capsys) -> None:
    """The seen-URL lookup is section-2 code on section-1's critical path too:
    a malformed history.json must cost the research section, not the digest."""
    def boom(hist, exclude=None):
        raise ValueError("corrupt history")

    _stub_wsj(monkeypatch, tmp_path)
    monkeypatch.setattr(generate.history, "research_urls", boom)
    monkeypatch.setattr(generate, "RESEARCH_SOURCES", ())

    written = _run_main(tmp_path)
    capsys.readouterr()
    assert all(p["url"] for p in written["picks"])
    assert written["research"] == []


def test_main_writes_research_into_picks_json(tmp_path, monkeypatch, capsys) -> None:
    """The happy path: research actually reaches the file the mailer reads, and
    picks keeps its exact existing shape alongside it."""
    _stub_wsj(monkeypatch, tmp_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = Item(firm="Goldman Sachs", show="Exchanges", title="Live one",
                 url="https://podcasts.apple.com/live",
                 published=now - datetime.timedelta(hours=2), kind="podcast")
    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (lambda n: [fresh],))

    written = _run_main(tmp_path)
    capsys.readouterr()

    assert [r["url"] for r in written["research"]] == ["https://podcasts.apple.com/live"]
    assert set(written["picks"][0]) >= {"label", "title", "url", "summary"}


def test_a_second_run_the_same_day_re_emits_the_first_runs_research(
        tmp_path, monkeypatch, capsys) -> None:
    """The workflow fires two full runs before the 09:00 ET send and the SECOND
    run's picks.json is the one mailed. Today's own history entries must not
    suppress it, or the shipped digest's research section is empty."""
    _stub_wsj(monkeypatch, tmp_path)
    now = datetime.datetime.now(datetime.timezone.utc)
    fresh = Item(firm="Morgan Stanley", show="Thoughts on the Market", title="Same item",
                 url="https://podcasts.apple.com/same",
                 published=now - datetime.timedelta(hours=2), kind="podcast")
    monkeypatch.setattr(generate, "RESEARCH_SOURCES", (lambda n: [fresh],))

    first = _run_main(tmp_path)
    second = _run_main(tmp_path)           # same cwd, so it reads run 1's history
    capsys.readouterr()

    assert [r["url"] for r in first["research"]] == ["https://podcasts.apple.com/same"]
    assert [r["url"] for r in second["research"]] == ["https://podcasts.apple.com/same"]
