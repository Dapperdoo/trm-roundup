/**
 * TRM Live — Cloudflare Worker
 *
 * Two endpoints:
 *   GET /          -> league standings + fixtures, scraped from the league site
 *                    (trm-fantasy.onrender.com). That page already applies the
 *                    fantasy scoring AND shows, per fixture: the score, status
 *                    (live / full time / upcoming), the goal scorers & assists,
 *                    and every owned player with points + goals + assists. We
 *                    parse all of that out so the LIVE page can render rich
 *                    match boxes. (FIFA's own match feed is not used — it is
 *                    empty for this tournament.)
 *   GET /players   -> name -> live-points map for the current round, plus a
 *                    name -> full-tournament-total map, from FIFA's players.json.
 *
 * Both are edge-cached ~60s.
 */

const UPSTREAM     = "https://trm-fantasy.onrender.com/wc";
const FIFA_PLAYERS = "https://play.fifa.com/json/fantasy/players.json";
const FIFA_ROUNDS  = "https://play.fifa.com/json/fantasy/rounds.json";

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, OPTIONS",
  "content-type": "application/json; charset=utf-8",
  "cache-control": "public, max-age=60",
};

const TEAMS = [
  { slug: "back-of-the-van-united", name: "Back of the Van United", manager: "Joe S" },
  { slug: "look-at-his-face", name: "Look at his face. Just Look at his FACE!", manager: "Sam" },
  { slug: "anamaduwa-athletic", name: "Anamaduwa Athletic", manager: "Tom" },
  { slug: "shatners-bassoon", name: "Shatner's Bassoon", manager: "Joe A" },
  { slug: "trossys-giants", name: "Trossy's Giants", manager: "Dave" },
  { slug: "50-shades-of-oshea", name: "50 Shades of O'Shea", manager: "Wigs" },
  { slug: "von-neumann-trombone", name: "Von Neumann Trombone", manager: "Jeremy" },
  { slug: "dyers-rusty-9-iron", name: "Dyer's Rusty 9 Iron", manager: "Nick" },
  { slug: "lloyds-food-and-wine", name: "Lloyd's Food and Wine", manager: "Chris" },
  { slug: "denton-burn", name: "Denton Burn", manager: "Dan" },
  { slug: "trippier-and-trippier", name: "Trippier & Trippier", manager: "Tristan" },
  { slug: "propaganda-parade", name: "Propaganda Parade", manager: "Malik" },
  { slug: "snacobs-ladder", name: "Snacob's Ladder", manager: "Jake" },
];

// The site tags each owned player with a short team code; map them to managers.
// NOTE the league site uses "T&T" (not "TRIP") for Trippier & Trippier.
const CODE = {
  VAN: "Joe S", FACE: "Sam", ANAM: "Tom", SHAT: "Joe A", TROS: "Dave", SHEA: "Wigs",
  VNT: "Jeremy", IRON: "Nick", LLYD: "Chris", BURN: "Dan", TRIP: "Tristan",
  "T&T": "Tristan", PROP: "Malik", SNAC: "Jake",
};

