#!/usr/bin/env python3
"""
build_player_stats.py
=====================
Builds docs/player-stats.json: a per-player, per-round KEY-ACTION log for the
squad-page click-through, sourced entirely from our OWN live feed (the Cloudflare
worker) -- no third-party API, no key, no quota.

The live feed already exposes, per owned player per fixture:
    { name, manager, pts, goals, assists }
tagged with the player's CURRENT owner, plus a round label ("Round of 32" ...).
We accumulate that, round by round, into a log the squad page can tally.

Because every entry carries the owner at the time it was recorded, "owned-games
-only" filtering is automatic: a transfer simply starts logging the player under
his new manager, and the squad page sums only the rounds whose manager matches.

LIMITS (free, honest):
* The feed only shows the CURRENT round's games, so this log accrues from the
  moment it starts running (the knockouts) onward -- it cannot backfill the
  group stage.
* The feed carries no minutes, so there are NO clean sheets here (the 60-minute
  rule can't be honoured without minutes). Goals, assists and round points only.

Never crashes the build: any failure is logged to docs/_playerstats_debug.txt
and the script exits 0.
"""

import os, re, sys, json, time, traceback, unicodedata, urllib.request

OUT     = "docs/player-stats.json"
DEBUG   = "docs/_playerstats_debug.txt"
OWNED   = "docs/owned-players.json"
WORKER  = os.environ.get("WORKER_URL", "https://trm-live.dapperdon.workers.dev").strip()

# Round label -> ordering number (group matchdays then knockouts).
ROUND_NUM = {
    "round of 32": 4, "last 32": 4,
    "round of 16": 5, "last 16": 5,
    "quarter-finals": 6, "quarter finals": 6, "quarterfinals": 6,
    "semi-finals": 7, "semi finals": 7, "semifinals": 7,
    "third place": 8, "third-place": 8, "3rd place": 8,
    "final": 9,
}

_log = []
def log(m): _log.append(str(m))

def write_debug(status):
    try:
        with open(DEBUG, "w", encoding="utf-8") as f:
            f.write("STATUS: %s\n" % status)
            f.write("when: %s\n" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            f.write("source: %s\n" % WORKER)
            f.write("\n".join(_log) + "\n")
    except Exception:
        pass

def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()

def round_num(label):
    if not label:
        return None
    key = norm(label).replace("-", " ").strip()
    if key in ROUND_NUM:
        return ROUND_NUM[key]
    m = re.search(r"matchday\s+(\d+)", key)
    if m:
        return int(m.group(1))
    m = re.search(r"round of\s+(\d+)", key)
    if m:
        return {32: 4, 16: 5, 8: 6, 4: 7, 2: 9}.get(int(m.group(1)))
    return None

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def main():
    state   = load_json(OUT, {})
    if not isinstance(state, dict):
        state = {}
    players = state.get("players", {})
    meta    = state.get("meta", {})

    try:
        req = urllib.request.Request(WORKER, headers={"user-agent": "trm-playerstats/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            feed = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log("feed fetch failed: %s" % e)
        write_debug("ERROR (feed fetch)")
        return

    label   = feed.get("matchday")
    rnum    = round_num(label)
    fixtures = feed.get("fixtures", []) or []
    log("feed round label: %r  roundNum: %s  fixtures: %d" % (label, rnum, len(fixtures)))
    if not label:
        log("no round label in feed -- skipping (nothing to attribute).")
        write_debug("WARN (no round label)")
        return

    seen = 0
    for fx in fixtures:
        st = fx.get("status")
        if st not in ("finished", "live"):
            continue
        for p in fx.get("players", []):
            name = (p.get("name") or "").strip()
            mgr  = p.get("manager")
            if not name or not mgr:
                continue
            k = norm(name)
            rec = players.setdefault(k, {"name": name, "rounds": {}})
            rec["name"] = name  # keep latest display spelling
            # Upsert this round's entry. Re-runs overwrite, so the final (FT)
            # numbers win once the games are done.
            prev = rec["rounds"].get(label, {})
            # don't let a 'live' read clobber a 'finished' one
            if prev.get("status") == "finished" and st == "live":
                continue
            rec["rounds"][label] = {
                "roundNum": rnum,
                "manager":  mgr,
                "goals":    p.get("goals") or 0,
                "assists":  p.get("assists") or 0,
                "pts":      p.get("pts"),
                "status":   st,
            }
            seen += 1

    meta["updated"]   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["source"]    = "live-feed"
    meta["lastRound"] = label
    meta["note"]      = ("Accrues from the knockouts onward; goals/assists/points "
                         "only. No minutes, so no clean sheets.")
    state["meta"]     = meta
    state["players"]  = players
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        log("save failed: %s" % e)
        write_debug("ERROR (save)")
        return

    log("player rows captured this run: %d   total players logged: %d"
        % (seen, len(players)))
    coverage(players)
    write_debug("OK")

def coverage(players):
    owned = load_json(OWNED, [])
    if not owned:
        return
    have = set(players.keys())
    matched = [p["name"] for p in owned if norm(p.get("name", "")) in have]
    log("--- coverage --- owned: %d   appear in stats log so far: %d"
        % (len(owned), len(matched)))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        write_debug("FATAL")
    sys.exit(0)
