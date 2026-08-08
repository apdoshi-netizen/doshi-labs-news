"""Storyline identity, coverage windows, and history persistence."""
import json

from wsjdaily.history import (
    blocked_story_keys,
    covered_story_keys,
    load,
    norm_story_key,
    norm_title,
    prior_keys,
    save,
)


def test_norm_title_strips_prefixes_and_punctuation() -> None:
    assert norm_title("Exclusive | Trump Has Called Warsh") == norm_title("Trump Has Called Warsh")
    assert norm_title("Opinion | The Everything Tax") == norm_title("The Everything Tax")


def test_norm_story_key_is_order_independent() -> None:
    assert norm_story_key("KKR Integer") == "integer+kkr"
    assert norm_story_key("integer, KKR") == "integer+kkr"


def test_norm_story_key_deduplicates_and_caps_at_four_tokens() -> None:
    assert norm_story_key("kkr kkr integer") == "integer+kkr"
    assert len(norm_story_key("a b c d e f").split("+")) == 4


def test_norm_story_key_returns_none_for_empty_or_missing() -> None:
    assert norm_story_key(None) is None
    assert norm_story_key("") is None
    assert norm_story_key("   ") is None
    assert norm_story_key("!!! ???") is None


HIST = {
    "2026-08-04": [{"title": "Older Story", "url": "u0", "storyKey": "ccc+ddd"}],
    "2026-08-05": [{"title": "Old Story", "url": "u1", "storyKey": "aaa+bbb"}],
    "2026-08-06": [{"title": "KKR Near Deal to Buy Integer", "url": "u2", "storyKey": "integer+kkr"}],
    "2026-08-07": [{"title": "Today Pick", "url": "u3", "storyKey": "zzz+yyy"}],
}


def test_hard_block_covers_two_days_and_ignores_today() -> None:
    blocked = blocked_story_keys(HIST, "2026-08-07")
    assert "integer+kkr" in blocked      # yesterday -> hard blocked
    assert "aaa+bbb" in blocked          # two days back -> also hard blocked
    assert "zzz+yyy" not in blocked      # today's own entry never blocks itself


def test_hard_block_does_not_reach_three_days_back() -> None:
    blocked = blocked_story_keys(HIST, "2026-08-07")
    covered = covered_story_keys(HIST, "2026-08-07")
    assert "ccc+ddd" not in blocked      # three days back is outside the hard block
    assert "ccc+ddd" in covered          # but still surfaced in the soft window


def test_soft_window_surfaces_older_keys_for_model_judgment() -> None:
    covered = covered_story_keys(HIST, "2026-08-07")
    assert "aaa+bbb" in covered
    assert "integer+kkr" in covered
    assert "ccc+ddd" in covered
    assert "zzz+yyy" not in covered


def test_entries_without_a_story_key_are_skipped_not_crashed() -> None:
    hist = {"2026-08-06": [{"title": "Legacy Row", "url": "u"}]}
    assert blocked_story_keys(hist, "2026-08-07") == set()
    assert covered_story_keys(hist, "2026-08-07") == []


def test_prior_keys_ignores_today_and_respects_21_day_cutoff() -> None:
    titles, urls = prior_keys(HIST, "2026-08-07")
    assert norm_title("Old Story") in titles
    assert "u2" in urls
    assert "u3" not in urls  # today's own picks never exclude themselves


def test_prior_keys_covers_the_full_21_day_window() -> None:
    hist = {
        "2026-07-17": [{"title": "Exactly 21 Days Back", "url": "u21", "storyKey": None}],
        "2026-07-16": [{"title": "22 Days Back", "url": "u22", "storyKey": None}],
    }
    titles, urls = prior_keys(hist, "2026-08-07")
    assert "u21" in urls   # 21 days back is still inside the window
    assert "u22" not in urls  # 22 days back has aged out


def test_save_writes_story_key_and_prunes_beyond_21_days(tmp_path) -> None:
    path = str(tmp_path / "history.json")
    hist = {"2026-01-01": [{"title": "Ancient", "url": "old", "storyKey": "x+y"}]}
    picks = [
        {"title": "New Pick", "url": "https://wsj.com/a", "storyKey": "apollo+easyjet"},
        {"title": "Empty Slot", "url": "", "storyKey": None},
    ]
    save(hist, "2026-08-07", picks, path)
    written = json.loads(open(path).read())
    assert "2026-01-01" not in written                       # pruned
    assert written["2026-08-07"] == [
        {"title": "New Pick", "url": "https://wsj.com/a", "storyKey": "apollo+easyjet"}
    ]                                                        # url-less pick omitted


