#!/usr/bin/env python3
"""
The Morning After — daily fantasy roundup generator.

Reads the league's own JSON feed (the Cloudflare worker), which already tags
every fixture with its calendar matchday and attributes every owned player to a
manager with exact points. The script:
  1. picks the LATEST completed matchday (the most recent day of finished games),
  2. computes each manager's points FROM THAT DAY's fixtures only, in Python,
  3. hands those exact numbers to Claude purely to write the witty prose,
  4. renders template.html -> docs/roundup.html.

Because the day's data is computed deterministically from the feed, the report
is always scoped to a single matchday and the points/players can't drift.
Only secret needed: ANTHROPIC_API_KEY.
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request

import anthropic

FEED_URL = "https://trm-live.dapperdon.workers.dev"
TEMPLATE_PATH = "template.html"
OUTPUT_PATH = "docs/roundup.html"
MODEL = "claude-sonnet-4-6"
UA = "Mozilla/5.0 (compatible; TRM-Roundup/1.0; +https://github.com)"

MANAGERS = {
    "Joe S":   "Back of the Van United — real name Sheerin; a universally popular, supremely gifted ex-pro footballer whose flair career was repeatedly wrecked by injuries — he spent at least half of it stuck in the physio room on the treatment table rather than on the pitch, which is endlessly worth taking the piss out of; loved the party-boy lifestyle as much as the game; utterly baffled by modern tech.",
    "Sam":     "Look at his face. Just Look at his FACE! — expressive stage & TV performer; loves beer, dancing, music; witty, scatty; loves Shakespeare even more than football; brother of Wigs; a cricket man.",
    "Joe A":   "Shatner's Bassoon — a laid-back actor who loves the easy life and can be lazy, but make NO mistake he is genuinely keen to do well and win the league like everyone else; rib the laziness/easygoing streak, but never portray him as disinterested or unbothered about his results; main rival is Tristan.",
    "Tom":     "Anamaduwa Athletic — party animal and dance-music DJ, constantly away travelling and at festivals. ROTATE the running gags about him and do NOT reuse the same one each edition — in particular do NOT always say he is in a different time zone eating curry. Mix it up across: at a psy-trance festival in a field of hippies; asleep on a long-haul flight at 10,000 feet over somewhere far-flung; genuinely unsure which country or time zone he is in; eating curry with his bare hands; or the running fact that he has never actually won this league despite years of trying. Main rival is Nick.",
    "Dave":    "Trossy's Giants — aka 'Trossy Ginge'; lecturer and poet; loves wordplay and puns; city-break traveller; food, beer and cigarettes.",
    "Wigs":    "50 Shades of O'Shea — counsellor; gregarious, gentle and witty; loves cricket as well as football; brother of Sam.",
    "Jeremy":  "Von Neumann Trombone — 'the professor'; programmer, super-smart and witty; historically a top fantasy manager; a niggling 5-a-side tackler; measured 'Swiss' type.",
    "Nick":    "Dyer's Rusty 9 Iron — 'rusty iron' because he hammers his shots high over the crossbar like a wild golf swing; very tall, loud deep voice; on the football pitch a reckless menace who flies into clumsy, dangerous, badly mistimed tackles — frequently on purpose, leaving opponents in his wake; loves his beer and food; rib him for the wayward shooting and the reckless, dangerous tackling, NOT for tactics; main rival is Tom.",
    "Dan":     "Denton Burn — musician who lives off-grid; smart, alternative, very witty; historically a top fantasy player; main ally is Malik.",
    "Chris":   "Lloyd's Food and Wine — aka 'Lloydy'; tall, eclectic, never sits still (biking, travelling, dancing); builds his own electrical kit; the 'mad scientist' to Jeremy's professor; main rival is Jake.",
    "Tristan": "Trippier & Trippier — big Russian guy raised in London; loves football and sweeties; witty but doesn't suffer fools; throws his hands up in disgust; main rival is Joe A.",
    "Malik":   "Propaganda Parade — quirky, smart Icelandic man managing from afar; signs anyone who has worn a Manchester United or Portugal shirt; main ally is Dan.",
    "Jake":    "Snacob's Ladder — renewable-energy project manager; loves wind turbines and mushrooms; never stops; overspent badly on Harry Kane and filled the rest with cheap unknowns; plays psy-trance like Tom; main rival is Chris (Lloydy). Carries two grudges worth airing when apt: (1) he bitterly resents Jeremy for swapping out an injured player Jake had bought and replacing him with an uninjured alternative, when every other manager had been happy to let the honest mistake be rectified; (2) he despises Lloydy's use of AI and indulges paranoid fantasies about what Lloydy is secretly cooking up with AI in his private life and across his business property — his personal office complex.",
}
RELATIONSHIPS = ("Joe A vs Tristan (rivals), Tom vs Nick (rivals), Chris vs Jake (rivals), "
                 "Malik & Dan (allies), Sam & Wigs (brothers).")

# Display rename(s): the league source uses a very long team name; shorten it
# everywhere the roundup shows it (kept in sync with the Live page).
TEAM_RENAME = {"Look at his face. Just Look at his FACE!": "Look At His Face!"}
def disp_team(t):
    return TEAM_RENAME.get(t, t)

SYSTEM_PROMPT = """You write "The Morning After", the daily bulletin of a private 13-manager World Cup 2026 fantasy football league. It recaps ONE matchday — the most recent day of completed fixtures.

