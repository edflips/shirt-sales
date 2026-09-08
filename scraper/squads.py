from __future__ import annotations

import json
import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "shirt-sales-project (personal research; contact ed@paperuncle.com)"}

PLAYERS_PATH = "scraper/players.json"

# Maps team keys from teams.TEAMS to their Wikipedia article title. National
# teams use the most recent call-up list, which Wikipedia also publishes as a
# "Current squad" table (with a different column layout — see fetch_squad).
# Any team missing here leaves player detection to the title heuristic alone,
# which is far noisier, so keep this in step with TEAMS.
WIKI_TITLES = {
    "Arsenal": "Arsenal F.C.",
    "Aston Villa": "Aston Villa F.C.",
    "Bournemouth": "AFC Bournemouth",
    "Brentford": "Brentford F.C.",
    "Brighton": "Brighton & Hove Albion F.C.",
    "Chelsea": "Chelsea F.C.",
    "Crystal Palace": "Crystal Palace F.C.",
    "Everton": "Everton F.C.",
    "Fulham": "Fulham F.C.",
    "Ipswich": "Ipswich Town F.C.",
    "Leicester": "Leicester City F.C.",
    "Liverpool": "Liverpool F.C.",
    "Manchester City": "Manchester City F.C.",
    "Manchester United": "Manchester United F.C.",
    "Newcastle": "Newcastle United F.C.",
    "Nottingham Forest": "Nottingham Forest F.C.",
    "Southampton": "Southampton F.C.",
    "Tottenham": "Tottenham Hotspur F.C.",
    "West Ham": "West Ham United F.C.",
    "Wolves": "Wolverhampton Wanderers F.C.",
    "Leeds": "Leeds United F.C.",
    "Sunderland": "Sunderland A.F.C.",
    "Sheffield United": "Sheffield United F.C.",
    "Burnley": "Burnley F.C.",
    "Derby": "Derby County F.C.",
    "Middlesbrough": "Middlesbrough F.C.",
    "Coventry": "Coventry City F.C.",
    "Watford": "Watford F.C.",
    "QPR": "Queens Park Rangers F.C.",
    "Stoke": "Stoke City F.C.",
    "Millwall": "Millwall F.C.",
    "Preston": "Preston North End F.C.",
    "Luton": "Luton Town F.C.",
    "Cardiff": "Cardiff City F.C.",
    "Swansea": "Swansea City F.C.",
    "Norwich": "Norwich City F.C.",
    "Bristol City": "Bristol City F.C.",
    "Blackburn": "Blackburn Rovers F.C.",
    "Hull": "Hull City F.C.",
    "Plymouth": "Plymouth Argyle F.C.",
    "Sheffield Wednesday": "Sheffield Wednesday F.C.",
    "West Brom": "West Bromwich Albion F.C.",
    "Birmingham": "Birmingham City F.C.",
    "Blackpool": "Blackpool F.C.",
    "Real Madrid": "Real Madrid CF",
    "Barcelona": "FC Barcelona",
    "Bayern Munich": "FC Bayern Munich",
    "Paris Saint-Germain": "Paris Saint-Germain F.C.",
    "Juventus": "Juventus FC",
    "AC Milan": "AC Milan",
    "Inter Milan": "Inter Milan",
    "Boca Juniors": "Boca Juniors",
    "River Plate": "Club Atlético River Plate",
    "Ajax": "AFC Ajax",
    "England": "England national football team",
    "Scotland": "Scotland national football team",
    "Wales": "Wales national football team",
    "Republic of Ireland": "Republic of Ireland national football team",
    "Northern Ireland": "Northern Ireland national football team",
    "Germany": "Germany national football team",
    "France": "France national football team",
    "Spain": "Spain national football team",
    "Brazil": "Brazil national football team",
    "Argentina": "Argentina national football team",
    "Italy": "Italy national football team",
    "Portugal": "Portugal national football team",
    "Netherlands": "Netherlands national football team",
    "Belgium": "Belgium national football team",
    "Croatia": "Croatia national football team",
    "USA": "United States men's national soccer team",
    "Japan": "Japan national football team",
    "South Korea": "South Korea national football team",
    "Mexico": "Mexico national football team",
    "Colombia": "Colombia national football team",
    "Uruguay": "Uruguay national football team",
    "Senegal": "Senegal national football team",
    "Morocco": "Morocco national football team",
    "Nigeria": "Nigeria national football team",
    "Ghana": "Ghana national football team",
    "Turkey": "Turkey national football team",
    "Denmark": "Denmark national football team",
    "Sweden": "Sweden national football team",
    "Poland": "Poland national football team",
    "Czech Republic": "Czech Republic national football team",
    "Austria": "Austria national football team",
    "Switzerland": "Switzerland national football team",
}

