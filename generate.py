#!/usr/bin/env python3
"""
WSJ Daily generator — runs in GitHub Actions.

Fetch live WSJ headlines (Google News RSS) for 5 slots, ask the Claude API to
pick the best per slot + write a one-line summary, resolve each pick to its
direct wsj.com URL, and write picks.json. The workflow then commits picks.json,
and the Apps Script mailer reads it and emails at 9 AM ET.

Uses curl (present on GitHub runners) for every HTTP call — the method verified
to work against Google News' article/batchexecute endpoints. Requires env var
ANTHROPIC_API_KEY. If the Claude call fails, falls back to a keyword heuristic
so a picks.json is always produced. `--dry-run` compares curation with filters
on vs. off against one live pool, printing results without writing anything.
"""
import os, sys, re, json, subprocess, urllib.parse, email.utils, datetime
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from wsjdaily import filters, history
from wsjdaily.slots import CANONICAL_ORDER, RESOLVE_ORDER, SLOTS, by_key

MODEL = "claude-sonnet-5"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MAX_RESOLVE_TRIES = 6          # more grounds for rejection than before
BLOCKED_SECTIONS = {"pro", "podcasts"}


def curl(args):
    return subprocess.run(["curl", "-sL", "--max-time", "30", "-A", UA] + args,
                          capture_output=True, text=True).stdout


