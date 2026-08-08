"""Content source adapters.

Each adapter module exposes one function, `fetch(now) -> list[Item]`. Adapters
never write files, never call the model, and never import one another.
"""
import datetime
from dataclasses import dataclass

KINDS = ("podcast", "article")


@dataclass(frozen=True)
class Item:
    """One publication from a non-WSJ source."""

    firm: str                        # "Goldman Sachs" | "J.P. Morgan" | "Morgan Stanley"
    show: str | None                 # show name; None for written research
    title: str
    url: str
    published: datetime.datetime     # MUST be timezone-aware
    kind: str                        # one of KINDS
    duration: str | None = None      # e.g. "5 min"; None for articles
    summary: str = ""                # publisher-written, truncated

    def __post_init__(self) -> None:
        if self.published.tzinfo is None or self.published.utcoffset() is None:
            raise ValueError(
                "Item.published must be timezone-aware; got naive datetime for "
                + repr(self.title[:60])
            )
        if self.kind not in KINDS:
            raise ValueError("Item.kind must be one of %r; got %r" % (KINDS, self.kind))
