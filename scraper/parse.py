from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from .teams import (
    TEAMS, BLANK_SIGNALS, OFF_TOPIC_SIGNALS, OFF_TOPIC_PATTERNS, SHORTS_ONLY_PATTERN,
    RETRO_SIGNALS, SIZE_WORDS, CONDITION_WORDS, SHIRT_WORDS, BRAND_WORDS, NOISE_WORDS,
)

_OFF_TOPIC_RE = re.compile("|".join(OFF_TOPIC_PATTERNS), re.IGNORECASE)
_SHORTS_RE = re.compile(SHORTS_ONLY_PATTERN[0], re.IGNORECASE)
_SHIRT_OR_KIT_RE = re.compile(SHORTS_ONLY_PATTERN[1], re.IGNORECASE)
# Any 4-digit year 1960-2029 in the title, including inside a range like
# "1913-2006" — anything before the current season's start year marks an
# old shirt. (A current season pair like "2026/27" only yields 2026.)
_YEAR_RE = re.compile(r"\b((?:19[6-9]|20[0-2])\d)\b")

_PLAYERS_PATH = os.path.join(os.path.dirname(__file__), "players.json")


def _fold_accents(s: str) -> str:
    """Lowercase and strip diacritics, so "Mbappé" matches a title typed as "Mbappe"."""
    decomposed = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _load_known_players() -> dict[str, set[str]]:
    """Per-team current-squad surnames, refreshed monthly by scraper.squads.

    Falls back to an empty dict (no cross-check) if the file is missing —
    e.g. before the squad workflow has ever run — so player detection still
    works via the stop-word heuristic alone, just with less precision.
    """
    try:
        with open(_PLAYERS_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {team: {_fold_accents(n) for n in names} for team, names in data.items()}


KNOWN_PLAYERS = _load_known_players()

# Second year is 1-4 digits to catch "2026/2027", "2026/27", "26/27", and "2026/7".
_SEASON_RE = re.compile(r'\b(\d{2,4})[/\-](\d{1,4})\b')
_NUMBER_RE = re.compile(r'(?<![/\-\d])(\d{1,2})(?![/\-\d])')
_NAME_BEFORE_NUMBER = re.compile(r'\b([A-Z][a-zA-Z\'\-]{2,}(?:\s[A-Z][a-zA-Z\'\-]{2,})?)\s+#?(\d{1,2})\b')
_NAME_AFTER_NUMBER = re.compile(r'#?(\d{1,2})\s+([A-Z][a-zA-Z\'\-]{2,}(?:\s[A-Z][a-zA-Z\'\-]{2,})?)\b')

_STOP_WORDS = SIZE_WORDS | CONDITION_WORDS | SHIRT_WORDS | BRAND_WORDS | NOISE_WORDS | {
    "fc", "afc", "cf", "sc", "utd", "united", "city", "town", "rovers",
    "wanderers", "albion", "athletic", "hotspur", "palace", "forest",
    "with", "without", "and", "the", "for", "not",
}

_REPLICA_SIGNALS = ["replica", "retro", "vintage", "classic", "throwback"]
# "bnwt"/"bnwot" describe tag status, not authenticity — that's tracked via the
# Vinted condition field instead (see fetch.NEW_WITH_TAGS_STATUS_ID), not here.
_AUTHENTIC_SIGNALS = ["authentic", "match worn", "match-worn", "player issue", "player-issue"]


def detect_team(title: str) -> str | None:
    lower = title.lower()
    # Longest alias first to avoid partial matches (e.g. "City" matching before "Manchester City")
    candidates = []
    for team, aliases in TEAMS.items():
        for alias in aliases:
            if re.search(r'\b' + re.escape(alias) + r'\b', lower):
                candidates.append((len(alias), team))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _expand_year(raw: str, century_base: int | None = None) -> int:
    if len(raw) == 4:
        return int(raw)
    if len(raw) == 2:
        return 2000 + int(raw)
    if len(raw) == 1 and century_base is not None:
        decade_base = century_base - (century_base % 10)
        year = decade_base + int(raw)
        return year if year > century_base else year + 10
    return int(raw)


def detect_season(title: str) -> str | None:
    """Find a season pair like '2026/2027', '2026/27', or '2026/7'.

    Requires the second year to be exactly one more than the first — this is
    what rejects noise matches (sizes, prices, etc.) that also look like N/M.
    """
    for match in _SEASON_RE.finditer(title):
        y1 = _expand_year(match.group(1))
        y2 = _expand_year(match.group(2), century_base=y1)
        if y2 == y1 + 1:
            return f"{y1}/{y2}"
    return None


def current_season() -> str:
    """The football season in progress right now (season runs roughly Jul-May)."""
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}/{start_year + 1}"


def matches_current_season(title: str) -> bool:
    """True unless the title marks the shirt as old.

    Sellers usually only state a season to flag an *old* shirt — current-season
    listings are typically posted with no year at all — so an absent season is
    treated as current, not excluded. "Old" is any of: a season pair other
    than the current one ("23/24"), a retro/vintage/reissue word, or a lone
    year before the current season's start ("Arsenal 2004 home shirt",
    "Retro 90/92" — the latter isn't a valid season pair, so the year check
    is what catches it).
    """
    current = current_season()
    season = detect_season(title)
    if season is not None and season != current:
        return False
    lower = title.lower()
    if any(word in lower for word in RETRO_SIGNALS):
        return False
    start_year = int(current[:4])
    return not any(int(y) < start_year for y in _YEAR_RE.findall(title))


