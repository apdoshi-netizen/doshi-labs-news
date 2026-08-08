"""Content source adapters.

Each adapter module exposes one function, `fetch(now) -> list[Item]`. Adapters
never write files, never call the model, and never import one another.
"""
import datetime
import re
from dataclasses import dataclass

KINDS = ("podcast", "article")

# Generous enough that every real summary observed fits whole. Measured across
# the four podcast feeds, the useful text runs 252-759 chars; this only bites on
# an outlier, and then breaks at a word rather than mid-syllable.
SUMMARY_CHARS = 800


def clean_summary(raw: str | None) -> str:
    """First paragraph of a publisher blurb, whitespace-normalised.

    Podcast descriptions run 2,000-5,300 characters, but only the opening
    paragraph is the actual summary -- everything after it is a full transcript,
    a recording date, legal disclaimers, or ad-network boilerplate. Splitting on
    the first blank line keeps the substance and drops all of that, which is why
    this is not simply a longer truncation.

    Truncates on a word boundary when a paragraph really is over-long, so the
    email never cuts mid-word the way a hard character slice does.
    """
    text = (raw or "").replace("\xa0", " ").strip()
    if not text:
        return ""
    first = re.split(r"\n\s*\n", text)[0]
    first = re.sub(r"\s+", " ", first).strip()
    if len(first) <= SUMMARY_CHARS:
        return first
    return first[:SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


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