def fetch_candidates_unfiltered() -> dict:
    """Fetch and clean the candidate pool for every slot, with no filters.reject applied.

    Returns contiguously indexed rows, up to 20 per slot, so filtering
    downstream operates on the wider pool rather than an already-truncated one.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    out = {}
    for slot in SLOTS:
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(slot.query) + "&hl=en-US&gl=US&ceid=US:en")
        try:
            root = ET.fromstring(curl([url]))
        except Exception:
            out[slot.key] = []
            continue
        rows, seen = [], set()
        for it in root.find("channel").findall("item"):
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
            for i, (dt, t, u) in enumerate(rows[:20])
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


def curate_with_claude(cands: dict, covered: list[str]) -> list[dict]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    tiered = {}
    for slot in SLOTS:
        fresh, fallback = filters.tier(cands.get(slot.key, []))
        tiered[slot.key] = {
            "fresh": [{"i": c["i"], "title": c["title"], "ageHrs": c["ageHrs"]} for c in fresh],
            "older": [{"i": c["i"], "title": c["title"], "ageHrs": c["ageHrs"]} for c in fallback],
        }

    rubric = (
        "Macro: the story with the broadest cross-asset, market-moving significance "
        "(central banks, major data prints like CPI/jobs/GDP, fiscal/tariff/policy). "
        "NOT daily price roundups, voter sentiment, or political color. "
        "Industry/Company/Transaction: one concrete corporate story -- a NAMED deal/M&A, "
        "financing, IPO, material earnings, or major regulatory/legal event, ideally with a "
        "dollar figure or named parties. NOT box-office, sports, lifestyle, or human interest. "
        "Op-Ed: one substantive argument column (prefer economics/business/policy). "
        "Tech: one consequential tech-industry development (AI, chips, big-tech strategy, "
        "major product, regulation, notable research). NOT gadget reviews or lifestyle-tech. "
        "Sports: PREFER sports business/finance/economics -- franchise sales and valuations, "
        "media-rights deals, private-equity or sovereign money in leagues, stadium financing, "
        "league/CBA economics, betting, athlete investing. If and only if the candidates "
        "contain no such story, pick the single most important sports headline instead.")

    user = (
        "Candidate WSJ headlines by slot. Each slot has a 'fresh' list (<=24h old) and an "
        "'older' list. ageHrs = hours old.\n"
        + json.dumps(tiered, ensure_ascii=False)
        + "\n\nStorylines already emailed in the last 7 days (do NOT repeat one unless the "
          "candidate is a materially NEW development, e.g. a deal closing or collapsing, not "
          "a restatement):\n" + json.dumps(covered, ensure_ascii=False)
        + "\n\nFor EACH slot pick the ONE headline that best fits that slot per the rubric.\n"
          "RECENCY: choose from 'fresh' unless it is empty or every option in it fits the slot "
          "poorly; only then fall back to 'older'.\n"
          "TOPICAL FIT comes first for every slot except Sports, whose fallback rule is in the "
          "rubric. A fresh but off-topic headline must NOT be chosen.\n"
          "DIVERSITY: the 5 picks must be 5 distinct stories about distinct subjects. Do not "
          "let two slots cover the same underlying event.\n"
          "Reply with EXACTLY five lines and nothing else, one per slot, each formatted:\n"
          "slot name|chosen id|storykey|summary of <=25 words grounded only in the headline\n"
          "storykey = 2-4 lowercase entity words joined by '+' identifying the underlying "
          "story, e.g. kkr+integer or apollo+easyjet.\n"
          "Slot names exactly: " + ", ".join(CANONICAL_ORDER) + ". "
          "If a slot has no candidates use id -1, storykey -, and summary: No WSJ pick today.")

    payload = json.dumps({
        "model": MODEL, "max_tokens": 1024,
        "system": "You are a financial news editor. Follow the rubric exactly.\n\n" + rubric,
        "messages": [{"role": "user", "content": user}],
    })
    resp = curl(["-H", "x-api-key: " + key, "-H", "anthropic-version: 2023-06-01",
                 "-H", "content-type: application/json", "--data", payload,
                 "https://api.anthropic.com/v1/messages"])
    try:
        data = json.loads(resp)
    except Exception:
        raise RuntimeError("non-JSON API response: " + resp[:400])
    if "content" not in data:
        raise RuntimeError("API error response: " + json.dumps(data)[:400])
    # Grab the first text block (some models may return non-text blocks first).
    text = next((b.get("text") for b in data["content"] if b.get("type") == "text"), None)
    if not text:
        raise RuntimeError("no text block; content=" + json.dumps(data["content"])[:400])
    return parse_selections(text)


def parse_selections(text: str) -> list[dict]:
    """Parse 'slot|id|storykey|summary' lines.

    Line-based on purpose — immune to the quote/JSON-escaping breakage LLM JSON
    is prone to. Three-field lines are still accepted with storyKey=None, so a
    model format regression degrades to the previous behaviour instead of
    failing the run.
    """
    valid = set(CANONICAL_ORDER)
    sels: list[dict] = []
    for line in text.strip().splitlines():
        parts = [p.strip().strip("*`") for p in line.split("|", 3)]
        if len(parts) == 4:
            slot, idx, raw_key, summary = parts
        elif len(parts) == 3:
            slot, idx, summary = parts
            raw_key = None
        else:
            continue
        try:
            idx = int(idx)
        except ValueError:
            continue
        if slot in valid and slot not in {s["slot"] for s in sels}:
            sels.append({"slot": slot, "i": idx,
                         "storyKey": history.norm_story_key(raw_key), "summary": summary})
    if len(sels) != len(SLOTS):
        raise RuntimeError("parsed %d/%d selections; raw=%r" % (len(sels), len(SLOTS), text[:300]))
    return sels


def heuristic(cands: dict) -> list[dict]:
    """Offline fallback. Consumes the SAME filtered, tiered pool the model sees,
    so an API outage cannot reintroduce the market wraps we filter out.

    Summary is left empty on purpose: using the headline as the summary printed
    the title twice in the email.
    """
    picks = []
    for slot in SLOTS:
        fresh, fallback = filters.tier(cands.get(slot.key, []))
        pool = fresh or fallback
        chosen = pool[0] if pool else None
        picks.append({"slot": slot.key, "i": chosen["i"] if chosen else -1,
                      "storyKey": None, "summary": ""})
    return picks


def resolve_one(gn):
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


def is_claimable(url: str, story_key: str | None, blocked_keys: set, used_keys: set,
                 used_urls: set, prior_urls: set) -> str:
    """Return "" if this resolved candidate is usable, else a rejection reason.

    Section blocking has to happen HERE rather than on the candidate pool:
    candidates carry Google News URLs, and /pro/ (a separate paid WSJ tier) and
    /podcasts/ (audio, not an article) are only visible once resolved.

    `used_keys` is shared across slots and grows as slots resolve in
    RESOLVE_ORDER, which is what makes Sports outrank Industry on a shared
    story. A None story_key never blocks -- a missing dedup key must not cost
    an article.
    """
    if url_section(url) in BLOCKED_SECTIONS:
        return "section"
    if url in used_urls or url in prior_urls:
        return "dup-url"
    if story_key and (story_key in blocked_keys or story_key in used_keys):
        return "storyline"
    return ""


def dry_run(date: str) -> None:
    """Fetch one live pool and curate it twice -- filters off, then on.

    Prints both pick sets side by side and writes NOTHING. Costs 2 API calls.
    The 'filters off' arm approximates the previous behaviour; it is not a
    bit-exact replay, since the prompt itself changed.
    """
    hist = history.load()
    raw = fetch_candidates_unfiltered()
    filtered = {s.key: filters.reject(by_key(s.key), raw.get(s.key, [])) for s in SLOTS}
    for pool in (raw, filtered):
        for rows in pool.values():
            for i, c in enumerate(rows):
                c["i"] = i

    print("=" * 78)
    for label, pool, covered in (
        ("BEFORE (filters off)", raw, []),
        ("AFTER  (filters on)", filtered, history.covered_story_keys(hist, date)),
    ):
        print(label)
        try:
            sels = curate_with_claude(pool, covered)
        except Exception as e:
            print("  curation failed: " + str(e)[:200])
            continue
        for slot_key in CANONICAL_ORDER:
            s = next((x for x in sels if x["slot"] == slot_key), None)
            chosen = next((c for c in pool.get(slot_key, []) if s and c["i"] == s["i"]), None)
            print("  %-34s %s" % (slot_key, chosen["title"][:60] if chosen else "(none)"))
        print("-" * 78)

    for slot in SLOTS:
        print("pool %-34s raw=%-3d filtered=%d"
              % (slot.key, len(raw.get(slot.key, [])), len(filtered.get(slot.key, []))))


def main():
    date = datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    if "--dry-run" in sys.argv:
        dry_run(date)
        return

    # Rescue mode (late cron tick): only regenerate if the day's picks are
    # missing, stale, or empty — otherwise exit without spending an API call.
    if os.environ.get("RESCUE_ONLY"):
        try:
            prev = json.load(open("picks.json"))
            if prev.get("date") == date and any(p.get("url") for p in prev.get("picks", [])):
                print("rescue: healthy picks for today already exist; nothing to do", file=sys.stderr)
                return
        except Exception:
            pass
        print("rescue: today's picks missing/empty — regenerating", file=sys.stderr)

    hist = history.load()
    prior_titles, prior_urls = history.prior_keys(hist, date)
    blocked_keys = history.blocked_story_keys(hist, date)
    covered = history.covered_story_keys(hist, date)

    cands = fetch_candidates()

    # Drop articles already sent on a previous day, BEFORE curation, so the model
    # cannot pick a repeat. Reindex so ids stay contiguous.
    dropped = 0
    for key, lst in cands.items():
        kept = [c for c in lst if history.norm_title(c["title"]) not in prior_titles]
        dropped += len(lst) - len(kept)
        for i, c in enumerate(kept):
            c["i"] = i
        cands[key] = kept
    print("dedup: dropped %d previously-sent candidate(s); history spans %d day(s)"
          % (dropped, len(hist)), file=sys.stderr)

    try:
        selections = curate_with_claude(cands, covered)
        print("curation: Claude", file=sys.stderr)
    except Exception as e:
        print("curation: heuristic fallback (" + str(e)[:400] + ")", file=sys.stderr)
        selections = heuristic(cands)

    picks_by_slot: dict[str, dict] = {}
    used_urls: set[str] = set()
    used_story_keys: set[str] = set()

    # Resolve in precedence order so the first slot to claim a story keeps it
    # (Sports outranks Industry); the email still renders in CANONICAL_ORDER.
    for slot_key in RESOLVE_ORDER:
        sel = next((x for x in selections if x["slot"] == slot_key), None)
        lst = cands.get(slot_key, [])
        chosen = None
        if sel and sel.get("i", -1) >= 0:
            chosen = next((c for c in lst if c["i"] == sel["i"]), None)

        # Try the model's pick first, then the rest as fallbacks.
        order = ([chosen] if chosen else []) + [c for c in lst if c is not chosen]
        picked = None
        for cand in order[:MAX_RESOLVE_TRIES]:
            direct = resolve_one(cand["url"])
            if not direct:
                continue
            # The storyKey describes the MODEL's pick, so it only applies when
            # this candidate is that pick; fallbacks carry no key.
            cand_key = sel.get("storyKey") if (sel and cand is chosen) else None
            reason = is_claimable(direct, cand_key, blocked_keys, used_story_keys,
                                  used_urls, prior_urls)
            if reason:
                print("  skip %s: %s" % (reason, cand["title"][:50]), file=sys.stderr)
                continue
            picked = (cand, direct)
            break

        if not picked:
            print("FAIL " + slot_key + ": no usable candidate", file=sys.stderr)
            picks_by_slot[slot_key] = {"slot": slot_key, "label": slot_key, "title": "",
                                       "url": "", "summary": "No WSJ pick today.",
                                       "storyKey": None, "source": "WSJ"}
            continue

        cand, direct = picked
        used_urls.add(direct)
        # Only carry the model's summary and storyKey if we used the model's
        # pick -- otherwise they describe a different article.
        is_model_pick = sel is not None and cand is chosen
        story_key = sel.get("storyKey") if is_model_pick else None
        if story_key:
            used_story_keys.add(story_key)
        summary = (sel.get("summary") or "")[:200] if is_model_pick else ""
        print("OK   " + slot_key + ": " + cand["title"][:55], file=sys.stderr)
        picks_by_slot[slot_key] = {"slot": slot_key, "label": slot_key, "title": cand["title"],
                                   "url": direct, "summary": summary,
                                   "storyKey": story_key, "source": "WSJ"}

    picks = [picks_by_slot[k] for k in CANONICAL_ORDER]

    if not any(p["url"] for p in picks):
        # Almost always means Google CAPTCHA-flagged this runner's IP. Do NOT
        # write an all-empty picks.json (the mailer's freshness check then sends
        # a compact alert instead of an empty digest), and fail the job so the
        # next scheduled tick retries on a fresh runner.
        print("ERROR: zero slots resolved — likely blocked runner IP; leaving previous "
              "picks.json in place and failing so a later run retries", file=sys.stderr)
        sys.exit(1)

    result = {"date": date, "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), "picks": picks}
    with open("picks.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    history.save(hist, date, picks)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
