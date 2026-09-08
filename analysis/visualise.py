from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from scraper.store import get_conn, get_mine_conn

OUTPUT_PATH = "docs/index.html"
TEMPLATE_PATH = Path(__file__).with_name("dashboard.html")

MIN_SOLD_FOR_ANALYSIS = 10
UPLOADS_WINDOW_DAYS = 90

_SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL+", "Kids"]

# The dashboard's time-filter presets. Every view is pre-computed here so the
# page embeds ~7 small aggregate blocks instead of one record per listing —
# its size no longer grows with the database.
RANGES = [
    ("all", "All", None), ("90", "3 months", 90), ("30", "1 month", 30), ("14", "2 weeks", 14),
    ("7", "1 week", 7), ("3", "3 days", 3), ("1", "1 day", 1),
]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    conn = get_conn()
    all_df = pd.read_sql_query("SELECT * FROM listings", conn.raw)
    runs_df = pd.read_sql_query("SELECT * FROM scrape_runs ORDER BY started_at", conn.raw)
    conn.close()

    mine_conn = get_mine_conn()
    try:
        mine_df = pd.read_sql_query("SELECT * FROM my_listings", mine_conn.raw)
    except Exception:
        mine_df = pd.DataFrame()
    mine_conn.close()
    return all_df, runs_df, mine_df


