# shirt-sales

Which football shirts sell on Vinted UK, and for how much?

A daily scraper tracks every new-with-tags, current-season football shirt listed by UK sellers on vinted.co.uk, watches each one until it sells, and turns the result into a spreadsheet and a dashboard. Team, player name and shirt number are parsed from listing titles and cross-checked against current squads.

This repo is public (unlimited GitHub Actions minutes); the data isn't — everything scraped, and everything about the owner's own listings/sales, lives in two private [Turso](https://turso.tech) databases reached only via secrets, never committed here. See `docs/06-running.md` → "Repository visibility".

## How it works

- **Scrape** (`scraper/`) — searches Vinted's JSON API as a guest for every team by name ("Arsenal shirt" / "kit" / "top"), filters to "New with tags" + current season + actual football shirts, stores listings in Turso, and re-checks active listings on a rotation to detect when they sell.
- **Analyse** (`analysis/`) — `export.py` writes a spreadsheet; `visualise.py` builds a self-contained dashboard with inline-SVG charts, including a "My shop" section that tracks the owner's own Vinted listings (from the public wardrobe endpoint) and hand-recorded sales against the market. Both are deployed to Netlify, never committed to this repo.
- **Automate** (`.github/workflows/`) — `scrape.yml` runs the whole pipeline daily (triggered by a Netlify scheduled function, since GitHub's own cron proved unreliable here) and deploys spreadsheet + dashboard to Netlify; `update-squads.yml` refreshes the player-squad list from Wikipedia monthly. The dashboard sits behind HTTP Basic Auth. If Vinted starts blocking the scraper, the run fails and opens a GitHub Issue.

## Running locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export TURSO_MARKET_URL=... TURSO_MARKET_TOKEN=...   # turso db show/tokens create — see docs/06-running.md
export TURSO_MINE_URL=... TURSO_MINE_TOKEN=...

python -m scraper.run          # scrape + re-check
python -m analysis.export      # spreadsheet
python -m analysis.visualise   # dashboard → docs/index.html
python -m scraper.squads       # refresh scraper/players.json
```

Preview the dashboard with `python3 -m http.server 8765 --directory docs`.

## Docs

The reasoning behind every decision lives in `docs/`:

| | |
|---|---|
| `01-overview.md` | Goals, scope, research questions |
| `02-data-model.md` | Fields collected, the two-database Turso schema |
| `03-scraping-strategy.md` | Vinted's API, the condition/season/off-topic filters, sold detection |
| `04-parsing.md` | Team, player, number and season extraction from titles |
| `05-analysis.md` | Spreadsheet tabs and dashboard charts |
| `06-running.md` | Setup, scheduling, Turso, Netlify deploy and access control, repo visibility |
