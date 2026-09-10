# Scraping Strategy

## API endpoint

Vinted exposes a JSON API used by its own web frontend. All scraping targets this directly — no HTML parsing of search results required.

```
GET https://www.vinted.co.uk/api/v2/catalog/items
    ?search_text=football+shirt
    &page=1
    &per_page=96
    &order=newest_first
    &status_ids[]=6
```

`status_ids[]=6` filters to Vinted's "New with tags" condition — this is the "with labels" scope constraint, applied at the API level rather than by guessing from the title. Confirmed by probing `GET /api/v2/catalog/filters` (which names filter `id=3, code=status` but doesn't expose option IDs for guest sessions) and then comparing live results per candidate ID:

| `status_ids[]` | Condition returned |
|---|---|
| 1 | New without tags |
| 2 | Very good |
| 3 | Good |
| 4 | Satisfactory |
| 5 | (no results — unused) |
| 6 | **New with tags** |

Similarly, `country_ids[]` was tested during UK-scoping work and made no measurable difference — `vinted.co.uk` already restricts results to UK-registered sellers by domain, so no country param is sent.

### Search queries run per scrape

One search per **team name × suffix** — "Arsenal shirt", "Arsenal kit", "Arsenal top", for every team in `teams.TEAMS` (`fetch.build_queries()`, ~270 queries). `teams.SEARCH_NAMES` overrides the name where UK sellers habitually write something else ("Man Utd", "Spurs", "PSG", "Sheff Wed"); `teams.SEARCH_SUFFIXES` is the UK vocabulary — *shirt*, *kit*, *top*. "Jersey" and "soccer" were dropped deliberately: UK sellers almost never use them, and they mostly surfaced American listings (NFL/NBA/MLS), which is where a lot of the early noise came from.

This replaced four generic phrases ("football shirt", "football jersey", …). Those had two problems: recall was bounded by whether a seller happened to use the phrase — "Barcelona 26/27 kit" or "Arsenal home 26/27" never entered the pipeline at all — and precision was poor because the phrases match training tops, polos and other sports. Searching by team name catches the phrase-less titles and makes nearly every result team-attributable, which the player-name parser needs anyway (`04-parsing.md`).

Queries run `SEARCH_WORKERS` (6) at a time with the usual per-request pause, each paginating its own results. Because every query names a team, a result whose **title** names no recognised team matched on its description or the seller's location instead — "Leeds kit" returns first-aid kits from sellers in Leeds — and is dropped. Results are deduplicated by `listing_id` across queries before storing (a shirt found by both "Man Utd shirt" and "Manchester United kit" is stored once).

Scale, measured on 2026-09-07: "Arsenal shirt" alone returns ~550 in-scope Arsenal listings, against the 30 the generic searches had found — the generic phrases were capped at the newest 960 across *all* teams, so they saw perhaps a tenth of the market. Per-team searching sees most of it, which is the point, but it makes the pool tens of thousands of listings rather than ~2,400 — see "Re-check runtime" for what that means.

## Authentication

The API requires a valid token but **no login is needed**. Hitting the homepage first causes Vinted to issue a guest JWT (`access_token_web` cookie) automatically. The scraper does this on session initialisation.

No Playwright, no CAPTCHA, no VPN required. Confirmed in verification run.

## Tech stack

| Tool | Purpose |
|---|---|
| `requests` | API calls + listing HTML page fetches |
| `libsql` | Storage — two Turso (hosted libSQL) databases; see `02-data-model.md` |

## Where the data lives

All listing data comes from the JSON API response. Key fields and their actual API locations (confirmed in verification run):

