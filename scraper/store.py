import os
from datetime import datetime, timedelta, timezone

from . import db

# Two separate Turso databases (see docs/06-running.md): the large, fast-growing
# scraped market data, and the owner's own small, slow-growing listings/sales.
# Splitting them means a leaked token for one never exposes the other, and the
# market database can be wiped/rebuilt without touching sale history.
#
# Each is an embedded replica: a local file that syncs with the remote (reads
# hit the local copy; writes go straight to the remote leader). The local path
# is just a scratch cache — gitignored, rebuilt from the remote on every run.
MARKET_DB_PATH = "data/shirts.db"
MINE_DB_PATH = "data/mine.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn(local_path: str = MARKET_DB_PATH) -> db.Connection:
    return db.connect(local_path, os.environ["TURSO_MARKET_URL"], os.environ["TURSO_MARKET_TOKEN"])


def get_mine_conn(local_path: str = MINE_DB_PATH) -> db.Connection:
    return db.connect(local_path, os.environ["TURSO_MINE_URL"], os.environ["TURSO_MINE_TOKEN"])


def init_db(conn: db.Connection) -> None:
    """Market database: the scraped listings and a log of each run."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            listing_id        TEXT PRIMARY KEY,
            url               TEXT,
            title             TEXT,
            asking_price      REAL,
            favourites        INTEGER,
            view_count        INTEGER,
            upload_timestamp  TEXT,
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
            bumped            INTEGER DEFAULT 0,   -- ever seen with Vinted's paid "bump" (promoted=true in search results)
            bumped_days       INTEGER DEFAULT 0,   -- number of scrape runs it was seen bumped on
            last_checked_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS scrape_runs (
            run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT,
            finished_at     TEXT,
            listings_found  INTEGER,
            listings_new    INTEGER,
            listings_sold   INTEGER
        );
    """)
    conn.commit()


def init_mine_db(conn: db.Connection) -> None:
    """The owner's own listings/sales database."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS my_listings (
            listing_id        TEXT PRIMARY KEY,
            source            TEXT,       -- 'vinted' (wardrobe endpoint) or 'manual' (hand-recorded)
            url               TEXT,
            title             TEXT,
            team              TEXT,
            player_name       TEXT,
            has_player_name   INTEGER DEFAULT 0,
            shirt_number      TEXT,
            size              TEXT,
            condition         TEXT,
            asking_price      REAL,
            sold_price        REAL,
            favourites        INTEGER,
            view_count        INTEGER,
            listed_at         TEXT,
            sold_at           TEXT,
            sold              INTEGER DEFAULT 0,
            bumped            INTEGER DEFAULT 0,
            first_seen_at     TEXT,
            last_seen_at      TEXT
        );
    """)
    conn.commit()