def _num(v, digits: int | None = None):
    """Native float/int for JSON; NaN/None -> None."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or pd.isna(v):
        return None
    v = float(v)
    if digits is not None:
        v = round(v, digits)
    return int(v) if v.is_integer() else v


def _str(v) -> str | None:
    return v if isinstance(v, str) and v else None


def _norm_size(raw) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    upper = raw.upper()
    if "YEAR" in upper or " CM" in upper:
        return "Kids"
    token = upper.split("/")[0].strip()
    if token in ("XXS", "XS"):
        return "XS"
    if token in ("S", "M", "L", "XL", "XXL"):
        return token
    if token in ("XXXL", "3XL", "4XL", "5XL", "XXXXL", "XXXXXL"):
        return "3XL+"
    return None


def _prepare(all_df: pd.DataFrame) -> pd.DataFrame:
    df = all_df.copy()
    df["_u"] = pd.to_datetime(df["upload_timestamp"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d")
    df["_z"] = df["size"].map(_norm_size)
    df["has_player_name"] = df["has_player_name"].fillna(0).astype(int)
    df["sold"] = df["sold"].fillna(0).astype(int)
    if "bumped" not in df.columns:
        df["bumped"] = 0
    df["bumped"] = df["bumped"].fillna(0).astype(int)
    return df


# ---------------------------------------------------------------------------
# One view = every aggregate the page shows, for one time range
# ---------------------------------------------------------------------------

def _top_counts(series: pd.Series, n: int) -> list[dict]:
    counts = series.dropna().value_counts()
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{"label": str(k), "value": int(v)} for k, v in ordered]


def _uploads_per_day(df: pd.DataFrame, cutoff: str | None) -> list[dict]:
    dates = df["_u"].dropna().sort_values()
    if dates.empty:
        return []
    end = dates.iloc[-1]
    start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=UPLOADS_WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
    if cutoff and cutoff > start:
        start = cutoff
    if dates.iloc[0] > start:
        start = dates.iloc[0]
    per_day = dates.value_counts()
    rows, day = [], datetime.strptime(start, "%Y-%m-%d")
    while day.strftime("%Y-%m-%d") <= end:
        key = day.strftime("%Y-%m-%d")
        rows.append({"date": key, "value": int(per_day.get(key, 0))})
        day += timedelta(days=1)
    return rows


def _sold_section(df: pd.DataFrame) -> dict:
    sold = df[df["sold"] == 1]
    out: dict = {"available": len(sold) >= MIN_SOLD_FOR_ANALYSIS, "count": int(len(sold)), "min_required": MIN_SOLD_FOR_ANALYSIS}
    if not out["available"]:
        return out

    speed = sold[sold["team"].notna() & sold["time_to_sell_days"].notna()].groupby("team")["time_to_sell_days"].agg(["mean", "count"])
    speed = speed[speed["count"] >= 5].sort_values("mean")
    out["sellSpeed"] = [{"label": str(t), "value": _num(r["mean"], 1), "n": int(r["count"])} for t, r in speed.iterrows()]

    named, blank = sold[sold["has_player_name"] == 1], sold[sold["has_player_name"] == 0]
    out["namedSoldCount"], out["blankSoldCount"] = int(len(named)), int(len(blank))
    out["namedVsBlank"] = [
        {"metric": "Avg asking price", "format": "gbp", "named": _num(named["asking_price"].mean(), 2), "blank": _num(blank["asking_price"].mean(), 2)},
        {"metric": "Avg days to sell", "format": "days", "named": _num(named["time_to_sell_days"].mean(), 1), "blank": _num(blank["time_to_sell_days"].mean(), 1)},
        {"metric": "Avg favourites", "format": "num1", "named": _num(named["favourites"].mean(), 2), "blank": _num(blank["favourites"].mean(), 2)},
    ]

    # Paid bump vs not: does £1.45 for 3 days at the top of search actually
    # move the shirt? Sell rate is over every listing in the group; the other
    # metrics are over the ones that sold.
    bumped, organic = df[df["bumped"] == 1], df[df["bumped"] == 0]
    b_sold, o_sold = bumped[bumped["sold"] == 1], organic[organic["sold"] == 1]
    out["bumpedCounts"] = {"bumped": int(len(bumped)), "organic": int(len(organic)),
                           "bumpedSold": int(len(b_sold)), "organicSold": int(len(o_sold))}
    out["bumpedVsOrganic"] = [
        {"metric": "Sell rate", "format": "pctAuto",
         "bumped": _num(100 * len(b_sold) / len(bumped), 1) if len(bumped) else None,
         "organic": _num(100 * len(o_sold) / len(organic), 1) if len(organic) else None},
        {"metric": "Avg days to sell", "format": "days", "bumped": _num(b_sold["time_to_sell_days"].mean(), 1), "organic": _num(o_sold["time_to_sell_days"].mean(), 1)},
        {"metric": "Avg asking price", "format": "gbp", "bumped": _num(b_sold["asking_price"].mean(), 2), "organic": _num(o_sold["asking_price"].mean(), 2)},
        {"metric": "Avg favourites", "format": "num1", "bumped": _num(b_sold["favourites"].mean(), 2), "organic": _num(o_sold["favourites"].mean(), 2)},
    ] if len(bumped) and len(organic) else []

    bands = []
    for lo, hi in [(0, 10), (10, 20), (20, 30), (30, 50), (50, 10_000)]:
        seen = df[(df["asking_price"] >= lo) & (df["asking_price"] < hi)]
        if seen.empty:
            continue
        sd = seen[seen["sold"] == 1]
        bands.append({"label": f"£{lo}–{hi}" if hi < 10_000 else f"£{lo}+",
                      "sellRate": _num(100 * len(sd) / len(seen), 1), "avgDays": _num(sd["time_to_sell_days"].mean(), 1),
                      "total": int(len(seen)), "sold": int(len(sd))})
    out["priceBands"] = bands
    return out


def _view(df: pd.DataFrame, cutoff: str | None) -> dict:
    sub = df if cutoff is None else df[df["_u"].notna() & (df["_u"] >= cutoff)]
    total = int(len(sub))
    sold = int(sub["sold"].sum())
    named = int(sub["has_player_name"].sum())
    teamed = int(sub["team"].notna().sum())
    prices = sub["asking_price"].dropna()

    bins = [{"label": f"£{lo}–{lo + 5}", "value": int(((prices >= lo) & (prices < lo + 5)).sum())} for lo in range(0, 60, 5)]
    bins.append({"label": "£60+", "value": int((prices >= 60).sum())})

    team_counts = sub["team"].value_counts()
    top_teams = sorted(team_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    spread = []
    for team, _ in top_teams[:10]:
        p = sub.loc[sub["team"] == team, "asking_price"].dropna()
        if p.empty:
            continue
        q = p.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        spread.append({"label": str(team), "n": int(len(p)), "p10": _num(q[0.1], 2), "p25": _num(q[0.25], 2),
                       "median": _num(q[0.5], 2), "p75": _num(q[0.75], 2), "p90": _num(q[0.9], 2)})
    spread.sort(key=lambda r: r["median"], reverse=True)

    faves = []
    for team, _ in top_teams[:12]:
        f = sub.loc[sub["team"] == team, "favourites"].fillna(0)
        faves.append({"label": str(team), "value": _num(f.mean(), 2), "n": int(len(f))})
    faves.sort(key=lambda r: r["value"], reverse=True)

    size_counts = sub["_z"].value_counts()
    sizes = [{"label": s, "value": int(size_counts[s])} for s in _SIZE_ORDER if size_counts.get(s, 0)]

    return {
        "cutoff": cutoff,
        "kpis": {"total": total, "sold": sold, "active": total - sold,
                 "teamPct": round(100 * teamed / total) if total else 0, "teamed": teamed,
                 "namedPct": round(100 * named / total) if total else 0, "named": named, "blank": total - named,
                 "bumped": int(sub["bumped"].sum()), "bumpedPct": round(100 * int(sub["bumped"].sum()) / total) if total else 0,
                 "medianPrice": _num(prices.median(), 2), "avgFaves": _num(sub["favourites"].mean(), 2)},
        "teams": _top_counts(sub["team"], 20), "players": _top_counts(sub["player_name"], 15),
        "prices": bins, "priceSpread": spread, "favesByTeam": faves, "sizes": sizes,
        "uploads": _uploads_per_day(sub, cutoff), "sold": _sold_section(sub),
    }


# ---------------------------------------------------------------------------
# My shop
# ---------------------------------------------------------------------------

def _my_rows(mine_df: pd.DataFrame, all_df: pd.DataFrame) -> list[dict]:
    if mine_df.empty:
        return []
    sold_df = all_df[all_df["sold"] == 1]
    # Vinted rows carry full ISO timestamps, hand-recorded sales just dates;
    # without format="mixed" pandas infers one format from the first row and
    # coerces the other kind to NaT.
    listed = pd.to_datetime(mine_df["listed_at"], utc=True, errors="coerce", format="mixed")
    sold_at = pd.to_datetime(mine_df["sold_at"], utc=True, errors="coerce", format="mixed")
    days = (sold_at - listed).dt.total_seconds() / 86400
    rows = []
    for i, r in mine_df.iterrows():
        team = _str(r["team"])
        market = all_df[all_df["team"] == team]["asking_price"].dropna() if team else pd.Series(dtype=float)
        market_days = sold_df[sold_df["team"] == team]["time_to_sell_days"].dropna() if team else pd.Series(dtype=float)
        rows.append({
            "id": r["listing_id"], "source": r["source"], "url": _str(r["url"]),
            "title": r["title"], "team": team, "playerName": _str(r["player_name"]),
            "shirtNumber": _str(r["shirt_number"]), "size": _str(r["size"]),
            "listedAt": listed[i].strftime("%Y-%m-%d") if pd.notna(listed[i]) else None,
            "soldAt": sold_at[i].strftime("%Y-%m-%d") if pd.notna(sold_at[i]) else None,
            "askingPrice": _num(r["asking_price"], 2), "soldPrice": _num(r["sold_price"], 2),
            "sold": int(r["sold"]), "favourites": _num(r["favourites"]), "viewCount": _num(r["view_count"]),
            "bumped": int(r["bumped"] or 0) if "bumped" in mine_df.columns else 0,
            "daysToSell": _num(days[i], 1),
            "marketMedian": _num(market.median(), 2) if len(market) else None,
            "marketCount": int(len(market)),
            "marketDaysToSell": _num(market_days.mean(), 1) if len(market_days) >= 3 else None,
        })
    rows.sort(key=lambda x: (x["sold"], x["soldAt"] or x["listedAt"] or ""))
    return rows


def _my_view(rows: list[dict], cutoff: str | None) -> dict:
    keep = [r for r in rows if cutoff is None or (r["listedAt"] and r["listedAt"] >= cutoff) or (r["soldAt"] and r["soldAt"] >= cutoff)]
    sold = [r for r in keep if r["sold"]]
    prices = [r["soldPrice"] for r in sold if r["soldPrice"] is not None]
    dts = [r["daysToSell"] for r in sold if r["daysToSell"] is not None]
    return {"rows": keep, "sold": len(sold), "active": len(keep) - len(sold),
            "revenue": _num(sum(prices), 2) if prices else None,
            "avgDaysToSell": _num(sum(dts) / len(dts), 1) if dts else None}


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

def prep(all_df: pd.DataFrame, runs_df: pd.DataFrame, mine_df: pd.DataFrame | None = None) -> dict:
    from scraper.mine import MY_USERNAME, MY_PROFILE_URL

    now = datetime.now(timezone.utc)
    df = _prepare(all_df)

    first_seen = pd.to_datetime(all_df["first_seen_at"], utc=True, errors="coerce").min()
    days_tracking = int((now - first_seen).days) + 1 if pd.notna(first_seen) else 0

    updated = runs_df["finished_at"].max() if not runs_df.empty else None
    updated_dt = pd.to_datetime(updated, utc=True, errors="coerce") if updated else None
    anchor = updated_dt if updated_dt is not None and pd.notna(updated_dt) else now

    season_start = now.year if now.month >= 7 else now.year - 1
    my_rows = _my_rows(mine_df if mine_df is not None else pd.DataFrame(), all_df)

    views = {}
    for key, label, days in RANGES:
        cutoff = (anchor - timedelta(days=days)).strftime("%Y-%m-%d") if days else None
        view = _view(df, cutoff)
        view["label"] = label
        view["mine"] = _my_view(my_rows, cutoff)
        views[key] = view

    return {
        "meta": {
            "updated": anchor.strftime("%-d %b %Y, %H:%M UTC") if updated_dt is not None and pd.notna(updated_dt) else "not yet run",
            "season": f"{season_start}/{str(season_start + 1)[-2:]}",
            "daysTracking": days_tracking,
            "ranges": [{"key": k, "label": l} for k, l, _ in RANGES],
        },
        "views": views,
        "mine": {"username": MY_USERNAME, "profileUrl": MY_PROFILE_URL, "rows": my_rows},
    }


def build_html(data: dict) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    # "</" inside the JSON would terminate the <script> block early
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    total = data["views"]["all"]["kpis"]["total"]
    return (
        template
        .replace("__DATA__", payload)
        # the count-up reads data-count with parseFloat, which stops at a comma
        .replace("__TOTAL_RAW__", str(total))
        .replace("__TOTAL__", f"{total:,}")
        .replace("__UPDATED__", data["meta"]["updated"])
        .replace("__SEASON__", data["meta"]["season"])
    )


def generate(output_path: str = OUTPUT_PATH) -> None:
    print("Loading data from Turso...")
    all_df, runs_df, mine_df = load()
    print(f"  {len(all_df)} listings, {int(all_df['sold'].fillna(0).sum())} sold, {len(mine_df)} of my own")

    data = prep(all_df, runs_df, mine_df)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(build_html(data), encoding="utf-8")
    print(f"Dashboard written to {output_path} ({Path(output_path).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else OUTPUT_PATH
    generate(output_path=out)
