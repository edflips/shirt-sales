# Analysis & Output

Two outputs are regenerated from the two Turso databases (`02-data-model.md`) on every scraper run, both built in `analysis/`, both deployed straight to Netlify — neither is ever committed to the repo (`06-running.md`):

- `analysis/export.py` → `shirt-sales.xlsx` (spreadsheet, described below)
- `analysis/visualise.py` + `analysis/dashboard.html` → `docs/index.html` (dashboard — see `06-running.md`)

Both only ever see rows already scoped to "New with tags" + current season (the filtering happens at scrape time, not here — see `03-scraping-strategy.md`).

## Dashboard: `docs/index.html`

A single self-contained HTML file with no external dependencies: `visualise.py` computes **one pre-aggregated view per time-filter preset** (seven of them — every KPI, chart series and the My shop rows for that range) and embeds those as JSON into `analysis/dashboard.html` (the template — CSS, markup, and a small vanilla-JS chart renderer). The page never sees individual listings, so its size is ~50 KB whether the database holds 2,000 rows or 200,000; the filter just swaps which view is displayed. (An earlier version embedded one record per listing and aggregated in the browser — fine at 2,400 rows, ~6 MB at 50,000, so it was replaced once per-team searching made that scale realistic.)

**Time filter.** A row of presets above the KPI tiles — All · 3 months · 1 month · 2 weeks · 1 week · 3 days · 1 day — scopes the *entire* page: hero, tiles, every chart, the sold analysis (which locks again if the window has fewer than 10 sales) and My shop. The rule is *listings uploaded on or after the cutoff*, with the cutoff anchored to the data's last-update time rather than the viewer's clock, so "1 week" means the same thing to everyone until the next run. Sold statistics within a window therefore mean "of the listings uploaded in this window, those that have since sold" — one consistent denominator. The choice persists in `localStorage`, and changing it re-runs the grow-in animation. Plotly was dropped: hand-built SVG is what makes the animation possible and keeps the marks thin and the chrome quiet. Rebuilt from scratch on every run via `python -m analysis.visualise`.

Design rules the charts follow (from the dataviz method used to build it): one series colour per chart (blue), bars ≤ 24px with a rounded data-end and square baseline, hairline gridlines, values in text tokens rather than the series colour, a hover tooltip on every mark, a **Table** toggle on every chart as its accessible twin, a dark theme only (pinned with `data-theme="dark"` on `<html>` — the owner's preference; a light token set and a header toggle existed briefly and were removed), and `prefers-reduced-motion` disables every animation. The two-series chart (named vs blank, once sold) uses the validated blue/orange pair with a legend.

Sections and charts:

- **Hero** — one count-up hero number (shirts in the selected range) with active / sold / days-tracking beneath it and a "last updated" status
- **Time filter** — see above
- **KPI tiles** — sold, team identified (%), with a player name (%), median asking price, share of listings seen with a paid bump
- **01 Who's listing what** — Listings by team (top 20); Named vs blank (a meter, not a two-slice donut); Player names; Demand by team (avg favourites, top 12 by volume)
- **02 Pricing & supply** — Asking price by team (median dot, middle-50% bar, 10th–90th line; top 10); Asking price distribution (£5 bands); New listings per day (draw-on line over the selected range, capped at 90 days); Size mix
- **03 What actually sells** — locked card until `MIN_SOLD_FOR_ANALYSIS` (10) listings in the range have sold, then: Named vs blank once sold (horizontal paired bars, one row per metric on its own scale — replaces the old single-axis grouped bar that mixed £, days and favourites); Price bands (sell rate and average days to sell as two side-by-side charts — replaces the old dual-axis chart); **Bumped vs not bumped** (sell rate over all listings in each group, then days to sell / asking price / favourites over those that sold — the "does paying £1.45 to bump actually work?" question); Days to sell by team (min 5 sold).

- **04 My shop** — the owner's own listings and sales (`my_listings` table, see `06-running.md` → My shop): four tiles (shirts sold, revenue, average days to sell, listed now) and one table with a row per shirt — listed date, price (sold price if sold, otherwise asking), the price as a percentage above/below the **market median asking price for the same team** with how many tracked listings that's based on, and status (active with views/favourites, or sold with date, days to sell, and the market's average days to sell for that team once at least 3 have sold). A table rather than a chart because it's a handful of rows the reader wants to scan individually.

Dropped from the old dashboard: the condition-breakdown donut (every stored listing is now "New with tags" by construction, so it was a single-slice pie).

## Spreadsheet: `shirt-sales.xlsx`

Regenerated on each scraper run from the market Turso database.

### Tab 1: Raw Data

One row per listing. All fields from the data model. Frozen header row, auto-filter enabled.

Columns: `listing_id`, `title`, `team`, `has_player_name`, `player_name`, `shirt_number`, `asking_price`, `favourites`, `upload_timestamp`, `sold`, `time_to_sell_days`, `size`, `condition`, `season`, `url`

### Tab 2: By Team

Aggregated per team, sold listings only:

| Column | Calculation |
|---|---|
| Team | Group key |
| Total sold | COUNT |
| Avg asking price | MEAN |
| Median asking price | MEDIAN |
| Avg time to sell (days) | MEAN of time_to_sell_days |
| Avg favourites | MEAN |
| % with player name | COUNT(has_player_name) / total |
| Best selling player | MODE of player_name |

### Tab 3: Named vs Blank

Grouped by `has_player_name` (True/False), across all teams and per team:

- Avg price
- Avg time to sell
- Avg favourites
- Volume sold

Key question answered here: do named shirts sell faster/higher?

### Tab 4: Player Names

For shirts with a player name, grouped by `player_name`:

- Times listed
- Times sold
- Avg price
- Avg favourites
- Avg time to sell
- Teams they appear for

Surfaces which players are in most demand.

### Tab 5: Price Bands

Bucket listings into price ranges (£0–10, £10–20, £20–30, £30–50, £50+):

- Volume in each band
- Sell rate (sold / total seen)
- Avg time to sell per band

### Tab 6: Interesting Stats

Manually curated or auto-generated highlights:

- Fastest selling shirt ever recorded
- Most favourited listing
- Highest priced sold shirt
- Team with fastest avg sell time
- Most common player name across all teams
- Named vs blank sell speed ratio

## Key metrics defined

**Sell rate**: `sold_count / total_seen` — only meaningful after enough time has passed for listings to age.

**Demand score**: `favourites / days_listed` — normalises favourites by how long the listing has been up.

**Time to sell**: Upper bound. Listings that never sold are excluded from this metric (they'd skew it high). Flag them separately.

## Minimum data thresholds

Don't surface stats for groups with fewer than 10 sold listings — too noisy. Show a "not enough data" label instead.

## Libraries

```python
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from scraper.store import get_conn   # Turso connection, not sqlite3 directly
```

The dashboard needs nothing beyond pandas — its charts are plain SVG built in the browser by `analysis/dashboard.html`.
