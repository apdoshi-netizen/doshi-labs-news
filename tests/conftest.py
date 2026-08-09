"""Make test hermeticity enforced rather than observed.

Every network call in this project goes through `wsjdaily.http.curl`. Tests are
supposed to monkeypatch it, but nothing checked that they did -- and twice
during development a change quietly sent the suite to the live network. Both
times the only symptom was wall-clock time (0.07s -> 13.44s); no assertion
failed, because hitting the real API returns real data and the tests passed.

This fixture replaces `curl` everywhere with one that raises, so a test that
forgets to patch fails loudly and immediately instead of silently depending on
Apple, Google News, and jpmorgan.com being up.

Patching `wsjdaily.http.curl` alone is NOT enough: four modules do
`from wsjdaily.http import curl`, which binds the function object into their own
namespace. Each binding is patched, and the list is discovered at runtime so a
new module importing curl is covered without anyone remembering to update this.
"""
import sys

import pytest

import wsjdaily.http


class UnpatchedNetworkCall(BaseException):
    """Raised when a test reaches the real network.

    Deliberately derived from BaseException, not Exception. Every adapter wraps
    its fetch in `except Exception` so one dead source cannot break the run --
    correct in production, but it would swallow this guard and let the test pass
    with an empty result, hiding the very mistake the guard exists to surface.
    BaseException sails through those handlers, the same trick pytest uses for
    its own control-flow exceptions.
    """


def _modules_binding_curl() -> list:
    """Every project module holding a reference to the real `curl`."""
    import generate  # noqa: F401  - importing it pulls in the whole tree

    real = wsjdaily.http.curl
    mods = []
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if name != "generate" and not name.startswith("wsjdaily"):
            continue
        if getattr(mod, "curl", None) is real:
            mods.append(mod)
    return mods


@pytest.fixture(autouse=True)
def _no_unpatched_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that calls curl without patching it first.

    Autouse, so it applies to every test. A test that legitimately fakes curl
    monkeypatches its own module afterwards, which overrides this and is undone
    in reverse at teardown -- so the guard is back in place for the next test.
    """
    def _raise(args: list[str]) -> str:
        target = (args or ["<no args>"])[-1]
        raise UnpatchedNetworkCall(
            "A test called curl without patching it, which would hit the real "
            "network. Target: %s. Monkeypatch the curl binding in the module "
            "under test, e.g. monkeypatch.setattr(wsjdaily.sources.apple, "
            '"curl", fake).' % target[:120]
        )

    for mod in _modules_binding_curl():
        monkeypatch.setattr(mod, "curl", _raise)
