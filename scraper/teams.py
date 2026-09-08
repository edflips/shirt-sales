TEAMS = {
    # Premier League 2024/25
    "Arsenal":              ["arsenal", "afc arsenal", "the gunners"],
    "Aston Villa":          ["aston villa", "villa", "avfc"],
    "Bournemouth":          ["bournemouth", "afc bournemouth", "cherries"],
    "Brentford":            ["brentford", "bees"],
    "Brighton":             ["brighton", "brighton & hove", "brighton and hove", "seagulls", "bhafc"],
    "Chelsea":              ["chelsea", "cfc", "the blues"],
    "Crystal Palace":       ["crystal palace", "palace", "cpfc", "eagles"],
    "Everton":              ["everton", "efc", "toffees"],
    "Fulham":               ["fulham", "ffc"],
    "Ipswich":              ["ipswich", "ipswich town", "itfc"],
    "Leicester":            ["leicester", "leicester city", "lcfc", "foxes"],
    "Liverpool":            ["liverpool", "lfc", "the reds"],
    "Manchester City":      ["manchester city", "man city", "mcfc"],
    "Manchester United":    ["manchester united", "man utd", "man united", "mufc"],
    "Newcastle":            ["newcastle", "newcastle united", "nufc", "magpies", "toon"],
    "Nottingham Forest":    ["nottingham forest", "nffc", "forest"],
    "Southampton":          ["southampton", "saints", "stfc"],
    "Tottenham":            ["tottenham", "spurs", "thfc", "tottenham hotspur"],
    "West Ham":             ["west ham", "west ham united", "whufc", "hammers"],
    "Wolves":               ["wolves", "wolverhampton", "wolverhampton wanderers", "wwfc"],
    # Championship
    "Leeds":                ["leeds", "leeds united", "lufc"],
    "Sunderland":           ["sunderland", "safc", "black cats"],
    "Sheffield United":     ["sheffield united", "sufc", "blades"],
    "Burnley":              ["burnley", "bfc", "clarets"],
    "Derby":                ["derby", "derby county", "dcfc", "rams"],
    "Middlesbrough":        ["middlesbrough", "boro", "mfc"],
    "Coventry":             ["coventry", "coventry city", "ccfc", "sky blues"],
    "Watford":              ["watford", "wfc", "hornets"],
    "QPR":                  ["qpr", "queens park rangers"],
    "Stoke":                ["stoke", "stoke city", "potters"],
    "Millwall":             ["millwall", "the den"],
    "Preston":              ["preston", "preston north end", "pne"],
    "Luton":                ["luton", "luton town", "ltfc", "hatters"],
    "Cardiff":              ["cardiff", "cardiff city"],
    "Swansea":              ["swansea", "swansea city", "swans"],
    "Norwich":              ["norwich", "norwich city", "ncfc", "canaries"],
    "Bristol City":         ["bristol city"],
    "Blackburn":            ["blackburn", "blackburn rovers", "brfc"],
    "Hull":                 ["hull", "hull city", "tigers"],
    "Plymouth":             ["plymouth", "plymouth argyle", "pafc", "pilgrims"],
    "Sheffield Wednesday":  ["sheffield wednesday", "swfc", "owls"],
    "West Brom":            ["west brom", "west bromwich", "wba", "baggies"],
    "Birmingham":           ["birmingham", "birmingham city", "bcfc", "blues"],
    "Blackpool":            ["blackpool", "tangerines"],
    # Major global clubs — not a priority per 01-overview.md, but common enough
    # on Vinted UK that leaving them out lets their name leak into player-name
    # detection (e.g. "Real Madrid Mbappe" without team-stripping first)
    "Real Madrid":          ["real madrid"],
    "Barcelona":            ["barcelona", "fc barcelona"],
    "Bayern Munich":        ["bayern munich", "bayern", "fc bayern"],
    "Paris Saint-Germain":  ["paris saint-germain", "psg", "paris saint germain"],
    "Juventus":             ["juventus", "juve"],
    "AC Milan":             ["ac milan"],
    "Inter Milan":          ["inter milan", "internazionale"],
    "Boca Juniors":         ["boca juniors"],
    "River Plate":          ["river plate"],
    "Ajax":                 ["ajax"],
    # National teams
    "England":              ["england", "three lions", "eng"],
    "Scotland":             ["scotland", "sco"],
    "Wales":                ["wales", "cymru", "wal"],
    "Republic of Ireland":  ["republic of ireland", "ireland", "roi"],
    "Northern Ireland":     ["northern ireland"],
    "Germany":              ["germany", "deutschland", "dfb", "ger"],
    "France":               ["france", "fra", "les bleus"],
    "Spain":                ["spain", "espana", "esp", "la roja"],
    "Brazil":               ["brazil", "brasil", "cbf", "bra"],
    "Argentina":            ["argentina", "arg", "albiceleste"],
    "Italy":                ["italy", "italia", "azzurri", "ita"],
    "Portugal":             ["portugal", "por"],
    "Netherlands":          ["netherlands", "holland", "dutch", "ned"],
    "Belgium":              ["belgium", "bel", "red devils"],
    "Croatia":              ["croatia", "cro"],
    "USA":                  ["usa", "usmnt", "uswnt", "united states"],
    "Japan":                ["japan", "jpn"],
    "South Korea":          ["south korea", "korea"],
    "Mexico":               ["mexico", "mex"],
    "Colombia":             ["colombia", "col"],
    "Uruguay":              ["uruguay", "uru"],
    "Senegal":              ["senegal", "sen"],
    "Morocco":              ["morocco", "mar"],
    "Nigeria":              ["nigeria", "nga", "super eagles"],
    "Ghana":                ["ghana", "black stars"],
    "Turkey":               ["turkey", "turkiye", "tur"],
    "Denmark":              ["denmark", "den"],
    "Sweden":               ["sweden", "swe"],
    "Poland":               ["poland", "pol"],
    "Czech Republic":       ["czech republic", "czechia", "cze"],
    "Austria":              ["austria", "aut"],
    "Switzerland":          ["switzerland", "sui", "swi"],
}

