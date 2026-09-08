from __future__ import annotations

import os
import re
import json
import time
import random
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.vinted.co.uk/api/v2"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.vinted.co.uk/",
}

def build_queries() -> list[str]:
    """One search per (team name × suffix), e.g. "Arsenal shirt", "Arsenal kit".

    Generic phrases ("football shirt") were too blunt: recall was bounded by
    whether a seller happened to use the phrase, and "jersey"/"soccer" pulled
    in American listings. Searching by team name catches "Arsenal home 26/27"
    and "Barcelona 26/27 kit" and makes almost every result team-attributable.
    """
    from .teams import TEAMS, SEARCH_NAMES, SEARCH_SUFFIXES
    queries: list[str] = []
    for team in TEAMS:
        for name in SEARCH_NAMES.get(team, [team]):
            for suffix in SEARCH_SUFFIXES:
                queries.append(f"{name} {suffix}")
    return queries


PER_PAGE = 96
SEARCH_WORKERS = 4

# Vinted's "status" (condition) filter ID for "New with tags" — confirmed by
# probing /api/v2/catalog/filters and comparing live results per status_ids[] value.
NEW_WITH_TAGS_STATUS_ID = 6

_JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)
# A sold listing's page still returns 200 but drops the product JSON-LD and
# shows a "Sold" badge; an active page has JSON-LD with availability InStock.
# A purchase in progress (buyer has paid, transaction not yet complete) looks
# the same but with a "Reserved" badge — that's the moment the sale happened.
_SOLD_BADGE_RE = re.compile(r">\s*Sold\s*<")
_RESERVED_BADGE_RE = re.compile(r">\s*Reserved\s*<")


