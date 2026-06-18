#!/usr/bin/env python3
"""
TRM World Cup After-Hours — self-hosted daily generator (FREE Gemini version).

Runs once a day on GitHub Actions:
  1. Fetches the league pages from trm-fantasy.onrender.com (server-rendered HTML).
  2. Strips them to text.
  3. Asks Google's Gemini API (free tier) to read the data AND write the playful,
     party-themed roundup — returning structured JSON.
  4. Renders it into template.html and writes docs/index.html, which GitHub Pages
     serves as the public website.

No browser, no developer access to the original site, no database, and NO COST.
The only secret needed is GEMINI_API_KEY (a free key from aistudio.google.com,
set as a GitHub repo secret). A single short request per day sits far inside the
free tier.
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request

from google import genai
from google.genai import types
from bs4 import BeautifulSoup

BASE = "https://trm-fantasy.onrender.com"
INDEX_URL = f"{BASE}/wc"
TEMPLATE_PATH = "template.html"
OUTPUT_PATH = "docs/index.html"
MODEL = "gemini-2.5-flash"   # free tier; plenty for one run a day

UA = "Mozilla/5.0 (compatible; TRM-AfterHours/1.0; +https://github.com)"

# ---- Manager personalities (edit these to taste) -------------------------
MANAGERS = {
    "Joe S":   "Back of the Van United — real name Sheerin; universally popular ex-pro footballer who loved the party-boy lifestyle as much as the game; utterly baffled by modern tech (internet, apps, AI).",
    "Sam":     "Look at his face. Just Look at his FACE! — expressive professional stage & TV performer; loves beer, dancing, music and a good yarn; very witty, slightly scatty; loves football but loves belting out Shakespeare even more; brother of Wigs; also a cricket man.",
    "Joe A":   "Shatner's Bassoon — laid-back actor, forever on holiday or in the pub; main rival is Tristan.",
    "Tom":     "Anamaduwa Athletic — party animal and dance-music DJ; always travelling, never sure which country he's in; lives in Asia eating curries with his bare hands; main rival is Nick.",
    "Dave":    "Trossy's Giants — aka 'Trossy Ginge'; lecturer and poet; loves wordplay and puns; regular city-break traveller; loves food, beer and cigarettes (usually all together).",
    "Wigs":    "50 Shades of O'Shea — counsellor; gregarious, gentle and witty; loves cricket as well as football; brother of Sam.",
    "Jeremy":  "Von Neumann Trombone — 'the professor'; computer programmer, super-smart and witty; historically one of the two most successful fantasy managers (with Dan); a niggling tackler at 5-a-side; a dependable, measured 'Swiss' type.",
    "Nick":    "Dyer's Rusty 9 Iron — 'rusty iron' because he skies shots over the bar like a golf club; very tall, loud deep voice; sharp tactical football mind; loves his beer and food; main rival is Tom.",
    "Dan":     "Denton Burn — musician who lives off-grid; smart, alternative, very witty; historically a top fantasy player; main ally is Malik.",
    "Chris":   "Lloyd's Food and Wine — aka 'Lloydy'; tall, eclectic, always doing things (mountain biking, travelling, dancing) rather than sitting still; builds his own electrical kit; the 'mad scientist' to Jeremy's measured professor; main rival is Jake.",
    "Tristan": "Trippier & Trippier — big Russian guy raised in London; loves football and sweeties; witty but doesn't suffer fools; throws his hands up in disgust when displeased; main rival is Joe A.",
    "Malik":   "Propaganda Parade — quirky, smart Icelandic man managing from afar; signs anyone who has worn a Manchester United or Portugal shirt; main ally is Dan.",
    "Jake":    "Snacob's Ladder — renewable-energy project manager; loves wind turbines and mushrooms; never stops; spends a lot of time in the dentist's chair; overspent badly on Harry Kane and filled the rest of his squad with cheap players he'd never heard of; plays psy-trance/techno like Tom; main rival is Chris (Lloydy).",
}
RELATIONSHIPS = ("Joe A vs Tristan (rivals), Tom vs Nick (rivals), Chris vs Jake (rivals), "
                 "Malik & Dan (allies), Sam & Wigs (brothers).")

SYSTEM_PROMPT = """You are the columnist for "TRM World Cup After-Hours", a daily morning roundup of a private 13-manager World Cup 2026 fantasy football league, themed as dispatches from an all-night party/festival (the league site plays a psytrance festival mix; several managers are DJs and party animals). Lean into that party framing — managers holding court by the bar, slumped in the corner, first on the dancefloor, calling a taxi — but keep the real football substance.

You will be given the raw text of the league's standings/fixtures page and each manager's squad page. READ that data and WRITE the column.

TONE: warm, witty, affectionate roast, like a local columnist who knows everyone personally. Tease, never wound. ~100-150 words per manager.

CRITICAL ACCURACY RULE: a player showing 0 points may simply NOT HAVE PLAYED YET — their nation's match may be upcoming or still live (check the fixtures list: each game is FULL TIME, LIVE, or has a future kickoff time). NEVER describe a player as having blanked/flopped/done nothing unless their nation's game is FULL TIME and they returned 0 or negative. For players whose game is upcoming or live, say "yet to play" / "still to come" / "provisional" instead, and where it adds colour, mention which notable players a manager still has to come. The "flop" highlight must only name a player whose game is finished.

