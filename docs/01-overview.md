# Vinted Football Shirt Sales — Project Overview

## Goal

Scrape Vinted UK to understand which football shirts sell best, broken down by:

- Team
- Whether the shirt has a player name and/or number
- Price
- Demand (favourites count)
- Time to sell

## Research questions

1. Do named/numbered shirts sell faster or for more than blank shirts?
2. Which teams have the highest demand (favourites) relative to supply?
3. Which teams' shirts sell fastest?
4. Is there a sweet spot price range per team?
5. Which player names command a premium or sell quickest?

## Scope

- Platform: Vinted UK (`vinted.co.uk`)
- Category: Football shirts (men's, women's, kids')
- Target: Active listings + sold listings
- Data range: Build forward from project start (historical backfill not possible via Vinted)
- Teams: every team in `scraper/teams.py` — Premier League, Championship, a handful of major global clubs, and the main national teams. Listings are found by searching **each team by name** ("Arsenal shirt" / "kit" / "top"), so a team not in that list is simply never searched for (see `03-scraping-strategy.md`)
- **Condition**: only "New with tags" listings are tracked (filtered via Vinted's own `status_ids[]=6` condition filter — see `03-scraping-strategy.md`)
- **Season**: only the current season (e.g. `2026/2027`) — a title with no season mentioned is assumed current, a title naming an older season is excluded (see `04-parsing.md`)
- **Football shirts only**: the search terms also match NFL/NBA/cricket jerseys, polos, hoodies, training tops, boots and novelty merch; those are dropped by a keyword filter before storage (see `04-parsing.md` → Off-topic filtering)

These filters were added after the first unscoped scrape (~3,400 listings across every condition/season, ~28% of them not football shirts at all) turned out too noisy and too slow to re-check daily.

## Output

- A spreadsheet (`shirt-sales.xlsx`) updated on each scraper run — see `05-analysis.md` for tabs
- A dashboard (`docs/index.html`, self-contained HTML with inline-SVG charts) updated on each run and auto-deployed to Netlify behind HTTP Basic Auth — see `05-analysis.md` and `06-running.md`

## Docs in this folder

| File | Contents |
|---|---|
| `01-overview.md` | This file — goals, scope, research questions |
| `02-data-model.md` | Fields collected per listing and their sources |
| `03-scraping-strategy.md` | How to scrape Vinted, anti-bot considerations |
| `04-parsing.md` | Extracting team/player data from listing titles |
| `05-analysis.md` | Analysis logic and spreadsheet structure |
| `06-running.md` | How to run and schedule the scraper |
