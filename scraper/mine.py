"""Track the owner's own Vinted listings and sales alongside the market data.

Active listings come from Vinted's public wardrobe endpoint (the same call
the member profile page makes; works for a guest session). Sold items and
sale prices are not exposed anywhere public, so those are recorded directly
into the shirt-sales-mine Turso database by hand (see add_sale() below) — the
dashboard's "My shop" section merges both sources.
"""
from __future__ import annotations

import logging

from . import db
from .fetch import _make_session, _check_sold_via_jsonld, _upload_ts_from_photo, _parse_price, _sleep
from .parse import parse_title
from .store import now_iso

logger = logging.getLogger(__name__)

MY_USERNAME = "podbury23"
MY_USER_ID = 3125884991
# The owner bumps every listing (£1.45 / 3 days) as a matter of course, and
# Vinted never reports promoted=true for one's own items, so assume bumped
# unless add_sale() is told otherwise.
MY_LISTINGS_BUMPED_BY_DEFAULT = True
MY_PROFILE_URL = f"https://www.vinted.co.uk/member/{MY_USER_ID}-{MY_USERNAME}"

WARDROBE_URL = "https://www.vinted.co.uk/api/v2/wardrobe/{user_id}/items"

_UPSERT = """
    INSERT INTO my_listings (
        listing_id, source, url, title, team, player_name, has_player_name, shirt_number,
        size, condition, asking_price, sold_price, favourites, view_count,
        listed_at, sold_at, sold, bumped, first_seen_at, last_seen_at
    ) VALUES (
        :listing_id, :source, :url, :title, :team, :player_name, :has_player_name, :shirt_number,
        :size, :condition, :asking_price, :sold_price, :favourites, :view_count,
        :listed_at, :sold_at, :sold, :bumped, :now, :now
    )
    ON CONFLICT(listing_id) DO UPDATE SET
        bumped = MAX(excluded.bumped, my_listings.bumped),
        url = excluded.url, title = excluded.title, team = excluded.team,
        player_name = excluded.player_name, has_player_name = excluded.has_player_name,
        shirt_number = excluded.shirt_number, size = excluded.size, condition = excluded.condition,
        asking_price = excluded.asking_price,
        sold_price = COALESCE(excluded.sold_price, my_listings.sold_price),
        favourites = COALESCE(excluded.favourites, my_listings.favourites),
        view_count = COALESCE(excluded.view_count, my_listings.view_count),
        listed_at = COALESCE(excluded.listed_at, my_listings.listed_at),
        sold_at = COALESCE(excluded.sold_at, my_listings.sold_at),
        sold = MAX(excluded.sold, my_listings.sold),
        last_seen_at = excluded.last_seen_at
"""