| Data | API field |
|---|---|
| ID | `item.id` |
| Title | `item.title` |
| Price | `item.price.amount` |
| Favourites | `item.favourite_count` |
| Views | `item.view_count` |
| Upload timestamp | `item.photo.high_resolution.timestamp` (Unix int) |
| Condition | `item.status` (e.g. "Very good", "New with tags") |
| Size | `item.size_title` |
| Paid bump | `item.promoted` (`true` while the seller's £1.45 / 3-day bump is active; ~4% of a results page). Search results only — the item page and the wardrobe endpoint never show it, so the owner's own bumps are recorded by hand in `data/my_sales.json` or picked up when the market scrape finds the listing |

Note: `item.status` is the **condition label**, not sold status. There is no sold flag in the search API response.

## Season scoping

Vinted has no season/year facet for clothing, so this is applied client-side after the API responds. `scraper.parse.matches_current_season(title)` keeps an item unless its title explicitly names a *different* season — most current-season listings don't mention a year at all, so "no season in title" is treated as current, not excluded. See `04-parsing.md` for the season regex and `02-data-model.md` for the reasoning.

## Off-topic filtering

The search terms ("football shirt", "football jersey", "football top", "soccer shirt") match far more than replica football shirts: American football and NBA jerseys, cricket shirts, polos, hoodies, training tops, football boots, trading cards, novelty tees. `scraper.parse.is_off_topic(title)` drops anything matching `OFF_TOPIC_SIGNALS` (in `teams.py`) before it's parsed or stored — see `04-parsing.md`. Vinted has no category facet on search that would do this server-side.

## Scraper flow

```
Session init:
  GET https://www.vinted.co.uk/ → guest JWT issued as cookie

Re-check existing active listings:
  For each active listing in DB:
    GET listing HTML page
    Parse JSON-LD offers.availability
    If not "InStock" → mark_sold()

Search for new listings:
  For each query in SEARCH_QUERIES:
    Paginate through results pages (status_ids[]=6 applied server-side)
    For each item:
      Skip if not a UK/GBP listing, if the title is off-topic (is_off_topic),
        or if the title names a non-current season (matches_current_season)
      If listing_id not in DB → included in the next upsert_listings_batch() call
      If already in DB → update favourites + last_seen_at

Log run stats to scrape_runs table
```

## Sold listing detection

The individual item API (`/api/v2/items/{id}`) returns 404 for guest tokens, so sold status can't come from a single-item lookup.

**Confirmed approach**: load the listing's HTML page and read the JSON-LD structured data block:

```json
{
  "@type": "Product",
  "offers": {
    "availability": "InStock"
  }
}
```

- `InStock` → **active**
- Any other availability → **sold**
- 200 with **no** product JSON-LD but a visible **"Sold" badge** (`>Sold<` in the HTML) → **sold**. This is how Vinted actually renders a sold listing — confirmed on 2026-09-07 against a known sale (the owner's own Chelsea kit). Until then this case was filed as "unknown" *and* counted as a block signal, so real sales were being missed and eight in a row would have tripped the circuit breaker.
- 200 with no product JSON-LD but a **"Reserved" badge** → **reserved**, recorded as a sale from that moment. Reserved is what a purchase in progress looks like (buyer has paid, transaction completes a day or two later — confirmed on the owner's Man Utd kit the day before it went through), so it's the more accurate sale timestamp. A seller can also reserve manually for a buyer who then doesn't pay; that's rare enough to accept, and the run logs sold/reserved/gone counts separately so it stays visible.
- HTTP 404 / 410 → **gone** (delisted/removed)
- Anything else → **unknown**: a dropped connection, a 5xx or 429 (after 3 attempts with backoff), or a 200 with neither product JSON-LD nor a Sold badge (a challenge/interstitial page)

"sold" and "gone" are treated the same way: `mark_sold()` is called, recording `sold_detected_at` and calculating `time_to_sell_days`. **"unknown" is not** — the listing stays active and is simply re-checked on the next run. This distinction matters: the first version returned "gone" for any request error, so a single dropped connection recorded a fake sale (this happened on the 2026-09-07 run), and a bot block or a page-markup change would have marked every listing in the database sold in one go. The run also logs a warning if more than 20% of re-checks come back unknown, since that pattern means Vinted is blocking us or the page changed, not that the network hiccupped.

## Rate limiting

- **Between requests**: 3–8 seconds (randomised)
- **Between queries**: additional 3–8 second pause
- **Run frequency**: daily via GitHub Actions cron
- No IP rotation or VPN required

## Re-check runtime and parallelism

`recheck_active()` re-checks every active (unsold) listing with one HTTP request each, keeping the same 3–8 s pause between requests. Serially that was 5+ hours for the first unscoped scrape (~3,400 listings). Scoping to "New with tags" + current season cut the *stored* set to a few hundred — but because `status_ids[]=6` is applied server-side, each search query now returns up to 960 matching listings instead of the ~14% of a generic top-960 that happened to be NWT, so the second run found 2,100+ in-scope listings and the pool is ~2,400 and growing.

Two things bound the re-check:

1. **Rotation** (`store.get_listings_due_for_recheck`). Most shirts that sell do so in their first couple of weeks, so that's where day-level time-to-sell resolution matters: listings under `RECHECK_DAILY_UNDER_DAYS` (14) old are checked every run; older ones every `RECHECK_OLDER_EVERY_DAYS` (3), least-recently-checked first; and the batch is capped at `RECHECK_MAX_PER_RUN` (4,000). A listing whose last check got no answer (unknown/blocked) keeps `last_checked_at` NULL and goes to the front of the next run's queue. The run logs "Re-checking N of M active listings" so the backlog is visible.
2. **Modest parallelism**: `RECHECK_WORKERS` (4) threads, one `requests.Session` each (sessions aren't thread-safe), each still pausing 3–8 s between its own requests — ~0.7 requests/second in aggregate. Six workers were tried first and drew HTTP 429s from Vinted within a minute (2026-09-07 run 34124236615, which is what opened the first `scrape-blocked` issue), so raising concurrency further is the wrong lever; rotation is what keeps the runtime bounded as the pool grows. Workers stagger their first request by 0–4 s so they don't all open a session at once.

## Detecting being blocked

Parallelism raises the chance Vinted objects, so the scraper watches for it explicitly. `BlockMonitor` (in `fetch.py`) is shared across the worker threads and counts **block signals**:

| Response | Treated as | Block signal? |
|---|---|---|
| 200 with product JSON-LD | active / sold | no (resets the consecutive count) |
| 404 / 410 | gone | no |
| 403 | blocked | **yes** |
| 429, still after 3 attempts with 20 s+ backoff | blocked | **yes** |
| 5xx after 3 attempts, or a network error after 3 attempts | unknown | no |
| 200 with no product JSON-LD but a "Sold" badge | sold | no (resets the consecutive count) |
| 200 with no product JSON-LD but a "Reserved" badge | reserved (recorded as sold) | no (resets the consecutive count) |
| 200 with neither product JSON-LD nor a Sold badge (a challenge/interstitial page) | unknown | **yes** |

Eight consecutive signals trip a **circuit breaker**: remaining listings are marked `skipped` without being requested, so a block doesn't turn into an hour of hammering. Search requests returning 403/429 count too. None of `unknown`/`blocked`/`skipped` is ever recorded as a sale.

At the end of the run `run.py` writes `logs/run-status.json` with the counts and sets `alert: true` if the breaker tripped, 5+ re-checks were refused, or more than 20% got no usable answer. What happens with that is in `06-running.md` → Alerting.

## Pagination

The API returns `pagination.total_pages`. Scrape until either:
- Current page ≥ `total_pages`, or
- No items returned on a page
