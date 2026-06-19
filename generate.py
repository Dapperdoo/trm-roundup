#!/usr/bin/env python3
"""
The Morning After — self-hosted daily generator (Claude version).

Runs daily on GitHub Actions: fetches the league pages, has Claude write the
playful roundup, renders it into template.html, and writes docs/index.html
which GitHub Pages serves. Only secret needed: ANTHROPIC_API_KEY.
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request

import anthropic
from bs4 import BeautifulSoup

BASE = "https://trm-fantasy.onrender.com"
INDEX_URL = f"{BASE}/wc"
TEMPLATE_PATH = "template.html"
OUTPUT_PATH = "docs/index.html"
MODEL = "claude-sonnet-4-6"

UA = "Mozilla/5.0 (compatible; TRM-Roundup/1.0; +https://github.com)"

MANAGERS = {
    "Joe S": "Back of the Van United — aka 'Sheerin'; universally popular ex-pro footballer, a real flair player whose career was forever interrupted by injuries; loved the party-boy lifestyle as much as the game; utterly baffled by modern tech (internet, apps, AI).",
    "Sam": "Look at his face. Just Look at his FACE! — aka 'Troughts' or 'The Bard'; expressive professional stage & TV performer; loves beer, dancing, music and a good yarn; very witty, slightly scatty; loves football but loves belting out Shakespeare even more; brother of Wigs; also a cricket man.",
    "Joe A": "Shatner's Bassoon — an actor, occasionally called 'Nicely' (use that nickname sparingly); lives for the good life and the easy life, forever on holiday or in the pub; main rival is Tristan.",
    "Tom": "Anamaduwa Athletic — aka 'Tomo'; party animal and dance-music DJ; always travelling, never sure which country he's in; lives in Asia eating curries with his bare hands; main rival is Nick; perennial foot-of-the-table scrapper with Dave.",
    "Dave": "Trossy's Giants — aka 'Trossy Ginge'; lecturer and poet; loves wordplay and puns; proper nocturnal, rarely out of bed before the afternoon; secretly records the Culture Show on a dated VHS machine; regular city-break traveller; loves food, beer and cigarettes (usually all together); rivals with both Jake and Tom — he and Tom in particular love finishing one place above each other, usually scrapping near the bottom.",
    "Wigs": "50 Shades of O'Shea — aka 'Wogsy'; counsellor; gregarious and witty; loves cricket as well as football; brother of Sam. (Never describe Wigs as 'gentle'.)",
    "Jeremy": "Von Neumann Trombone — aka 'Jezza' or 'Jerry'; 'the professor'; a measured, witty boffin — and the programmer who literally runs the league and codes its website; super-smart; historically one of the two most successful fantasy managers (with Dan); a niggling tackler at 5-a-side; a dependable, measured 'Swiss' type.",
    "Nick": "Dyer's Rusty 9 Iron — 'rusty iron' because he skies shots over the bar like a golf club; very tall, loud deep voice; sharp tactical football mind; loves his beer and food; main rival is Tom.",
    "Dan": "Denton Burn — musician who lives off-grid, happiest out in the fresh air just enjoying nature; smart, alternative, very witty; historically a top fantasy player.",
    "Chris": "Lloyd's Food and Wine — aka 'Lloydy' / 'Loopy Lloydy'; tall, eclectic, always doing things (mountain biking, travelling, dancing) rather than sitting still; builds his own electrical kit — the full-blown mad scientist to Jeremy's measured boffin, all wild contraptions and half-baked optimisation theories; main rival is Jake.",
    "Tristan": "Trippier & Trippier — aka 'The Russian'; big Russian guy raised in London; fiercely, relentlessly competitive; loves football and sweeties; witty but doesn't suffer fools; throws his hands up in disgust when displeased; main rival is Joe A.",
    "Malik": "Propaganda Parade — quirky, smart Icelandic man managing from afar; signs anyone who has ever worn a Manchester United or Portugal shirt.",
    "Jake": "Snacob's Ladder — aka 'Jake the Snake' or 'Snakey'; renewable-energy project manager with a permanently jam-packed diary; loves wind turbines and mushrooms; never stops; spends a lot of time in the dentist's chair; famously turned up to the school sports day race with his own starting block; overspent badly on Harry Kane and filled the rest of his squad with cheap players he'd never heard of; plays psy-trance/techno like Tom; rivals with Chris (Lloydy) and Dave; the league's designated whipping boy, fair game for a little extra ribbing.",
}
RELATIONSHIPS = ("Joe A vs Tristan (rivals), Tom vs Nick (rivals), Chris vs Jake (rivals), "
                 "Dave vs Jake (rivals), Dave vs Tom (rivals — perennial scrappers who love "
                 "finishing one place above the other, usually near the foot of the table), "
                 "Sam & Wigs (brothers). Dan and Malik are loosely allied — mention only lightly.")

SYSTEM_PROMPT = """You write "The Morning After", the daily bulletin of a private 13-manager World Cup 2026 fantasy football league. It is a cheeky, take-the-piss, morning-after report on YESTERDAY'S MATCHDAY: it leads with the day's fixtures and the points they produced, tells each manager's readers who actually scored (and who flopped), how that moved them in the table, and gives a rival a dig where the standings invite it. Football first, with teeth.

