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
    "Tristan": "Trippier & Trippier — a born-and-raised Londoner, NOT Russian; he just happens to look Russian, so the lads call him 'The Russian' (use the nickname, but never claim he is Russian or was raised in Russia); fiercely, relentlessly competitive; loves football and sweeties; witty but doesn't suffer fools; throws his hands up in disgust when displeased; main rival is Joe A.",
    "Malik": "Propaganda Parade — quirky, smart Icelandic man managing from afar; signs anyone who has ever worn a Manchester United or Portugal shirt.",
    "Jake": "Snacob's Ladder — aka 'Jake the Snake' or 'Snakey'; renewable-energy project manager with a permanently jam-packed diary; loves wind turbines and mushrooms; never stops; spends a lot of time in the dentist's chair; famously turned up to the school sports day race with his own starting block; overspent badly on Harry Kane and filled the rest of his squad with cheap players he'd never heard of; plays psy-trance/techno like Tom; rivals with Chris (Lloydy) and Dave; the league's designated whipping boy, fair game for a little extra ribbing.",
}
RELATIONSHIPS = ("Joe A vs Tristan (rivals), Tom vs Nick (rivals), Chris vs Jake (rivals), "
                 "Dave vs Jake (rivals), Dave vs Tom (rivals — perennial scrappers who love "
                 "finishing one place above the other, usually near the foot of the table), "
                 "Sam & Wigs (brothers). Dan and Malik are loosely allied — mention only lightly.")

# Each calendar "matchday" maps to the exact fixtures played that day. The league
# site stops showing a game's date once it is FULL TIME, so we can't read this
# back later — it's pinned here. Keyed by the matchday's evening date; games that
# kick off after midnight (UK) still belong to that same evening's matchday.
SCHEDULE = {
    "2026-06-18": [("CZE", "RSA"), ("SUI", "BIH"), ("CAN", "QAT"), ("MEX", "KOR")],
    "2026-06-19": [("USA", "AUS"), ("SCO", "MAR"), ("BRA", "HAI"), ("TUR", "PAR")],
    "2026-06-20": [("NED", "SWE"), ("GER", "CIV"), ("ECU", "CUW"), ("TUN", "JPN")],
    "2026-06-21": [("ESP", "KSA"), ("BEL", "IRN"), ("URU", "CPV"), ("NZL", "EGY")],
    "2026-06-22": [("ARG", "AUT"), ("FRA", "IRQ"), ("NOR", "SEN"), ("JOR", "ALG")],
    "2026-06-23": [("POR", "UZB"), ("ENG", "GHA"), ("PAN", "CRO"), ("COL", "COD")],
}

