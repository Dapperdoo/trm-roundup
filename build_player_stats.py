#!/usr/bin/env python3
"""
build_player_stats.py
=====================
Builds docs/player-stats.json: a per-player KEY-ACTION log for the WC2026
tournament, sourced from API-Football (https://www.api-football.com/).

Each log entry is one match a player featured in, recording the raw actions we
care about: minutes, started/sub, goals, assists, cards, and a clean-sheet flag.
Clean sheets are credited ONLY when a GK/DEF played >= 60 minutes AND the team
conceded zero -- a late substitute defender is NOT credited (the rule Joe asked
for). Fantasy points themselves are NOT recomputed here; the squad page reads
this log and tallies the owned-games-only actions for display.

Design notes
------------
* Quota-aware. Free tier = 100 requests/day. We cache processed fixtures in the
  output file and only fetch NEW finished fixtures (capped per run by MAX_NEW).
  The fixtures-list call is throttled to once per LIST_INTERVAL seconds so the
  every-20-min workflow doesn't burn the daily budget.
* Never crashes the workflow. Any failure is swallowed, logged to
  docs/_playerstats_debug.txt, and the script exits 0.
* Auth: defaults to the direct api-sports.io endpoint (header x-apisports-key).
  Set API_FOOTBALL_RAPIDAPI=1 if the key came from RapidAPI instead.
"""

import os, re, sys, json, time, traceback, unicodedata, urllib.request, urllib.parse

OUT     = "docs/player-stats.json"
DEBUG   = "docs/_playerstats_debug.txt"
OWNED   = "docs/owned-players.json"

KEY       = os.environ.get("API_FOOTBALL_KEY", "").strip()
LEAGUE    = os.environ.get("API_FOOTBALL_LEAGUE", "1").strip()      # 1 = FIFA World Cup
SEASON    = os.environ.get("API_FOOTBALL_SEASON", "2026").strip()
USE_RAPID = os.environ.get("API_FOOTBALL_RAPIDAPI", "").strip() == "1"
MAX_NEW   = int(os.environ.get("PLAYERSTATS_MAX_NEW", "15"))        # player-calls per run
LIST_INTERVAL = int(os.environ.get("API_FOOTBALL_LIST_INTERVAL", "3600"))  # secs
REQ_SLEEP = float(os.environ.get("API_FOOTBALL_SLEEP", "6"))       # secs between calls

BASE = ("https://api-football-v1.p.rapidapi.com/v3" if USE_RAPID
        else "https://v3.football.api-sports.io")

FINISHED_STATUS = {"FT", "AET", "PEN"}   # match is over

# API-Football league.round -> our round number (GW). Group MD1/2/3 = 1/2/3,
# then knockouts. Used so the squad page can filter to owned-games-only.
ROUND_NUM = {
    "group stage - 1": 1, "group stage - 2": 2, "group stage - 3": 3,
    "round of 32": 4, "round of 16": 5,
    "quarter-finals": 6, "quarter finals": 6,
    "semi-finals": 7, "semi finals": 7,
    "3rd place final": 8, "third place final": 8, "final": 8,
}

_log_lines = []
def log(msg):
    _log_lines.append(str(msg))