MATCHDAY CONCEPT — IMPORTANT. The tournament is played in ROUNDS (each nation plays three group games, one per round) and the source page labels the round e.g. "Group Matchday 2". IGNORE that as your headline. For THIS bulletin a "matchday" means a single CALENDAR DAY of football (2-5 fixtures a day). The tournament kicked off on Thursday 11 June 2026 = Matchday 1; each later calendar day adds one (12 June = MD2, 13 June = MD3, and so on). This edition recaps YESTERDAY (the calendar day just finished). The matchday label is computed for you and supplied as "recap_matchday_label" (e.g. "Matchday 8"), for the date "recap_date" — use it exactly; do NOT do any date arithmetic yourself. Because each nation plays only once per round, a player's this-round points come from their single fixture, which falls on exactly ONE calendar day — so a manager's haul FOR YESTERDAY = the points of their players whose nations played (went FULL TIME) yesterday. Lead every write-up with that. Fixtures on later days are out of scope for the narrative; mention them only as a brief teaser.

You are given the raw text of the league standings/fixtures page and, under "each_managers_own_squad", every manager's squad keyed by that manager's name — each entry lists only THAT manager's players with their country, price and this-round points. The standings/fixtures text also gives each game's status (FULL TIME, LIVE, or a future kickoff time) and date. READ it and WRITE the column from it.

STRICT PLAYER OWNERSHIP — THE MOST IMPORTANT RULE: every player belongs to exactly ONE manager — the one under whose name they appear in "each_managers_own_squad". Before writing a manager's entry, look at THAT manager's player list, and treat it as a closed whitelist: you may name ONLY players from that exact list, with the exact points shown beside them. Do NOT rely on your own knowledge of football to decide who a player belongs to — a famous player (e.g. Lionel Messi) belongs to whichever manager's list actually contains him, and to NO ONE ELSE. Never put a player in a manager's write-up unless that player's name physically appears in that manager's own list. Never invent players or points. When in doubt, leave a player out.

EVERY MANAGER WRITE-UP MUST INCLUDE (this is the whole point — never omit it):
- Their points from yesterday's matchday and their current league position.
- The standout players BY NAME with their points — who hauled and who blanked. Use ONLY the real player names and points from that manager's own squad list; do NOT copy any names from this instruction. (The shape to aim for reads like: "<their top scorer> led the way with <pts>, <another> chipped in <pts>, while <a player> drew a blank" — but filled only with that manager's actual players and actual points.) This player-by-player detail is the most important content; a write-up without named players and their points has failed.
- How the result moved them in the table (climbed, slipped, held), and the gap to a rival where it's worth a dig.
- A brief nod to any of their players still to come (see accuracy rule), as a teaser only.

