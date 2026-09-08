# Data Model

## Per-listing fields

| Field | Type | Source | Notes |
|---|---|---|---|
| `listing_id` | string | API `id` | Vinted's internal ID |
| `url` | string | API `url` | Full listing URL |
| `title` | string | API `title` | Raw title as posted |
| `asking_price` | float | API `price.amount` | GBP |
| `favourites` | int | API `favourite_count` | Snapshot at scrape time |
| `view_count` | int | API `view_count` | Snapshot at scrape time |
| `upload_timestamp` | datetime | API `photo.high_resolution.timestamp` | Unix int → ISO 8601; precise upload time |
| `sold` | int | Derived | 0 = active, 1 = sold/gone |
| `first_seen_at` | datetime | Scraper | When we first recorded it |
| `last_seen_at` | datetime | Scraper | Most recent scrape |
| `sold_detected_at` | datetime | Scraper | When sold status first detected |
| `time_to_sell_days` | float | Calculated | `sold_detected_at - upload_timestamp` (upper bound) |
| `team` | string | Parsed | See `04-parsing.md` |
| `has_player_name` | int | Parsed | 1 if name on back of shirt |
| `has_number` | int | Parsed | 1 if number on shirt |
| `player_name` | string | Parsed | Extracted name if present |
| `shirt_number` | string | Parsed | Extracted number if present |
| `season` | string | Parsed | Normalised `YYYY/YYYY`, e.g. `2026/2027` — from `2026/2027`, `2026/27`, `26/27`, or `2026/7` in the title |
| `size` | string | API `size_title` | As listed by seller |
| `condition` | string | API `status` | Vinted condition label ("Very good", "New with tags", etc.) |
| `is_replica` | int | Parsed | 1 if replica/retro/vintage signals in title |
| `is_authentic` | int | Parsed | 1 if authentic/match-worn/player-issue signals in title |
| `bumped` | int | API `promoted` | 1 if ever seen with Vinted's paid "bump" (£1.45 for 3 days, pushes the listing up search results). Only visible in search results, not on the item page or wardrobe |
| `bumped_days` | int | Derived | Number of scrape runs on which it was seen bumped — a bump can be bought repeatedly |
| `last_checked_at` | datetime | Scraper | When the sold-status re-check last got an answer; drives the re-check rotation (`03-scraping-strategy.md`) |

## Notes on key fields

**`upload_timestamp`**: The search API does not return a `created_at` field. The upload timestamp is extracted from `item.photo.high_resolution.timestamp`, a Unix integer that corresponds to when the listing was created. Confirmed accurate against live listings.

**`condition`**: The API field `item.status` contains the condition label, not sold status. This was confirmed during the verification run. As of the scope-tightening pass, only listings where this equals `"New with tags"` are fetched in the first place (see `03-scraping-strategy.md`) — the field is kept in the schema mainly for clarity/debugging, not as a live filter.

**`season`**: Parsed from the title with `scraper.parse.detect_season()`. The regex accepts a 1-4 digit second year and the match is only accepted if the second year equals the first year + 1 — this rejects noise like a size range ("10/12") that would otherwise look like a season. Only listings with no season (assumed current) or with the *current* season are stored; a listing naming an older season is discarded before it reaches the database (`scraper.parse.matches_current_season()`).

**`is_authentic`**: Deliberately does **not** include "bnwt"/"bnwot" — those describe tag status (covered by the `condition`/status-filter mechanism above), not shirt authenticity. Conflating the two was an early bug; keeping them separate is what "tightening up" the parsing meant.

**`time_to_sell_days`**: Upper bound — the listing sold sometime between `last_seen_at` (last confirmed active) and `sold_detected_at`. With daily runs the margin of error is ≤1 day.

**Sold detection**: Vinted's individual item API returns 404 for guest tokens. Sold status is detected by loading the listing's HTML page and reading the JSON-LD `offers.availability` field: `InStock` = active, anything else = sold/gone.

## Storage: two Turso databases, not a file in this repo

The repo is public (see `06-running.md`); the data isn't. Both databases live on [Turso](https://turso.tech) (hosted libSQL — SQLite-compatible, every connection requires a bearer token, no anonymous access), reached only from GitHub Actions via secrets. Nothing in the repo — code or git history — ever contains scraped data or the owner's sale prices again.

- **`shirt-sales-market`** — `listings` + `scrape_runs` below. Large (tens of thousands of rows), grows daily.
- **`shirt-sales-mine`** — `my_listings` only. Small, the owner's own data. Separated so a leaked token for one database never exposes the other, and the market database can be wiped/rebuilt without touching sale history.

```sql
-- shirt-sales-market
CREATE TABLE listings (
    listing_id        TEXT PRIMARY KEY,
    url               TEXT,
    title             TEXT,
    asking_price      REAL,
    favourites        INTEGER,
    view_count        INTEGER,
    upload_timestamp  TEXT,       -- ISO 8601
    sold              INTEGER DEFAULT 0,
    first_seen_at     TEXT,
    last_seen_at      TEXT,
    sold_detected_at  TEXT,
    time_to_sell_days REAL,
    team              TEXT,
    has_player_name   INTEGER DEFAULT 0,
    has_number        INTEGER DEFAULT 0,
    player_name       TEXT,
    shirt_number      TEXT,
    season            TEXT,
    size              TEXT,
    condition         TEXT,
    is_replica        INTEGER DEFAULT 0,
    is_authentic      INTEGER DEFAULT 0,
    bumped            INTEGER DEFAULT 0,
    bumped_days       INTEGER DEFAULT 0,
    last_checked_at   TEXT
);

CREATE TABLE scrape_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT,
    finished_at     TEXT,
    listings_found  INTEGER,
    listings_new    INTEGER,
    listings_sold   INTEGER
);

-- shirt-sales-mine
CREATE TABLE my_listings (           -- the owner's own listings and sales (see 05/06)
    listing_id        TEXT PRIMARY KEY,   -- Vinted item id, or a generated id for hand-recorded sales
    source            TEXT,               -- 'vinted' (wardrobe endpoint) or 'manual' (add_sale(), see 06-running.md)
    url               TEXT,
    title             TEXT,
    team              TEXT,
    player_name       TEXT,
    has_player_name   INTEGER DEFAULT 0,
    shirt_number      TEXT,
    size              TEXT,
    condition         TEXT,
    asking_price      REAL,
    sold_price        REAL,               -- only known for manual entries; Vinted doesn't expose it
    favourites        INTEGER,
    view_count        INTEGER,
    listed_at         TEXT,
    sold_at           TEXT,
    sold              INTEGER DEFAULT 0,
    bumped            INTEGER DEFAULT 0,
    first_seen_at     TEXT,
    last_seen_at      TEXT
);
```

`scraper/db.py` is a small compatibility shim over the raw `libsql` Python client: measured directly, the client returns plain tuples (no `row["column"]` access) and only accepts positional `?` parameters (no `:name`-style dict binding), despite being marketed as sqlite3-compatible. The shim translates `:name` SQL text to positional calls and wraps rows using `cursor.description`, so every query in `store.py`/`mine.py` keeps its original SQL text unchanged. Read-only analytics code (`analysis/export.py`, `analysis/visualise.py`) uses `connection.raw` — pandas' `read_sql_query` works fine against the unshimmed client directly, since those queries take no parameters.
