#!/usr/bin/env python3
"""
NWSL schedule updater
Fetches the current week's games from ESPN's API and rewrites the game-table
section of index.qmd. The FAQ and footer are left untouched.

Usage:
    python update_schedule.py

Requirements:
    pip install requests
"""

import sys
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

# ── CONFIGURE ────────────────────────────────────────────────────────────────

# How many days ahead to show (starting from today).
DAYS_AHEAD: int = 7

# Streaming links for each broadcast network ESPN reports.
# Add or update entries as broadcast deals change.
STREAM_LINKS: dict[str, str] = {
    "CBSSN":           "[CBS Sports Network](https://www.paramountplus.com/)",
    "CBS":             "[CBS](https://www.paramountplus.com/)",
    "Paramount+":      "[Paramount+](https://www.paramountplus.com/)",
    "Victory+":        "[Victory+](https://victoryplus.com/)",
    "ION":             "[ION](https://www.ionnwsl.com/)",
    "ESPN":            "[ESPN](https://plus.espn.com/)",
    "ESPN2":           "[ESPN2](https://plus.espn.com/), [ESPN app](https://plus.espn.com/)",
    "ESPNU":           "[ESPNU](https://plus.espn.com/)",
    "ESPN+":           "[ESPN+](https://plus.espn.com/)",
    "ESPN Deportes":   "[ESPN Deportes](https://plus.espn.com/)",
    "ABC":             "[ABC](https://plus.espn.com/)",
    "Prime Video":     "[Prime Video](http://www.amazon.com/nwsl)",
    "NWSL+":           "[NWSL+](https://www.nwslsoccer.com/plus)",
}

# Minutes added to the announced time per network to get the approximate
# actual kickoff (accounting for pregame coverage length).
NETWORK_BUFFERS: dict[str, int] = {
    "CBSSN":         0,
    "CBS":           0,
    "Paramount+":    0,
    "Victory+":      8,
    "ION":           4,
    "ESPN":          0,
    "ESPN2":         0,
    "ESPNU":         0,
    "ESPN+":         0,
    "ESPN Deportes": 0,
    "ABC":           0,
    "Prime Video":   10,
    "NWSL+":         0,
}

# Short team names to use in the tables.
# Key = ESPN's full displayName, Value = your preferred label.
# If a team name isn't listed here, the ESPN name is used as-is.
SHORT_NAMES: dict[str, str] = {
    "Angel City FC":        "Angel City",
    "Bay FC":               "Bay",
    "Chicago Red Stars":    "Chicago",
    "Houston Dash":         "Houston",
    "Kansas City Current":  "Kansas City",
    "North Carolina Courage": "North Carolina",
    "NJ/NY Gotham FC":      "NJ/NY",
    "Gotham FC":            "NJ/NY",
    "Orlando Pride":        "Orlando",
    "Portland Thorns FC":   "Portland",
    "Racing Louisville FC": "Louisville",
    "San Diego Wave FC":    "San Diego",
    "Seattle Reign FC":     "Seattle",
    "OL Reign":             "Seattle",
    "Utah Royals FC":       "Utah Royals",
    "Washington Spirit":    "Washington",
    # 2026 teams (names as returned by ESPN API):
    "Boston Legacy FC":     "Boston",
    "Denver Summit FC":     "Denver",
    "Chicago Stars FC":     "Chicago",
}

# ── END CONFIGURE ─────────────────────────────────────────────────────────────

ET = ZoneInfo("America/New_York")
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.nwsl/scoreboard"

APPROX_NOTE = (
    "*Kickoff times are approximate based on historical trends. "
    "On the national channels (i.e., ABC, CBS, etc.), there is always the risk "
    "of kickoff time shifting to accomodate previous programs running late."
)


def fetch_games(start: date, end: date) -> list[dict]:
    """Fetch NWSL events from ESPN API for a date range."""
    params = {
        "dates": f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}",
        "limit": 50,
    }
    resp = requests.get(ESPN_URL, params=params, timeout=10)
    resp.raise_for_status()
    events = resp.json().get("events", [])

    # If the date-range param returns nothing, fall back to ESPN's default window
    if not events:
        print("  Date-range query returned no results; falling back to ESPN default.")
        resp2 = requests.get(ESPN_URL, params={"limit": 50}, timeout=10)
        resp2.raise_for_status()
        events = resp2.json().get("events", [])

    return events