TONE: cheeky, witty, take-the-piss — a local-paper columnist who knows everyone personally and enjoys a proper wind-up. Mock the duff captain picks, the vanity-priced flops, the bottom-half flailing, the two-point manager acting like he's won the thing. Lean on each manager's character. Give Jake (Snacob's Ladder) a slightly bigger dig than the rest — he's the designated whipping boy and can take it. Keep it affectionate ribbing between mates, never genuinely cruel: tease the football and the decisions, nothing below the belt. About 115-175 words per manager.

ACCURACY RULE: a player on 0 may simply NOT HAVE PLAYED YET. Check the fixtures — only call a 0 a blank if that player's nation's game is FULL TIME (played yesterday). If it is upcoming or live, say "still to play" / "yet to come" and never call it a blank. The "top_haul", "bargain" and "flop" notes must each name a player whose game is FINISHED.

Provide your answer ONLY by calling the publish_roundup tool — do not write any prose outside the tool call, and do not narrate your reasoning. Fill these fields:
- matchday_label: use the supplied recap_matchday_label verbatim.
- status_live: true if any of yesterday's games were still unfinished/live at fetch time, else false.
- standings: every team with its manager and total, ordered 1st to last.
- still_to_play: a short teaser of the nations kicking off tonight / the next calendar day, or 'Everyone's arrived.'
- notes.top_haul / notes.bargain / notes.flop: each "player (manager) — pts" (bargain = best points-per-million among players who PLAYED yesterday; flop = worst return for price among players who PLAYED yesterday).
- lead: a one-paragraph scene-setter on yesterday's matchday (may include <b>…</b>).
- articles: one per manager in standings order, each with manager, headline and body.
Use the manager FIRST NAMES exactly as given in the profiles. Include all 13 managers."""

def fetch(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last}")

def to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join([ln for ln in lines if ln])

def team_slugs(index_html):
    m = re.search(r'<script[^>]*type="application/json"[^>]*>([^<]*\|[^<]*)</script>', index_html)
    if m:
        slugs = [s for s in m.group(1).strip().strip('"').split("|") if s]
        if len(slugs) >= 5:
            return slugs
    return ["back-of-the-van-united", "look-at-his-face", "anamaduwa-athletic",
            "shatners-bassoon", "trossys-giants", "50-shades-of-oshea",
            "von-neumann-trombone", "dyers-rusty-9-iron", "lloyds-food-and-wine",
            "denton-burn", "trippier-and-trippier", "propaganda-parade", "snacobs-ladder"]

# Fixed mapping of team page slug -> (team name, manager). Lets us hand each
# squad to the AI clearly tagged with its owner, so players can't be cross-attributed.
TEAM_BY_SLUG = {
    "back-of-the-van-united": ("Back of the Van United", "Joe S"),
    "look-at-his-face": ("Look at his face. Just Look at his FACE!", "Sam"),
    "anamaduwa-athletic": ("Anamaduwa Athletic", "Tom"),
    "shatners-bassoon": ("Shatner's Bassoon", "Joe A"),
    "trossys-giants": ("Trossy's Giants", "Dave"),
    "50-shades-of-oshea": ("50 Shades of O'Shea", "Wigs"),
    "von-neumann-trombone": ("Von Neumann Trombone", "Jeremy"),
    "dyers-rusty-9-iron": ("Dyer's Rusty 9 Iron", "Nick"),
    "lloyds-food-and-wine": ("Lloyd's Food and Wine", "Chris"),
    "denton-burn": ("Denton Burn", "Dan"),
    "trippier-and-trippier": ("Trippier & Trippier", "Tristan"),
    "propaganda-parade": ("Propaganda Parade", "Malik"),
    "snacobs-ladder": ("Snacob's Ladder", "Jake"),
}

def gather():
    t0 = time.time()
    index_html = fetch(INDEX_URL)
    print(f"  index fetched in {time.time()-t0:.1f}s", flush=True)
    standings_text = to_text(index_html)
    squads = {}
    for slug in team_slugs(index_html):
        team, manager = TEAM_BY_SLUG.get(slug, (slug, slug))
        label = f"{manager} — {team}"
        ts = time.time()
        try:
            squads[label] = to_text(fetch(f"{BASE}/wc/team/{slug}"))
            print(f"  {slug} in {time.time()-ts:.1f}s", flush=True)
        except Exception as e:
            print(f"  warn: {slug}: {e}", file=sys.stderr, flush=True)
    return standings_text, squads

def write_copy(standings_text, squads):
    # Compute the matchday here so the model never has to do date arithmetic
    # (which previously tempted it to "think out loud" instead of answering).
    today = datetime.datetime.utcnow().date()
    recap = today - datetime.timedelta(days=1)
    md_num = (recap - datetime.date(2026, 6, 11)).days + 1
    payload = {
        "todays_date_utc": today.strftime("%Y-%m-%d (%A)"),
        "recap_matchday_label": f"Matchday {md_num}",
        "recap_date": recap.strftime("%A %d %B %Y"),
        "manager_profiles": MANAGERS,
        "relationships": RELATIONSHIPS,
        "standings_and_fixtures_page_text": standings_text,
        "each_managers_own_squad": squads,
    }
    # The roundup comes back as a forced TOOL CALL, not free text. This makes
    # the model emit structured, schema-valid data with no preamble and no
    # hand-written JSON to misformat — killing the parse/preamble failures.
    roundup_tool = {
        "name": "publish_roundup",
        "description": "Publish the finished daily roundup as structured data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "matchday_label": {"type": "string"},
                "status_live": {"type": "boolean"},
                "standings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "team": {"type": "string"},
                            "manager": {"type": "string"},
                            "total": {"type": "integer"},
                        },
                        "required": ["team", "manager", "total"],
                    },
                },
                "still_to_play": {"type": "string"},
                "notes": {
                    "type": "object",
                    "properties": {
                        "top_haul": {"type": "string"},
                        "bargain": {"type": "string"},
                        "flop": {"type": "string"},
                    },
                    "required": ["top_haul", "bargain", "flop"],
                },
                "lead": {"type": "string"},
                "articles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "manager": {"type": "string"},
                            "headline": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["manager", "headline", "body"],
                    },
                },
            },
            "required": ["matchday_label", "status_live", "standings",
                         "still_to_play", "notes", "lead", "articles"],
        },
    }
    # Streaming avoids the ~10-min non-streaming duration cap; the generous
    # timeout plus max_retries=1 and the outer loop ride out any network blip.
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=600.0,
        max_retries=1,
    )
    last = None
    for attempt in range(3):
        t0 = time.time()
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=10000,
                temperature=0.6,
                system=SYSTEM_PROMPT,
                tools=[roundup_tool],
                tool_choice={"type": "tool", "name": "publish_roundup"},
                messages=[{"role": "user", "content": json.dumps(payload)}],
            ) as stream:
                msg = stream.get_final_message()
            print(f"  Claude responded in {time.time()-t0:.1f}s "
                  f"(stop_reason={getattr(msg, 'stop_reason', None)})", flush=True)
            data = None
            for block in (msg.content or []):
                if getattr(block, "type", None) == "tool_use":
                    data = block.input
                    break
            if not isinstance(data, dict):
                raise RuntimeError(f"no publish_roundup tool call (stop_reason={getattr(msg, 'stop_reason', None)})")
            # Authoritative matchday label, set here so the model can't fumble it.
            data["matchday_label"] = f"Matchday {md_num}"
            if len(data.get("articles", [])) < 10 or len(data.get("standings", [])) < 10:
                raise ValueError("too few managers in tool output")
            return data
        except Exception as e:
            last = e
            print(f"  attempt {attempt + 1} failed after {time.time()-t0:.1f}s: {e}",
                  file=sys.stderr, flush=True)
            time.sleep(10)
    raise RuntimeError(f"Claude failed after retries: {last}")

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"

def render(data):
    rows, arts = [], []
    standings = data["standings"]
    by_mgr = {a["manager"]: a for a in data["articles"]}
    n = len(standings)
    for i, r in enumerate(standings):
        cls = ' class="r1"' if i == 0 else (' class="r13"' if i == n - 1 else "")
        rows.append(f'<tr{cls}><td class="pos">{i+1}</td><td>'
                    f'<span class="tname">{esc(r["team"])}</span>'
                    f'<span class="mgr">{esc(r["manager"])}</span></td>'
                    f'<td class="tot">{esc(r["total"])}</td></tr>')
        a = by_mgr.get(r["manager"], {"headline": "", "body": ""})
        rcls = "rank top" if i == 0 else ("rank low" if i == n - 1 else "rank")
        arts.append(f'<article><div class="rankbar">'
                    f'<span class="{rcls}">{ordinal(i+1)}</span>'
                    f'<span class="team">{esc(r["team"])} &middot; {esc(r["manager"])}</span>'
                    f'<span class="pts">{esc(r["total"])} pts</span></div>'
                    f'<h2 class="head">{esc(a["headline"])}</h2>'
                    f'<p>{a["body"]}</p>'
                    f'<div class="byline">The Morning After</div></article>')
    notes = data.get("notes", {})
    notes_rows = (
        f'<tr><td><span class="tname" style="color:var(--gold)">Top points haul</span>'
        f'<span class="mgr">{esc(notes.get("top_haul",""))}</span></td></tr>'
        f'<tr><td><span class="tname" style="color:var(--green)">Best-value pick</span>'
        f'<span class="mgr">{esc(notes.get("bargain",""))}</span></td></tr>'
        f'<tr><td><span class="tname" style="color:var(--magenta)">Priciest flop</span>'
        f'<span class="mgr">{esc(notes.get("flop",""))}</span></td></tr>')
    is_live = bool(data.get("status_live"))
    caveat = ('<p class="note">Figures are a live snapshot — some games were still in play when this '
              'edition refreshed, so zeros for those nations mean "still to come", not a no-show. '
              'The page refreshes again after the next round of games.</p>') if is_live else ""
    md = data.get("matchday_label", "World Cup 2026")
    repl = {
        "{{MATCHDAY_LABEL}}": esc(md),
        "{{MATCHDAY_SHORT}}": esc(md.replace("Group ", "").replace("Matchday", "MD")),
        "{{DATE_LABEL}}": datetime.datetime.utcnow().strftime("%A %d %B %Y"),
        "{{STATUS_CHIP}}": "Games still to come" if is_live else "All games settled",
        "{{LEAD}}": f'<p class="lead">{data.get("lead","")}</p>',
        "{{ARTICLES}}": "\n".join(arts),
        "{{STANDINGS_ROWS}}": "\n".join(rows),
        "{{NOTES_ROWS}}": notes_rows,
        "{{STILL_TO_PLAY}}": esc(data.get("still_to_play", "")),
        "{{CAVEAT}}": caveat,
    }
    html = open(TEMPLATE_PATH, encoding="utf-8").read()
    for k, v in repl.items():
        html = html.replace(k, v)
    return html

def main():
    print("Fetching league pages...", flush=True)
    standings_text, squads = gather()
    print(f"Writing the column with Claude... ({len(squads)} squads gathered)", flush=True)
    data = write_copy(standings_text, squads)
    html = render(data)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH} ({len(data['articles'])} managers)", flush=True)

if __name__ == "__main__":
    main()
