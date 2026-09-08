# Running the Scraper

## Project structure

```
shirt-sales/
├── docs/                   ← this folder + the generated dashboard (gitignored, deployed not committed)
│   ├── index.html          ← dashboard, deployed to Netlify each run, not in git
│   └── shirt-sales.xlsx    ← spreadsheet, moved here at deploy time so Basic Auth covers it too
├── data/                   ← local scratch only, entirely gitignored — see "Storage" below
│   └── shirts.db           ← embedded-replica cache of the market Turso database
├── scraper/
│   ├── __init__.py
│   ├── fetch.py            ← Vinted API client + sold detection
│   ├── parse.py            ← title parsing: team, player, season
│   ├── squads.py           ← scrapes current squads from Wikipedia
│   ├── players.json        ← output of squads.py, refreshed monthly (public data, stays in git)
│   ├── db.py                ← sqlite3-compatibility shim over the raw libsql client
│   ├── store.py            ← reads/writes the two Turso databases
│   ├── mine.py             ← the owner's own listings/sales
│   ├── teams.py            ← team name lookup data
│   └── run.py              ← entry point
├── analysis/
│   ├── __init__.py
│   ├── export.py           ← Turso → Excel
│   ├── visualise.py        ← Turso → aggregates → docs/index.html
│   └── dashboard.html      ← dashboard template (CSS + inline-SVG chart renderer)
├── netlify/
│   ├── edge-functions/auth.ts      ← HTTP Basic Auth in front of the dashboard
│   └── functions/trigger-scrape.mts ← scheduled: dispatches the daily scrape at 01:17 UTC
├── netlify.toml
├── .github/
│   └── workflows/
│       ├── scrape.yml          ← daily: scrape → export → dashboard → Netlify deploy (fallback cron 04:17)
│       └── update-squads.yml   ← monthly: refresh scraper/players.json
├── requirements.txt
└── venv/                   ← local only, gitignored
```

## Setup (local)

```bash
cd /Users/ed/apps/shirt-sales
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### requirements.txt

```
requests
pandas
openpyxl
beautifulsoup4
lxml
libsql
```

### Storage: Turso, not this repo

The repo is public (see below) so nothing scraped, and nothing of the owner's own, can live in it. Two [Turso](https://turso.tech) databases (hosted libSQL — every connection needs a bearer token, no anonymous access) hold everything; `data/` locally is just a gitignored embedded-replica cache, rebuilt from the remote on every run — see `02-data-model.md` for the schema and the `scraper/db.py` compatibility shim.

Env vars, set as GitHub Actions secrets (never in the repo):

| Secret | Database |
|---|---|
| `TURSO_MARKET_URL`, `TURSO_MARKET_TOKEN` | `shirt-sales-market` — scraped listings |
| `TURSO_MINE_URL`, `TURSO_MINE_TOKEN` | `shirt-sales-mine` — the owner's own listings/sales |

Local development needs the same four in the shell environment (`export TURSO_MARKET_URL=...` etc, or a gitignored `.env` loaded before running). Get fresh values any time with the Turso CLI (`turso auth login` once, then):
```bash
turso db show shirt-sales-market --url
turso db tokens create shirt-sales-market
```

## Running locally

```bash
# Single scrape run
python -m scraper.run

# Regenerate spreadsheet from existing data
python -m analysis.export

# Regenerate the dashboard from existing data
python -m analysis.visualise

# Refresh the player-squad cross-check list (also runs monthly in CI)
python -m scraper.squads