// The league's matchday calendar: which day each fixture is played. Used to tag
// fixtures so the LIVE page can group by matchday. Pairs are order-free. Finished
// games lose their date on the page, so this fills it in; upcoming games still
// carry a date on the page and use that.
const SCHEDULE = {
  "2026-06-18": [["CZE","RSA"],["SUI","BIH"],["CAN","QAT"],["MEX","KOR"]],
  "2026-06-19": [["USA","AUS"],["SCO","MAR"],["BRA","HAI"],["TUR","PAR"]],
  "2026-06-20": [["NED","SWE"],["GER","CIV"],["ECU","CUW"],["TUN","JPN"]],
  "2026-06-21": [["ESP","KSA"],["BEL","IRN"],["URU","CPV"],["NZL","EGY"]],
  "2026-06-22": [["ARG","AUT"],["FRA","IRQ"],["NOR","SEN"],["JOR","ALG"]],
  "2026-06-23": [["POR","UZB"],["ENG","GHA"],["PAN","CRO"],["COL","COD"]],
  // Round 3 (Group Matchday 3) — final group games play in simultaneous pairs, so
  // 6 games per evening slate. Keys are the EVENING SLATE (post-midnight kickoffs
  // shifted back a day to match matchdayKey), so finished games tag correctly.
  "2026-06-24": [["SUI","CAN"],["BIH","QAT"],["MAR","HAI"],["SCO","BRA"],["RSA","KOR"],["CZE","MEX"]],
  "2026-06-25": [["ECU","GER"],["CUW","CIV"],["TUN","NED"],["JPN","SWE"],["TUR","USA"],["PAR","AUS"]],
  "2026-06-26": [["NOR","FRA"],["SEN","IRQ"],["URU","ESP"],["CPV","KSA"],["NZL","BEL"],["EGY","IRN"]],
  "2026-06-27": [["CRO","GHA"],["PAN","ENG"],["COD","UZB"],["COL","POR"],["JOR","ARG"],["ALG","AUT"]],
  // Knockouts — Round of 32. Keys are the UK EVENING SLATE: post-midnight kickoffs
  // are shifted back 12h (matchdayKey) so a 02:00 game groups with the prior evening.
  "2026-06-28": [["RSA","CAN"]],
  "2026-06-29": [["BRA","JPN"],["GER","PAR"],["NED","MAR"]],
  "2026-06-30": [["CIV","NOR"],["FRA","SWE"],["MEX","ECU"]],
  "2026-07-01": [["ENG","COD"],["BEL","SEN"],["USA","BIH"]],
  "2026-07-02": [["ESP","AUT"],["POR","CRO"],["SUI","ALG"]],
  "2026-07-03": [["AUS","EGY"],["ARG","CPV"],["COL","GHA"]],
};
const PAIR_DATE = {};
for (const day of Object.keys(SCHEDULE)) for (const pr of SCHEDULE[day]) PAIR_DATE[pr.slice().sort().join("|")] = day;
// A fixture's matchday = its tournament day; shift back 12h so a post-midnight
// kickoff (e.g. 04:00) belongs to the previous evening's slate.
function matchdayKey(ds) {
  const d = new Date(ds);
  if (isNaN(d)) return null;
  return new Date(d.getTime() - 12 * 3600 * 1000).toISOString().slice(0, 10);
}

// ----- league site (table + fixtures + scorers + owned players) ----------------

function htmlToLines(html) {
  const text = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, "\n")
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10)))
    .replace(/&amp;/g, "&").replace(/&apos;/g, "'").replace(/&quot;/g, '"')
    .replace(/&pound;/g, "£").replace(/&nbsp;/g, " ")
    .replace(/&ndash;/g, "–").replace(/&mdash;/g, "—")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  return text.split("\n").map((s) => s.replace(/[ \t]+/g, " ").trim()).filter(Boolean);
}

// The standings row renders, after the team+manager: one number per round played
// so far (R1, R2, ... R{N}, where N = current round), then the cumulative Total.
// So a row carries N+1 numbers: [R1, R2, ..., R{N}, Total]. We must take the LAST
// of those as the Total — NOT the third number — because at round 3+ there are
// four+ columns and the old "first three" logic mistook R3 for the Total.
// `roundNum` (= N, the number of score columns) is derived from the table header
// and works for the group stage AND the knockouts; we index fixed positions so a
// trailing number (the next row's rank) is ignored.
function parseStandings(flat, roundNum) {
  const out = [];
  const N = (roundNum && roundNum >= 1) ? roundNum : null;
  for (const t of TEAMS) {
    const idx = flat.indexOf(t.name);
    if (idx === -1) continue;
    const after = flat.slice(idx + t.name.length, idx + t.name.length + 80);
    const nums = (after.match(/-?\d+/g) || []).map(Number);
    let r1, round, total;
    if (N && nums.length >= N + 1) {
      // Columns: R1 .. R{N} (current round), Total. Trailing numbers (next row's
      // rank) are ignored because we read fixed positions, not the last element.
      r1 = nums[0];
      round = nums[N - 1];
      total = nums[N];
    } else if (nums.length >= 2) {
      // N unknown: best-effort — Total is the last column, current round the one before.
      r1 = nums[0];
      total = nums[nums.length - 1];
      round = nums.length >= 2 ? nums[nums.length - 2] : nums[0];
    } else {
      continue;
    }
    out.push({ slug: t.slug, team: t.name, manager: t.manager, r1, round, total });
  }
  out.sort((a, b) => b.total - a.total);
  out.forEach((r, i) => (r.rank = i + 1));
  return out;
}