def fetch_my_items(session) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        resp = session.get(WARDROBE_URL.format(user_id=MY_USER_ID),
                           params={"page": page, "per_page": 20, "order": "relevance"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("items", [])
        items.extend(batch)
        if not batch or page >= data.get("pagination", {}).get("total_pages", 1):
            return items
        page += 1
        _sleep()


def _row_from_vinted(raw: dict) -> dict:
    title = raw.get("title", "")
    parsed = parse_title(title)
    # Wardrobe items differ from search items: a "photos" list instead of
    # "photo", "size" instead of "size_title", and view_count is always 0 for
    # other viewers (stats_visible is false) — so views are unknown, not zero.
    photos = raw.get("photos") or []
    if not raw.get("photo") and photos:
        raw = {**raw, "photo": photos[0]}
    return {
        "listing_id": str(raw["id"]),
        "source": "vinted",
        "url": raw.get("url") or f"https://www.vinted.co.uk/items/{raw['id']}",
        "title": title,
        "team": parsed["team"],
        "player_name": parsed["player_name"],
        "has_player_name": int(parsed["has_player_name"]),
        "shirt_number": parsed["shirt_number"],
        "size": raw.get("size_title") or raw.get("size"),
        "condition": raw.get("status"),
        "asking_price": _parse_price(raw),
        "sold_price": None,
        "favourites": raw.get("favourite_count") or 0,
        "view_count": raw.get("view_count") if raw.get("stats_visible") else None,
        "listed_at": _upload_ts_from_photo(raw),
        "sold_at": None,
        "sold": 0,
        "bumped": 1 if (raw.get("promoted") or MY_LISTINGS_BUMPED_BY_DEFAULT) else 0,
    }


def add_sale(mine_conn: db.Connection, *, title: str, sold_price: float, listed_at: str, sold_at: str,
             id: str | None = None, url: str | None = None, team: str | None = None,
             player_name: str | None = None, shirt_number: str | None = None, size: str | None = None,
             condition: str = "New with tags", asking_price: float | None = None,
             favourites: int | None = None, view_count: int | None = None,
             bumped: bool = MY_LISTINGS_BUMPED_BY_DEFAULT) -> None:
    """Record a hand-known sale directly into the mine database.

    Run this ad hoc (e.g. from a one-off shell command) when the owner
    reports a sale in conversation — not part of the scheduled workflow.
    Team/player/number are parsed from the title if not given explicitly.
    """
    parsed = parse_title(title)
    row = {
        "listing_id": id or f"manual-{abs(hash((title, sold_at)))}",
        "source": "manual", "url": url, "title": title,
        "team": team or parsed["team"],
        "player_name": player_name or parsed["player_name"],
        "has_player_name": int(bool(player_name or parsed["has_player_name"])),
        "shirt_number": shirt_number or parsed["shirt_number"],
        "size": size, "condition": condition,
        "asking_price": asking_price if asking_price is not None else sold_price,
        "sold_price": sold_price, "favourites": favourites, "view_count": view_count,
        "listed_at": listed_at, "sold_at": sold_at, "sold": 1,
        "bumped": int(bool(bumped)),
    }
    mine_conn.execute(_UPSERT, {**row, "now": now_iso()})
    mine_conn.commit()


def sync(market_conn: db.Connection, mine_conn: db.Connection) -> dict:
    """Refresh my_listings from Vinted's wardrobe. Returns counts."""
    now = now_iso()
    session = _make_session()
    try:
        raw_items = fetch_my_items(session)
    except Exception as e:  # requests.RequestException or a malformed response
        logger.warning("Couldn't fetch %s's wardrobe: %s — skipping my-listings sync", MY_USERNAME, e)
        raw_items = None

    newly_sold = 0
    if raw_items is not None:
        active_ids = set()
        for raw in raw_items:
            row = _row_from_vinted(raw)
            active_ids.add(row["listing_id"])
            mine_conn.execute(_UPSERT, {**row, "now": now})
        mine_conn.commit()

        # Anything we tracked from Vinted that has left the wardrobe: confirm via
        # the listing page before calling it sold (same rule as the market data —
        # a fetch error is 'unknown', never a sale).
        known = mine_conn.execute(
            "SELECT listing_id, url FROM my_listings WHERE source = 'vinted' AND sold = 0"
        ).fetchall()
        for listing_id, url in known:
            if listing_id in active_ids:
                continue
            status = _check_sold_via_jsonld(session, url)
            if status in ("sold", "reserved", "gone"):
                mine_conn.execute("UPDATE my_listings SET sold = 1, sold_at = ?, last_seen_at = ? WHERE listing_id = ?",
                                 (now, now, listing_id))
                newly_sold += 1
            _sleep()
        mine_conn.commit()

    # The market scrape sees bumps (promoted=true in search results); the
    # wardrobe doesn't. Two databases means this is two queries, not one join:
    # ask the market DB only about the (small) set of listings we track here.
    unbumped = [r["listing_id"] for r in mine_conn.execute("SELECT listing_id FROM my_listings WHERE bumped = 0").fetchall()]
    if unbumped:
        placeholders = ",".join("?" * len(unbumped))
        bumped_ids = {r["listing_id"] for r in market_conn.execute(
            f"SELECT listing_id FROM listings WHERE bumped = 1 AND listing_id IN ({placeholders})", unbumped
        ).fetchall()}
        if bumped_ids:
            mine_conn.executemany("UPDATE my_listings SET bumped = 1 WHERE listing_id = ?",
                                  [(lid,) for lid in bumped_ids])
            mine_conn.commit()

    counts = mine_conn.execute(
        "SELECT SUM(sold = 0), SUM(sold = 1), SUM(source = 'vinted'), SUM(source = 'manual') FROM my_listings"
    ).fetchone()
    return {
        "active": int(counts[0] or 0), "sold": int(counts[1] or 0),
        "from_vinted": int(counts[2] or 0), "manual": int(counts[3] or 0),
        "newly_sold": newly_sold, "wardrobe_ok": raw_items is not None,
    }