# Run everything
python -m scraper.run && python -m analysis.export && python -m analysis.visualise
```

## Scheduling

Runs daily via **GitHub Actions** (`.github/workflows/scrape.yml`), which also regenerates and deploys the dashboard on every run — see below.

Nothing generated is committed back to the repo — the database lives in Turso (above) and the spreadsheet/dashboard deploy straight to Netlify. The workflow's only lasting effect on `main` is if `scraper/players.json` changes (the separate monthly squad-refresh workflow).

## GitHub Actions cron

`.github/workflows/scrape.yml`:
- **Primary trigger: a Netlify scheduled function**, `netlify/functions/trigger-scrape.mts`, which calls GitHub's `workflow_dispatch` API at **01:17 UTC** (02:17 BST — most listings go up before midnight, a run takes about an hour, so the data is fresh well before morning). GitHub's own cron scheduler is best-effort and for this repo skipped or delayed *every* slot it was given (08:00 fired six hours late; 04:17 never fired), which is why the trigger lives on Netlify, whose scheduler is reliable.
- **Fallback: the workflow's own cron at 04:17 UTC**, gated by a `guard` job that skips the scrape if any run started in the previous 12 hours — so on a normal day the fallback does nothing, and if Netlify ever fails to fire, GitHub (eventually) does.
- Manual `workflow_dispatch` is always available (`gh workflow run "Daily Scrape"`).
- A `concurrency` group means two scrapes never run at once — a second one queues until the first finishes. This was added after GitHub fired the 08:00 scheduled run almost six hours late, on top of a manual run: both re-checked the same 2,400 listings simultaneously and the second to finish would have failed its push.

**The Netlify trigger needs a GitHub token** in the site's environment variables, named `GH_DISPATCH_TOKEN`: a *fine-grained* personal access token (github.com → Settings → Developer settings → Fine-grained tokens) scoped to the `shirt-sales` repository only, with the repository permission **Actions: Read and write** (nothing else). Fine-grained tokens expire (max one year) — set a calendar reminder; when it expires the Netlify function logs "GitHub refused the dispatch: 401" and the 04:17 fallback takes over until it's renewed. Set it from the repo folder with:

```bash
npx netlify env:set GH_DISPATCH_TOKEN "github_pat_..."
```

Test the trigger without waiting for 01:17: `npx netlify functions:invoke trigger-scrape` (or the function's URL, `/.netlify/functions/trigger-scrape`) — it should return "dispatched" and a new run should appear in `gh run list`. Mind the concurrency group: if a scrape is already in flight the dispatched one queues behind it.
- Steps: checkout → install deps → run scraper (reads/writes Turso) → `analysis.export` → `analysis.visualise` → move the spreadsheet into `docs/` → deploy `docs/` to Netlify → **alert check** (always runs, see below). No git commit step — there's nothing to commit.
- Typical duration: scales with the re-check rotation cap and the number of team queries, not with total database size (`03-scraping-strategy.md`).

## Alerting when Vinted blocks us

The scraper never records a sale from a refused or unanswered re-check (`03-scraping-strategy.md` → Detecting being blocked), so a block can't corrupt data — but it would silently stop data *accumulating*, so it has to be noticed. The last workflow step runs `python -m scraper.alert` with `if: always()`:

1. It reads `logs/run-status.json` (written by `scraper/run.py`) and posts the run's counts to the job summary, every run.
2. If the run flagged `alert: true`, it **opens a GitHub Issue** titled "Vinted scrape blocked (date)", labelled `scrape-blocked` and assigned to the repo owner — or, if one is already open, comments on it with the new run's numbers — and then **exits 1 so the workflow run is marked failed**. That's two notification channels: the issue (assignment → notification/email) and the failed run (GitHub's workflow-failure email).
3. Nothing is cleaned up automatically: close the issue yourself once runs are healthy again. Closing it means the next block opens a fresh one rather than commenting on a stale thread.

First real firing: [issue #1](https://github.com/edflips/shirt-sales/issues/1), 2026-09-07 — 27 of 2,416 re-checks refused with 429/403 at 6 workers. Nothing was recorded as sold from them. Workers were reduced to 4 and the re-check moved to rotation in response (`03-scraping-strategy.md`).

Alert conditions (`run.py`): the circuit breaker tripped (8 consecutive block signals), or 5+ re-checks were refused with 403/429, or more than 20% of re-checks got no usable answer. A single dropped connection or a couple of 5xxs does not alert.

To test the plumbing without being blocked: `python -m scraper.alert --dry-run` prints the `gh` commands it would run for whatever is in `logs/run-status.json`. The workflow needs `issues: write` permission (already set).

**Resolved**: pushing code while a run was in flight used to break that run's own end-of-job `git push` of the regenerated DB/spreadsheet/dashboard (non-fast-forward rejection) — this bit us on 2026-09-07 when the dashboard redesign was pushed mid-run. Moving storage to Turso removed the workflow's git-commit step entirely, so this failure mode no longer exists; code can be pushed freely regardless of an in-flight run.

**Resolved**: GitHub's scheduled trigger was unreliable here (08:00 fired ~6 h late on day one; 04:17 never fired on day two) — fixed by moving the trigger to a Netlify scheduled function, above.

**Resolved**: GitHub Actions minutes for a private repo (2,000/month free) were already short of the ~150 min/day steady-state usage measured on 2026-09-08 (~4,500 min/month) — fixed by making the repo public (unlimited minutes) once the data itself was moved out of git and out of reach (this section).

## My shop (tracking the owner's own listings)

`scraper/mine.py` runs at the end of every scrape (`MY_USERNAME` / `MY_USER_ID` at the top of the file — the id came from `GET /api/v2/users/podbury23`), writing to the `shirt-sales-mine` Turso database. Two sources feed the `my_listings` table:

- **Vinted, automatically** — `GET /api/v2/wardrobe/{user_id}/items` is the call the public profile page makes and works for a guest session (the `/users/{id}/items` endpoint does not). Every active listing is upserted each run with its favourites/views. A previously-seen listing that has left the wardrobe is checked via its listing page (the same JSON-LD rule as the market data, so a fetch error is never a sale) and marked sold with today's date. Vinted does **not** expose sold items or sale prices publicly, so this source only ever knows *that* something sold and roughly *when* (to within a day).
- **`mine.add_sale()`, by hand** — past sales, or any sale where the real price should be recorded. Run ad hoc (not part of the scheduled workflow) when the owner reports a sale, e.g.:
  ```bash
  export TURSO_MINE_URL=... TURSO_MINE_TOKEN=...
  python3 -c "
  from scraper.mine import add_sale
  from scraper.store import get_mine_conn
  add_sale(get_mine_conn(), title='Arsenal 26/27 home shirt Saka 7',
           sold_price=30, asking_price=32, listed_at='2026-08-20', sold_at='2026-08-28', size='M')
  "
  ```
  `title`, `sold_price`, `listed_at`, `sold_at` are required; `team`, `player_name`, `shirt_number`, `size`, `asking_price`, `url`, `id`, `bumped` are optional (team/player parsed from the title when not given; `bumped` defaults to **true** — `mine.MY_LISTINGS_BUMPED_BY_DEFAULT` — since the owner bumps everything as a matter of course and Vinted never reveals bumps on one's own items). This replaced hand-editing a committed `data/my_sales.json` file once the repo went public — see "Repository visibility" below.

The dashboard section compares each shirt's price with the median asking price of tracked listings for the same team, so it needs the team to be recognised (`04-parsing.md`).

## Repository visibility: public, with the data kept out of reach

The repo is **public**. It got there because GitHub Actions minutes for a *private* repo (2,000/month free) were already short of real measured usage (~150 min/day, ~4,500/month) — public repos get unlimited minutes. That only works because the data itself moved somewhere the repo's visibility doesn't touch:

- Scraped listings and the owner's sales live in Turso (above), reached only via secrets that are never in the repo.
- Nothing generated (database, spreadsheet, dashboard HTML) is committed — see "Scheduling".
- The dashboard itself stays behind Basic Auth on Netlify regardless of the repo's visibility (below) — the two are independent layers.
- Git history was squashed to a single fresh commit before the switch, so none of the pre-Turso commits (which did contain a committed database and `data/my_sales.json` with real sale prices) are reachable.
- `scraper/mine.py` hardcodes the owner's real Vinted username/id in source, now publicly visible in code — accepted deliberately, since that profile is already public on Vinted itself.

No live query API is exposed to the browser: the dashboard is still a static page rebuilt once a day and deployed to Netlify, identical in shape to before this migration — Turso is a drop-in replacement for the git-committed file as the source of truth, not a new public surface.

## Squad refresh (`.github/workflows/update-squads.yml`)

Runs `python -m scraper.squads` on the 1st of each month (plus manual `workflow_dispatch`) and commits `scraper/players.json` if it changed. This is what powers the player-name cross-check in `04-parsing.md` — squads change with transfers, so it needs to stay current rather than being a one-time build like `teams.py`. It's a separate, much less frequent workflow from the daily scrape since squad lists don't change day to day.

## Dashboard hosting (Netlify)

GitHub Pages was tried first but requires a paid plan for Pages on a private repo, so the dashboard is hosted on Netlify instead:

- Site: https://shirt-sales-dashboard.netlify.app
- Linked via the Netlify CLI (`netlify sites:create`), deployed with `netlify deploy --dir=docs --prod`
- Repo secrets `NETLIFY_AUTH_TOKEN` and `NETLIFY_SITE_ID` (set via `gh secret set`) authenticate the GitHub Actions deploy step — no local `.netlify/state.json` is committed; the env vars are sufficient
- `netlify.toml` declares the publish dir and edge-functions dir, and sets `X-Robots-Tag: noindex` on everything
- Netlify's injected **"Powered by Netlify" badge** (on by default for Free-plan projects created after 19 Aug 2026; a script at `/.netlify/scripts/hud`) is switched off — it can't be themed and clashed with the dark page. UI path: Project configuration → General → Powered by Netlify badge; or via the API, `netlify api updateSite --data '{"site_id":"…","body":{"built_with_badge_enabled":false}}'`. The footer carries a plain "Hosted on Netlify" credit instead.

### Access control (HTTP Basic Auth)

The site is behind HTTP Basic Auth so the aggregated data isn't public. Netlify's built-in visitor-access password needs a paid plan, so it's done with a Netlify **Edge Function** (`netlify/edge-functions/auth.ts`, matched to `/*`), which works on the free tier: it checks the `Authorization: Basic` header against the `DASH_USER` / `DASH_PASS` **site environment variables** and returns `401` + `WWW-Authenticate` otherwise, so the browser shows its native login prompt. It fails closed (503) if the env vars are missing.

Credentials live only in Netlify's env vars, never in the repo. To rotate:

```bash
npx netlify env:set DASH_PASS "new-password"
```

(from the linked project folder, or Site settings → Environment variables in the Netlify UI). Takes effect on the next request — no redeploy needed.

Because every `netlify deploy` is a full snapshot, the edge function is only live while it's part of the deployed code — a deploy from a checkout that predates it would remove it until the next deploy.

## First run checklist

- [x] Inspect live Vinted API response — field names confirmed
- [x] Confirm upload timestamp source (`photo.high_resolution.timestamp`)
- [x] Confirm `item.status` = condition label, not sold status
- [x] Confirm sold detection via JSON-LD `offers.availability`
- [x] Confirm no bot controls — guest JWT issued automatically
- [x] Dry run 5 items — parsing and DB insert working
- [x] Build `analysis/export.py` (spreadsheet generation)
- [x] Build `.github/workflows/scrape.yml` (GitHub Actions cron)
- [x] Run for first full scrape (all pages, all queries)
- [x] Build `analysis/visualise.py` (dashboard) + deploy to Netlify
- [x] Add `status_ids[]=6` (New with tags) + current-season filtering to cut scrape/re-check volume down from ~3,400 listings
- [x] Fix player-name false positives (per-word stop-word check, off-topic listing filter) and add Wikipedia squad cross-check (`scraper/squads.py` + monthly workflow)
- [x] Prune the pre-scope-tightening rows from `data/shirts.db` (3,361 → 443 for condition/season, → 339 after the off-topic filter; local backups `data/shirts.db.pre-cleanup-backup` and `data/shirts.db.pre-chart-fix-backup-*` kept, not committed)
- [x] Redesign the dashboard (inline SVG, animation, dark mode, table views) and put it behind Basic Auth
- [ ] Confirm sold detection on a real sold listing
- [ ] Confirm the 08:00 UTC scheduled trigger fires reliably
- [x] Run the squad-refresh workflow once manually to prove it works in CI before its first scheduled run on 1 Oct (2026-09-07: 85/86 teams in 3m51s, output identical to the local build — "No changes to commit")
- [x] Move storage to Turso (two databases), stop committing generated files, retire `data/my_sales.json` for `mine.add_sale()`, squash git history, make the repo public — see "Repository visibility"
- [ ] Confirm a scheduled (not manual) run completes cleanly against Turso

## Logs

Each run appends to `logs/scraper.log` (gitignored locally; stdout on GitHub Actions):

```
2026-09-06 08:00:01  INFO  === Scrape run started ===
2026-09-06 08:00:03  INFO  Re-checking 142 active listings for sold status
2026-09-06 08:04:11  INFO    9 newly sold/gone
2026-09-06 08:04:11  INFO  Searching Vinted for new listings (max 20 pages per query)
2026-09-06 08:12:44  INFO  Found 312 unique listings from search
2026-09-06 08:12:44  INFO  New listings added: 47
2026-09-06 08:12:44  INFO  === Scrape run complete ===
```