def _chunks(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


# Turso is a network round trip per statement, not a local file: measured
# directly, an individual INSERT+commit costs ~1s and executemany() is
# ~265ms/row even without a commit per row (it still issues one execute per
# row under the hood) — either would take hours for a day with thousands of
# new listings. A single multi-row `INSERT ... VALUES (...),(...),(...)`
# statement measured at ~2.6ms/row, ~100x faster than executemany, because
# it's genuinely one round trip for the whole batch. BATCH_SIZE keeps each
# statement's placeholder count comfortably under any engine's limit.
BATCH_SIZE = 300

_LISTING_COLUMNS = [
    "listing_id", "url", "title", "asking_price", "favourites", "view_count", "upload_timestamp",
    "sold", "first_seen_at", "last_seen_at", "sold_detected_at", "time_to_sell_days",
    "team", "has_player_name", "has_number", "player_name", "shirt_number",
    "season", "size", "condition", "is_replica", "is_authentic", "bumped", "bumped_days",
]


def _listing_row(item: dict, now: str) -> tuple:
    bumped = int(item.get("bumped") or 0)
    values = {
        **item, "listing_id": str(item["listing_id"]),
        "first_seen_at": now, "last_seen_at": now,
        "bumped": bumped, "bumped_days": bumped,  # a brand-new row's bumped_days starts equal to bumped
    }
    return tuple(values.get(c) for c in _LISTING_COLUMNS)


def upsert_listings_batch(conn: db.Connection, items: list[dict]) -> int:
    """Insert new listings / update last_seen_at + favourites + bumped tracking
    for existing ones, in as few round trips as possible. Returns how many were new.
    """
    if not items:
        return 0
    now = now_iso()
    ids = [str(i["listing_id"]) for i in items]

    existing_ids: set[str] = set()
    for chunk in _chunks(ids, BATCH_SIZE):
        placeholders = ",".join("?" * len(chunk))
        existing_ids.update(
            r["listing_id"] for r in
            conn.execute(f"SELECT listing_id FROM listings WHERE listing_id IN ({placeholders})", chunk).fetchall()
        )
    new_count = sum(1 for i in ids if i not in existing_ids)

    columns_sql = ",".join(_LISTING_COLUMNS)
    for chunk in _chunks(items, BATCH_SIZE):
        rows = [_listing_row(item, now) for item in chunk]
        values_sql = ",".join("(" + ",".join("?" * len(_LISTING_COLUMNS)) + ")" for _ in rows)
        flat = [v for row in rows for v in row]
        conn.execute(f"""
            INSERT INTO listings ({columns_sql}) VALUES {values_sql}
            ON CONFLICT(listing_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                favourites = excluded.favourites,
                bumped = MAX(listings.bumped, excluded.bumped),
                bumped_days = listings.bumped_days + excluded.bumped
        """, flat)
        conn.commit()
    return new_count


def mark_sold(conn: db.Connection, listing_id: str) -> None:
    """Mark a listing as sold, calculating time_to_sell from its upload_timestamp."""
    now = now_iso()
    row = conn.execute(
        "SELECT upload_timestamp FROM listings WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    if row is None:
        return

    time_to_sell = None
    upload_ts = row["upload_timestamp"]
    if upload_ts:
        try:
            uploaded = datetime.fromisoformat(upload_ts)
            detected = datetime.fromisoformat(now)
            time_to_sell = (detected - uploaded).total_seconds() / 86400
        except ValueError:
            pass

    conn.execute("""
        UPDATE listings
        SET sold = 1, sold_detected_at = ?, time_to_sell_days = ?, last_seen_at = ?
        WHERE listing_id = ?
    """, (now, time_to_sell, now, listing_id))
    conn.commit()


RECHECK_DAILY_UNDER_DAYS = 14   # listings younger than this are re-checked every run
RECHECK_OLDER_EVERY_DAYS = 3    # older ones every few days
RECHECK_MAX_PER_RUN = 4000


def ensure_columns(conn: db.Connection) -> None:
    """Additive migrations for the market database (listings created before a column existed)."""
    wanted = {
        "listings": [("last_checked_at", "TEXT"), ("bumped", "INTEGER DEFAULT 0"), ("bumped_days", "INTEGER DEFAULT 0")],
    }
    for table, columns in wanted.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def ensure_mine_columns(conn: db.Connection) -> None:
    """Additive migrations for the mine database."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(my_listings)").fetchall()}
    if "bumped" not in have:
        conn.execute("ALTER TABLE my_listings ADD COLUMN bumped INTEGER DEFAULT 0")
    conn.commit()


def get_listings_due_for_recheck(conn: db.Connection) -> tuple[list[dict], int]:
    """Active listings to re-check this run, and the total number active.

    Most shirts that sell do so in their first couple of weeks, so that's
    where day-level time-to-sell resolution matters: anything listed in the
    last RECHECK_DAILY_UNDER_DAYS is checked every run. Older listings are
    checked every RECHECK_OLDER_EVERY_DAYS, least-recently-checked first, and
    the whole batch is capped so the run's length stays bounded however large
    the pool grows. Listings never checked, or whose last check got no answer
    (unknown/blocked), have last_checked_at NULL and always come first.
    """
    now = datetime.now(timezone.utc)
    young_after = (now - timedelta(days=RECHECK_DAILY_UNDER_DAYS)).isoformat()
    stale_before = (now - timedelta(days=RECHECK_OLDER_EVERY_DAYS)).isoformat()
    total_active = conn.execute("SELECT COUNT(*) FROM listings WHERE sold = 0").fetchone()[0]
    rows = conn.execute(
        """
        SELECT listing_id, url FROM listings
        WHERE sold = 0 AND (
            last_checked_at IS NULL
            OR COALESCE(upload_timestamp, first_seen_at) >= ?
            OR last_checked_at < ?
        )
        ORDER BY last_checked_at IS NOT NULL, last_checked_at, COALESCE(upload_timestamp, first_seen_at) DESC
        LIMIT ?
        """,
        (young_after, stale_before, RECHECK_MAX_PER_RUN),
    ).fetchall()
    return [dict(r) for r in rows], int(total_active)


def mark_checked(conn: db.Connection, listing_ids: list[str]) -> None:
    """Every id gets the same timestamp, so this is one UPDATE...IN per chunk,
    not one statement per id (see BATCH_SIZE note above upsert_listings_batch).
    """
    if not listing_ids:
        return
    now = now_iso()
    for chunk in _chunks(listing_ids, BATCH_SIZE):
        placeholders = ",".join("?" * len(chunk))
        conn.execute(f"UPDATE listings SET last_checked_at = ? WHERE listing_id IN ({placeholders})",
                     [now, *chunk])
        conn.commit()


def log_run(conn: db.Connection, started_at: str, listings_found: int,
            listings_new: int, listings_sold: int) -> None:
    conn.execute("""
        INSERT INTO scrape_runs (started_at, finished_at, listings_found, listings_new, listings_sold)
        VALUES (?, ?, ?, ?, ?)
    """, (started_at, now_iso(), listings_found, listings_new, listings_sold))
    conn.commit()
