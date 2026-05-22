#!/usr/bin/env python3
"""
NWSL schedule updater
- index.qmd: current week's games (next DAYS_AHEAD days), markdown tables
- schedule.qmd: full season, HTML tables with team filter

Usage: python update_schedule.py
Requirements: pip install requests
"""

import re
import sys
import requests
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo

# ── CONFIGURE ────────────────────────────────────────────────────────────────

# Window for index.qmd (current week)
DAYS_AHEAD: int = 7

# Full season date range for schedule.qmd
SEASON_START = date(2026, 3, 1)
SEASON_END   = date(2026, 11, 30)

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

SHORT_NAMES: dict[str, str] = {
    "Angel City FC":          "Angel City",
    "Bay FC":                 "Bay",
    "Boston Legacy FC":       "Boston",
    "Chicago Stars FC":       "Chicago",
    "Denver Summit FC":       "Denver",
    "Houston Dash":           "Houston",
    "Kansas City Current":    "Kansas City",
    "North Carolina Courage": "North Carolina",
    "NJ/NY Gotham FC":        "NJ/NY",
    "Gotham FC":              "NJ/NY",
    "Orlando Pride":          "Orlando",
    "Portland Thorns FC":     "Portland",
    "Racing Louisville FC":   "Louisville",
    "San Diego Wave FC":      "San Diego",
    "Seattle Reign FC":       "Seattle",
    "OL Reign":               "Seattle",
    "Utah Royals FC":         "Utah Royals",
    "Washington Spirit":      "Washington",
}

# ── END CONFIGURE ─────────────────────────────────────────────────────────────

ET = ZoneInfo("America/New_York")
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.nwsl/scoreboard"
MD_LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

TZ_TOGGLE_HTML = """\
<div class="mb-3 d-flex align-items-center gap-2">
  <span class="text-muted small fw-semibold me-1">Time zone:</span>
  <button class="btn btn-sm btn-primary tz-btn" onclick="setTZ('America/New_York', 'ET', this)">ET</button>
  <button class="btn btn-sm btn-outline-secondary tz-btn" onclick="setTZ('America/Chicago', 'CT', this)">CT</button>
  <button class="btn btn-sm btn-outline-secondary tz-btn" onclick="setTZ('America/Denver', 'MT', this)">MT</button>
  <button class="btn btn-sm btn-outline-secondary tz-btn" onclick="setTZ('America/Los_Angeles', 'PT', this)">PT</button>
  <button class="btn btn-sm btn-outline-secondary tz-btn" onclick="setTZ('GMT', 'GMT', this)">GMT</button>
</div>"""

APPROX_NOTE = (
    "*Kickoff times are approximate based on historical trends. "
    "On the national channels (i.e., ABC, CBS, etc.), there is always the risk "
    "of kickoff time shifting to accomodate previous programs running late."
)

# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch_games(start: date, end: date) -> list[dict]:
    params = {
        "dates": f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}",
        "limit": 300,
    }
    resp = requests.get(ESPN_URL, params=params, timeout=10)
    resp.raise_for_status()
    events = resp.json().get("events", [])
    if not events:
        resp2 = requests.get(ESPN_URL, params={"limit": 50}, timeout=10)
        resp2.raise_for_status()
        events = resp2.json().get("events", [])
    return events


def parse_game(event: dict) -> dict | None:
    try:
        comp = event["competitions"][0]
        home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
        away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
        utc_dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        et_dt = utc_dt.astimezone(ET)
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

# ── SHARED FORMATTING ─────────────────────────────────────────────────────────

def short_name(full: str) -> str:
    return SHORT_NAMES.get(full, full)


def _kickoff_parts(et_dt: datetime, networks: list[str]) -> tuple[str, str, str | None]:
    """Return (announced, period, approx_or_None)."""
    announced = et_dt.strftime("%-I:%M")
    period = et_dt.strftime("%p")
    buffer = next((NETWORK_BUFFERS[n] for n in networks if n in NETWORK_BUFFERS), 0)
    approx = (et_dt + timedelta(minutes=buffer)).strftime("%-I:%M") if buffer else None
    return announced, period, approx


def format_kickoff_md(et_dt: datetime, networks: list[str]) -> str:
    announced, period, approx = _kickoff_parts(et_dt, networks)
    if approx:
        return f"{announced}/**{approx} {period} ET**"
    return f"**{announced} {period} ET**"


