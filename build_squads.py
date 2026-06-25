#!/usr/bin/env python3
"""
build_squads.py — regenerate docs/owned-players.json and the 13 docs/team-*.html
squad pages by scraping the league's own squad pages, so transfers are picked up
automatically each build.

No third-party dependencies (strips HTML with a regex, like the worker), so it
can't fail on a missing package. The whole run is wrapped so it NEVER crashes the
workflow and ALWAYS leaves docs/_squads_debug.txt describing what happened.

Safety: only writes the snapshot files if all 13 squads parse sane (~18 each,
~234 total); otherwise the existing files are left untouched.
"""

import os
import re
import sys
import json
import time
import traceback
import urllib.request

BASE = "https://trm-fantasy.onrender.com"
OWNED_PATH = "docs/owned-players.json"
DEBUG_PATH = "docs/_squads_debug.txt"
UA = "Mozilla/5.0 (compatible; TRM-Squads/1.0; +https://github.com)"

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

DISPLAY = {"Look at his face. Just Look at his FACE!": "Look At His Face!"}

# Position and 3-letter nation may be separated by a space OR a bullet
# (some squad pages render "GK GER", others "GK • GER"), so allow 1-4
# non-alphanumeric chars between them.
PLAYER = re.compile(
    r"(GK|DEF|MID|FWD)[^A-Za-z0-9]{1,4}([A-Z]{3})\b.*?£\s*([\d.]+)\s*m.*?GW\s*(\d+)\b.*?(-?\d+)\s*w\s+(-?\d+)"
)

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
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def fetch(url, tries=5):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(6 * (i + 1))
    raise RuntimeError(f"fetch failed {url}: {last}")


def strip_html(html):
    """Plain-regex HTML -> text (no external deps). Mirrors the worker's approach."""
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), html)
    html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), html)
    html = (html.replace("&amp;", "&").replace("&pound;", "£").replace("&nbsp;", " ")
                .replace("&apos;", "'").replace("&quot;", '"').replace("&bull;", "•")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return html


def parse_team(slug):
    """Return (players, blob). blob = whitespace-collapsed page text (for debug)."""
    text = strip_html(fetch(f"{BASE}/wc/team/{slug}"))
    idx = text.lower().find("our squad")
    if idx != -1:
        text = text[idx:]
    blob = re.sub(r"\s+", " ", text)
    players, prev = [], 0
    for m in PLAYER.finditer(blob):
        name = blob[prev:m.start()].strip(" •·|-")
        name = re.sub(r"^.*\(\s*\d+\s*/\s*\d+\s*\)\s*", "", name)
        prev = m.end()
        if not name or len(name) > 40:
            continue
        players.append({"name": name, "pos": m.group(1), "nation": m.group(2),
                        "price": float(m.group(3)), "gw": int(m.group(4)),
                        "round": int(m.group(5)), "total": int(m.group(6))})
    return players, blob


def team_html(team, manager, players):
    ordered = sorted(players, key=lambda p: POS_ORDER.get(p["pos"], 9))
    rows = []
    for p in ordered:
        base = p["total"] - p["round"]
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
    html = (HEAD.replace("{{TITLE}}", esc(disp)).replace("{{TEAM}}", esc(disp))
                .replace("{{MGR}}", esc(manager)).replace("{{SE}}", str(se))
                .replace("{{RD}}", str(rd)).replace("{{N}}", str(len(players)))
                .replace("{{ROWS}}", "".join(rows)))
    return html + SCRIPT + "</div></body></html>"


def write_debug(text):
    try:
        os.makedirs(os.path.dirname(DEBUG_PATH), exist_ok=True)
        with open(DEBUG_PATH, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def run():
    log = [f"python {sys.version.split()[0]}"]
    per_team = {}
    all_owned = []
    for slug, team, mgr in TEAMS:
        ps, blob = parse_team(slug)
        log.append(f"{slug}: parsed {len(ps)} players")
        if not (15 <= len(ps) <= 20):
            log.append(f"--- ABORT: {slug} parsed {len(ps)} (expected ~18). First 1800 chars of its squad text: ---")
            log.append(blob[:1800])
            write_debug("\n".join(log))
            print(f"WARN: {slug} parsed {len(ps)} — files untouched", file=sys.stderr)
            return
        per_team[slug] = (team, mgr, ps)
        for p in ps:
            all_owned.append({"name": p["name"], "manager": mgr, "nation": p["nation"],
                              "price": p["price"], "round": p["round"], "total": p["total"]})

    if not (200 <= len(all_owned) <= 260):
        log.append(f"ABORT: total {len(all_owned)} players out of range — files untouched")
        write_debug("\n".join(log))
        return

    os.makedirs(os.path.dirname(OWNED_PATH), exist_ok=True)
    with open(OWNED_PATH, "w", encoding="utf-8") as f:
        json.dump(all_owned, f, ensure_ascii=False)
    for slug, (team, mgr, ps) in per_team.items():
        with open(f"docs/team-{slug}.html", "w", encoding="utf-8") as f:
            f.write(team_html(team, mgr, ps))
    log.append(f"STATUS OK: wrote owned-players.json ({len(all_owned)} players) + {len(per_team)} squad pages")
    write_debug("\n".join(log))
    print("build_squads: success", flush=True)


def main():
    try:
        run()
    except Exception:
        # Never crash the workflow; always leave a diagnostic.
        write_debug("FATAL ERROR in build_squads.py:\n" + traceback.format_exc())
        print("build_squads: error (see docs/_squads_debug.txt)", file=sys.stderr)


if __name__ == "__main__":
    main()
