# Parsing Listing Titles

This describes the actual implementation in `scraper/parse.py` and `scraper/teams.py` — not a plan, the real regexes.

## Goal

Extract structured fields from free-text listing titles like:

> "Manchester United Rooney 10 home shirt 2011/12 XL"
> "Arsenal blank away top season 23/24 medium"
> "Liverpool FC Salah #11 adult medium football shirt"

## Team detection

`scraper.teams.TEAMS` is a dict of `{team_name: [aliases...]}` covering the Premier League, Championship, a handful of major global clubs (Real Madrid, Barcelona, Bayern, PSG, Juventus, both Milans, Boca, River, Ajax), and a set of national teams (`scraper/teams.py`). `detect_team()` matches every alias as a whole word (`\b...\b`, case-insensitive) and, if more than one alias matches, picks the **longest** alias — this stops a short alias like "villa" from winning over a more specific one when both could apply.

The global clubs aren't a priority per `01-overview.md`; they're there because they're common on Vinted UK and, without an entry, their name leaks into player-name detection ("Real Madrid Mbappe 9" parsed as player "Madrid Mbappe" until team-stripping could remove "Real Madrid" first).

## Off-topic filtering

`is_off_topic(title)` returns `True` if the lowercased title contains any phrase in `OFF_TOPIC_SIGNALS` (`teams.py`): other sports ("nfl", "nba", "cricket", "rugby", …), specific US gridiron/college/NBA team names that don't carry those words ("49ers", "jets", "buckeyes", "lakers", …), leisurewear that isn't a shirt ("polo", "hoodie", "1/4 zip", "training top", …), football-adjacent non-shirts ("boots", "trading card", "panini", "referee", …) and novelty merch ("snoopy") — or matches any regex in `OFF_TOPIC_PATTERNS`, which are word-bounded for terms that hide inside innocent words: **t-shirts and tees** (merchandise, not replica shirts — "tee" is inside "steel"), vests, shorts on their own, socks, scarves, hats. Searching by team name made this matter: "Arsenal shirt" returns every Arsenal *t-shirt* too, and they were ~a quarter of the results. It's checked in `fetch.search_all()` before a listing is parsed or stored, so these never reach the database. On the first scoped scrape it removed ~28% of what the search terms returned.

Two things to keep in mind when extending it: it's substring matching, so short signals need care ("f1 " has a trailing space for that reason); and a signal must never collide with a team alias in `TEAMS` — "saints" (Southampton) and "eagles" (Crystal Palace) are NFL names that are deliberately *not* on the list.

## Blank-shirt detection

`BLANK_SIGNALS` (in `teams.py`) is a list of phrases — "blank", "no name", "plain", "no print", etc. If any appears in the (lowercased) title, the listing is treated as blank and player/number detection is skipped entirely (`has_player_name`/`has_number` are `False`, `player_name`/`shirt_number` are `None`).

## Season detection

```python
_SEASON_RE = re.compile(r'\b(\d{2,4})[/\-](\d{1,4})\b')
```

Matches "2026/2027", "2026/27", "26/27", "2026-27", and "2026/7" (a single trailing digit). Each year is expanded to 4 digits (`_expand_year`): a 4-digit group is used as-is, a 2-digit group gets `2000 +`, and a 1-digit second group is placed in the first year's decade, rolling into the next decade if that would not be later than the first year (so "2026/7" → 2027, and "2029/0" → 2030).

**The match is only accepted if the second year equals the first year + 1.** This is what keeps the regex from matching unrelated number pairs in a title (a size range like "10/12", say) — those get rejected because 12 ≠ 10 + 1.

`current_season()` computes the season in progress from today's date (season runs roughly July–May: month ≥ 7 → `{year}/{year+1}`, else `{year-1}/{year}`).