def format_kickoff_html(et_dt: datetime, networks: list[str]) -> str:
    announced, period, approx = _kickoff_parts(et_dt, networks)
    if approx:
        return f"{announced}/<strong>{approx} {period} ET</strong>"
    return f"<strong>{announced} {period} ET</strong>"


def utc_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def time_cell_html(et_dt: datetime, networks: list[str]) -> str:
    """<td> with UTC data attributes for JS timezone conversion."""
    announced = utc_str(et_dt)
    buffer = next((NETWORK_BUFFERS[n] for n in networks if n in NETWORK_BUFFERS), 0)
    inner = format_kickoff_html(et_dt, networks)
    if buffer:
        approx = utc_str(et_dt + timedelta(minutes=buffer))
        return f'<td class="time-cell" data-announced="{announced}" data-approx="{approx}">{inner}</td>'
    return f'<td class="time-cell" data-announced="{announced}">{inner}</td>'


def format_stream_md(networks: list[str]) -> str:
    return ", ".join(STREAM_LINKS.get(n, n) for n in networks) or "TBD"


def format_stream_html(networks: list[str]) -> str:
    links = []
    for n in networks:
        md = STREAM_LINKS.get(n, n)
        m = MD_LINK.match(md)
        if m:
            text, url = m.group(1), m.group(2)
            links.append(f'<a href="{url}" target="_blank" rel="noopener">{text}</a>')
        else:
            links.append(md)
    return ", ".join(links) or "TBD"

# ── INDEX.QMD — markdown tables, current week ─────────────────────────────────

def build_index_content(games: list[dict]) -> str:
    """Markdown month/day headings (for TOC) + HTML tables with timezone data attributes."""
    games = sorted(games, key=lambda g: g["et_dt"])
    by_date: dict[date, list[dict]] = {}
    for g in games:
        by_date.setdefault(g["et_dt"].date(), []).append(g)

    lines: list[str] = ["```{=html}", TZ_TOGGLE_HTML, "```", ""]
    current_month = ""
    for d in sorted(by_date):
        month = d.strftime("%B")
        if month != current_month:
            lines += [f"## {month}", ""]
            current_month = month
        lines += [f"### {d.strftime('%A, %B %-d')}", "", "```{=html}"]
        lines += [
            '<table class="table">',
            "<thead><tr><th>Home</th><th>Away</th><th>Announced/Approx. Kickoff Time</th><th>Stream</th></tr></thead>",
            "<tbody>",
        ]
        for g in by_date[d]:
            home = short_name(g["home"])
            away = short_name(g["away"])
            lines.append(f"<tr>")
            lines.append(f"  <td>{home}</td><td>{away}</td>{time_cell_html(g['et_dt'], g['networks'])}<td>{format_stream_html(g['networks'])}</td>")
            lines.append("</tr>")
        lines += ["</tbody></table>", "```", ""]
    return "\n".join(lines)


def update_index_qmd(path: str, content_block: str) -> None:
    with open(path) as f:
        content = f.read()
    faq_idx = content.find("## FAQs")
    if faq_idx == -1:
        sys.exit("ERROR: Could not find '## FAQs' in index.qmd — aborting.")
    fm_end = content.find("---", 3) + 3
    prefix = content[:fm_end].rstrip()
    suffix = content[faq_idx:]
    with open(path, "w") as f:
        f.write(prefix + "\n\n" + content_block.rstrip() + "\n\n" + APPROX_NOTE + "\n\n" + suffix)

# ── SCHEDULE.QMD — HTML tables with team filter, full season ──────────────────

FILTER_JS = """\
<script>
function filterTeam(team) {
  document.querySelectorAll('.game-row').forEach(row => {
    row.style.display =
      (team === 'all' || row.dataset.home === team || row.dataset.away === team)
      ? '' : 'none';
  });

  document.querySelectorAll('.day-section').forEach(section => {
    const visible = [...section.querySelectorAll('.game-row')].some(r => r.style.display !== 'none');
    section.style.display = visible ? '' : 'none';
  });

  // Quarto wraps markdown ## headings in <section class="level2">
  document.querySelectorAll('section.level2').forEach(section => {
    const visible = [...section.querySelectorAll('.day-section')].some(d => d.style.display !== 'none');
    section.style.display = visible ? '' : 'none';
  });

  const anyVisible = [...document.querySelectorAll('.game-row')].some(r => r.style.display !== 'none');
  document.getElementById('no-results').style.display = anyVisible ? 'none' : 'block';
}
</script>"""