def parse_game(event: dict) -> dict | None:
    """Extract game info from an ESPN event. Returns None if the event is malformed."""
    try:
        comp = event["competitions"][0]
        home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
        away = next(c for c in comp["competitors"] if c["homeAway"] == "away")

        utc_dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        et_dt = utc_dt.astimezone(ET)

        # Collect broadcast networks in order, deduplicated
        networks: list[str] = []
        seen: set[str] = set()
        for geo in comp.get("geoBroadcasts", []):
            name = geo.get("media", {}).get("shortName", "").strip()
            if name and name not in seen:
                networks.append(name)
                seen.add(name)

        return {
            "home":     home["team"]["displayName"],
            "away":     away["team"]["displayName"],
            "et_dt":    et_dt,
            "networks": networks,
        }
    except (KeyError, StopIteration, ValueError):
        return None


def format_kickoff(et_dt: datetime, networks: list[str]) -> str:
    announced = et_dt.strftime("%-I:%M")  # e.g. "8:00" (no leading zero)
    period = et_dt.strftime("%p")         # "AM" or "PM"

    buffer = next((NETWORK_BUFFERS[n] for n in networks if n in NETWORK_BUFFERS), 0)

    if buffer:
        approx_dt = et_dt + timedelta(minutes=buffer)
        approx = approx_dt.strftime("%-I:%M")
        return f"{announced}/**{approx} {period} ET**"

    return f"**{announced} {period} ET**"


def format_stream(networks: list[str]) -> str:
    links = [STREAM_LINKS.get(n, n) for n in networks]
    return ", ".join(links) if links else "TBD"


def build_tables(games: list[dict]) -> str:
    """Group games by date and build Quarto-formatted markdown table blocks."""
    games = sorted(games, key=lambda g: g["et_dt"])

    by_date: dict[date, list[dict]] = {}
    for g in games:
        by_date.setdefault(g["et_dt"].date(), []).append(g)

    lines: list[str] = []
    current_month = ""

    for d in sorted(by_date):
        month = d.strftime("%B")
        if month != current_month:
            lines.append(f"## {month}")
            lines.append("")
            current_month = month

        lines.append(f"### {d.strftime('%A, %B %-d')}")
        lines.append("")
        lines.append("|**Home**|**Away**|**Announced/Approx. Kickoff Time**|**Stream**|")
        lines.append("|--------|---------|----------------------|---------|")

        for g in by_date[d]:
            home = SHORT_NAMES.get(g["home"], g["home"])
            away = SHORT_NAMES.get(g["away"], g["away"])
            kickoff = format_kickoff(g["et_dt"], g["networks"])
            stream = format_stream(g["networks"])
            lines.append(f"| {home} | {away} | {kickoff} | {stream} |")

        lines.append("")
        lines.append(': {tbl-colwidths="[66,66,80,80]"}')
        lines.append("")

    return "\n".join(lines)


def update_qmd(path: str, tables: str) -> None:
    """Replace only the game-table block in index.qmd; preserve FAQ and footer."""
    with open(path) as f:
        content = f.read()

    faq_marker = "## FAQs"
    faq_idx = content.find(faq_marker)
    if faq_idx == -1:
        sys.exit("ERROR: Could not find '## FAQs' in index.qmd — aborting.")

    # Everything up to end of YAML frontmatter
    fm_end = content.find("---", 3) + 3
    prefix = content[:fm_end].rstrip()

    # Everything from FAQs onward
    suffix = content[faq_idx:]

    new_content = (
        prefix
        + "\n\n"
        + tables.rstrip()
        + "\n\n"
        + APPROX_NOTE
        + "\n\n"
        + suffix
    )

    with open(path, "w") as f:
        f.write(new_content)


def main() -> None:
    today = datetime.now(ET).date()
    end = today + timedelta(days=DAYS_AHEAD)
    print(f"Fetching NWSL games {today} → {end}...")

    events = fetch_games(today, end)
    games = [g for e in events if (g := parse_game(e))]
    print(f"Found {len(games)} game(s)")

    if not games:
        print("No games found in window — index.qmd not modified.")
        return

    for g in games:
        nets = ", ".join(g["networks"]) or "TBD"
        print(f"  {g['et_dt'].strftime('%a %b %-d %-I:%M %p ET')}  "
              f"{g['away']} at {g['home']}  [{nets}]")

    tables = build_tables(games)
    update_qmd("index.qmd", tables)
    print("\nindex.qmd updated. Run `quarto render` to rebuild the site.")


if __name__ == "__main__":
    main()