def test_load_returns_empty_dict_when_file_is_missing(tmp_path) -> None:
    assert load(str(tmp_path / "nope.json")) == {}


def test_non_date_keys_do_not_break_the_window() -> None:
    """Regression: '_research' does NOT sort before the cutoff ('_' is 0x5F,
    after the digits), so it lands inside the window. Without a date-shape
    guard, iterating its inner dict yields strings and raises AttributeError
    in prior_keys and blocked_story_keys -- taking down the WSJ section."""
    from wsjdaily.history import RESEARCH_KEY, blocked_story_keys, prior_keys

    hist = {
        "2026-08-06": [{"title": "A", "url": "u", "storyKey": "a+b"}],
        RESEARCH_KEY: {"2026-08-06": ["https://podcasts.apple.com/x"]},
    }
    # The premise: the key does NOT sort before a date cutoff, so it cannot be
    # excluded by ordering alone and the guard is genuinely required.
    assert not (RESEARCH_KEY < "2026-07-17")
    titles, urls = prior_keys(hist, "2026-08-07")
    assert "u" in urls
    assert blocked_story_keys(hist, "2026-08-07") == {"a+b"}


def test_research_urls_collects_across_days() -> None:
    from wsjdaily.history import RESEARCH_KEY, research_urls

    hist = {RESEARCH_KEY: {"2026-08-06": ["u1", "u2"], "2026-08-07": ["u2", "u3"]}}
    assert research_urls(hist) == {"u1", "u2", "u3"}


def test_research_urls_excludes_todays_own_entries() -> None:
    """(a) The workflow runs main() twice before the 09:00 ET send and each run
    regenerates picks.json from scratch. If today's own entries counted as
    'seen', run 2 would suppress everything run 1 emitted -- and run 2's file is
    the one that gets mailed."""
    from wsjdaily.history import RESEARCH_KEY, research_urls

    hist = {RESEARCH_KEY: {"2026-08-08": ["emitted-this-morning"]}}
    assert research_urls(hist, exclude="2026-08-08") == set()


def test_research_urls_still_suppresses_previous_days() -> None:
    """(b) Cross-day dedup -- the case the overlapping-window rule exists for --
    must survive the today-skip."""
    from wsjdaily.history import RESEARCH_KEY, research_urls

    hist = {RESEARCH_KEY: {"2026-08-07": ["yesterday"], "2026-08-08": ["today"]}}
    assert research_urls(hist, exclude="2026-08-08") == {"yesterday"}


def test_research_urls_without_exclude_keeps_every_day() -> None:
    """The default stays non-destructive for callers that want the full set."""
    from wsjdaily.history import RESEARCH_KEY, research_urls

    hist = {RESEARCH_KEY: {"2026-08-07": ["yesterday"], "2026-08-08": ["today"]}}
    assert research_urls(hist) == {"yesterday", "today"}


def test_research_urls_on_a_legacy_history_is_empty() -> None:
    from wsjdaily.history import research_urls

    assert research_urls({"2026-08-06": [{"title": "A", "url": "u"}]}) == set()


def test_save_records_research_urls_and_prunes_them(tmp_path) -> None:
    import json

    from wsjdaily.history import RESEARCH_KEY, save

    path = str(tmp_path / "history.json")
    hist = {RESEARCH_KEY: {"2026-01-01": ["ancient"]}}
    save(hist, "2026-08-07", [], path, research_urls=["https://podcasts.apple.com/new"])
    written = json.loads(open(path).read())
    assert written[RESEARCH_KEY] == {"2026-08-07": ["https://podcasts.apple.com/new"]}
    assert "2026-01-01" not in written[RESEARCH_KEY], "pruned on the same 21-day cutoff"


def test_save_without_research_leaves_the_key_absent(tmp_path) -> None:
    import json

    from wsjdaily.history import RESEARCH_KEY, save

    path = str(tmp_path / "history.json")
    save({}, "2026-08-07", [{"title": "T", "url": "https://wsj.com/a", "storyKey": None}], path)
    assert RESEARCH_KEY not in json.loads(open(path).read())
