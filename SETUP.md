# WSJ Daily — setup

A daily 9:00 AM ET email with 4 curated, **direct-link** WSJ articles
(Macro / Industry-Company-Transaction / Op-Ed / Tech).

## How it works

- **GitHub Actions** runs every morning on GitHub's servers (a non-Google IP —
  this matters, see note): `generate.py` fetches live WSJ headlines, the Claude
  API picks the best per slot + writes a one-line summary, each pick is resolved
  to its **direct wsj.com URL**, and `picks.json` is committed to your repo.
- **Google Apps Script** runs at 9:00 AM ET: reads `picks.json` from your repo's
  raw URL and emails the digest to everyone in the recipients Doc.

> Why two systems: only a non-Google IP can turn Google News links into direct
> wsj.com URLs (Google CAPTCHA-blocks its own Apps Script IPs). GitHub Actions
> does the fetching/resolving; Apps Script only reads a plain file + sends mail.

## Files (in this folder)

- `generate.py` — the generator (runs in GitHub Actions).
- `.github/workflows/daily.yml` — the daily schedule + commit step.
- `mailer.gs` — the Google Apps Script mailer.

---

## Part A — GitHub (generation)

1. **Create a repo.** On github.com → New repository, e.g. `wsj-daily`.
   **Public** is simplest (contents are just headlines + links). If you want it
   private, tell me — Apps Script then needs a token to read the raw file.

2. **Add the files.** Put `generate.py` at the repo root and
   `.github/workflows/daily.yml` at that path. (Upload via the web UI or push
   from this folder.)

3. **Add your Claude API key as a secret.** Repo → Settings → Secrets and
   variables → Actions → **New repository secret**:
   - Name: `ANTHROPIC_API_KEY`
   - Value: `sk-ant-...` (from console.anthropic.com; you have credit)

4. **Run it once manually.** Repo → **Actions** tab → enable workflows if
   prompted → "WSJ Daily picks" → **Run workflow**. After ~1 min a `picks.json`
   commit appears in the repo. Open it — you should see today's date and 4
   `wsj.com` links.
   - In the run log, the generate step prints `curation: Claude` (good) or
     `curation: heuristic fallback (...)` — if it says heuristic, the API key or
     billing has an issue; fix and re-run.

5. **Note your raw URL:**
   `https://raw.githubusercontent.com/<you>/<repo>/main/picks.json`

## Part B — Google Apps Script (sending)

> **Sender identity:** Apps Script sends as the Google account that *owns* the
> project and authorized the trigger. There is no `from:` override without a
> verified send-as alias. So this project must live under
> **aaravpdoshi@gmail.com** — that is what puts it on the From line.

1. Signed in as **aaravpdoshi@gmail.com**: <https://script.google.com> → **New
   project**, name it `Doshi Labs: News`, paste all of `mailer.gs`.
2. In `CONFIG`, confirm **`PICKS_URL`** matches your raw URL from A-5 and
   **`RECIPIENTS`** lists who should get it.
3. Project Settings (gear) → **Time zone** → `America/New_York`.
4. Select **`sendTestNow`** → **Run**, authorize when prompted (you'll get an
   "unverified app" warning → Advanced → Go to Doshi Labs: News). Check the inbox
   for `[TEST] Doshi Labs: News — <date>`, **confirm the From line reads
   aaravpdoshi@gmail.com**, and click a link to confirm the WSJ article opens.
5. Select **`installTrigger`** → **Run**. Live — sends daily ~9:00 AM ET.
6. **Delete the old `sendDaily` trigger** on any previous copy of this project
   (the Wharton-owned one). Two live triggers = two emails a day. Leave the old
   project itself in place, trigger-less, as a rollback.

## Everyday use

- **Add/remove recipients:** edit `CONFIG.RECIPIENTS` in `mailer.gs` (first
  address is the To:, the rest are BCC'd) and save. Consumer Gmail allows 100
  recipients/day via Apps Script.
- **See today's picks without waiting:** open `picks.json` in the repo, or run
  the GitHub workflow manually.
- **Pause:** Apps Script → Triggers (clock icon) → delete the `sendDaily`
  trigger. Re-run `installTrigger` to resume.
- **Change send time:** edit `SEND_HOUR` in `mailer.gs`, re-run `installTrigger`.

## Timing & reliability

- Generation runs at 08:17, 10:17, and 11:17 UTC; the 11:17 tick is RESCUE-only
  (it regenerates only if the day's picks are still missing/empty, otherwise it
  exits without spending an API call). Send is 9:00 AM ET (13:00 UTC EDT /
  14:00 UTC EST) — a wide buffer even against GitHub's observed 45–110 min
  scheduler lag.
- If a morning's generation ever fails, `picks.json` keeps yesterday's date, and
  the mailer (which requires today's date) sends a one-line "no digest today"
  note instead of stale news.
- Weekends: WSJ publishes less, so some slots may be a day old — that's WSJ's
  cadence, not a fault. Weekdays are same-day.

## Notes

- The old **WSJ-FT Daily** Drive folder is fully unused now: the mailer reads
  `picks.json` from GitHub, and recipients live in `CONFIG.RECIPIENTS` rather
  than in the old **WSJ-FT Recipients** Doc. Dropping the Doc also dropped the
  `DocumentApp` OAuth scope — the script now needs only Gmail + UrlFetch.
- Your Anthropic API key lives only in the GitHub secret — not in Apps Script.
