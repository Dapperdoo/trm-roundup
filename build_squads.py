#!/usr/bin/env python3
"""
build_squads.py — regenerate docs/owned-players.json and the 13 docs/team-*.html
squad pages by scraping the league's own squad pages.

Each source squad page lists, per player: position, nation, PRICE, the gameweek
they joined, this-round points and season total. Running this before the roundup
each day means SQUAD TRANSFERS ARE PICKED UP AUTOMATICALLY — a manager swapping
players simply shows up here on the next build, with correct prices/totals, and
the value/overall/nations boxes and squad pages follow.

Safety: it parses ALL 13 squads first and only writes anything if every squad
came back sane (~18 players, ~234 total). A failed or partial scrape leaves the
existing snapshot files untouched, so it can never corrupt the site.
"""

import os
import re
import sys
import json
import time
import datetime
import urllib.request

from bs4 import BeautifulSoup

BASE = "https://trm-fantasy.onrender.com"
OWNED_PATH = "docs/owned-players.json"
UA = "Mozilla/5.0 (compatible; TRM-Squads/1.0; +https://github.com)"

# slug -> (team name as the source shows it, manager)
TEAMS = [
    ("back-of-the-van-united", "Back of the Van United", "Joe S"),
    ("look-at-his-face", "Look at his face. Just Look at his FACE!", "Sam"),
    ("anamaduwa-athletic", "Anamaduwa Athletic", "Tom"),
    ("shatners-bassoon", "Shatner's Bassoon", "Joe A"),
    ("trossys-giants", "Trossy's Giants", "Dave"),
    ("50-shades-of-oshea", "50 Shades of O'Shea", "Wigs"),
    ("von-neumann-trombone", "Von Neumann Trombone", "Jeremy"),
    ("dyers-rusty-9-iron", "Dyer's Rusty 9 Iron", "Nick"),
    ("lloyds-food-and-wine", "Lloyd's Food and Wine", "Chris"),
    ("denton-burn", "Denton Burn", "Dan"),
    ("trippier-and-trippier", "Trippier & Trippier", "Tristan"),
    ("propaganda-parade", "Propaganda Parade", "Malik"),
    ("snacobs-ladder", "Snacob's Ladder", "Jake"),
]

# Shorten the one very long team name everywhere it's displayed (matches the rest of the site).
DISPLAY = {"Look at his face. Just Look at his FACE!": "Look At His Face!"}

# A player meta line, e.g. "GK SUI • £4.7m • GW1". Tolerant of the separator char.
META = re.compile(r"^(GK|DEF|MID|FWD)\s+([A-Z]{3}).*?£\s*([\d.]+)\s*m.*?GW\s*(\d+)")
# The points line, e.g. "4w 9"  ->  this-round=4, season=9
PTS = re.compile(r"^(-?\d+)\s*w\s+(-?\d+)\s*$")

# Exact live-update script used on every squad page (reads the worker feed; colours
# a player's round blue once their fixture has kicked off; total = baked + live round).
SCRIPT = r'''<script>(function(){var W="https://trm-live.dapperdon.workers.dev";function nrm(s){return (s||'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/[^a-z0-9 ]/g,' ').replace(/\s+/g,' ').trim();}function poll(){fetch(W,{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){var used={},rp={};(d.fixtures||[]).forEach(function(f){if(f.status==='finished'||f.status==='live'){(f.players||[]).forEach(function(p){var k=nrm(p.name);used[k]=true;if(p.pts!=null)rp[k]=p.pts;});}});var tot=0,rnd=0;[].forEach.call(document.querySelectorAll('tr[data-p]'),function(tr){var bt=+tr.getAttribute('data-t');var key=nrm(tr.getAttribute('data-p'));var u=used[key]===true;var r=u?(rp[key]!=null?rp[key]:0):0;var t=bt+r;var rc=tr.querySelector('.rd'),tc=tr.querySelector('.tot');if(rc){rc.textContent=r;rc.style.color=u?'var(--cyan)':'';}if(tc)tc.textContent=t;rnd+=r;tot+=t;});var se=document.getElementById('sq-se'),sr=document.getElementById('sq-rd');if(se)se.textContent=tot;if(sr)sr.textContent=rnd;}).catch(function(){});}poll();setInterval(poll,30000);})();</script>'''

