#!/usr/bin/env python3
"""
The Morning After — self-hosted daily generator (Claude version).

Runs daily on GitHub Actions: fetches the league pages, has Claude write the
playful roundup, renders it into template.html, and writes docs/roundup.html
which GitHub Pages serves. Only secret needed: ANTHROPIC_API_KEY.

Each edition reports ONLY the fixtures completed since the previous edition. A
tiny state file (docs/roundup-state.json), committed alongside the page, records
which fixtures have already been written up; the next run diffs against it.
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
OUTPUT_PATH = "docs/roundup.html"
STATE_PATH = "docs/roundup-state.json"
MODEL = "claude-sonnet-4-6"

UA = "Mozilla/5.0 (compatible; TRM-Roundup/1.0; +https://github.com)"

MANAGERS = {
    "Joe S":   "Back of the Van United — real name Sheerin; universally popular ex-pro footballer who loved the party-boy lifestyle as much as the game; utterly baffled by modern tech (internet, apps, AI).",
    "Sam":     "Look at his face. Just Look at his FACE! — expressive professional stage & TV performer; loves beer, dancing, music and a good yarn; very witty, slightly scatty; loves football but loves belting out Shakespeare even more; brother of Wigs; also a cricket man.",
    "Joe A":   "Shatner's Bassoon — an actor; gives off a faintly unbothered, relaxed air (hint at it lightly at most, don't lean on it); main rival is Tristan.",
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

SYSTEM_PROMPT = """You write "The Morning After", the DAILY bulletin of a private 13-manager World Cup 2026 fantasy football league.

WHAT THIS EDITION COVERS — THE MOST IMPORTANT RULE: this is a daily report on ONLY the fixtures that have FINISHED SINCE THE LAST EDITION — never the whole round. You are given:
- "matchday_fixtures": the games (nation pairs) that went FULL TIME since the previous report. THESE, and only these, are what today's edition is about.
- "previously_reported_fixtures": games already written up in earlier editions. Do NOT review these again — they are old news. You may reference them only lightly for table context.
- Games that are still upcoming or live: "yet to come" — do not treat a 0 from them as a blank.
Every manager write-up must LEAD with that manager's points from THIS edition's matchday_fixtures (the points their players earned in those specific games), naming those players. If a manager had no players in the matchday_fixtures, say so briefly — a quiet night for them — and move on. Do not pad the piece by re-listing their whole round.

You are also given the raw text of the league standings/fixtures page and, under "each_managers_own_squad", every manager's squad keyed by that manager's name — each entry lists only THAT manager's players with their country, price and this-round points. Use the fixtures text to see each game's status (FULL TIME, LIVE, upcoming).

STRICT PLAYER OWNERSHIP: every player belongs to exactly ONE manager — the one under whose name they appear in "each_managers_own_squad". Treat that list as a closed whitelist: name ONLY players from that exact list, with the exact points shown. Do NOT use your own football knowledge to assign players. Never invent players or points. When in doubt, leave a player out.

EVERY MANAGER WRITE-UP MUST INCLUDE:
- Their points from THIS matchday (the matchday_fixtures), and their current league position for context.
- The standout players BY NAME with their points from those games — who hauled and who blanked. A write-up with no named players/points from the matchday_fixtures has failed (unless they genuinely had none, in which case say so).
- How it moved them in the table, and a dig at a rival where the table invites it.
- A brief nod to any of their players still to come.

TONE: dry and deadpan, affectionately ribbing the managers, economical (~90-130 words each). Lean on character sparingly; let the football do the work.

ACCURACY: only call a 0 a blank if that player's nation is in matchday_fixtures (i.e. finished). If their game is upcoming/live, it is "yet to come". The "flop" note must name a player whose game finished this matchday.

