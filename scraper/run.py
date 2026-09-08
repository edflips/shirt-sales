from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .store import (
    get_conn, get_mine_conn, init_db, init_mine_db, ensure_columns, ensure_mine_columns,
    upsert_listings_batch, get_listings_due_for_recheck, mark_checked, mark_sold, log_run,
)
from .fetch import search_all, recheck_active, BLOCK_MONITOR
from .mine import sync as sync_my_listings, MY_USERNAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/scraper.log"),
    ],
)
logger = logging.getLogger(__name__)

STATUS_PATH = Path("logs/run-status.json")

# Share of re-checks that may come back without an answer before we call it a block
UNANSWERED_SHARE_ALERT = 0.2
BLOCKED_COUNT_ALERT = 5


def _alert_reason(active: int, counts: Counter) -> str | None:
    if BLOCK_MONITOR.tripped():
        return f"circuit breaker tripped after consecutive block signals ({BLOCK_MONITOR.last_reason})"
    if counts["blocked"] >= BLOCKED_COUNT_ALERT:
        return f"{counts['blocked']} re-checks were refused (403/429)"
    unanswered = counts["unknown"] + counts["blocked"] + counts["skipped"]
    if active and unanswered / active > UNANSWERED_SHARE_ALERT:
        return f"{unanswered} of {active} re-checks ({100 * unanswered / active:.0f}%) got no usable answer"
    return None


def run(max_pages: int = 20) -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=== Scrape run started ===")
    BLOCK_MONITOR.reset()

    conn = get_conn()
    init_db(conn)
    ensure_columns(conn)
    mine_conn = get_mine_conn()
    init_mine_db(mine_conn)
    ensure_mine_columns(mine_conn)

    # Re-check active listings for sold status — young ones every run, older
    # ones on rotation, capped per run (see store.get_listings_due_for_recheck)
    active, total_active = get_listings_due_for_recheck(conn)
    logger.info("Re-checking %d of %d active listings for sold status", len(active), total_active)
    recheck_results = recheck_active(active)

    counts: Counter = Counter(status for _, status in recheck_results)
    SOLD_STATUSES = ("sold", "reserved", "gone")
    for listing_id, status in recheck_results:
        if status in SOLD_STATUSES:
            mark_sold(conn, listing_id)
    mark_checked(conn, [lid for lid, status in recheck_results if status == "active" or status in SOLD_STATUSES])
    sold_count = sum(counts[s] for s in SOLD_STATUSES)
    logger.info("  %d newly sold (%d sold, %d reserved, %d gone); %d unknown, %d blocked, %d skipped left active for next run",
                sold_count, counts["sold"], counts["reserved"], counts["gone"], counts["unknown"], counts["blocked"], counts["skipped"])

    # Search for new listings
    logger.info("Searching Vinted for new listings (max %d pages per query)", max_pages)
    items = search_all(max_pages=max_pages)
    logger.info("Found %d unique listings from search", len(items))

    new_count = upsert_listings_batch(conn, items)
    logger.info("New listings added: %d", new_count)
    log_run(conn, started_at, len(items), new_count, sold_count)

    mine = sync_my_listings(conn, mine_conn)
    logger.info("%s's shop: %d active, %d sold (%d newly), %d from Vinted, %d recorded by hand%s",
                MY_USERNAME, mine["active"], mine["sold"], mine["newly_sold"], mine["from_vinted"], mine["manual"],
                "" if mine["wardrobe_ok"] else " — wardrobe fetch failed")
    conn.close()
    mine_conn.close()

    reason = _alert_reason(len(active), counts)
    status = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "active_checked": len(active),
        "active_total": total_active,
        "sold": sold_count,
        "unknown": counts["unknown"],
        "blocked": counts["blocked"],
        "skipped": counts["skipped"],
        "block_signals": BLOCK_MONITOR.total,
        "breaker_tripped": BLOCK_MONITOR.tripped(),
        "found": len(items),
        "new": new_count,
        "alert": reason is not None,
        "alert_reason": reason,
    }
    STATUS_PATH.parent.mkdir(exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2))
    if reason:
        logger.error("ALERT: Vinted appears to be blocking us — %s", reason)
    logger.info("=== Scrape run complete ===")


if __name__ == "__main__":
    run()