# Surname particles that stay attached to the following word, matching how
# these names are actually printed on the back of a shirt (e.g. "DE BRUYNE",
# "VAN DIJK") rather than just the final word.
_SURNAME_PARTICLES = {"de", "van", "von", "da", "dos", "das", "el", "al", "bin", "di", "le"}

# Annotation links that can appear inside the Player cell alongside (or
# instead of) the actual name — e.g. "Kevin Danso (on loan from Tottenham
# Hotspur)" links both the player AND the loan-parent club, and captaincy
# is often its own link ("(vice-captain)"). The real player name is always
# the first non-annotation link in the cell.
_ROLE_LABELS = {
    "captain", "vice-captain", "c", "vc", "on loan",
    "3rd captain", "4th captain", "5th captain", "interim captain",
}


def _surname(full_name: str) -> str:
    words = full_name.split()
    if len(words) <= 1:
        return full_name
    if words[-2].lower() in _SURNAME_PARTICLES:
        return f"{words[-2]} {words[-1]}"
    return words[-1]


def fetch_squad(wiki_title: str) -> list[str]:
    """Scrape surnames of the current first-team squad from a club's Wikipedia page."""
    url = f"https://en.wikipedia.org/wiki/{wiki_title.replace(' ', '_')}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    headings = soup.find_all(["h2", "h3", "h4"])
    # Prefer an exact "Current squad" / "First-team squad" heading: some pages
    # have an earlier, unrelated one ("Pre-match squad photo routine") that a
    # bare substring match would pick up first.
    heading = next(
        (h for h in headings if re.match(r"^(current|first[- ]team) squad", h.get_text(" ", strip=True).lower())),
        None,
    ) or next(
        (h for h in headings if "squad" in h.get_text(" ", strip=True).lower()),
        None,
    )
    if heading is None:
        logger.warning("No squad heading found for %s", wiki_title)
        return []

    table = None
    node = heading
    while node is not None:
        node = node.find_next()
        if node is not None and node.name == "table":
            table = node
            break
    if table is None:
        logger.warning("No squad table found for %s", wiki_title)
        return []

    # Club tables are No. | Pos. | Nation | Player; national-team tables are
    # No. | Pos. | Player | Date of birth | Caps | Goals | Club. Find the
    # Player column from the header row rather than assuming a position.
    names: list[str] = []
    player_idx: int | None = None
    for row in table.find_all("tr"):
        # direct children only: club pages nest two column-tables inside a
        # wrapper table, and a recursive search would flatten the inner cells
        # into the wrapper row
        cells = row.find_all(["th", "td"], recursive=False)
        texts = [c.get_text(" ", strip=True) for c in cells]
        if player_idx is None:
            for i, text in enumerate(texts):
                if text.lower().startswith("player"):
                    player_idx = i
                    break
            continue
        if len(cells) <= player_idx:
            continue
        squad_no = texts[0]
        # empty is allowed: national squads often have no numbers assigned yet
        if not (squad_no.isdigit() or squad_no in ("", "—")):
            continue  # a repeated header or section row, not a squad member
        links = [
            a for a in cells[player_idx].find_all("a")
            if a.get_text(strip=True).lower() not in _ROLE_LABELS
        ]
        if not links:
            continue
        full_name = links[0].get_text(strip=True)
        if full_name:
            names.append(_surname(full_name))
    return names


def build_all_squads() -> dict[str, list[str]]:
    squads: dict[str, list[str]] = {}
    for team, wiki_title in WIKI_TITLES.items():
        try:
            names = fetch_squad(wiki_title)
            if names:
                squads[team] = sorted(set(names))
                logger.info("%s: %d players", team, len(names))
            else:
                logger.warning("%s: no players found, skipping", team)
        except requests.RequestException as e:
            logger.warning("Failed to fetch squad for %s: %s", team, e)
        time.sleep(random.uniform(1, 3))
    return squads


def refresh(path: str = PLAYERS_PATH) -> None:
    squads = build_all_squads()
    with open(path, "w") as f:
        json.dump(squads, f, indent=2, sort_keys=True)
    total = sum(len(v) for v in squads.values())
    logger.info("Wrote %d players across %d teams to %s", total, len(squads), path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    refresh()