def write_schedule_qmd(path: str, games: list[dict]) -> None:
    """Write schedule.qmd with markdown month headings (picked up by Quarto TOC)
    and HTML day tables with a team dropdown filter."""
    games = sorted(games, key=lambda g: g["et_dt"])

    # Group by month then date, preserving chronological order
    by_month: dict[str, dict[date, list[dict]]] = {}
    for g in games:
        month = g["et_dt"].strftime("%B")
        by_month.setdefault(month, {}).setdefault(g["et_dt"].date(), []).append(g)

    teams = sorted({
        name
        for g in games
        for name in (short_name(g["home"]), short_name(g["away"]))
    })

    # Mobile-only month jump nav (desktop uses the TOC sidebar)
    months_seen: list[str] = []
    for g in games:
        m = g["et_dt"].strftime("%B")
        if m not in months_seen:
            months_seen.append(m)

    mobile_jump = ['<div class="d-flex flex-wrap gap-1 align-items-center mb-3 d-md-none">']
    mobile_jump.append('  <span class="text-muted small fw-semibold me-1">Jump to:</span>')
    for month in months_seen:
        mobile_jump.append(f'  <a href="#{month.lower()}" class="btn btn-sm btn-outline-secondary">{month}</a>')
    mobile_jump.append('</div>')

    # Timezone toggle + dropdown filter
    dropdown = [
        *mobile_jump,
        TZ_TOGGLE_HTML,
        '<div class="mb-4 d-flex align-items-center gap-2">',
        '  <label for="team-select" class="fw-semibold text-nowrap mb-0">Filter by team:</label>',
        '  <select id="team-select" class="form-select" style="max-width: 220px;" onchange="filterTeam(this.value)">',
        '    <option value="all">All Teams</option>',
        *[f'    <option value="{t}">{t}</option>' for t in teams],
        '  </select>',
        '</div>',
        '<p id="no-results" style="display:none" class="text-muted">No games found for this team.</p>',
    ]

    lines = [
        "---",
        f'title: "{SEASON_START.year} NWSL Full Schedule"',
        "---",
        "",
        "*Kickoff times are approximate based on historical trends.",
        "",
        "```{=html}",
        *dropdown,
        "```",
        "",
    ]

    for month, dates in by_month.items():
        lines += [f"## {month}", "", "```{=html}"]
        for d in sorted(dates):
            lines.append(f'<div class="day-section"><h3>{d.strftime("%A, %B %-d")}</h3>')
            lines += [
                '<table class="table table-sm table-hover">',
                "<thead><tr><th>Home</th><th>Away</th><th>Announced/Approx. Kickoff Time</th><th>Stream</th></tr></thead>",
                "<tbody>",
            ]
            for g in dates[d]:
                home = short_name(g["home"])
                away = short_name(g["away"])
                stream = format_stream_html(g["networks"])
                lines.append(f'<tr class="game-row" data-home="{home}" data-away="{away}">')
                lines.append(f"  <td>{home}</td><td>{away}</td>{time_cell_html(g['et_dt'], g['networks'])}<td>{stream}</td>")
                lines.append("</tr>")
            lines += ["</tbody></table>", "</div>"]
        lines += ["```", ""]

    lines += ["```{=html}", FILTER_JS, "```", ""]

    with open(path, "w") as f:
        f.write("\n".join(lines))

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    today = datetime.now(ET).date()

    # index.qmd — current week
    week_end = today + timedelta(days=DAYS_AHEAD)
    print(f"Fetching current week games {today} → {week_end}...")
    week_games = [g for e in fetch_games(today, week_end) if (g := parse_game(e))]
    print(f"  Found {len(week_games)} game(s)")
    if week_games:
        update_index_qmd("index.qmd", build_index_content(week_games))
        print("  index.qmd updated")
    else:
        print("  No games found — index.qmd not modified")

    # schedule.qmd — full season
    print(f"\nFetching full season {SEASON_START} → {SEASON_END}...")
    season_games = [g for e in fetch_games(SEASON_START, SEASON_END) if (g := parse_game(e))]
    print(f"  Found {len(season_games)} game(s)")
    if season_games:
        write_schedule_qmd("schedule.qmd", season_games)
        print("  schedule.qmd updated")
    else:
        print("  No season games found — schedule.qmd not modified")

    print("\nRun `quarto render` to rebuild the site.")


if __name__ == "__main__":
    main()