HEAD = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
    "<title>{{TITLE}} — Squad</title>"
    "<style>:root{--bg:#0a0e14;--line:#1b2636;--ink:#e8eef6;--mut:#7e8ca0;--cyan:#22d3ee;--amber:#f5c451;--gk:#f5c451;--def:#34d399;--mid:#22d3ee;--fwd:#f472b6;}"
    "*{box-sizing:border-box;}body{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,Helvetica,sans-serif;line-height:1.5;}"
    ".wrap{max-width:760px;margin:0 auto;padding:18px 16px 60px;}a.back{color:var(--mut);text-decoration:none;font-size:13px;}a.back:hover{color:var(--cyan);}"
    "h1{font-size:26px;margin:14px 0 2px;font-weight:900;letter-spacing:-.4px;}.sub{color:var(--mut);font-size:13px;}"
    ".stats{display:flex;gap:24px;margin:16px 0 18px;}.stat b{display:block;font-size:22px;color:var(--cyan);font-variant-numeric:tabular-nums;}"
    ".stat span{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--mut);}"
    "table{width:100%;border-collapse:collapse;}th{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:var(--mut);text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);}"
    "th.num,td.num{text-align:right;}td{padding:9px 8px;border-bottom:1px solid var(--line);font-size:14px;}"
    ".pos{font-weight:800;font-size:11px;width:34px;color:var(--mut);}.pos.GK{color:var(--gk);}.pos.DEF{color:var(--def);}.pos.MID{color:var(--mid);}.pos.FWD{color:var(--fwd);}"
    ".pn{font-weight:600;}.nat{color:var(--mut);font-size:12px;width:44px;}.num{font-variant-numeric:tabular-nums;}.rd{color:var(--amber);font-weight:700;}.tot{color:var(--cyan);font-weight:800;}"
    "footer{margin-top:24px;border-top:1px solid var(--line);padding-top:12px;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.14em;}</style></head><body>"
    '<div class="wrap"><a class="back" href="./">← League table</a>'
    "<h1>{{TEAM}}</h1><div class=\"sub\">Managed by {{MGR}}</div>"
    '<div class="stats"><div class="stat"><b id="sq-se">{{SE}}</b><span>Season pts</span></div>'
    '<div class="stat"><b id="sq-rd">{{RD}}</b><span>This round</span></div>'
    '<div class="stat"><b>{{N}}</b><span>Squad</span></div></div>'
    '<table><thead><tr><th>Pos</th><th>Player</th><th>Nat</th><th class="num">Price</th>'
    '<th class="num">Rnd</th><th class="num">Total</th></tr></thead><tbody>{{ROWS}}</tbody></table>'
    "<footer>TRM Fantasy · World Cup 2026 · points update live during games; squad &amp; prices daily</footer>"
)

POS_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def fetch(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"fetch failed {url}: {last}")


def to_lines(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return [ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]


def parse_team(slug):
    lines = to_lines(fetch(f"{BASE}/wc/team/{slug}"))
    # Focus on the squad section if the marker is present.
    for i, l in enumerate(lines):
        if l.lower().startswith("our squad"):
            lines = lines[i + 1:]
            break
    players = []
    for i, l in enumerate(lines):
        m = META.match(l)
        if not m:
            continue
        pos, nat, price, gw = m.group(1), m.group(2), float(m.group(3)), int(m.group(4))
        name = lines[i - 1].strip() if i >= 1 else ""
        pm = PTS.match(lines[i + 1]) if i + 1 < len(lines) else None
        this_round = int(pm.group(1)) if pm else 0
        season = int(pm.group(2)) if pm else 0
        if not name:
            continue
        players.append({"name": name, "pos": pos, "nation": nat, "price": price,
                        "gw": gw, "round": this_round, "total": season})
    return players


def team_html(team, manager, players):
    ordered = sorted(players, key=lambda p: POS_ORDER.get(p["pos"], 9))
    rows = []
    for p in ordered:
        base = p["total"] - p["round"]            # season minus current round = baked baseline
        rows.append(
            f'<tr data-p="{esc(p["name"])}" data-r="{p["round"]}" data-t="{base}">'
            f'<td class="pos {p["pos"]}">{p["pos"]}</td>'
            f'<td class="pn">{esc(p["name"])}</td>'
            f'<td class="nat">{esc(p["nation"])}</td>'
            f'<td class="num">£{p["price"]:.1f}m</td>'
            f'<td class="num rd">{p["round"]}</td>'
            f'<td class="num tot">{p["total"]}</td></tr>'
        )
    se = sum(p["total"] for p in players)
    rd = sum(p["round"] for p in players)
    disp = DISPLAY.get(team, team)
    html = (HEAD
            .replace("{{TITLE}}", esc(disp))
            .replace("{{TEAM}}", esc(disp))
            .replace("{{MGR}}", esc(manager))
            .replace("{{SE}}", str(se))
            .replace("{{RD}}", str(rd))
            .replace("{{N}}", str(len(players)))
            .replace("{{ROWS}}", "".join(rows)))
    return html + SCRIPT + "</div></body></html>"


def main():
    per_team = {}
    all_owned = []
    for slug, team, mgr in TEAMS:
        try:
            ps = parse_team(slug)
        except Exception as e:
            print(f"WARN: scrape failed for {slug}: {e} — leaving snapshot files untouched", file=sys.stderr)
            return
        if not (15 <= len(ps) <= 20):
            print(f"WARN: {slug} parsed {len(ps)} players (expected ~18) — aborting, files untouched", file=sys.stderr)
            return
        per_team[slug] = (team, mgr, ps)
        for p in ps:
            all_owned.append({"name": p["name"], "manager": mgr, "nation": p["nation"],
                              "price": p["price"], "round": p["round"], "total": p["total"]})

    if not (200 <= len(all_owned) <= 260):
        print(f"WARN: total players {len(all_owned)} looks wrong — aborting, files untouched", file=sys.stderr)
        return

    os.makedirs(os.path.dirname(OWNED_PATH), exist_ok=True)
    with open(OWNED_PATH, "w", encoding="utf-8") as f:
        json.dump(all_owned, f, ensure_ascii=False)
    print(f"Wrote {OWNED_PATH} ({len(all_owned)} players)", flush=True)

    for slug, (team, mgr, ps) in per_team.items():
        with open(f"docs/team-{slug}.html", "w", encoding="utf-8") as f:
            f.write(team_html(team, mgr, ps))
    print(f"Wrote {len(per_team)} squad pages", flush=True)


if __name__ == "__main__":
    main()