SYSTEM_PROMPT = """You write "The Morning After", the daily bulletin of a private 13-manager World Cup 2026 fantasy football league. It is a cheeky, take-the-piss, morning-after report on YESTERDAY'S MATCHDAY: it leads with the day's fixtures and the points they produced, tells each manager's readers who actually scored (and who flopped), how that moved them in the table, and gives a rival a dig where the standings invite it. Football first, with teeth.

MATCHDAY = YESTERDAY'S GAMES ONLY — THE SINGLE MOST IMPORTANT RULE. This bulletin recaps EXACTLY ONE day of football: the matches played yesterday, and nothing else. You do NOT have to work out which games those were — you are told, precisely:
• recap_date — the day being recapped.
• matchday_fixtures — the ONLY fixtures in scope, e.g. "USA v AUS, SCO v MAR, BRA v HAI, TUR v PAR".
• matchday_nations — the ONLY national teams whose players are in scope, e.g. ["USA","AUS","SCO","MAR","BRA","HAI","TUR","PAR"]. These are the 3-letter country codes shown beside players in the squad lists.
• already_played_earlier — nations that played EARLIER in this round. Their players STILL show this round's points in the squad lists, which makes them dangerously tempting — but they did NOT play yesterday and are OUT OF SCOPE. Never name, credit or score any of their players. EXAMPLE: if BRA is in already_played_earlier, then Brazil's players (Bruno Guimarães, Matheus Cunha, Vinícius Júnior, etc.) do NOT count for this edition, no matter how many round points they are showing. Check every player you name against this list and drop them if their country appears here.
A manager's haul for yesterday = ONLY the points of their players whose country code is in matchday_nations. Add those up, name those players, and IGNORE every other player completely. Any player whose country is NOT in matchday_nations is OUT OF SCOPE — their game was on a different day or has not happened — so do not name them, score them, or call them blanks. Do NOT mention any fixture, nation, score or player that is not in yesterday's list: if Czech Republic, Switzerland, Canada, Mexico etc. are not in matchday_nations, they do not exist for this edition. Do NOT use the standings "round" column as the day's haul — it accumulates points from earlier days of the round; work the day's haul out yourself from the matchday_nations players only. (The group stage runs in rounds of one game per team, but those games are spread across many days — only the single day above counts here. The standings you are given are cumulative and used ONLY for current league positions.) If matchday_nations is empty, simply report there were no fixtures yesterday and keep it brief.

AUTHORITATIVE SCOREBOARD — READ THIS FIRST, IT OVERRIDES EVERYTHING. When each_managers_own_squad is given as STRUCTURED data — each manager mapping to {"yesterday_haul": N, "players_who_played": [{"name","nation","points"}, ...]} — it has ALREADY been filtered in code to yesterday's matchday and is the COMPLETE, FINAL and ONLY source of who scored what. It is not a hint; it is the whole truth. Hard rules, no exceptions: (a) a manager's haul for yesterday IS their yesterday_haul — state that exact number, never recompute it; (b) you may name ONLY players in that manager's players_who_played list, each with EXACTLY the points shown beside them; (c) a player listed with 0 points played and blanked — fair to say so; (d) an empty players_who_played list means NONE of that manager's players featured yesterday — say that plainly and wittily and just give their league position; (e) ANY player, nation, scoreline or result that is not in this data did not happen for this edition — you literally cannot see Turkey, Brazil, or anyone who played on another day, because they have been deliberately removed. NEVER add, infer, remember or reach for a player, nation or number from outside this data, not even one you "know" was playing. Do not mention a fixture or scoreline unless it involves a nation that appears in this scoreboard. If you ever feel the urge to write a result, check it is represented in the scoreboard first; if it isn't, drop it silently. (Only if each_managers_own_squad is instead RAW PAGE TEXT do the scoping/reading rules below apply.)

SILENT SCOPING — WRITE ONLY FINISHED PROSE. Everything above (matchday_nations, already_played_earlier, "in scope", "out of scope") is YOUR private working — the reader must NEVER see it. Do the scoping silently in your head, then write a clean, polished newspaper column. NEVER narrate your reasoning or decision process. NEVER write phrases like "wait, MAR is in already_played_earlier", "X is out of scope", "that shouldn't be in the write-up", "so their points don't count", or "Right:". NEVER use the words matchday_nations, already_played_earlier, "in scope" or "out of scope" anywhere in the output. If a player doesn't count, just leave them out — silently, with no explanation. Each body field must read as if it were printed in a newspaper: only the finished article, nothing about how you wrote it.

NO SELF-CORRECTION OF ANY KIND — JUST AS IMPORTANT. Never second-guess or correct yourself in the text about ANYTHING — not a result, a scoreline, who won or lost, a nation, a player or a number. The following are banned dead: "wait", "no,", "actually", "scratch that", "my mistake", "hang on", "correction", and any mid-sentence U-turn such as "Ivory Coast's defeat of Germany — wait, no, Germany won" (a real, embarrassing example — never do this). Settle every fact in your head BEFORE you write the sentence, get it right the first time, and state it once with total confidence. Every scoreline, result and points figure is in the data you are given — read it carefully and report it correctly; do not rely on memory and then walk it back on the page. If you genuinely cannot pin a fact down, write around it or leave it out — say less, never stumble. The column must read as slick, assured and witty from first word to last; a single visible correction ruins the whole effect.

You are given the raw text of the league standings/fixtures page and, under "each_managers_own_squad", every manager's squad keyed by that manager's name — each entry lists only THAT manager's players with their country and price. The standings/fixtures text also gives each game's status (FULL TIME, LIVE, or a future kickoff time) and date. READ it and WRITE the column from it.

READING PLAYER POINTS — CRITICAL. For an IN-SCOPE player (country code in matchday_nations), each player line shows TWO point figures: (1) their points for THIS round / gameweek — usually shown with a "w" (e.g. "15w") or under a "WK"/"Week" column — and (2) their cumulative SEASON total (the second, usually larger number, e.g. "16"). Use ONLY the this-round / gameweek figure as their points for yesterday; NEVER report the season total as yesterday's points — that is the single most common mistake. An in-scope player on 0 genuinely blanked yesterday (fair game to say so). Players who are NOT in matchday_nations are out of scope entirely: do not name them and never call them blanks.

There is NO captaincy in this league. Never mention captains, armbands, "captain picks" or anything of the sort — it does not exist here.

STRICT PLAYER OWNERSHIP — THE MOST IMPORTANT RULE: every player belongs to exactly ONE manager — the one under whose name they appear in "each_managers_own_squad". Before writing a manager's entry, look at THAT manager's player list, and treat it as a closed whitelist: you may name ONLY players from that exact list, with the exact points shown beside them. Do NOT rely on your own knowledge of football to decide who a player belongs to — a famous player (e.g. Lionel Messi) belongs to whichever manager's list actually contains him, and to NO ONE ELSE. Never put a player in a manager's write-up unless that player's name physically appears in that manager's own list. Never invent players or points. When in doubt, leave a player out.

EVERY MANAGER WRITE-UP MUST INCLUDE (this is the whole point — never omit it):
- Their haul from yesterday's matchday (the total from their IN-SCOPE players only) and their current league position.
- The standout in-scope players BY NAME with their points — who hauled and who blanked. Use ONLY real names and points from that manager's own squad list; do NOT copy any names from this instruction. (Shape to aim for: "<their top scorer> led the way with <pts>, <another> chipped in <pts>, while <a player> drew a blank" — filled only with that manager's actual in-scope players and actual points.) This player-by-player detail is the most important content.
- How yesterday moved them in the table (climbed, slipped, held), and a dig at a rival where the standings invite it.
- If a manager had NO players from yesterday's nations, say so plainly and wittily (nobody of theirs was on the pitch) and just note their league position — do NOT invent points or reach for players from other days.

TONE: cheeky and unapologetically take-the-piss — a local-paper columnist who knows everyone personally and never misses a chance to wind them up. Lean HARD into the ribbing: mock the vanity-priced flops, the duds, the bottom-half flailing, the two-point manager strutting like he's won the thing. Lean on each manager's character — but ONLY in the context of the games and their fantasy decisions, NEVER as standalone biography. Everyone in this private league already knows exactly who everyone is, so do NOT introduce or describe a manager for the reader's benefit. NO gratuitous character sketches (e.g. never write things like "Tristan, the big fiercely competitive Russian-raised Londoner who throws his hands up in disgust when things go wrong" — that's pointless throat-clearing, and it's also wrong). Only reach for a trait when a result, pick or scoreline gives you a way to land the joke THROUGH it; if a quirk doesn't connect to what actually happened on the pitch, leave it out entirely. Two standing favourites to come back to whenever they fit: (1) Joe S / "Sheerin" — a gifted flair player in his day whose career was wrecked by one injury after another, and who is hopelessly baffled by modern tech (the internet, apps, AI); rib the glass-bones playing days and the technophobia. (2) Jake / "Snakey" — who blew almost his entire budget on Harry Kane (£33.4m) and filled the rest of his squad on a shoestring; rib the Kane splurge hard. The "who on earth is that?" gag is fair game ONLY for his genuinely obscure, bargain-priced no-names — NOT for well-known players (he plainly knows the likes of Crysencio Summerville, Bruno Guimarães and other recognised names), and it is NOT embarrassing or surprising for Jake to score points through players other than Kane, so never frame his non-Kane returns that way. Jake is the designated whipping boy — give him the biggest digs of all. CHARACTER OWNERSHIP — IMPORTANT: each manager's traits and quirks belong ONLY to that manager; never transplant one manager's joke onto another. ONLY Sheerin is the injury-prone technophobe; ONLY Jake overspent on Kane; and so on — match every personality detail to the correct manager from the profiles, never another. Keep it affectionate ribbing between mates — sharp and merciless about the football and the decisions, but never genuinely cruel and nothing below the belt. About 115-175 words per manager.

ACCURACY RULE: only IN-SCOPE players (country in matchday_nations) can have scored yesterday; an in-scope player on 0 is a genuine blank. The "top_haul", "bargain" and "flop" notes must each name an in-scope player.

Provide your answer ONLY by calling the publish_roundup tool — no prose outside the tool call, no narration. Fill these fields:
- matchday_label: use the supplied recap_matchday_label verbatim.
- status_live: false (yesterday's games are finished).
- standings: every team with its manager and cumulative total, ordered 1st to last.
- still_to_play: a short teaser of TONIGHT'S fixtures, supplied as tonight_fixtures, or 'Everyone's arrived.' if none.
- notes.top_haul / notes.bargain / notes.flop: each "player (manager) — pts", chosen ONLY from in-scope players (country in matchday_nations); bargain = best points-per-million, flop = worst return for price.
- lead: a one-paragraph scene-setter on yesterday's matchday — name yesterday's fixtures only, never any others (may include <b>…</b>).
- articles: one per manager in standings order, each with manager, headline and body. The body is FINISHED newspaper prose only — never your reasoning or the scoping jargon (see SILENT SCOPING).
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

SLUG_BY_MANAGER = {mgr: (slug, name) for slug, (name, mgr) in TEAM_BY_SLUG.items()}

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

SCHEDULE_PATH = "schedule.json"

def _matchday_key(date_str, time_str):
    # A fixture's matchday = the tournament (US) calendar day. On the UK clock
    # a day's slate runs from evening into the next morning, so shifting back
    # 12h files an overnight kickoff (e.g. 01:30) under the right evening's day.
    dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return (dt - datetime.timedelta(hours=12)).date().strftime("%Y-%m-%d")

def load_schedule(standings_text):
    # Self-updating fixture calendar. Starts from the hand-verified seed, merges
    # anything captured on earlier runs, then captures today's still-upcoming
    # fixtures (they carry dates on the page; finished games lose them). So the
    # calendar extends itself through round 3 and the knockouts with no manual
    # input — each fixture is recorded while it's still in the future.
    sched = {k: [list(t) for t in v] for k, v in SCHEDULE.items()}
    try:
        with open(SCHEDULE_PATH, encoding="utf-8") as f:
            for k, fxs in json.load(f).items():
                bucket = sched.setdefault(k, [])
                for fx in fxs:
                    if list(fx) not in bucket:
                        bucket.append(list(fx))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  warn: reading {SCHEDULE_PATH}: {e}", file=sys.stderr, flush=True)
    for h, tm, a, d in re.findall(
            r"\b([A-Z]{3})\s+(\d{1,2}:\d{2})\s+([A-Z]{3})\s+(\d{4}-\d{2}-\d{2})", standings_text):
        bucket = sched.setdefault(_matchday_key(d, tm), [])
        if [h, a] not in bucket:
            bucket.append([h, a])
    try:
        with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
            json.dump(sched, f, sort_keys=True, indent=0)
    except Exception as e:
        print(f"  warn: writing {SCHEDULE_PATH}: {e}", file=sys.stderr, flush=True)
    return sched

def write_copy(standings_text, squads, teams=None):
    # Work out yesterday's matchday from the self-updating calendar, so the
    # report is scoped to EXACTLY that day's games — the only points in play
    # yesterday — and can't bleed into earlier days of the same round.
    today = datetime.datetime.utcnow().date()
    recap = today - datetime.timedelta(days=1)
    sched = load_schedule(standings_text)
    y_key = recap.strftime("%Y-%m-%d")
    y_fixtures = sched.get(y_key, [])
    y_nations = sorted({c for fx in y_fixtures for c in fx})
    # Nations that played EARLIER in the round still carry this-round points in
    # the squad lists. They did NOT play yesterday, so they are out of scope.
    earlier_nations = sorted({c for d, fxs in sched.items() if d < y_key
                              for fx in fxs for c in fx} - set(y_nations))
    t_fixtures = sched.get(today.strftime("%Y-%m-%d"), [])
    recap_label = "Matchday · " + recap.strftime("%a %d %b")

    # DETERMINISTIC SCOPING — the real fix for wrong-day bleed. If we have the
    # structured squads, compute each manager's scoreboard IN CODE: only their
    # players whose nation actually played the recap day, each with that day's
    # points and a pre-summed haul. The writer is then handed THIS and only this,
    # so a player from another day (e.g. an overnight Turkey game) is physically
    # absent from its data and cannot be named no matter how it's tempted.
    scoreboard = None
    if teams:
        yset = set(y_nations)
        scoreboard = {}
        for t in teams:
            mgr = t.get("manager")
            if not mgr:
                continue
            played = [
                {"name": p.get("name", ""),
                 "nation": (p.get("nation") or "").strip().upper(),
                 "points": int(p.get("round") or 0)}
                for p in t.get("players", [])
                if (p.get("nation") or "").strip().upper() in yset
            ]
            played.sort(key=lambda x: -x["points"])
            scoreboard[mgr] = {"yesterday_haul": sum(x["points"] for x in played),
                               "players_who_played": played}

    hauls = {m: sb["yesterday_haul"] for m, sb in scoreboard.items()} if scoreboard else {}

    payload = {
        "todays_date_utc": today.strftime("%Y-%m-%d (%A)"),
        "recap_date": recap.strftime("%A %d %B %Y"),
        "recap_matchday_label": recap_label,
        "matchday_fixtures": ", ".join(f"{h} v {a}" for h, a in y_fixtures) or "(none on record)",
        "matchday_nations": y_nations,
        "already_played_earlier": earlier_nations,
        "tonight_fixtures": ", ".join(f"{h} v {a}" for h, a in t_fixtures) or "(none on record)",
        "manager_profiles": MANAGERS,
        "relationships": RELATIONSHIPS,
        "standings_and_fixtures_page_text": standings_text,
    }
    if scoreboard is not None:
        # Structured, already-scoped player data — the safe path. Out-of-day
        # players simply aren't here, so the writer can't reach for them.
        payload["each_managers_own_squad"] = scoreboard
    else:
        # Fallback only (squad extraction unavailable): raw text, AI does scoping.
        payload["each_managers_own_squad"] = squads
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
            data["matchday_label"] = recap_label
            if len(data.get("articles", [])) < 10 or len(data.get("standings", [])) < 10:
                raise ValueError("too few managers in tool output")
            return data, hauls
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

SQUAD_SYSTEM = """You extract structured squad data for a fantasy football league. Under "each_managers_squad_text" you are given every manager's squad as raw page text. The pages use MANY different layouts — read each carefully. For EVERY manager, list EVERY player with these fields:
- name: the player's name.
- position: one of GK, DEF, MID, FWD.
- nation: the player's 3-letter country code IF the page shows it; if the page does not show nationalities at all, use "".
- price: the price exactly as shown, e.g. "£5.7m".
- round: their points THIS round / gameweek as an integer (the "w"/"WK"/"Week" figure; 0 if none).
- total: their cumulative SEASON points as an integer (the larger figure).
Layout hints: many entries read "Name POS • NAT • £5.7m • Since GW1" then "0w 2" (0 = round, 2 = season). Some are tables like "GK Yassine Bounou MAR 4.7 1 0 3" = position, name, nation, cost (= £4.7m), acquired-GW, round-points, total-points. Some pages omit nationalities entirely (use ""). NEVER invent a nationality the page doesn't show. Return ONLY by calling the publish_squads tool, using the manager FIRST NAMES exactly as provided."""