# How each team is searched for on Vinted. Default is the TEAMS key; override
# where UK sellers habitually write something shorter or different. Each name
# is combined with SEARCH_SUFFIXES ("shirt", "kit", "top" — UK usage; "jersey"
# and "soccer" mostly surface American listings and were dropped).
SEARCH_NAMES = {
    "Manchester United": ["Manchester United", "Man Utd"],
    "Manchester City": ["Man City", "Manchester City"],
    "Tottenham": ["Tottenham", "Spurs"],
    "Nottingham Forest": ["Nottingham Forest"],
    "Sheffield United": ["Sheffield United", "Sheff Utd"],
    "Sheffield Wednesday": ["Sheffield Wednesday", "Sheff Wed"],
    "West Brom": ["West Brom"],
    "Paris Saint-Germain": ["PSG", "Paris Saint-Germain"],
    "Bayern Munich": ["Bayern"],
    "Inter Milan": ["Inter Milan"],
    "AC Milan": ["AC Milan"],
    "Republic of Ireland": ["Ireland"],
    "USA": ["USA"],
    "South Korea": ["South Korea", "Korea"],
    "Czech Republic": ["Czech"],
}
SEARCH_SUFFIXES = ["shirt", "kit", "top"]

BLANK_SIGNALS = [
    "blank", "no name", "plain", "unprinted", "no print",
    "no number", "no badge", "nameless", "unpersonalised",
]