def _is_stop_word(name: str) -> bool:
    """True if ANY word in the candidate name is a stop word.

    Candidate names can be one or two words (see _NAME_BEFORE_NUMBER /
    _NAME_AFTER_NUMBER below). Checking only the full phrase against
    _STOP_WORDS let two-word noise like "Girls Size" or "Boys Large" slip
    through even though each individual word was already listed.
    """
    return any(w.lower().replace("'", "") in _STOP_WORDS for w in name.split())


def is_off_topic(title: str) -> bool:
    """True if the title is clearly not a football (soccer) replica shirt."""
    lower = title.lower()
    if any(signal in lower for signal in OFF_TOPIC_SIGNALS) or _OFF_TOPIC_RE.search(title):
        return True
    return bool(_SHORTS_RE.search(title)) and not _SHIRT_OR_KIT_RE.search(title)


def _matched_known_name(name: str, known_names: set[str]) -> str | None:
    """The squad-list form of a candidate name, or None if it isn't a known surname.

    Handles a plain single-word surname ("Salah"), a regex candidate that
    also captured a first name ("Lamine Yamal" when the squad list only has
    "Yamal" — returns "Yamal", so "Lionel Messi", "Afa Messi" and "Messi"
    all collapse to one player), and accents typed without diacritics
    ("Mbappe" vs "Mbappé"). Also falls back to matching just the last word
    against a multi-word known surname's last word ("Dijk" against squad
    entry "van Dijk") — the regex requires a capital letter, so a lowercase
    particle like "van" typed mid-title is never captured as part of the
    candidate.
    """
    words = name.split()
    for i in range(len(words)):
        suffix = " ".join(words[i:])
        if _fold_accents(suffix) in known_names:
            return suffix.title()
    last_word = _fold_accents(words[-1])
    for kn in known_names:
        if " " in kn and last_word == kn.split()[-1]:
            return kn.title()
    return None


def detect_player(title: str, known_names: set[str] | None = None) -> tuple[str | None, str | None]:
    """Returns (player_name, shirt_number) or (None, None).

    known_names, when given, is the detected team's current squad (surnames,
    lowercased) from KNOWN_PLAYERS. A candidate must then match a real squad
    member rather than just avoid the stop-word list — this is what catches
    the false positives ("Girls Size", brand names, etc.) that a Title-Case
    title makes look like a proper name but the stop-word list doesn't cover.
    Falls back to the stop-word heuristic when no squad data is available
    for the team (e.g. a national team, or a club not yet in scraper.squads).
    """
    def _accept(name: str) -> str | None:
        """The name to record, or None to reject the candidate."""
        if known_names:
            return _matched_known_name(name, known_names)
        return None if _is_stop_word(name) else name.title()

    # Try NAME before NUMBER
    for match in _NAME_BEFORE_NUMBER.finditer(title):
        name, number = match.group(1), match.group(2)
        accepted = _accept(name)
        if accepted and int(number) <= 99:
            return accepted, number
    # Try NUMBER before NAME
    for match in _NAME_AFTER_NUMBER.finditer(title):
        number, name = match.group(1), match.group(2)
        accepted = _accept(name)
        if accepted and int(number) <= 99:
            return accepted, number
    # Number only (no name found)
    for match in _NUMBER_RE.finditer(title):
        n = int(match.group(1))
        if 1 <= n <= 99:
            return None, match.group(1)
    return None, None


def _strip_season(title: str) -> str:
    return _SEASON_RE.sub(" ", title)


_NUMBER_LABEL_RE = re.compile(r'\b(?:no\.?|number)\b', re.IGNORECASE)


def _strip_number_labels(title: str) -> str:
    """Remove the literal word "number"/"no."/"no" before player detection.

    Otherwise "Mbappe Number 9" is rejected outright, because "number" is
    also a stop word (there to reject "Girls Size 10" style noise) — but
    here it's just a label pointing at the shirt number, not the noise
    itself, so we drop the label and re-match against "Mbappe 9".
    """
    return _NUMBER_LABEL_RE.sub(" ", title)


def _strip_team(title: str, team: str | None) -> str:
    """Remove team name and its known aliases from title before player detection."""
    if not team or team not in TEAMS:
        return title
    result = title
    for alias in sorted(TEAMS[team], key=len, reverse=True):
        result = re.sub(r'\b' + re.escape(alias) + r'\b', " ", result, flags=re.IGNORECASE)
    return result


def parse_title(title: str) -> dict:
    lower = title.lower()

    is_blank = any(signal in lower for signal in BLANK_SIGNALS)
    team = detect_team(title)
    season = detect_season(title)

    is_replica = any(s in lower for s in _REPLICA_SIGNALS)
    is_authentic = any(s in lower for s in _AUTHENTIC_SIGNALS)

    if is_blank:
        return {
            "team": team,
            "has_player_name": False,
            "has_number": False,
            "player_name": None,
            "shirt_number": None,
            "season": season,
            "is_replica": is_replica,
            "is_authentic": is_authentic,
        }

    # No recognised team, no player claim: with no squad to check against and
    # a Title Case title, the stop-word heuristic alone produces "Heavy Cotton"
    # and "Dri Fit" as players at volume. A shirt whose team we can't name
    # isn't usable in the named-vs-blank analysis anyway.
    if team is None:
        player_name, shirt_number = None, None
    else:
        # Strip season and team words before player detection to avoid false matches
        clean = _strip_number_labels(_strip_team(_strip_season(title), team))
        player_name, shirt_number = detect_player(clean, known_names=KNOWN_PLAYERS.get(team))

    return {
        "team": team,
        "has_player_name": player_name is not None,
        "has_number": shirt_number is not None,
        "player_name": player_name,
        "shirt_number": shirt_number,
        "season": season,
        "is_replica": is_replica,
        "is_authentic": is_authentic,
    }