def _make_session() -> requests.Session:
    session = requests.Session()
    # Hit the homepage first — Vinted issues a guest JWT as access_token_web cookie
    session.headers.update({**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"})
    session.get("https://www.vinted.co.uk/", timeout=30)
    session.headers.update({"Accept": "application/json, text/plain, */*"})
    return session


def _sleep() -> None:
    time.sleep(random.uniform(3, 8))


def _upload_ts_from_photo(item: dict) -> str | None:
    ts = item.get("photo", {}).get("high_resolution", {}).get("timestamp")
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return None


def _is_uk_listing(raw: dict) -> bool:
    """
    vinted.co.uk already scopes to UK-registered sellers — confirmed in verification.
    Guard against the edge case of a non-GBP price slipping through.
    """
    price = raw.get("price", {})
    if isinstance(price, dict):
        return price.get("currency_code", "GBP") == "GBP"
    return True


def _parse_price(raw: dict) -> float | None:
    price_raw = raw.get("price")
    if isinstance(price_raw, dict):
        try:
            return float(price_raw.get("amount", 0))
        except (ValueError, TypeError):
            return None
    if price_raw is not None:
        try:
            return float(price_raw)
        except (ValueError, TypeError):
            return None
    return None


def _parse_item(raw: dict) -> dict:
    from .parse import parse_title

    title = raw.get("title", "")
    parsed = parse_title(title)

    return {
        "listing_id": str(raw["id"]),
        "url": raw.get("url") or f"https://www.vinted.co.uk/items/{raw['id']}",
        "title": title,
        "asking_price": _parse_price(raw),
        "favourites": raw.get("favourite_count") or 0,
        "view_count": raw.get("view_count") or 0,
        "upload_timestamp": _upload_ts_from_photo(raw),
        # condition is stored in item["status"] — NOT sold status
        "condition": raw.get("status"),
        "size": raw.get("size_title"),
        "sold": 0,
        "sold_detected_at": None,
        "time_to_sell_days": None,
        # Vinted's paid "bump" (£1.45 for 3 days, pushes the listing up search
        # results) shows as promoted=true in search results only
        "bumped": 1 if raw.get("promoted") else 0,
        **parsed,
        "has_player_name": int(parsed["has_player_name"]),
        "has_number": int(parsed["has_number"]),
        "is_replica": int(parsed["is_replica"]),
        "is_authentic": int(parsed["is_authentic"]),
    }


RECHECK_ATTEMPTS = 3
# 6 workers drew 429s from Vinted within a minute on 2026-09-07; 4 keeps the
# aggregate rate under ~0.7 requests/s. Runtime is bounded by rotation and
# the per-run cap instead (store.get_listings_due_for_recheck).
RECHECK_WORKERS = 4
BLOCK_TRIP = 8  # consecutive block signals before the re-check stops itself


class BlockMonitor:
    """Shared across re-check threads: counts block signals, trips a stop flag.

    A 'block signal' is a response that looks like Vinted refusing us rather
    than the network hiccupping: HTTP 403, a 429 that survives backoff, or a
    200 whose page has no product JSON-LD (what a challenge/interstitial page
    looks like). Any good response resets the consecutive count, so BLOCK_TRIP
    genuinely means "N in a row", not N scattered across the run.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.consecutive = 0
        self.total = 0
        self.last_reason = ""
        self._stop = threading.Event()

    def signal(self, url: str, reason: str) -> None:
        with self._lock:
            self.consecutive += 1
            self.total += 1
            self.last_reason = f"{reason} on {url}"
            if self.consecutive >= BLOCK_TRIP and not self._stop.is_set():
                self._stop.set()
                logger.error("Stopping re-check: %d consecutive block signals (last: %s)",
                             self.consecutive, self.last_reason)

    def ok(self) -> None:
        with self._lock:
            self.consecutive = 0

    def tripped(self) -> bool:
        return self._stop.is_set()

    def reset(self) -> None:
        with self._lock:
            self.consecutive = self.total = 0
            self.last_reason = ""
            self._stop.clear()


BLOCK_MONITOR = BlockMonitor()

_thread_local = threading.local()


def _thread_session() -> requests.Session:
    """One session per worker thread (requests.Session isn't thread-safe)."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        # stagger so six workers don't all hit the homepage in the same instant
        time.sleep(random.uniform(0, 4))
        session = _thread_local.session = _make_session()
    return session


def _check_sold_via_jsonld(session: requests.Session, url: str) -> str:
    """
    Load the listing HTML page and read JSON-LD offers.availability.
    Returns: 'active', 'sold', 'reserved' (a purchase in progress — treated
    as sold from this moment), 'gone' (404/410 — the listing was removed),
    'blocked' (Vinted refused the request), or 'unknown' (no trustworthy
    answer this run).

    Only 'sold' and 'gone' are ever recorded as a sale. 'unknown' and
    'blocked' are deliberately distinct from 'gone': a dropped connection, a
    5xx, a 429, or a page whose markup we don't recognise says nothing about
    whether the shirt sold, so the listing is left active and re-checked on
    the next run. Treating those as 'gone' would record fake sales — and a
    bot block or a markup change would mark the entire database sold at once.
    """
    resp = None
    for attempt in range(1, RECHECK_ATTEMPTS + 1):
        try:
            resp = session.get(url, timeout=30,
                               headers={**session.headers, "Accept": "text/html,application/xhtml+xml,*/*"})
        except requests.RequestException as e:
            # network-level failure: not a block signal, just retry
            if attempt < RECHECK_ATTEMPTS:
                wait = 5 * attempt + random.uniform(0, 3)
                logger.warning("Listing page %s failed (attempt %d/%d): %s — retrying in %.0fs",
                               url, attempt, RECHECK_ATTEMPTS, e, wait)
                time.sleep(wait)
                continue
            logger.warning("Listing page %s failed after %d attempts: %s — leaving active",
                           url, RECHECK_ATTEMPTS, e)
            return "unknown"

        if resp.status_code in (404, 410):
            BLOCK_MONITOR.ok()
            return "gone"
        if resp.status_code == 403:
            BLOCK_MONITOR.signal(url, "HTTP 403")
            logger.warning("Listing page %s: HTTP 403 — blocked", url)
            return "blocked"
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < RECHECK_ATTEMPTS:
                wait = (20 if resp.status_code == 429 else 5) * attempt + random.uniform(0, 5)
                logger.warning("Listing page %s: HTTP %d (attempt %d/%d) — retrying in %.0fs",
                               url, resp.status_code, attempt, RECHECK_ATTEMPTS, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 429:
                BLOCK_MONITOR.signal(url, "HTTP 429")
                return "blocked"
            logger.warning("Listing page %s: HTTP %d after %d attempts — leaving active",
                           url, resp.status_code, RECHECK_ATTEMPTS)
            return "unknown"
        if not resp.ok:
            logger.warning("Listing page %s: HTTP %d — leaving active", url, resp.status_code)
            return "unknown"
        break
    else:
        return "unknown"

    for match in _JSONLD_RE.finditer(resp.text):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if data.get("@type") == "Product":
            BLOCK_MONITOR.ok()
            availability = data.get("offers", {}).get("availability", "")
            # Schema.org: InStock = available, OutOfStock / Discontinued = sold/gone
            if "InStock" in availability:
                return "active"
            return "sold"

    # No product JSON-LD: that's what a sold listing looks like (confirmed on
    # a known sale, 2026-09-07) — as long as the page shows the Sold badge.
    if _SOLD_BADGE_RE.search(resp.text):
        BLOCK_MONITOR.ok()
        return "sold"
    if _RESERVED_BADGE_RE.search(resp.text):
        BLOCK_MONITOR.ok()
        return "reserved"

    BLOCK_MONITOR.signal(url, "no product JSON-LD and no Sold/Reserved badge (challenge page?)")
    logger.warning("No JSON-LD Product block or Sold/Reserved badge on %s (status %s) — leaving active", url, resp.status_code)
    return "unknown"


def fetch_search_page(session: requests.Session, query: str, page: int) -> dict:
    params = {
        "search_text": query,
        "page": page,
        "per_page": PER_PAGE,
        "order": "newest_first",
        "status_ids[]": NEW_WITH_TAGS_STATUS_ID,
    }
    resp = session.get(f"{BASE_URL}/catalog/items", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _search_query(query: str, max_pages: int) -> list[dict]:
    """All pages of one search, filtered to in-scope listings (raw API dicts).

    Because every query names a team, a result whose *title* names no
    recognised team matched on its description or seller location instead
    ("Leeds kit" returns first-aid kits from sellers in Leeds) — dropped.
    """
    from .parse import matches_current_season, is_off_topic, detect_team

    session = _thread_session()
    found: list[dict] = []
    for page in range(1, max_pages + 1):
        if BLOCK_MONITOR.tripped():
            break
        try:
            data = fetch_search_page(session, query, page)
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (403, 429):
                BLOCK_MONITOR.signal(f"search '{query}' page {page}", f"HTTP {status}")
            logger.warning("Search failed (query=%s page=%d): %s", query, page, e)
            break
        BLOCK_MONITOR.ok()

        items = data.get("items", [])
        if not items:
            break
        for raw in items:
            if not raw.get("id") or not _is_uk_listing(raw):
                continue
            title = raw.get("title", "")
            if is_off_topic(title) or not matches_current_season(title) or detect_team(title) is None:
                continue
            found.append(raw)

        if page >= data.get("pagination", {}).get("total_pages", 1):
            break
        _sleep()

    _sleep()
    return found


def search_all(max_pages: int = 20) -> list[dict]:
    """Run every team query (SEARCH_WORKERS at a time), dedupe by listing id, parse."""
    from .parse import current_season

    queries = build_queries()
    logger.info("Searching %d team queries (status=New with tags, season=%s or unstated)",
                len(queries), current_season())

    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
        per_query = list(pool.map(lambda q: _search_query(q, max_pages), queries))

    seen_ids: set[str] = set()
    results: list[dict] = []
    for query, found in zip(queries, per_query):
        new = 0
        for raw in found:
            item_id = str(raw["id"])
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            results.append(_parse_item(raw))
            new += 1
        logger.debug("  %-32s %3d results, %3d new", query, len(found), new)
    return results


def recheck_active(active_listings: list[dict]) -> list[tuple[str, str]]:
    """
    Check each active listing's current status via JSON-LD, RECHECK_WORKERS
    listings at a time. Each worker keeps the usual 3–8 s pause between its
    own requests, so total rate is roughly workers / 5.5 requests per second.
    Returns list of (listing_id, status) where status is 'active', 'sold',
    'gone', 'unknown', 'blocked', or 'skipped' (the circuit breaker tripped
    before this listing was reached — see BlockMonitor).
    """
    def check(listing: dict) -> tuple[str, str]:
        if BLOCK_MONITOR.tripped():
            return listing["listing_id"], "skipped"
        status = _check_sold_via_jsonld(_thread_session(), listing["url"])
        logger.debug("  %s → %s", listing["listing_id"], status)
        _sleep()
        return listing["listing_id"], status

    with ThreadPoolExecutor(max_workers=RECHECK_WORKERS) as pool:
        return list(pool.map(check, active_listings))