def extract_squads(squads):
    tool = {
        "name": "publish_squads",
        "description": "Return every manager's full squad as structured data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "teams": {"type": "array", "items": {"type": "object", "properties": {
                    "manager": {"type": "string"},
                    "players": {"type": "array", "items": {"type": "object", "properties": {
                        "name": {"type": "string"},
                        "position": {"type": "string"},
                        "nation": {"type": "string"},
                        "price": {"type": "string"},
                        "round": {"type": "integer"},
                        "total": {"type": "integer"},
                    }, "required": ["name", "position", "nation", "price", "round", "total"]}},
                }, "required": ["manager", "players"]}},
            },
            "required": ["teams"],
        },
    }
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=600.0, max_retries=1)
    payload = {"each_managers_squad_text": squads}
    last = None
    for attempt in range(2):
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=16000, temperature=0,
                system=SQUAD_SYSTEM, tools=[tool],
                tool_choice={"type": "tool", "name": "publish_squads"},
                messages=[{"role": "user", "content": json.dumps(payload)}],
            ) as stream:
                msg = stream.get_final_message()
            for block in (msg.content or []):
                if getattr(block, "type", None) == "tool_use" and isinstance(block.input, dict):
                    return block.input.get("teams", [])
            raise RuntimeError("no publish_squads tool call")
        except Exception as e:
            last = e
            print(f"  squad extract attempt {attempt+1} failed: {e}", file=sys.stderr, flush=True)
            time.sleep(8)
    raise RuntimeError(f"squad extraction failed: {last}")