`matches_current_season(title)` returns `True` unless the title marks the shirt as old. A title with no season at all is treated as current — sellers typically only state a season to flag an *old* shirt, so an absent season is the normal way of writing a current listing, not a sign it should be excluded. "Old" is any of three things: a season pair other than the current one ("23/24"); a retro word (`RETRO_SIGNALS`: retro, vintage, reissue, throwback, remake, bringback); or a **lone year** before the current season's start ("Arsenal 2004 home shirt", and "Retro 90/92" — 90/92 isn't a consecutive pair so the season regex ignores it, and the year check is what catches it). This function gates what gets stored at all (see `03-scraping-strategy.md`).

## Player name and number detection

Only runs on non-blank titles **whose team was recognised**. Once the pool grew to ~2,400 listings, the unrecognised-team titles were producing "Heavy Cotton", "Dri Fit" and "Graphic T-Shirt" as top player names — with no squad to check against, the stop-word heuristic alone can't keep up with apparel vocabulary at that volume. A shirt whose team can't be named isn't usable in the named-vs-blank analysis anyway, so it gets `player_name = shirt_number = None`.

For recognised teams, detection runs after three preprocessing steps to cut false positives:

1. `_strip_season(title)` — removes anything the season regex matched, so "2021/22" doesn't get read as a number/name pair.
2. `_strip_team(title, team)` — removes the detected team's own aliases, so "Liverpool Salah 11" doesn't parse "Liverpool" as a player name.
3. `_strip_number_labels(title)` — removes the literal words "number" / "no." / "no", so "Mbappe Number 9" is matched as "Mbappe 9". "number" is also a stop word (to reject noise), and without this step the whole "Mbappe Number" candidate would be thrown away instead of just the label.

Against the cleaned title, three regexes are tried in order, returning on the first match:

```python
_NAME_BEFORE_NUMBER = re.compile(r'\b([A-Z][a-zA-Z\'\-]{2,}(?:\s[A-Z][a-zA-Z\'\-]{2,})?)\s+#?(\d{1,2})\b')
_NAME_AFTER_NUMBER  = re.compile(r'#?(\d{1,2})\s+([A-Z][a-zA-Z\'\-]{2,}(?:\s[A-Z][a-zA-Z\'\-]{2,})?)\b')
_NUMBER_RE          = re.compile(r'(?<![/\-\d])(\d{1,2})(?![/\-\d])')
```

1. Name then number ("Salah 11")
2. Number then name ("11 Salah")
3. Number only, no plausible name nearby — returns `(None, shirt_number)`

A candidate name is one or two words. It's rejected if **any** of its words is in `_STOP_WORDS`, and a candidate number is rejected if it's not in `1..99`. `_STOP_WORDS` is the union of several lists in `teams.py`:

- `SIZE_WORDS` — xs/s/m/l/xl…, small/medium/large, kids/boys/girls/men/women, "age", "years"
- `CONDITION_WORDS` — new/used/worn/tags…
- `SHIRT_WORDS` — shirt(s), top(s), jersey(s), kit, home/away/third, football, soccer, tee, fan…
- `BRAND_WORDS` — adidas, nike, puma, umbro, castore, kappa, hummel, "score", "draw" (Score Draw, the retro brand)…
- `NOISE_WORDS` — marketing/product-line and acronym words that are Title Case in titles but never a surname: "number", "team", "world", "cup", "tiro", "tabela", "aeroready", "nwt", "bnwt", "msrp"…
- plus club suffixes ("fc", "united", "city", "rovers", "hotspur"…) and function words

The per-word check matters: an earlier version tested the whole phrase, so "Girls Size 10" got through as player "Girls Size" even though "girls" and "size" were both listed. Apostrophes are stripped before the check so "Women's" matches "womens". Every word is lowercased for comparison — Vinted titles are Title Case throughout, which is exactly why capitalization is such a weak "proper noun" signal here and why the squad cross-check below exists.

### Cross-checking against real squads

