# WSJ Daily — handoff / current state

A daily 9:00 AM ET email of 5 curated, direct-link WSJ articles
(Macro / Industry-Company-Transaction / Op-Ed / Tech / Sports) plus a Street
Research section (Goldman Sachs / J.P. Morgan / Morgan Stanley). **Live and running.**

## Architecture (two halves)

1. **GitHub Actions** (repo `apdoshi-netizen/wsj-daily`) — runs on GitHub's
   non-Google IP (required; see gotcha #1). `generate.py`:
   fetch live WSJ headlines from Google News RSS → filter/dedup → Claude API
   (`claude-sonnet-5`) picks best per slot + one-line summary → resolve each to
   its **direct wsj.com URL** → write `picks.json` + `history.json`, which the
   workflow commits. Secret: `ANTHROPIC_API_KEY` (repo → Settings → Secrets).
2. **Google Apps Script** (`mailer.gs`) — 9:00 AM ET trigger reads `picks.json`
   from the repo's raw URL and emails everyone in `CONFIG.RECIPIENTS`. Touches
   only GitHub-raw + Gmail; can't be CAPTCHA'd. **Project must be owned by
   aaravpdoshi@gmail.com** — see gotcha #8.

Data flow: GitHub cron → `generate.py` → commit `picks.json` →
`raw.githubusercontent.com/apdoshi-netizen/wsj-daily/main/picks.json` →
Apps Script `sendDaily` → email.

## Files

| File | What it is |
|---|---|
| `generate.py` | The generator (runs in CI). Fetch, dedup, curate, resolve. |
| `wsjdaily/` | Pure filter + history logic, unit-tested. |
| `wsjdaily/sources/` | Source adapters: WSJ, Apple podcasts, JPM web. |
| `tests/` | pytest suite for `wsjdaily/`. |
| `.github/workflows/daily.yml` | Cron schedule + commit step. |
| `.github/workflows/tests.yml` | CI: runs `pytest tests/` on push/PR. |
| `mailer.gs` | Apps Script mailer (paste into script.google.com). |
| `picks.json` | Latest generated picks (committed by CI). |
| `history.json` | 21-day memory of sent articles, for dedup. |
| `SETUP.md` | Full first-time setup runbook. |

## Live config

- **Schedule (UTC):** `17 8`, `17 10`, and `17 11` (the 11:17 run is
  RESCUE-only — regenerates just when the day's picks are missing/empty).
  GitHub delays ticks 45–110 min, so these sit well before the 13:00 UTC
  (9 AM EDT) send.
- **Model:** `claude-sonnet-5`. ~2 real API calls/day → est. **$1–3/month**
  (check console.anthropic.com → Usage).
- **From:** aaravpdoshi@gmail.com, display name `Doshi Labs: News`.
- **Recipients:** `CONFIG.RECIPIENTS` in `mailer.gs` — currently just
  apdoshi@wharton.upenn.edu. First address is To:, rest are BCC'd.
- **Send time:** 9:00 AM ET (Apps Script trigger, `installTrigger`).

## Key gotchas (hard-won — don't relearn these)

1. **Only a non-Google IP can turn Google News links into direct wsj.com URLs.**
   Google CAPTCHA-blocks its own datacenter IPs (Apps Script → 302
   `google.com/sorry/`). That's the entire reason resolution lives in GitHub
   Actions, not Apps Script.
2. **The resolver is an unofficial Google endpoint** (`batchexecute`). Works,
   but Google could change it. Also, individual GitHub runner IPs are sometimes
   pre-flagged → a run resolves 0/5. Handled: a 0-resolved run writes nothing
   and exits 1, so the mailer sends a compact "no digest" alert (not an empty
   email) and a later tick retries on a fresh runner IP.
3. **Resolve needs `curl -L` + `Cookie: CONSENT=YES+`**; without `-L` you get an
   empty 302 body and no signature.
4. **WSJ legacy RSS (`feeds.a.dj.com`) is dead** — frozen at Jan 2025. Live
   source is Google News RSS.
5. **Model reply is pipe-delimited** (`slot|id|storykey|summary`), not JSON — a
   Sonnet JSON reply broke `json.loads` once. Three-field lines
   (`slot|id|summary`, no storyKey) are also accepted for backward
   compatibility — `parse_selections()` handles both.
6. **`claude-sonnet-5` returns a thinking block first** — extract the first
   `type=="text"` block, not `content[0]`.
7. **Dedup has two layers.** History keys by date and ignores today's own
   entry, so the 3 scheduled ticks (`17 8`, `17 10`, `17 11` UTC) don't exclude
   their own earlier picks. Layer one matches article identity (normalized
   title / resolved URL) over 21 days — only literal repeats are blocked, so a
   *different* article on the same story still passes. Layer two is the
   storyline window described in gotcha #10.
8. **Apps Script sends as the account that OWNS the project** and authorized the
   trigger. `GmailApp`'s `from:` option only accepts an address already verified
   as a "Send mail as" alias in *that* account, and Penn Workspace may block
   external aliases. That's why the project was re-homed from the Wharton account
   to aaravpdoshi@gmail.com rather than aliased. If mail ever starts arriving
   from the wrong address, check which account owns the live project.
9. **The market-wrap filter is Macro-only.** Applied globally it rejects real
   deal stories -- "Mitie Shares Soar on $4.2 Billion Takeover by OCS" matches
   the same shape. See `Slot.reject_market_wraps`.
10. **Storyline dedup is hybrid:** exact storyKey match within 2 days is a hard
    block; days 3-7 are shown to the model, which judges whether a development
    is materially new. Keys are sorted token sets, so order does not matter.
11. **Slots resolve in `RESOLVE_ORDER`, not `CANONICAL_ORDER`.** Sports resolves
    before Industry so a sports-business deal lands in Sports. The email still
    renders in canonical order.
12. **GS and MS podcast RSS feeds have no `<link>` element** -- only .mp3
    enclosures -- and firm-site episode URLs cannot be built from titles (every
    constructed slug 404s). Apple's lookup API is the only source of a stable
    per-episode link, and it supplies clean ISO dates too.
13. **The research window is trailing 24h, not the calendar day.** Morgan
    Stanley publishes 16:00-17:30 ET, hours AFTER the 09:00 ET send, so a
    same-day filter can never include the only daily publisher.
14. **`_research` in history.json is not date-shaped and must be skipped by
    `_window`.** "_research" > "2026-.." because "_" is 0x5F, so it passes the
    cutoff test and would be iterated as if it were a list of picks.

## Known limitations / backlog candidates

- Links open only because the reader is logged into WSJ (UPenn access). If that
  lapses, links hit a paywall.
- Summaries are grounded in the **headline only** (article bodies are paywalled
  to the fetcher), so they add context, not new facts.
- Weekends: WSJ publishes less; some slots may be a day old.
- No `sendNow` (clean one-off resend) yet — only `sendTestNow`, which prefixes
  `[TEST]`.
- Op-ed slot leans political some days; could add source/topic diversity rules.

## Handy commands

```bash
# See today's picks (uncached):
gh api repos/apdoshi-netizen/wsj-daily/contents/picks.json --jq '.content' | base64 -d

# Manually run generation:
gh workflow run daily.yml --repo apdoshi-netizen/wsj-daily

# Local test (no key → heuristic curation):
python3 generate.py

# A/B the curation against one live pool without writing anything
# (also previews the Street Research section):
ANTHROPIC_API_KEY=sk-ant-... python3 generate.py --dry-run

# Run the unit tests (no API key needed):
python3 -m pytest tests/ -v
```
