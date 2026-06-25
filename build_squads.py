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

# Every team's squad page is a DIFFERENT bespoke template. Across the 13 teams the
# observed variations include: name before the position, name after it; nation present
# or entirely absent; price as "£5.0m" or a bare "5.0"; points as "0w 14" or as bare
# Wk/Total columns; and rows sometimes prefixed with a sequential index (01, 02, ...).
# So we don't pattern-match a fixed layout. We anchor on each POSITION token and then
# extract each field by WHAT IT IS:
#   nation  = a 3-letter all-caps code near the position (optional)
#   price   = a "£x.xm" value, else the only decimal number in the slice
#   points  = "<round>w <total>", else the last two integers (… wk, total)
#   name    = the words between position and nation/price, else the words before position
POS_RE = re.compile(r"\b(GK|DEF|MID|FWD)\b")
NAT_RE = re.compile(r"\b([A-Z]{3})\b")
POS_SET = {"GK", "DEF", "MID", "FWD"}
PRICE_GBP = re.compile(r"£\s*([\d.]+)\s*m")
PRICE_BARE = re.compile(r"\b(\d+\.\d+)\b")
POINTS_W = re.compile(r"(-?\d+)\s*w\s+(-?\d+)")
INT = re.compile(r"-?\d+")
NAME_STOP = {
    "squad", "points", "total", "week", "since", "value", "balance", "season", "dossier",
    "current", "our", "report", "dispatches", "network", "standings", "numbers", "reserves",
    "cash", "war", "chest", "base", "strength", "wire", "transfer", "transfers", "the",
    "world", "cup", "archive", "history", "league", "breaking", "news", "this", "pts",
    "cast", "presents", "intelligence", "fin", "board", "programme", "component", "inventory",
    "active", "components", "people", "funds", "valuation", "output", "schematic", "assembly",
    "wk", "personnel",
}


def clean_name(pre):
    """Take the trailing run of name-like tokens before an anchor (drops page chrome)."""
    toks = re.split(r"\s+", pre.replace("•", " ").replace("·", " ").replace("|", " ").replace("/", " "))
    out = []
    for w in reversed(toks):
        lw = re.sub(r"[^0-9a-zA-Z]", "", w).lower()
        if not lw:
            continue
        if lw in NAME_STOP or re.match(r"^\d", w.strip()) or "£" in w or re.match(r"^gw\d", lw):
            break
        out.insert(0, w)
        if len(out) >= 4:
            break
    return " ".join(out).strip(" •·|-/")

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
    """Return (players, blob). blob = whitespace-collapsed page text (for debug).

    Layout-independent — see the field notes above POS_RE. For each POSITION token we
    identify this player's optional NATION, then read PRICE (£ or bare decimal) and the
    points ('Nw T' or the trailing Wk/Total integers), and finally the NAME (between
    position and nation/price, or the words just before the position token).
    """
    blob = re.sub(r"\s+", " ", strip_html(fetch(f"{BASE}/wc/team/{slug}")))

    # Drop the page footer (the closing "Standings" link and any flavour text after it)
    # so trailing numbers can't be mistaken for the last player's score.
    first = POS_RE.search(blob)
    if first:
        foot = re.search(r"standings", blob[first.end():], re.I)
        if foot:
            blob = blob[:first.end() + foot.start()]

    # Some templates prefix each row with a sequential index (01, 02, …). If we detect a
    # full increasing run of them before the position tokens, strip the indices so they
    # aren't read as scores.
    idx = [int(x) for x in re.findall(r"\b(\d{1,2})\s+(?=(?:GK|DEF|MID|FWD)\b)", blob)]
    if len(idx) >= 10 and idx[0] in (0, 1) and idx == list(range(idx[0], idx[0] + len(idx))):
        blob = re.sub(r"\b\d{1,2}\s+(?=(?:GK|DEF|MID|FWD)\b)", "", blob)

    anchors = list(POS_RE.finditer(blob))
    players, prev = [], 0
    for i, a in enumerate(anchors):
        pos = a.group(1)
        nxt = anchors[i + 1].start() if i + 1 < len(anchors) else len(blob)

        nat = None
        nm = NAT_RE.search(blob, a.end())
        if nm and nm.start() < nxt and nm.start() - a.end() <= 40 and nm.group(1) not in POS_SET:
            nat = nm
        field_start = nat.end() if nat else a.end()
        region = blob[field_start:nxt]

        gm = PRICE_GBP.search(region)
        if gm:
            price, pend, price_at = float(gm.group(1)), gm.end(), gm.start()
        else:
            bm = PRICE_BARE.search(region)
            if not bm:
                prev = nxt
                continue
            price, pend, price_at = float(bm.group(1)), bm.end(), bm.start()

        wm = POINTS_W.search(region)
        if wm:
            rnd, tot, consumed = int(wm.group(1)), int(wm.group(2)), wm.end()
        else:
            its = list(INT.finditer(region[pend:]))
            if not its:
                prev = nxt
                continue
            tot = int(its[-1].group())
            rnd = int(its[-2].group()) if len(its) >= 2 else 0
            consumed = pend + its[-1].end()

        between = blob[a.end():nat.start()] if nat else region[:price_at]
        if re.search(r"[A-Za-z]{2,}", between):
            name = re.sub(r"\s+", " ", between).strip(" •·|-/")
        else:
            name = clean_name(blob[prev:a.start()])
        prev = field_start + consumed
        if not name or len(name) > 40:
            continue
        players.append({"name": name, "pos": pos, "nation": nat.group(1) if nat else "",
                        "price": price, "round": rnd, "total": tot})
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