def write_debug(status):
    try:
        with open(DEBUG, "w", encoding="utf-8") as f:
            f.write("STATUS: %s\n" % status)
            f.write("when: %s\n" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            f.write("endpoint: %s  league=%s season=%s rapidapi=%s\n"
                    % (BASE, LEAGUE, SEASON, USE_RAPID))
            f.write("\n".join(_log_lines) + "\n")
    except Exception:
        pass

def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def headers():
    if USE_RAPID:
        return {"x-rapidapi-key": KEY,
                "x-rapidapi-host": "api-football-v1.p.rapidapi.com"}
    return {"x-apisports-key": KEY}

def api(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    errs = data.get("errors")
    if errs:
        # API-Football returns errors as {} (none) or a dict/list of messages
        if isinstance(errs, dict) and errs:
            log("API errors for %s: %s" % (path, errs))
        elif isinstance(errs, list) and errs:
            log("API errors for %s: %s" % (path, errs))
    return data.get("response", []), data

def round_num(label):
    return ROUND_NUM.get(norm(label).replace("-", " ").strip(),
                         ROUND_NUM.get(norm(label)))

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def main():
    if not KEY:
        log("No API_FOOTBALL_KEY in environment -- nothing to do.")
        write_debug("SKIP (no key)")
        return

    state = load_json(OUT, {})
    if not isinstance(state, dict):
        state = {}
    players   = state.get("players", {})
    processed = set(state.get("processedFixtures", []))
    meta      = state.get("meta", {})

    # ---- throttle the fixtures-list call -------------------------------------
    last_list = meta.get("lastListFetch", 0)
    now = time.time()
    if LIST_INTERVAL and (now - last_list) < LIST_INTERVAL and processed:
        log("Within LIST_INTERVAL (%ss since last) -- skipping, no API calls."
            % int(now - last_list))
        write_debug("SKIP (throttled)")
        return

    # ---- 1) list fixtures ----------------------------------------------------
    try:
        fixtures, raw = api("/fixtures", {"league": LEAGUE, "season": SEASON})
    except Exception as e:
        log("fixtures list failed: %s" % e)
        write_debug("ERROR (fixtures list)")
        return
    log("fixtures returned: %d (results=%s)" % (len(fixtures), raw.get("results")))
    if not fixtures:
        log("No fixtures -- check league/season ids. Sample raw keys: %s"
            % list(raw.keys()))
        meta["lastListFetch"] = now
        state["meta"] = meta
        save(state, players, processed)
        write_debug("WARN (0 fixtures)")
        return

    # map fixtureId -> scoreline so we can derive clean sheets
    finfo = {}
    finished = []
    for fx in fixtures:
        fid   = fx.get("fixture", {}).get("id")
        st    = fx.get("fixture", {}).get("status", {}).get("short")
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        rnd   = fx.get("league", {}).get("round", "")
        hid = teams.get("home", {}).get("id"); aid = teams.get("away", {}).get("id")
        finfo[fid] = {
            "homeId": hid, "awayId": aid,
            "homeGoals": goals.get("home"), "awayGoals": goals.get("away"),
            "homeName": teams.get("home", {}).get("name"),
            "awayName": teams.get("away", {}).get("name"),
            "round": rnd, "roundNum": round_num(rnd),
        }
        if st in FINISHED_STATUS:
            finished.append(fid)

    new = [fid for fid in finished if str(fid) not in processed and fid not in processed]
    log("finished=%d  already-processed=%d  new-to-fetch=%d (cap %d)"
        % (len(finished), len(processed), len(new), MAX_NEW))

    # ---- 2) fetch per-player stats for new finished fixtures ------------------
    fetched = 0
    for fid in new[:MAX_NEW]:
        info = finfo.get(fid, {})
        try:
            rows, _ = api("/fixtures/players", {"fixture": fid})
        except Exception as e:
            log("players fetch failed for fixture %s: %s" % (fid, e))
            continue
        for teamblock in rows:
            tid = teamblock.get("team", {}).get("id")
            conceded = (info.get("awayGoals") if tid == info.get("homeId")
                        else info.get("homeGoals"))
            opp = (info.get("awayName") if tid == info.get("homeId")
                   else info.get("homeName"))
            for pr in teamblock.get("players", []):
                pl = pr.get("player", {})
                st = (pr.get("statistics") or [{}])[0]
                games = st.get("games", {}) or {}
                gl    = st.get("goals", {}) or {}
                cd    = st.get("cards", {}) or {}
                mins  = games.get("minutes") or 0
                pos   = (games.get("position") or "").upper()[:1]
                sub   = bool(games.get("substitute"))
                appeared = mins and mins > 0
                if not appeared:
                    continue  # didn't play -> no log entry
                goals_for = gl.get("total") or 0
                assists   = gl.get("assists") or 0
                yel = cd.get("yellow") or 0
                red = cd.get("red") or 0
                clean_sheet = (pos in ("G", "D") and mins >= 60
                               and conceded == 0)
                name = pl.get("name") or ""
                k = norm(name)
                if not k:
                    continue
                rec = players.setdefault(k, {
                    "name": name, "apiId": pl.get("id"), "log": []})
                # avoid duplicate entry if a fixture is somehow reprocessed
                if any(e.get("fixture") == fid for e in rec["log"]):
                    continue
                rec["log"].append({
                    "fixture": fid,
                    "round": info.get("round"),
                    "roundNum": info.get("roundNum"),
                    "opp": opp,
                    "mins": mins,
                    "started": (not sub),
                    "goals": goals_for,
                    "assists": assists,
                    "yellow": yel,
                    "red": red,
                    "cleanSheet": bool(clean_sheet),
                })
        processed.add(fid)
        fetched += 1
        time.sleep(REQ_SLEEP)

    meta["lastListFetch"] = now
    meta["lastRun"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["fixturesTotal"] = len(fixtures)
    meta["fixturesFinished"] = len(finished)
    state["meta"] = meta
    save(state, players, processed)
    log("fetched this run: %d  total players logged: %d" % (fetched, len(players)))

    coverage_report(players)
    write_debug("OK")

def save(state, players, processed):
    state["players"] = players
    state["processedFixtures"] = sorted(str(x) for x in processed)
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log("save failed: %s" % e)

def coverage_report(players):
    """Compare owned players to what the API gave us, so name-match gaps surface."""
    owned = load_json(OWNED, [])
    if not owned:
        log("coverage: owned-players.json not found/empty")
        return
    have = set(players.keys())
    matched, missing = [], []
    for p in owned:
        k = norm(p.get("name", ""))
        (matched if k in have else missing).append(p.get("name", ""))
    log("--- COVERAGE ---")
    log("owned players: %d  matched in API: %d  UNMATCHED: %d"
        % (len(owned), len(matched), len(missing)))
    if missing:
        log("UNMATCHED (need alias or not yet played):")
        for n in sorted(set(missing)):
            log("  - %s" % n)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        write_debug("FATAL")
    sys.exit(0)