Use the manager FIRST NAMES exactly as given. Keep any thinking extremely brief. Your reply MUST contain the requested JSON value at the very end, with nothing after the final bracket, and no code fences."""


# Work is split into small bounded calls. The frame call also reports the
# fixtures so we can persist them and scope the next edition.

def frame_task(prev_fixtures):
    prev = ", ".join(prev_fixtures) if prev_fixtures else "(none — this is the first edition; treat all currently-finished games as this matchday)"
    return (
        "TASK: Produce ONLY the frame of today's bulletin — do NOT write the per-manager articles.\n\n"
        f"previously_reported_fixtures (already covered, do NOT report again): {prev}\n\n"
        "Identify, from the fixtures text, every game in this round currently at FULL TIME (as \"HOME-AWAY\" "
        "using the three-letter codes shown, e.g. \"CZE-RSA\"). matchday_fixtures = those FULL TIME games "
        "that are NOT in previously_reported_fixtures — i.e. the games finished since the last edition. "
        "Scope the notes (top_haul / bargain / flop) to players who played in matchday_fixtures only.\n\n"
        "Return valid JSON in exactly this shape, as the LAST thing in your reply (no code fences):\n"
        "{\n"
        '  "matchday_label": "<the round label exactly as shown, e.g. Group Matchday 2>",\n'
        '  "status_live": <true if any games are still live or upcoming, else false>,\n'
        '  "fulltime_fixtures": ["HOME-AWAY", ... every game currently FULL TIME this round],\n'
        '  "matchday_fixtures": ["HOME-AWAY", ... the NEW finished games this edition reports on],\n'
        '  "standings": [ {"team": "...", "manager": "...", "total": <int>}, ... ALL managers, 1st to last ],\n'
        '  "still_to_play": "<comma-separated nations not yet kicked off, or \'Everyone has arrived.\'>",\n'
        '  "notes": {\n'
        '     "top_haul": "<player (manager) — pts>",\n'
        '     "bargain": "<best points-per-million among players who played this matchday: player (manager) — pts from price>",\n'
        '     "flop": "<worst return for price among players who played this matchday: player (manager) — pts from price>"\n'
        '  },\n'
        '  "lead": "<one-paragraph scene-setter for THIS matchday\'s action only, may include bold tags>"\n'
        "}\n"
        "Include every manager in standings."
    )


def article_task(batch, matchday_fixtures):
    names = ", ".join(batch)
    md = ", ".join(matchday_fixtures) if matchday_fixtures else "(none finished since last edition)"
    return (
        "TASK: Write the per-manager write-ups for ONLY these managers, in this exact order: "
        f"{names}.\n\n"
        f"matchday_fixtures (the ONLY games this edition covers): {md}\n\n"
        "Each write-up LEADS with that manager's points from these matchday_fixtures and names the players "
        "who scored them (and any who blanked in those games). Ignore players whose games were in earlier "
        "editions; note upcoming players only as 'yet to come'. If a manager had no players in the "
        "matchday_fixtures, say so in a line. Apply the STRICT PLAYER OWNERSHIP rule. ~90-130 words each.\n\n"
        "Return, as the LAST thing in your reply, a JSON array (no code fences) of exactly "
        f"{len(batch)} objects, one per manager named above, in that order:\n"
        '[ {"manager": "<first name>", "headline": "<short headline>", "body": "<~90-130 words>"} ]'
    )


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


def load_state():
    """Previously-reported fixtures. Tolerant of a missing/corrupt file (returns
    empty, which makes the next edition a first-edition bootstrap)."""
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        fx = data.get("reported_fixtures", [])
        return [str(x) for x in fx] if isinstance(fx, list) else []
    except Exception as e:
        print(f"  no usable state ({e}); treating as first edition", flush=True)
        return []


def save_state(fixtures):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    payload = {
        "reported_fixtures": sorted(set(fixtures)),
        "updated": datetime.datetime.utcnow().isoformat() + "Z",
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"  state saved: {len(payload['reported_fixtures'])} reported fixtures", flush=True)


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


def _loads(text, array=False):
    """Parse the JSON value out of a reply that may be preceded by reasoning
    prose. Tries each opening-bracket position (widest first) until one parses
    to the final closing bracket — so a stray bracket in prose can't break it."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    o, c = ("[", "]") if array else ("{", "}")
    end = text.rfind(c)
    if end == -1:
        raise ValueError("no closing bracket in reply")
    last_err = None
    for s, ch in enumerate(text):
        if ch != o:
            continue
        try:
            return json.loads(text[s:end + 1])
        except Exception as e:
            last_err = e
    raise ValueError(f"no parseable JSON found ({last_err})")