Return ONLY valid JSON in exactly this shape:
{
  "matchday_label": "<e.g. Group Matchday 1>",
  "status_live": <true if any of today's games were still live or upcoming, else false>,
  "standings": [ {"team": "...", "manager": "...", "total": <int>}, ...  ordered 1st to last ],
  "still_to_play": "<comma-separated nations not yet kicked off, or 'Everyone's arrived.'>",
  "notes": {
     "top_haul": "<player (manager) — pts>",
     "bargain": "<best points-per-million among players who PLAYED: player (manager) — pts from £x.xm>",
     "flop": "<worst return for price among players who PLAYED: player (manager) — pts from £x.xm>"
  },
  "lead": "<one-paragraph scene-setter, may include <b>…</b>>",
  "articles": [ {"manager": "...", "headline": "...", "body": "..."}, ... one per manager, in standings order ]
}
Use the manager FIRST NAMES exactly as given in the profiles. Include all 13 managers."""


def fetch(url, tries=5):
    """GET a URL as text, with retries — Render free tier can cold-start slowly."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(8 * (i + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join([ln for ln in lines if ln])


def team_slugs(index_html):
    """The /wc page embeds a pipe-delimited slug list in a JSON script tag."""
    m = re.search(r'<script[^>]*type="application/json"[^>]*>([^<]*\|[^<]*)</script>', index_html)
    if m:
        slugs = [s for s in m.group(1).strip().strip('"').split("|") if s]
        if len(slugs) >= 5:
            return slugs
    return ["back-of-the-van-united", "look-at-his-face", "anamaduwa-athletic",
            "shatners-bassoon", "trossys-giants", "50-shades-of-oshea",
            "von-neumann-trombone", "dyers-rusty-9-iron", "lloyds-food-and-wine",
            "denton-burn", "trippier-and-trippier", "propaganda-parade", "snacobs-ladder"]


def gather():
    index_html = fetch(INDEX_URL)
    standings_text = to_text(index_html)
    squads = {}
    for slug in team_slugs(index_html):
        try:
            squads[slug] = to_text(fetch(f"{BASE}/wc/team/{slug}"))
        except Exception as e:
            print(f"  warn: {slug}: {e}", file=sys.stderr)
    return standings_text, squads


def write_copy(standings_text, squads):
    payload = {
        "manager_profiles": MANAGERS,
        "relationships": RELATIONSHIPS,
        "standings_and_fixtures_page_text": standings_text,
        "squad_pages_text": squads,
    }
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=MODEL,
        contents=json.dumps(payload),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",   # forces clean JSON output
            max_output_tokens=8000,
            temperature=0.9,
        ),
    )
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lstrip().startswith("json") else text
        text = text.strip().rstrip("`").strip()
    data = json.loads(text)
    if len(data.get("articles", [])) < 10 or len(data.get("standings", [])) < 10:
        raise ValueError("AI returned too few managers; aborting to avoid a broken page.")
    return data


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(n % 10, 'th')}"


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
                    f'<span class="team">{esc(r["team"])} · {esc(r["manager"])}</span>'
                    f'<span class="pts">{esc(r["total"])} pts</span></div>'
                    f'<h2 class="head">{esc(a["headline"])}</h2>'
                    f'<p>{a["body"]}</p>'
                    f'<div class="byline">After-Hours Report</div></article>')
    notes = data.get("notes", {})
    notes_rows = (
        f'<tr><td><span class="tname" style="color:var(--gold)">Last one dancing</span>'
        f'<span class="mgr">{esc(notes.get("top_haul",""))}</span></td></tr>'
        f'<tr><td><span class="tname" style="color:var(--green)">Cheapest round bought</span>'
        f'<span class="mgr">{esc(notes.get("bargain",""))}</span></td></tr>'
        f'<tr><td><span class="tname" style="color:var(--magenta)">Asleep in the corner</span>'
        f'<span class="mgr">{esc(notes.get("flop",""))}</span></td></tr>')
    is_live = bool(data.get("status_live"))
    caveat = ('<p class="note">Figures are a live snapshot from mid-party — some games were still '
              'in play when this edition refreshed, so zeros for those nations mean "still en route", '
              'not a no-show. The page refreshes again after the next round of games.</p>') if is_live else ""
    md = data.get("matchday_label", "World Cup 2026")
    repl = {
        "{{MATCHDAY_LABEL}}": esc(md),
        "{{MATCHDAY_SHORT}}": esc(md.replace("Group ", "").replace("Matchday", "MD")),
        "{{DATE_LABEL}}": "Last orders: " + datetime.datetime.utcnow().strftime("%A %d %B %Y"),
        "{{STATUS_CHIP}}": "DJ still spinning" if is_live else "Last track played",
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
    print("Fetching league pages…")
    standings_text, squads = gather()
    print("Writing the column with Gemini...")
    data = write_copy(standings_text, squads)
    html = render(data)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH}  ({len(data['articles'])} managers)")


if __name__ == "__main__":
    main()