def squad_page_html(team, manager, players):
    order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    players = sorted(players, key=lambda p: (order.get(str(p.get("position", "")).upper(), 9),
                                             -int(p.get("total") or 0)))
    rows = ""
    for p in players:
        pos = str(p.get("position", "")).upper()
        rows += (f'<tr data-p="{esc(p.get("name", ""))}" data-r="{int(p.get("round") or 0)}" data-t="{int(p.get("total") or 0)}">'
                 f'<td class="pos {esc(pos)}">{esc(pos)}</td>'
                 f'<td class="pn">{esc(p.get("name", ""))}</td>'
                 f'<td class="nat">{esc(p.get("nation", "") or "—")}</td>'
                 f'<td class="num">{esc(p.get("price", ""))}</td>'
                 f'<td class="num rd">{esc(p.get("round", 0))}</td>'
                 f'<td class="num tot">{esc(p.get("total", 0))}</td></tr>')
    season = sum(int(p.get("total") or 0) for p in players)
    rnd = sum(int(p.get("round") or 0) for p in players)
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
            f"<title>{esc(team)} — Squad</title><style>"
            ":root{--bg:#0a0e14;--line:#1b2636;--ink:#e8eef6;--mut:#7e8ca0;--cyan:#22d3ee;--amber:#f5c451;"
            "--gk:#f5c451;--def:#34d399;--mid:#22d3ee;--fwd:#f472b6;}"
            "*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--ink);"
            "font-family:Arial,Helvetica,sans-serif;line-height:1.5;}"
            ".wrap{max-width:760px;margin:0 auto;padding:18px 16px 60px;}"
            "a.back{color:var(--mut);text-decoration:none;font-size:13px;}a.back:hover{color:var(--cyan);}"
            "h1{font-size:26px;margin:14px 0 2px;font-weight:900;letter-spacing:-.4px;}"
            ".sub{color:var(--mut);font-size:13px;}"
            ".stats{display:flex;gap:24px;margin:16px 0 18px;}"
            ".stat b{display:block;font-size:22px;color:var(--cyan);font-variant-numeric:tabular-nums;}"
            ".stat span{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--mut);}"
            "table{width:100%;border-collapse:collapse;}"
            "th{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--mut);text-align:left;"
            "padding:7px 8px;border-bottom:1px solid var(--line);}th.num,td.num{text-align:right;}"
            "td{padding:9px 8px;border-bottom:1px solid var(--line);font-size:14px;}"
            ".pos{font-weight:800;font-size:11px;width:34px;color:var(--mut);}"
            ".pos.GK{color:var(--gk);}.pos.DEF{color:var(--def);}.pos.MID{color:var(--mid);}.pos.FWD{color:var(--fwd);}"
            ".pn{font-weight:600;}.nat{color:var(--mut);font-size:12px;width:44px;}"
            ".num{font-variant-numeric:tabular-nums;}.rd{color:var(--amber);font-weight:700;}.tot{color:var(--cyan);font-weight:800;}"
            "footer{margin-top:24px;border-top:1px solid var(--line);padding-top:12px;color:var(--mut);"
            "font-size:11px;text-transform:uppercase;letter-spacing:.14em;}"
            "</style></head><body><div class=\"wrap\">"
            "<a class=\"back\" href=\"./\">← League table</a>"
            f"<h1>{esc(team)}</h1><div class=\"sub\">Managed by {esc(manager)}</div>"
            f"<div class=\"stats\"><div class=\"stat\"><b id=\"sq-se\">{season}</b><span>Season pts</span></div>"
            f"<div class=\"stat\"><b id=\"sq-rd\">{rnd}</b><span>This round</span></div>"
            f"<div class=\"stat\"><b>{len(players)}</b><span>Squad</span></div></div>"
            "<table><thead><tr><th>Pos</th><th>Player</th><th>Nat</th>"
            "<th class=\"num\">Price</th><th class=\"num\">Rnd</th><th class=\"num\">Total</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            "<footer>TRM Fantasy · World Cup 2026 · points update live during games; squad &amp; prices daily</footer>"
            "<script>(function(){var W=\"https://trm-live.dapperdon.workers.dev\";"
            f"var M={json.dumps(manager)};"
            "function poll(){fetch(W,{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){var live={};"
            "(d.fixtures||[]).forEach(function(f){(f.players||[]).forEach(function(p){if(p.manager===M&&p.pts!=null)live[p.name]=p.pts;});});"
            "var tot=0,rnd=0;[].forEach.call(document.querySelectorAll('tr[data-p]'),function(tr){"
            "var br=+tr.getAttribute('data-r'),bt=+tr.getAttribute('data-t');"
            "var lv=live[tr.getAttribute('data-p')];var r=(lv!=null)?lv:br;var t=bt-br+r;"
            "var rc=tr.querySelector('.rd'),tc=tr.querySelector('.tot');if(rc)rc.textContent=r;if(tc)tc.textContent=t;rnd+=r;tot+=t;});"
            "var se=document.getElementById('sq-se'),sr=document.getElementById('sq-rd');if(se)se.textContent=tot;if(sr)sr.textContent=rnd;"
            "}).catch(function(){});}"
            "poll();setInterval(poll,60000);})();</script>"
            "</div></body></html>")