You are handed already-computed, already-correct figures: the day's finished fixtures with scores, and for each manager the exact players of theirs who featured in those fixtures with their exact points (and which blanked). You do NOT calculate anything and you do NOT decide who owns whom — just turn the supplied numbers into lively prose.

HARD RULES:
- Use ONLY the players and points supplied for that manager. Never add a player who is not in their supplied list. Never change a points value. Never invent.
- "haul" is that manager's total for THIS matchday (already summed for you). Lead with it.
- NO CAPTAINCY: this league has no captain feature whatsoever. Managers only pick a squad and score from the best 11; nobody selects a captain. NEVER mention captains, a captain pick, the armband, vice-captains, or doubling/multiplying a player's points. That feature does not exist here.
- A player marked played=false did NOT feature (bench/unused). You may state this plainly and neutrally where relevant — e.g. to help explain a thin matchday haul — by simply noting they did not play ("X was an unused sub", "X didn't feature"). Attach NO qualitative judgement to a non-appearance: never frame it as "no harm done", harmless or fine, and never call it harmful, embarrassing, poor or costly either. It is a cold, neutral fact, nothing more. A player who DID play and returned 0 or negative IS a blank, and that is fair game for ribbing.
- Mention current league position/total only as context.

TONE: dry, deadpan, affectionate ribbing; economical (~90-130 words per manager). Lean on each manager's character sparingly; let the football do the work. Use first names exactly as given.

