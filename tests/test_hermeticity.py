"""The hermeticity guard must actually guard something.

A conftest fixture that silently does nothing is worse than none at all: it
creates the impression of protection. These tests pin that the guard is
installed on every curl binding and that it fires.
"""
import pytest

import generate
import wsjdaily.http
from wsjdaily.sources import apple, jpm_web, wsj

from .conftest import UnpatchedNetworkCall

MODULES = [
    pytest.param(wsjdaily.http, id="wsjdaily.http"),
    pytest.param(generate, id="generate"),
    pytest.param(apple, id="sources.apple"),
    pytest.param(jpm_web, id="sources.jpm_web"),
    pytest.param(wsj, id="sources.wsj"),
]


@pytest.mark.parametrize("mod", MODULES)
def test_every_curl_binding_is_guarded(mod) -> None:
    """Patching wsjdaily.http alone would miss the four `from ... import curl`
    bindings, which is the whole reason the fixture discovers them at runtime."""
    with pytest.raises(UnpatchedNetworkCall):
        mod.curl(["https://example.com/should-never-be-fetched"])


def test_the_guard_names_the_target_it_blocked() -> None:
    """The message has to be actionable -- a bare failure sends someone hunting."""
    with pytest.raises(UnpatchedNetworkCall, match="itunes.apple.com"):
        apple.curl(["https://itunes.apple.com/lookup?id=1"])


def test_an_adapter_reaching_the_network_fails_loudly() -> None:
    """The realistic case, and the reason the guard derives from BaseException.

    Every adapter wraps its fetch in `except Exception` so one dead source
    cannot break the run. That is right in production but would swallow the
    guard here, returning [] and letting the test pass -- hiding exactly the
    mistake we are trying to surface. It must propagate instead.
    """
    import datetime

    with pytest.raises(UnpatchedNetworkCall):
        apple.fetch(datetime.datetime.now(datetime.timezone.utc))


def test_the_guard_survives_collect_researchs_isolation_too() -> None:
    """collect_research has its own two layers of `except Exception`."""
    import datetime

    with pytest.raises(UnpatchedNetworkCall):
        generate.collect_research(datetime.datetime.now(datetime.timezone.utc), set())


def test_a_test_may_still_patch_curl_for_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must not block legitimate fakes, or every existing test breaks."""
    monkeypatch.setattr(apple, "curl", lambda args: '{"results": []}')
    assert apple.curl(["anything"]) == '{"results": []}'