const isCode = (s) => /^[A-Z]{3}$/.test(s);
const isScoreOrTime = (s) => /^\d{1,2}\s*[–-]\s*\d{1,2}$/.test(s) || /^\d{1,2}:\d{2}$/.test(s);
const BULLET = /^[●■]$/;
const SYMS = /^[⚽Ⓐ︎️\s]+$/;                       // a symbols-only token (goals/assists)
const codeTok = (s) => { const m = (s || "").match(/^\(([A-Z0-9&]{2,6})\)$/); return m ? m[1] : null; };
const isPoints = (s) => /^-?\d+$/.test(s) || s === "–" || s === "-";
// A finished-match status token. Group games show "FULL TIME"; knockout games may
// instead show "AET", "PENS"/"PENALTIES", or "FT", so accept all of those.
const FINISHED = /full ?time|\bft\b|\baet\b|\bpens?\b|penalt/i;

// Detect a fixture header at index i. The page renders, as separate tokens:
//   HOME  "4–1"|"18:00"  AWAY  <status...>
// status is finished ("FULL TIME"/"AET"/"PENS"), "LIVE" (sometimes a "●" bullet
// first), or a date for upcoming games. Returns the header + body start index.
function detectFixture(L, i) {
  if (!isCode(L[i]) || !isScoreOrTime(L[i + 1] || "") || !isCode(L[i + 2] || "")) return null;
  const mid = L[i + 1], isTime = /:/.test(mid);
  if (isTime) {
    const hasDate = /^\d{4}-\d{2}-\d{2}$/.test(L[i + 3] || "");
    return { home: L[i], away: L[i + 2], mid, isTime, status: "upcoming", date: hasDate ? L[i + 3] : null, next: i + (hasDate ? 4 : 3) };
  }
  const a = L[i + 3] || "", b = L[i + 4] || "";
  if (FINISHED.test(a)) return { home: L[i], away: L[i + 2], mid, isTime, status: "finished", next: i + 4 };
  if (/live/i.test(a))  return { home: L[i], away: L[i + 2], mid, isTime, status: "live", next: i + 4 };
  if (BULLET.test(a) && /live/i.test(b)) return { home: L[i], away: L[i + 2], mid, isTime, status: "live", next: i + 5 };
  if (FINISHED.test(a + " " + b))        return { home: L[i], away: L[i + 2], mid, isTime, status: "finished", next: i + 5 };
  return null;
}