def _dump_raw(label, text, stop):
    t = (text or "").replace("\n", " ")
    print(f"  [debug] {label}: raw_len={len(text or '')} stop_reason={stop}",
          file=sys.stderr, flush=True)
    print(f"  [debug] head: {t[:1000]}", file=sys.stderr, flush=True)
    print(f"  [debug] tail: {t[-600:]}", file=sys.stderr, flush=True)


def _ask(client, task, payload, max_tokens, array=False, tries=2):
    """One bounded Claude call. Room for reasoning + JSON; JSON extracted from
    the tail. Logs the raw reply on failure."""
    user = f"{task}\n\n<data>\n{json.dumps(payload)}\n</data>"
    last = None
    for attempt in range(tries):
        t0 = time.time()
        text, stop = "", None
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=max_tokens,
                temperature=0.5,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                msg = stream.get_final_message()
            stop = getattr(msg, "stop_reason", None)
            text = "".join(getattr(b, "text", "") for b in (msg.content or [])).strip()
            print(f"    call done in {time.time()-t0:.1f}s "
                  f"(stop_reason={stop}, len={len(text)})", flush=True)
            if not text:
                raise RuntimeError(f"empty response (stop_reason={stop})")
            return _loads(text, array=array)
        except Exception as e:
            last = e
            _dump_raw(f"attempt {attempt+1} failed: {e}", text, stop)
            time.sleep(6)
    raise RuntimeError(f"call failed after {tries} tries: {last}")


def _chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def write_copy(standings_text, squads):
    prev_fixtures = load_state()
    print(f"  previously reported fixtures: {len(prev_fixtures)}", flush=True)
    payload = {
        "manager_profiles": MANAGERS,
        "relationships": RELATIONSHIPS,
        "previously_reported_fixtures": prev_fixtures,
        "standings_and_fixtures_page_text": standings_text,
        "each_managers_own_squad": squads,
    }
    client = anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=600.0,
        max_retries=1,
    )

    print("  generating frame (fixtures/standings/notes/lead)...", flush=True)
    frame = _ask(client, frame_task(prev_fixtures), payload, max_tokens=10000, array=False)
    standings = frame.get("standings", [])
    if len(standings) < 10:
        raise RuntimeError(f"frame returned too few standings ({len(standings)})")
    matchday_fixtures = frame.get("matchday_fixtures", []) or []
    fulltime_fixtures = frame.get("fulltime_fixtures", []) or []
    print(f"  matchday fixtures this edition: {len(matchday_fixtures)} "
          f"(of {len(fulltime_fixtures)} finished)", flush=True)
    payload["matchday_fixtures"] = matchday_fixtures

    order = [r["manager"] for r in standings]
    articles = []
    for batch in _chunks(order, 3):
        print(f"  generating articles for {', '.join(batch)}...", flush=True)
        try:
            arts = _ask(client, article_task(batch, matchday_fixtures), payload,
                        max_tokens=4000 * len(batch), array=True)
            if not isinstance(arts, list) or len(arts) < len(batch):
                got = len(arts) if isinstance(arts, list) else "?"
                raise RuntimeError(f"batch returned {got} of {len(batch)}")
            articles.extend(arts)
        except Exception as e:
            print(f"  batch failed ({e}); falling back to one manager at a time",
                  file=sys.stderr, flush=True)
            for mgr in batch:
                arts = _ask(client, article_task([mgr], matchday_fixtures), payload,
                            max_tokens=6000, array=True)
                articles.extend(arts)

    data = dict(frame)
    data["articles"] = articles
    if len(data.get("articles", [])) < 10:
        raise RuntimeError(f"assembled too few articles ({len(data.get('articles', []))})")
    print(f"  copy complete: {len(articles)} articles, {len(standings)} standings rows",
          flush=True)
    # Persist what we have now reported so the next edition only covers new games.
    if fulltime_fixtures:
        save_state(sorted(set(prev_fixtures) | set(fulltime_fixtures)))
    return data


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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
    caveat = ('<p class="note">Figures are a live snapshot - some games were still in play when this '
              'edition refreshed, so zeros for those nations mean still to come, not a no-show.</p>') if is_live else ""
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
    print(f"Wrote {OUTPUT_PATH}  ({len(data['articles'])} managers)", flush=True)


if __name__ == "__main__":
    main()