def write_squad_pages(teams):
    out_dir = os.path.dirname(OUTPUT_PATH)
    n = 0
    for t in teams:
        info = SLUG_BY_MANAGER.get(t.get("manager"))
        if not info:
            continue
        slug, name = info
        with open(os.path.join(out_dir, f"team-{slug}.html"), "w", encoding="utf-8") as f:
            f.write(squad_page_html(name, t.get("manager"), t.get("players", [])))
        n += 1
    return n

def main():
    # Reliability: the schedule fires at a couple of backup times in case
    # GitHub silently drops the first one. So that the backups don't rebuild a
    # day that's already done, a SCHEDULED run skips if today's edition is
    # already published. Manual ("workflow_dispatch") runs always rebuild, so
    # you can still force a refresh or a fix whenever you like.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        recap = datetime.datetime.utcnow().date() - datetime.timedelta(days=1)
        recap_label = "Matchday · " + recap.strftime("%a %d %b")
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                if recap_label in f.read():
                    print(f"Already built for {recap_label}; skipping this scheduled run.", flush=True)
                    return
        except FileNotFoundError:
            pass
    print("Fetching league pages...", flush=True)
    standings_text, squads = gather()
    # Extract structured squads UP FRONT. Used both to scope the roundup
    # deterministically (out-of-day players are removed before the writer ever
    # sees them) AND to build the squad pages / nation map further down.
    try:
        print("Extracting structured squads...", flush=True)
        teams = extract_squads(squads)
        print(f"  extracted {len(teams)} squads", flush=True)
    except Exception as e:
        teams = None
        print(f"  warn: squad extraction failed; roundup falls back to text scoping: {e}",
              file=sys.stderr, flush=True)
    print(f"Writing the column with Claude... ({len(squads)} squads gathered)", flush=True)
    data, hauls = write_copy(standings_text, squads, teams)
    html = render(data)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH} ({len(data['articles'])} managers)", flush=True)

    # Per-matchday table movement, computed deterministically from the day's
    # hauls. The table BEFORE yesterday's games = each manager's current total
    # minus what they scored yesterday; ranking that gives the "before" order.
    # The live page compares it with the current ranks to draw the arrows. No
    # saved snapshots and no day-long warm-up — it's correct on the first build.
    try:
        if not hauls:
            raise RuntimeError("no per-manager hauls (needs structured squads)")
        totals = {s["manager"]: int(s.get("total", 0)) for s in data["standings"]}
        before = {m: totals[m] - int(hauls.get(m, 0)) for m in totals}
        order = sorted(before, key=lambda m: (-before[m], m))
        base_ranks = {m: i + 1 for i, m in enumerate(order)}
        with open(os.path.join(os.path.dirname(OUTPUT_PATH), "baseline.json"), "w", encoding="utf-8") as f:
            json.dump({"ranks": base_ranks}, f)
        print(f"Wrote baseline.json (pre-matchday ranks for {len(base_ranks)} teams)", flush=True)
    except Exception as e:
        print(f"  warn: movement baseline skipped: {e}", file=sys.stderr, flush=True)

    # Native squad pages (clean restyle of each team's squad). Wrapped so a
    # squad-extraction hiccup can never break the main roundup build.
    try:
        if not teams:
            raise RuntimeError("no structured squads available this run")
        n = write_squad_pages(teams)
        print(f"Wrote {n} squad pages", flush=True)

        # Owned players grouped by 3-letter nation code. The source site only
        # attaches owned players to a fixture once it's live/finished, so before
        # kickoff the live board can't read them from the feed. This map lets the
        # page compute "to play" (squad nations vs nations with a match left this
        # round) and pre-kickoff fixture player lists from the squads instead.
        nat = {}
        for t in teams:
            mgr = t.get("manager")
            for p in t.get("players", []):
                code = (p.get("nation") or "").strip().upper()
                if len(code) == 3 and code.isalpha():
                    nat.setdefault(code, []).append({"name": p.get("name", ""), "manager": mgr})
        with open(os.path.join(os.path.dirname(OUTPUT_PATH), "owned-by-nation.json"), "w", encoding="utf-8") as f:
            json.dump(nat, f)
        print(f"Wrote owned-by-nation.json ({len(nat)} nations)", flush=True)
    except Exception as e:
        print(f"  warn: squad pages skipped this run: {e}", file=sys.stderr, flush=True)

if __name__ == "__main__":
    main()
