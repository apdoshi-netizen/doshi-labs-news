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
    "2026-08-05": [{"title": "Old Story", "url": "u1", "storyKey": "aaa+bbb"}],
    "2026-08-06": [{"title": "KKR Near Deal to Buy Integer", "url": "u2", "storyKey": "integer+kkr"}],
    "2026-08-07": [{"title": "Today Pick", "url": "u3", "storyKey": "zzz+yyy"}],
}


def test_hard_block_covers_two_days_and_ignores_today() -> None:
    blocked = blocked_story_keys(HIST, "2026-08-07")
    assert "integer+kkr" in blocked      # yesterday -> hard blocked
    assert "aaa+bbb" not in blocked      # 2 days back is outside the window
    assert "zzz+yyy" not in blocked      # today's own entry never blocks itself


def test_soft_window_surfaces_older_keys_for_model_judgment() -> None:
    covered = covered_story_keys(HIST, "2026-08-07")
    assert "aaa+bbb" in covered
    assert "integer+kkr" in covered
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