Keep any thinking extremely brief. Output ONLY the requested JSON value at the very end, no code fences."""


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


def get_feed():
    return json.loads(fetch(FEED_URL))


def pretty_day(iso):
    try:
        return datetime.datetime.strptime(iso, "%Y-%m-%d").strftime("%A %d %B")
    except Exception:
        return iso or ""


def build_brief(feed):
    """Deterministically reduce the feed to a single-matchday brief."""
    standings = sorted(feed.get("standings", []), key=lambda s: s.get("rank", 99))
    fixtures = feed.get("fixtures", [])
    finished = [f for f in fixtures if f.get("status") == "finished" and f.get("matchday")]
    if not finished:
        # No completed, matchday-tagged fixtures in the feed yet (e.g. mid-round with
        # games still live, or the brief gap between rounds). Nothing to recap — signal
        # the caller to exit cleanly rather than crashing the workflow.
        return None
    # Prefer the latest FULLY-complete matchday so we never publish a half-done
    # slate (e.g. an evening's late kickoffs still to play). The worker already
    # groups post-midnight kickoffs into the correct evening via a 12h shift.
    days = {}
    for f in fixtures:
        d = f.get("matchday")
        if d:
            days.setdefault(d, []).append(f)
    complete = [d for d, fs in days.items() if all(x.get("status") == "finished" for x in fs)]
    if not complete:
        # No fully-finished day in the feed yet — e.g. the start of a round/stage when
        # only some of the current day's games are done. NEVER publish a half-finished
        # slate: bail out cleanly and let a later run publish once the day completes.
        return None
    target = max(complete)
    md = [f for f in finished if f["matchday"] == target]

    mgr = {}
    for s in standings:
        mgr[s["manager"]] = {
            "manager": s["manager"], "team": disp_team(s["team"]), "rank": s["rank"],
            "total": s["total"], "round": s.get("round"), "remaining": s.get("remaining", 0),
            "haul": 0, "players": [],
        }
    pool = []
    fixture_lines = []
    for f in md:
        line = f'{f["home"]} {f.get("score","")} {f["away"]}'
        fixture_lines.append(line)
        for p in f.get("players", []):
            m = p.get("manager")
            if m not in mgr:
                continue
            played = p.get("pts") is not None
            pts = p.get("pts") or 0
            rec = {"name": p["name"], "pts": pts, "goals": p.get("goals", 0),
                   "assists": p.get("assists", 0), "fixture": f'{f["home"]}-{f["away"]}',
                   "played": played}
            mgr[m]["players"].append(rec)
            if played:
                mgr[m]["haul"] += pts
            pool.append({**rec, "manager": m})

    played_pool = [r for r in pool if r["played"]]
    top = max(played_pool, key=lambda r: r["pts"]) if played_pool else None

    upcoming = [f for f in fixtures if f.get("status") != "finished"]
    still = [f'{f["home"]}-{f["away"]}' for f in upcoming]

    return {
        "round_label": feed.get("matchday") or "World Cup 2026",
        "matchday_date": target,
        "matchday_day_label": pretty_day(target),
        "fixtures": fixture_lines,
        "managers_in_order": [s["manager"] for s in standings],
        "standings": [{"team": disp_team(s["team"]), "manager": s["manager"], "total": s["total"]} for s in standings],
        "by_manager": mgr,
        "top_haul_player": top,
        "day_player_pool": played_pool,
        "still_to_play": still,
    }


def _loads(text, array=False):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    o, c = ("[", "]") if array else ("{", "}")
    end = text.rfind(c)
    if end == -1:
        raise ValueError("no closing bracket in reply")
    err = None
    for s, ch in enumerate(text):
        if ch != o:
            continue
        try:
            return json.loads(text[s:end + 1])
        except Exception as e:
            err = e
    raise ValueError(f"no parseable JSON found ({err})")


def _dump_raw(label, text, stop):
    t = (text or "").replace("\n", " ")
    print(f"  [debug] {label}: raw_len={len(text or '')} stop_reason={stop}", file=sys.stderr, flush=True)
    print(f"  [debug] head: {t[:900]}", file=sys.stderr, flush=True)
    print(f"  [debug] tail: {t[-500:]}", file=sys.stderr, flush=True)


def _ask(client, task, payload, max_tokens, array=False, tries=2):
    user = f"{task}\n\n<data>\n{json.dumps(payload)}\n</data>"
    last = None
    for attempt in range(tries):
        t0 = time.time()
        text, stop = "", None
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=max_tokens, temperature=0.6,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                msg = stream.get_final_message()
            stop = getattr(msg, "stop_reason", None)
            text = "".join(getattr(b, "text", "") for b in (msg.content or [])).strip()
            print(f"    call done in {time.time()-t0:.1f}s (stop={stop}, len={len(text)})", flush=True)
            if not text:
                raise RuntimeError(f"empty response (stop={stop})")
            return _loads(text, array=array)
        except Exception as e:
            last = e
            _dump_raw(f"attempt {attempt+1} failed: {e}", text, stop)
            time.sleep(6)
    raise RuntimeError(f"call failed after {tries} tries: {last}")


def _chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def write_copy(brief):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=600.0, max_retries=1)

    # 1) Frame: lead + notes (phrasing only; figures come from the brief).
    frame_task = (
        "TASK: Write the scene-setting LEAD and the three sidebar NOTES for this edition, which recaps the "
        f"matchday of {brief['matchday_day_label']} ({brief['round_label']}).\n"
        "The day's finished fixtures and the day's player pool (name, manager, pts, goals, assists) are in the data. "
        "top_haul_player is the single best score of the day — use it for top_haul. For bargain pick a strong return "
        "from a less-glamorous name in the pool; for flop name a recognisable player from the pool who blanked "
        "(pts 0 or less). Use ONLY players in day_player_pool, with their exact points.\n"
        "Return ONLY this JSON at the end (no code fences):\n"
        '{ "lead": "<one punchy paragraph on the day\'s action, may use <b>..</b>>",'
        ' "notes": { "top_haul": "<Player (Manager) — N pts>", "bargain": "<Player (Manager) — N pts>",'
        ' "flop": "<Player (Manager) — blank>" } }'
    )
    frame_payload = {
        "round_label": brief["round_label"],
        "matchday_day_label": brief["matchday_day_label"],
        "fixtures": brief["fixtures"],
        "top_haul_player": brief["top_haul_player"],
        "day_player_pool": brief["day_player_pool"],
        "still_to_play": brief["still_to_play"],
    }
    frame = _ask(client, frame_task, frame_payload, max_tokens=3000, array=False)

    # 2) Articles in small batches; each manager gets their exact computed figures.
    order = brief["managers_in_order"]
    articles = []
    for batch in _chunks(order, 4):
        records = []
        for m in batch:
            r = brief["by_manager"][m]
            records.append({
                "manager": m, "team": r["team"], "rank": r["rank"], "total": r["total"],
                "haul_this_matchday": r["haul"], "remaining_to_play": r["remaining"],
                "profile": MANAGERS.get(m, ""),
                "their_players_today": r["players"],
            })
        task = (
            "TASK: Write the per-manager write-ups for these managers, in this exact order: "
            f"{', '.join(batch)}.\n"
            f"This edition recaps the matchday of {brief['matchday_day_label']}. RELATIONSHIPS: {RELATIONSHIPS}\n"
            "For each manager use ONLY their_players_today (name, pts, goals, assists, played). Lead with "
            "haul_this_matchday and name who scored it / who blanked (played=true, pts<=0). If their_players_today "
            "is empty or all played=false, say it was a quiet matchday for them. ~90-130 words each.\n"
            "Return ONLY a JSON array at the end (no code fences) of exactly "
            f"{len(batch)} objects in that order:\n"
            '[ {"manager": "<first name>", "headline": "<short headline>", "body": "<~90-130 words>"} ]'
        )
        try:
            arts = _ask(client, task, {"managers": records}, max_tokens=2500 * len(batch), array=True)
            if not isinstance(arts, list) or len(arts) < len(batch):
                raise RuntimeError(f"batch returned {len(arts) if isinstance(arts,list) else '?'} of {len(batch)}")
            articles.extend(arts)
        except Exception as e:
            print(f"  batch failed ({e}); one at a time", file=sys.stderr, flush=True)
            for m in batch:
                r = brief["by_manager"][m]
                rec = {"manager": m, "team": r["team"], "rank": r["rank"], "total": r["total"],
                       "haul_this_matchday": r["haul"], "remaining_to_play": r["remaining"],
                       "profile": MANAGERS.get(m, ""), "their_players_today": r["players"]}
                one = (
                    f"TASK: Write the write-up for {m} only, for the matchday of {brief['matchday_day_label']}. "
                    "Use ONLY their_players_today; lead with haul_this_matchday. ~90-130 words. "
                    'Return ONLY a JSON array of one object: [ {"manager":"' + m + '","headline":"..","body":".."} ]'
                )
                arts = _ask(client, one, {"managers": [rec]}, max_tokens=2500, array=True)
                articles.extend(arts)

    print(f"  copy complete: {len(articles)} articles", flush=True)
    return {
        "matchday_label": f"{brief['round_label']} · {brief['matchday_day_label']}",
        "status_live": False,
        "standings": brief["standings"],
        "still_to_play": ", ".join(brief["still_to_play"]) if brief["still_to_play"] else "Everyone has played.",
        "notes": frame.get("notes", {}),
        "lead": frame.get("lead", ""),
        "articles": articles,
    }


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
                    f'<h2 class="head">{esc(a.get("headline",""))}</h2>'
                    f'<p>{a.get("body","")}</p>'
                    f'<div class="byline">The Morning After</div></article>')
    notes = data.get("notes", {})
    notes_rows = (
        f'<tr><td><span class="tname" style="color:var(--gold)">Top points haul</span>'
        f'<span class="mgr">{esc(notes.get("top_haul",""))}</span></td></tr>'
        f'<tr><td><span class="tname" style="color:var(--green)">Best-value pick</span>'
        f'<span class="mgr">{esc(notes.get("bargain",""))}</span></td></tr>'
        f'<tr><td><span class="tname" style="color:var(--magenta)">Notable blank</span>'
        f'<span class="mgr">{esc(notes.get("flop",""))}</span></td></tr>')
    md = data.get("matchday_label", "World Cup 2026")
    repl = {
        "{{MATCHDAY_LABEL}}": esc(md),
        "{{MATCHDAY_SHORT}}": esc(md.split("·")[-1].strip() if "·" in md else md),
        "{{DATE_LABEL}}": datetime.datetime.utcnow().strftime("%A %d %B %Y"),
        "{{STATUS_CHIP}}": "Matchday settled",
        "{{LEAD}}": f'<p class="lead">{data.get("lead","")}</p>',
        "{{ARTICLES}}": "\n".join(arts),
        "{{STANDINGS_ROWS}}": "\n".join(rows),
        "{{NOTES_ROWS}}": notes_rows,
        "{{STILL_TO_PLAY}}": esc(data.get("still_to_play", "")),
        "{{CAVEAT}}": "",
    }
    html = open(TEMPLATE_PATH, encoding="utf-8").read()
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def main():
    print("Fetching league feed...", flush=True)
    feed = get_feed()
    brief = build_brief(feed)
    if brief is None:
        print("No completed matchday in the feed yet — nothing to recap. Exiting cleanly.", flush=True)
        return
    print(f"Recapping matchday {brief['matchday_date']} ({brief['matchday_day_label']}): "
          f"{len(brief['fixtures'])} fixtures, {len(brief['day_player_pool'])} owned players played", flush=True)
    print("Writing the column with Claude...", flush=True)
    data = write_copy(brief)
    html = render(data)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH}  ({len(data['articles'])} managers)", flush=True)


if __name__ == "__main__":
    main()
