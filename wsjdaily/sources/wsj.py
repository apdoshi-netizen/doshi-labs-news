"""WSJ candidate fetching and direct-URL resolution.

Moved out of generate.py when non-WSJ sources arrived. Behaviour is unchanged.

Two things here are load-bearing and must not be "cleaned up":
  * resolve_one uses an unofficial Google endpoint (batchexecute) and needs
    curl -L plus `Cookie: CONSENT=YES+`. Without -L the response is an empty
    302 body carrying no signature.
  * Only a NON-Google IP can turn Google News links into direct wsj.com URLs;
    Google CAPTCHA-blocks its own datacenter ranges. That is why resolution
    runs in GitHub Actions rather than in Apps Script.
"""
import datetime
import email.utils
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from wsjdaily import filters
from wsjdaily.http import curl
from wsjdaily.slots import SLOTS

BLOCKED_SECTIONS = {"pro", "podcasts"}
RAW_POOL_CAP = 60              # pre-filter cap; filtering runs on a wide pool


def fetch_candidates_unfiltered() -> dict:
    """Fetch and clean the candidate pool for every slot, with no filters.reject applied.

    Returns contiguously indexed rows, up to RAW_POOL_CAP per slot, so
    filtering downstream operates on the wider pool rather than an
    already-truncated one. The cap is deliberately far above the post-filter
    cap of 15: truncating BEFORE filtering is a strict reduction, and it lands
    hardest on the slots carrying the most rejection logic, which were
    measured falling below MAX_RESOLVE_TRIES fallbacks.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {}
    for slot in SLOTS:
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(slot.query) + "&hl=en-US&gl=US&ceid=US:en")
        # The channel lookup belongs INSIDE this try. XML-valid RSS with no
        # <channel> element makes find() return None, and .findall() on None
        # raises AttributeError -- which would escape fetch_candidates and
        # main() entirely, aborting all five slots instead of emptying one and
        # (the workflow's commit step has no `if: always()`) destroying a digest
        # that may already have been built. One bad response costs its own slot.
        try:
            channel = ET.fromstring(curl([url])).find("channel")
            items = channel.findall("item") if channel is not None else []
        except Exception:
            out[slot.key] = []
            continue
        rows, seen = [], set()
        for it in items:
            if (it.findtext("source") or "").strip() != "WSJ":
                continue
            title = re.sub(r"\s*-\s*WSJ\s*$", "", (it.findtext("title") or "").strip())
            if not title or title in seen:
                continue
            try:
                dt = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:
                continue
            if (now - dt).total_seconds() > slot.max_age_hrs * 3600:
                continue
            seen.add(title)
            rows.append((dt, title, it.findtext("link")))
        rows.sort(reverse=True)
        out[slot.key] = [
            {"i": i, "title": t, "ageHrs": round((now - dt).total_seconds() / 3600, 1), "url": u}
            for i, (dt, t, u) in enumerate(rows[:RAW_POOL_CAP])
        ]
    return out


def fetch_candidates() -> dict:
    """Fetch the unfiltered candidate pool, then apply filters.reject per slot."""
    raw = fetch_candidates_unfiltered()
    out = {}
    for slot in SLOTS:
        kept = filters.reject(slot, raw.get(slot.key, []))[:15]
        for i, c in enumerate(kept):
            c["i"] = i
        out[slot.key] = kept
    return out


def resolve_one(gn: str) -> str | None:
    m = re.search(r'/articles/([^?]+)', gn)
    if not m:
        return None
    aid = m.group(1)
    page = curl(["-H", "Cookie: CONSENT=YES+", "https://news.google.com/articles/" + aid])
    sg = (re.search(r'data-n-a-sg="([^"]+)"', page) or [None, None])[1]
    ts = (re.search(r'data-n-a-ts="([^"]+)"', page) or [None, None])[1]
    nid = (re.search(r'data-n-a-id="([^"]+)"', page) or [None, None])[1] or aid
    if not (sg and ts):
        return None
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
             'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (nid, ts, sg))
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
    resp = curl(["-H", "Content-Type: application/x-www-form-urlencoded;charset=UTF-8", "-H", "Cookie: CONSENT=YES+",
                 "--data", "f.req=" + urllib.parse.quote(freq),
                 "https://news.google.com/_/DotsSplashUi/data/batchexecute"])
    u = re.findall(r'https?://[^"\\]*wsj\.com[^"\\]*', resp)
    return u[0] if u else None


def url_section(url: str) -> str:
    """First path segment of a wsj.com URL, e.g. 'tech' or 'pro'. '' if none."""
    m = re.match(r"https?://(?:www\.)?wsj\.com/([^/?#]+)", url or "")
    return m.group(1) if m else ""
