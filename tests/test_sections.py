"""WSJ URL section extraction and candidate claimability."""
from generate import BLOCKED_SECTIONS, is_claimable, url_section
from wsjdaily.history import norm_title


def test_extracts_the_first_path_segment() -> None:
    assert url_section("https://www.wsj.com/tech/sk-hynix-abc123") == "tech"
    assert url_section("https://www.wsj.com/business/deals/easyjet-fbe3") == "business"


def test_identifies_the_blocked_sections_seen_in_history() -> None:
    assert url_section("https://www.wsj.com/pro/central-banking/ecb-xyz") in BLOCKED_SECTIONS
    assert url_section("https://www.wsj.com/podcasts/minute-briefing/abc") in BLOCKED_SECTIONS


def test_bare_article_urls_are_not_blocked() -> None:
    """These resolve and work; they are just non-canonical."""
    assert url_section("https://www.wsj.com/articles/microsoft-profit-abc") not in BLOCKED_SECTIONS


def test_handles_urls_with_query_strings_and_no_path() -> None:
    assert url_section("https://www.wsj.com/opinion/socialism-2b3f?mod=hp_lead") == "opinion"
    assert url_section("https://www.wsj.com/") == ""
    assert url_section("") == ""


OK_URL = "https://www.wsj.com/business/deals/serie-a-abc123"


def test_claimable_candidate_returns_empty_reason() -> None:
    assert is_claimable(OK_URL, "silverlake+seriea", set(), set(), set(), set()) == ""


def test_blocked_section_is_rejected() -> None:
    pro = "https://www.wsj.com/pro/central-banking/ecb-xyz"
    assert is_claimable(pro, "ecb+rates", set(), set(), set(), set()) == "section"


def test_url_already_used_today_or_on_a_prior_day_is_rejected() -> None:
    assert is_claimable(OK_URL, None, set(), set(), {OK_URL}, set()) == "dup-url"
    assert is_claimable(OK_URL, None, set(), set(), set(), {OK_URL}) == "dup-url"


def test_storyline_hard_blocked_in_the_last_two_days_is_rejected() -> None:
    assert is_claimable(OK_URL, "integer+kkr", {"integer+kkr"}, set(), set(), set()) == "storyline"


def test_sports_claims_a_story_and_industry_is_then_rejected_for_it() -> None:
    """Diversity precedence: Sports resolves first and claims the story, so the
    same story is no longer claimable when Industry is resolved."""
    used_keys: set[str] = set()
    assert is_claimable(OK_URL, "silverlake+seriea", set(), used_keys, set(), set()) == ""
    used_keys.add("silverlake+seriea")          # Sports claims it
    assert is_claimable(OK_URL, "silverlake+seriea", set(), used_keys, set(), set()) == "storyline"


def test_missing_story_key_never_blocks_on_storyline() -> None:
    """A null key degrades to URL matching; it must not cost an article."""
    assert is_claimable(OK_URL, None, {"integer+kkr"}, {"a+b"}, set(), set()) == ""


SERIE_A = "Silver Lake Buys Stake in Serie A for $2 Billion"


def test_keyless_fallback_duplicating_a_claimed_title_is_rejected() -> None:
    """The in-run hole: a fallback carries no storyKey, so only the title guard
    can stop it re-claiming a story another slot already took this run."""
    other_url = "https://www.wsj.com/business/deals/serie-a-different-xyz"
    used_titles = {norm_title(SERIE_A)}
    assert is_claimable(other_url, None, set(), set(), set(), set(),
                        SERIE_A, used_titles) == "dup-story"


def test_title_guard_ignores_prefixes_and_punctuation() -> None:
    """norm_title strips 'Exclusive |' style prefixes, so a re-headlined
    duplicate of a claimed story is still caught."""
    assert is_claimable(OK_URL + "-2", None, set(), set(), set(), set(),
                        "Exclusive | " + SERIE_A, {norm_title(SERIE_A)}) == "dup-story"


def test_title_guard_is_in_run_only_and_never_consults_history() -> None:
    """A None storyKey must still never block on HISTORY grounds -- an unclaimed
    title passes even with the history sets populated."""
    assert is_claimable(OK_URL, None, {"integer+kkr"}, {"a+b"}, set(), set(),
                        "Apollo to Buy easyJet for $7.7 Billion", {norm_title(SERIE_A)}) == ""


def test_title_guard_defaults_off_for_existing_callers() -> None:
    """The pre-existing 6-argument contract keeps working unchanged."""
    assert is_claimable(OK_URL, None, set(), set(), set(), set()) == ""
