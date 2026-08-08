"""End-to-end test of main()'s selection/resolve loop.

Fully hermetic: the network layer (`resolve_one`) and the API layer
(`curate_with_claude`) are monkeypatched, the candidate pool is injected, and
cwd is moved to `tmp_path` so `picks.json` / `history.json` in the repo can
never be touched.

Covers the three highest-risk seams of the loop:
  a. the model's chosen id maps to the right article (id-mapping seam),
  b. a section-blocked (/pro/) model pick advances instead of emptying the slot,
  c. a story claimed by Sports cannot be re-claimed by Industry via a fallback
     candidate carrying no storyKey (cross-slot diversity, in-run).
"""
import datetime
import json

import pytest
from zoneinfo import ZoneInfo

import generate

SERIE_A = "Silver Lake Buys Stake in Serie A for $2 Billion"

# Google News url -> resolved direct wsj.com url. `None` means unresolvable.
RESOLVED = {
    "gn://macro-0": "https://www.wsj.com/economy/jobs-report-a1",
    "gn://macro-1": "https://www.wsj.com/economy/cpi-cools-b2",
    "gn://macro-2": "https://www.wsj.com/economy/fed-holds-c3",
    # Sports and Industry carry the SAME story at DIFFERENT wsj urls.
    "gn://sports-0": "https://www.wsj.com/business/deals/serie-a-sports-d4",
    "gn://ind-0": "https://www.wsj.com/business/deals/kkr-integer-e5",
    "gn://ind-1": "https://www.wsj.com/business/deals/serie-a-industry-f6",
    "gn://ind-2": "https://www.wsj.com/business/deals/easyjet-g7",
    "gn://oped-0": "https://www.wsj.com/opinion/everything-tax-h8",
    # Tech's model pick lands in the blocked /pro/ tier.
    "gn://tech-0": "https://www.wsj.com/pro/tech/nvidia-chip-i9",
    "gn://tech-1": "https://www.wsj.com/tech/meta-agent-j0",
}


def _pool(*pairs: tuple[str, str]) -> list[dict]:
    return [
        {"i": i, "title": t, "ageHrs": 5.0, "url": u}
        for i, (t, u) in enumerate(pairs)
    ]


CANDIDATES = {
    "Macro": _pool(
        ("Jobs Report Beats Expectations", "gn://macro-0"),
        ("CPI Cools in June", "gn://macro-1"),
        ("Fed Holds Rates Steady", "gn://macro-2"),
    ),
    "Industry / Company / Transaction": _pool(
        ("KKR to Acquire Integer for $5 Billion", "gn://ind-0"),
        (SERIE_A, "gn://ind-1"),
        ("Apollo to Buy easyJet for $7.7 Billion", "gn://ind-2"),
    ),
    "Op-Ed": _pool(("Opinion | The Everything Tax", "gn://oped-0")),
    "Tech": _pool(
        ("Nvidia Ships New Chip", "gn://tech-0"),
        ("Meta Releases Coding Agent", "gn://tech-1"),
    ),
    "Sports": _pool((SERIE_A, "gn://sports-0")),
}

# The model's picks. Macro deliberately picks id 2 (not the first row) so a
# broken id mapping cannot pass by accident. Industry picks the KKR story,
# which yesterday's history hard-blocks. Tech picks the /pro/ row.
SELECTIONS = [
    {"slot": "Macro", "i": 2, "storyKey": "fed+rates", "summary": "Fed stands pat."},
    {"slot": "Industry / Company / Transaction", "i": 0, "storyKey": "integer+kkr",
     "summary": "KKR buys Integer."},
    {"slot": "Op-Ed", "i": 0, "storyKey": "tax+everything", "summary": "A tax argument."},
    {"slot": "Tech", "i": 0, "storyKey": "chip+nvidia", "summary": "New chip ships."},
    {"slot": "Sports", "i": 0, "storyKey": "seriea+silverlake", "summary": "Silver Lake buys in."},
]


@pytest.fixture
def picks(tmp_path, monkeypatch, capsys) -> dict:
    """Run main() in an isolated cwd and return the picks.json it wrote."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESCUE_ONLY", raising=False)
    monkeypatch.setattr(generate.sys, "argv", ["generate.py"])

    today = datetime.datetime.now(ZoneInfo("America/New_York")).date()
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    (tmp_path / "history.json").write_text(json.dumps({
        yesterday: [{"title": "KKR Nears Deal for Integer",
                     "url": "https://www.wsj.com/business/deals/kkr-integer-old",
                     "storyKey": "integer+kkr"}]
    }))

    monkeypatch.setattr(generate, "fetch_candidates",
                        lambda: {k: [dict(c) for c in v] for k, v in CANDIDATES.items()})
    monkeypatch.setattr(generate, "resolve_one", lambda gn: RESOLVED.get(gn))
    monkeypatch.setattr(generate, "curate_with_claude",
                        lambda cands, covered: [dict(s) for s in SELECTIONS])

    generate.main()
    capsys.readouterr()
    return {p["slot"]: p for p in json.loads((tmp_path / "picks.json").read_text())["picks"]}


def test_chosen_id_maps_to_the_right_article(picks: dict) -> None:
    """Seam (a): id 2 must resolve to the third Macro row, not the first."""
    assert picks["Macro"]["title"] == "Fed Holds Rates Steady"
    assert picks["Macro"]["url"] == "https://www.wsj.com/economy/fed-holds-c3"
    assert picks["Macro"]["summary"] == "Fed stands pat."


def test_section_blocked_pick_advances_instead_of_emptying_the_slot(picks: dict) -> None:
    """Seam (b): the /pro/ pick is skipped and the next candidate is used."""
    assert picks["Tech"]["title"] == "Meta Releases Coding Agent"
    assert generate.url_section(picks["Tech"]["url"]) not in generate.BLOCKED_SECTIONS
    # Fallback, not the model's pick -- its summary/storyKey must not be carried.
    assert picks["Tech"]["summary"] == ""
    assert picks["Tech"]["storyKey"] is None


def test_sports_claiming_a_story_forces_industry_onto_a_different_one(picks: dict) -> None:
    """Seam (c): Industry's fallback must not duplicate the story Sports claimed.

    Sports resolves first (RESOLVE_ORDER) and claims the Serie A story.
    Industry's model pick is hard-blocked by yesterday's history, so it falls
    through to a candidate carrying the SAME headline at a different wsj url.
    That fallback carries no storyKey, so only the in-run title guard can stop
    it -- without which both slots email the same event.
    """
    assert picks["Sports"]["title"] == SERIE_A
    assert picks["Industry / Company / Transaction"]["title"] != SERIE_A
    assert picks["Industry / Company / Transaction"]["title"] == (
        "Apollo to Buy easyJet for $7.7 Billion")