The stop-word list alone can't catch everything — Vinted titles are Title Case throughout, so almost any word "looks like" a proper noun to a capitalization-based regex. When the title's detected team has current-squad data available (`scraper.players.json`, built by `scraper/squads.py` — see below), a name candidate must match an actual player on that squad rather than merely avoid the stop-word list (`_matches_known_name` in `parse.py`). This is what catches most of what stop-words miss. It applies whenever the team is known and has squad data — every team in `TEAMS`, clubs and national teams alike, is mapped in `scraper.squads.WIKI_TITLES`; a team missing from that map would fall back to the stop-word-only heuristic, which at the current volume is noticeably noisy (national teams produced "Piece Set" and "Dirt Laundry" as players before they were added), so keep the two in step. When a candidate matches, the recorded name is the squad's surname form, so "Lionel Messi", "AFA Messi" and "Messi" all count as one player.

`scraper/squads.py` scrapes each team's "Current squad" (or "First-team squad") table on Wikipedia — no official API, same reasoning as scraping Vinted itself. Club tables are `No. | Pos. | Nation | Player`; national-team tables (the latest call-up list) are `No. | Pos. | Player | Date of birth | Caps | Goals | Club`, so the Player column is located from the header row rather than by position. It extracts each player's surname (keeping a leading particle like "de"/"van" attached, matching how names are actually printed on a shirt back — e.g. "VAN DIJK"), filters out captaincy annotation links ("(vice-captain)") and loan-parent-club links that sit in the same table cell ("Kevin Danso (on loan from Tottenham Hotspur)" — naively taking the last link in the cell grabs Tottenham, not Danso), and writes the result to `scraper/players.json`. Matching is accent-insensitive ("Mbappe" matches a squad entry of "Mbappé") and, for a two-word squad surname, also accepts just the trailing word ("Dijk" matches "van Dijk" — the regex requires a capital letter, so a lowercase-typed particle is never captured as part of the candidate).

Run manually with `python -m scraper.squads`; refreshed automatically by `.github/workflows/update-squads.yml` on the 1st of each month (squads change with transfers, so this needs to stay current — see `06-running.md`).

## Condition

`condition` is stored as whatever Vinted's `item.status` API field returns verbatim ("New with tags", "Very good", etc.) — there's no internal simplified scale. As of the scope-tightening pass, only "New with tags" listings are fetched at all (`fetch.NEW_WITH_TAGS_STATUS_ID`, see `03-scraping-strategy.md`), so in practice this column is currently constant across stored rows; it's kept for clarity and in case the scope is loosened later.

## Replica / authentic detection

```python
_REPLICA_SIGNALS = ["replica", "retro", "vintage", "classic", "throwback"]
_AUTHENTIC_SIGNALS = ["authentic", "match worn", "match-worn", "player issue", "player-issue"]
```

Simple substring checks against the lowercased title. **Not** included: "bnwt"/"bnwot" — those describe tag status (new-with-tags vs new-without-tags), which is a different concept from replica-vs-authentic and is instead handled by the condition filter. Early on these were conflated (a "BNWT" replica shirt would have been mis-flagged as "authentic"); that's fixed.

## Pipeline

Before parsing, in `fetch.search_all()`: `is_off_topic(title)` → drop; `matches_current_season(title)` → drop if it names another season. Then `parse_title`:

```
raw title
  → detect blank signal → if blank, return early (no player/number parsing)
  → detect_team()
  → detect_season()
  → detect_replica / detect_authentic signal checks
  → strip season, strip team name, strip "number"/"no." labels
  → detect_player() on the cleaned title, cross-checked against
    KNOWN_PLAYERS[team] when squad data exists for the team
  → return structured dict
```

Unmatched titles are stored with `team=NULL`; ~40% of the current (scoped) listings get a team match, so the team list may need extending over time based on what's showing up unmatched. Player-name detection is deliberately conservative: on the current data it yields ~10 names, of which the team-matched ones are all verified squad members and the remaining few (a Jamaica "Bob Marley" collab jersey, a *Riverdale*-themed tee) are titles where no regex would help.