# Listings that match the search terms but aren't football (soccer) replica
# shirts at all — other sports, leisurewear, or non-shirt merch. Filtered out
# entirely before parsing/storage; see fetch.search_all().
OFF_TOPIC_SIGNALS = [
    "nfl", "nba", "mlb", "ncaa", "cricket", "rugby", "netball", "hockey",
    "basketball", "baseball", "american football", "f1 ", "formula 1",
    "esports", "polo", "hoodie", "tank top", "sweatshirt", "1/4 zip",
    "1/4-zip", "quarter zip", "training top", "tracksuit", "compression",
    # Football-adjacent but not a shirt
    "boots", "trading card", "panini", "adrenalyn", "top trumps",
    "referee", "base layer", "pyjamas", "pjamas", "pajamas",
    # Novelty/branded merch, not a real club listing
    "peanuts", "snoopy",
    # US gridiron/college teams that slip past the generic signals above
    # (avoid any name also used as a soccer-club nickname, e.g. "saints"/
    # "eagles" in TEAMS — those must stay off this list)
    "49ers", "jets", "giants", "chiefs", "cowboys", "packers", "steelers",
    "patriots", "dolphins", "broncos", "chargers", "raiders", "buccaneers",
    "seahawks", "cardinals", "colts", "titans", "texans", "browns",
    "bengals", "ravens", "commanders", "vikings", "bears", "buckeyes",
    "jaguars", "horned frogs", "cyclones", "penn state", "nittany",
    "brooklyn nets", "lakers", "celtics", "warriors", "knicks", "bulls",
]

# Word-bounded off-topic patterns (regex), for terms that are substrings of
# innocent words: a merchandise t-shirt / tee is not a replica football shirt,
# but "tee" sits inside "steel" and "t shirt" inside "shirt".
OFF_TOPIC_PATTERNS = [
    r"\bt[- ]?shirts?\b", r"\btees?\b", r"\bvests?\b", r"\bpolos?\b",
    r"\bsocks\b", r"\bscarf\b", r"\bhats?\b", r"\bcaps?\b", r"\bbeanie\b",
]
# Shorts on their own are off-topic; "shirt and shorts" / a full kit is fine.
SHORTS_ONLY_PATTERN = (r"\bshorts\b", r"\b(shirts?|kit|top)\b")

# A title with any of these is an old shirt however it's phrased, so it is
# out of the current-season scope even when no season is written.
RETRO_SIGNALS = ["retro", "vintage", "reissue", "re-issue", "throwback", "remake", "bringback"]

SIZE_WORDS = {
    "xs", "s", "m", "l", "xl", "xxl", "xxxl", "xxxxl", "xxxxxl",
    "2xl", "3xl", "4xl", "5xl", "xsmall", "small", "medium", "large",
    "extra", "junior", "youth", "kids", "boys", "girls", "adult", "age",
    "mens", "womens", "ladies", "men", "man", "women",
}

CONDITION_WORDS = {
    "new", "used", "good", "excellent", "fair", "satisfactory",
    "worn", "tagged", "tags",
}

# Brand/product-line and generic marketing words that look like proper nouns
# in Vinted's Title Case titles but are never a player's surname.
BRAND_WORDS = {
    "adidas", "nike", "puma", "umbro", "castore", "kappa", "hummel",
    "joma", "macron", "errea", "capelli", "score", "draw",
}

NOISE_WORDS = {
    "age", "years", "year", "number", "team", "world", "cup", "ultimate",
    "total", "tiro", "tabela", "aeroready", "vaporknit", "competition",
    "original", "official", "unofficial", "zip", "training", "rush",
    "uptown", "energy", "size", "vneck", "nwt", "bnwt", "bnwot", "msrp",
}

SHIRT_WORDS = {
    "shirt", "shirts", "top", "tops", "jersey", "jerseys", "kit", "kits",
    "strip", "football", "soccer", "tee", "fan",
    "home", "away", "third", "fourth", "goalkeeper", "gk",
    "authentic", "replica", "retro", "vintage", "classic",
    "match", "worn", "signed", "season",
}