// Parse every fixture block. The league page tokenises each owned player across
// SEPARATE tokens — name, "(CODE)", optional ⚽/Ⓐ symbol tokens, then points —
// and lists goal scorers/assists in a "⚽ Name (CODE)" run before the players.
function parseFixtures(allLines) {
  const start = allLines.findIndex((l) => /THIS ROUND'?S GAMES/i.test(l));
  const L = start >= 0 ? allLines.slice(start) : allLines;
  const fixtures = [];
  let i = 0;
  while (i < L.length) {
    const d = detectFixture(L, i);
    if (!d) { i += 1; continue; }
    const fx = {
      home: d.home, away: d.away,
      score: d.isTime ? null : d.mid.replace(/\s+/g, ""),
      kickoff: d.isTime ? d.mid : null,
      date: d.date || null,
      status: d.status,
      scorers: [],
      players: [],
    };
    i = d.next;
    while (i < L.length) {
      if (detectFixture(L, i)) break;                 // next fixture
      const t = L[i] || "";
      if (/owned player/i.test(t)) { i += 1; continue; }
      // Goal-scorers fragment: a token that begins with a football. Split on the
      // ball into names; a following "(CODE)" token tags the last scorer as owned.
      if (/^\s*⚽/.test(t)) {
        t.split(/⚽[︎️]?/).map((x) => x.trim()).filter(Boolean)
          .forEach((n) => fx.scorers.push({ name: n, manager: null }));
        const c = codeTok(L[i + 1]);
        if (c) { if (fx.scorers.length) fx.scorers[fx.scorers.length - 1].manager = CODE[c] || null; i += 1; }
        i += 1; continue;
      }
      // Owned player row: name token, then "(CODE)", then symbol token(s), then points.
      const c = codeTok(L[i + 1]);
      if (c) {
        const name = t.trim();
        let j = i + 2, goals = 0, assists = 0, pts = null;
        while (j < L.length && SYMS.test(L[j])) {
          goals += (L[j].match(/⚽/g) || []).length;
          assists += (L[j].match(/Ⓐ/g) || []).length;
          j += 1;
        }
        if (j < L.length && isPoints(L[j])) {
          pts = (L[j] === "–" || L[j] === "-") ? null : parseInt(L[j], 10);
          j += 1;
        }
        if (CODE[c]) fx.players.push({ name, manager: CODE[c], pts, goals, assists });
        i = j; continue;
      }
      i += 1;
    }
    fixtures.push(fx);
  }
  return fixtures;
}

async function buildFeed() {
  const res = await fetch(UPSTREAM, {
    headers: { "user-agent": "trm-live/1.0 (+github pages scoreboard)" },
    cf: { cacheTtl: 30, cacheEverything: true },
  });
  if (!res.ok) throw new Error("upstream " + res.status);
  const lines = htmlToLines(await res.text());
  const flat = lines.join(" ");
  // How many score columns each standings row has (R1..R{N}, then Total) drives
  // parseStandings. The most robust signal is the standings HEADER itself — count
  // the R-columns — because it works for the group stage AND the knockouts, where
  // there is no "Matchday N" label. Fall back to the matchday number if needed.
  let roundNum = null;
  { let maxR = 0, m; const re = /\bR(\d+)\b/g; while ((m = re.exec(flat))) { const k = parseInt(m[1], 10); if (k > maxR && k <= 20) maxR = k; } if (maxR >= 1) roundNum = maxR; }
  const mdNum = flat.match(/Group Matchday\s+(\d+)|Matchday\s+(\d+)/i);
  if (!roundNum && mdNum) roundNum = parseInt(mdNum[1] || mdNum[2], 10);
  // Human round label for the page/roundup: the group matchday or a knockout round.
  const labelMatch = flat.match(/Group Matchday\s+\d+|Round of \d+|Last \d+|Quarter[\- ]?finals?|Semi[\- ]?finals?|Third[\- ]place(?:\s+play[\- ]?off)?|Final\b/i);
  const roundLabel = labelMatch ? labelMatch[0] : (mdNum ? mdNum[0] : null);
  const standings = parseStandings(flat, roundNum);
  const fixtures = parseFixtures(lines);
  // Tag each fixture with its matchday (calendar day). Upcoming games carry a
  // date on the page; finished/live games don't, so fall back to the SCHEDULE.
  for (const f of fixtures) {
    f.matchday = f.date
      ? matchdayKey(f.date + "T" + (f.kickoff || "12:00"))
      : (PAIR_DATE[[f.home, f.away].sort().join("|")] || null);
  }
  if (standings.length < 10) throw new Error("parsed too few teams (" + standings.length + ")");

  // "remaining" = owned players in a not-finished fixture (still to play this round).
  const rem = {};
  for (const f of fixtures) {
    if (f.status === "finished") continue;
    for (const p of f.players) rem[p.manager] = (rem[p.manager] || 0) + 1;
  }
  for (const s of standings) s.remaining = rem[s.manager] || 0;

  return {
    updated: new Date().toISOString(),
    matchday: roundLabel,
    anyLive: fixtures.some((f) => f.status === "live"),
    standings,
    fixtures,
  };
}

// ----- FIFA per-player live points (squad pages) -------------------------------

async function fetchJson(url, ttl) {
  const res = await fetch(url, {
    headers: { "user-agent": "trm-live/2.0 (+github pages scoreboard)" },
    cf: { cacheTtl: ttl || 45, cacheEverything: true },
  });
  if (!res.ok) throw new Error("fetch " + url + " -> " + res.status);
  return res.json();
}

function currentRound(rounds) {
  const live = (r) => (r.tournaments || []).some(
    (t) => t.status && !/sched|pre|upcoming|not/i.test(t.status) && !/complete|played|finish|full/i.test(t.status));
  return rounds.find((r) => r.status === "playing")
    || rounds.find(live)
    || rounds.filter((r) => r.status === "complete").pop()
    || rounds[rounds.length - 1];
}

// A player's FULL tournament total, independent of which manager owns him and
// since when. Prefer FIFA's own season figure (stats.totalPoints); if that's
// absent/zero, fall back to summing the per-round breakdown (array or object form).
function seasonTotal(p) {
  const s = (p && p.stats) || {};
  if (typeof s.totalPoints === "number" && s.totalPoints !== 0) return s.totalPoints;
  const rp = s.roundPoints;
  let sum = 0, any = false;
  if (Array.isArray(rp)) {
    for (const e of rp) {
      const v = (e && typeof e === "object") ? (e.points != null ? e.points : (e.value != null ? e.value : e.pts)) : e;
      if (typeof v === "number") { sum += v; any = true; }
    }
  } else if (rp && typeof rp === "object") {
    for (const k in rp) { if (typeof rp[k] === "number") { sum += rp[k]; any = true; } }
  }
  if (any) return sum;
  return (typeof s.totalPoints === "number") ? s.totalPoints : 0;
}

async function buildPlayers() {
  const [players, rounds] = await Promise.all([fetchJson(FIFA_PLAYERS), fetchJson(FIFA_ROUNDS)]);
  const cur = currentRound(rounds);
  const cr = String(cur.id);
  const map = {};
  const totals = {};
  for (const p of players) {
    const full = norm((p.firstName || "") + " " + (p.lastName || ""));
    const known = p.knownName ? norm(p.knownName) : null;
    // Current-round points (used by squad pages) — unchanged behaviour.
    const rp = (p.stats && p.stats.roundPoints) || {};
    const pts = rp[cr];
    if (pts != null) {
      if (full) map[full] = pts;
      if (known) map[known] = pts;
    }
    // Full-tournament total (ownership-independent) for the value/overall boxes.
    const tot = seasonTotal(p);
    if (full) totals[full] = tot;
    if (known) totals[known] = tot;
  }
  // Authoritative override: the league's own feed attributes each OWNED player's
  // current-round points under the exact display name the squad pages use. Overlay
  // it so short/colliding names (e.g. "Danilo") resolve to the correct player
  // rather than a same-named FIFA entry.
  try {
    const feed = await buildFeed();
    for (const f of (feed.fixtures || [])) {
      for (const pl of (f.players || [])) {
        if (pl.pts != null) map[norm(pl.name)] = pl.pts;
      }
    }
  } catch (e) { /* feed unavailable: keep the FIFA-only map */ }
  return { updated: new Date().toISOString(), round: cur.id, players: map, totals };
}

function norm(s) {
  return (s || "")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
}

// ----- routing -----------------------------------------------------------------

async function serve(cacheKey, builder, ctx) {
  const cache = caches.default;
  const key = new Request(cacheKey);
  const hit = await cache.match(key);
  if (hit) return hit;
  let body;
  try {
    body = JSON.stringify(await builder());
  } catch (e) {
    return new Response(JSON.stringify({ error: String((e && e.message) || e) }), { status: 502, headers: CORS });
  }
  const resp = new Response(body, { headers: CORS });
  ctx.waitUntil(cache.put(key, resp.clone()));
  return resp;
}

// TEMP debug: returns the exact line tokens the parser sees, plus what it parsed.
// Lets us fix the fixture parser against the real HTML. Not cached. Remove later.
async function buildRaw() {
  const res = await fetch(UPSTREAM, {
    headers: { "user-agent": "trm-live/1.0 (+github pages scoreboard)" },
    cf: { cacheTtl: 5, cacheEverything: false },
  });
  const html = await res.text();
  const lines = htmlToLines(html);
  const start = lines.findIndex((l) => /THIS ROUND'?S GAMES/i.test(l));
  return {
    updated: new Date().toISOString(),
    totalLines: lines.length,
    gamesIndex: start,
    fixturesParsed: parseFixtures(lines).length,
    lines: start >= 0 ? lines.slice(start, start + 220) : lines.slice(0, 220),
  };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    const path = new URL(request.url).pathname.replace(/\/+$/, "");
    if (path.endsWith("/players")) {
      return serve("https://trm-live.internal/players", buildPlayers, ctx);
    }
    if (path.endsWith("/raw")) {
      try {
        return new Response(JSON.stringify(await buildRaw()), { headers: CORS });
      } catch (e) {
        return new Response(JSON.stringify({ error: String((e && e.message) || e) }), { status: 502, headers: CORS });
      }
    }
    return serve("https://trm-live.internal/data", buildFeed, ctx);
  },
};
