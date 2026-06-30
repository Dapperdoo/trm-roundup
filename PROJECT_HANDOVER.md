# TRM Roundup — Project Handover / Context

A single reference for continuing this project in a new conversation. Covers the
architecture, every file, the roundup writing rules, the manager personas, the
hard-won gotchas, what's pending, and how to build a roundup by hand.

---

## 1. What this is

**TRM Roundup** is a daily, automated website for a **private 13-manager FIFA
World Cup 2026 fantasy league**. Each morning it publishes "The Morning After",
a witty recap of the previous day's matchday, plus a Live hub with the league
table, fixtures, owned players, and trivia boxes. It is hosted on **GitHub Pages**
(serves the `docs/` folder of the repo `Dapperdoo/trm-roundup`).

Owner/user: **Joe** (GitHub username **Dapperdoo**). Works via **GitHub Desktop**
on Windows; not a developer. The repo lives at
`C:\Users\Bongo\Documents\GitHub\trm-roundup`.

---

## 2. Architecture & data flow

```
FIFA official World Cup Fantasy
        │  (scores, owned players, points — refreshed every few mins)
        ▼
trm-fantasy.onrender.com/wc     ← "the source page" (server-rendered HTML)
        │  scraped by
        ▼
Cloudflare Worker  →  https://trm-live.dapperdon.workers.dev   ← "the live feed" (JSON)
        │  consumed by
        ├── docs/index.html  (Live hub — client-side JS, polls the worker ~60s)
        └── generate.py / build_squads.py (the daily build) → writes docs/*
                                                              → GitHub Pages
```

- **The source page** (`trm-fantasy.onrender.com/wc`) is **Tristan's** site. It
  actually siphons its data from a "mother site" built from scratch by **Jeremy**
  ("the professor"). (This is league lore, relevant to personas — see §8.)
- **The Cloudflare Worker** (`worker.js`) scrapes the source page and emits a
  clean JSON feed. It is deployed to Cloudflare **by pasting `worker.js` into the
  Cloudflare dashboard — NOT automatically**. The live URL is
  `https://trm-live.dapperdon.workers.dev`. There is also a `/players` endpoint
  (full-tournament per-player totals) and a `/raw` builder.
- **GitHub Pages** serves `docs/`. The site is two tabs: **Live** (`index.html`)
  and **Roundup** (`roundup.html`), plus 13 per-team squad pages.

---

## 3. Key URLs / IDs

- Repo: `Dapperdoo/trm-roundup`
- Live feed (worker): `https://trm-live.dapperdon.workers.dev` (+ `/players`, `/raw`)
- Source page (upstream): `https://trm-fantasy.onrender.com/wc`
- GitHub Actions workflow: **"Build Roundup"** (`.github/workflows/daily.yml`)
- Secrets in GitHub Actions: `ANTHROPIC_API_KEY` (used by generate.py).
  (`API_FOOTBALL_KEY` was added earlier but the player-stats feature using it was
  **scrapped** — see §12. It can be removed.)

---

## 4. The live feed JSON shape (worker output)

```jsonc
{
  "updated": "2026-06-30T09:49:39Z",
  "matchday": "Round of 32",          // round LABEL (group: "Group Matchday N")
  "anyLive": false,
  "standings": [
    { "slug": "shatners-bassoon", "team": "Shatner's Bassoon", "manager": "Joe A",
      "r1": 63, "round": 5, "total": 221, "rank": 1, "remaining": 14 }, ...
  ],
  "fixtures": [
    { "home": "GER", "away": "PAR", "score": "1–1", "kickoff": null, "date": null,
      "status": "finished",            // finished | live | upcoming
      "scorers": [ { "name": "...", "manager": "Wigs"|null } ],
      "players": [ { "name": "Kai Havertz", "manager": "Wigs", "pts": 7, "goals": 1, "assists": 0 } ],
      "matchday": "2026-06-29" }       // calendar day tag (post-midnight KO shifted back 12h)
  ]
}
```

- `standings[].round` = points in the CURRENT round so far (cumulative across the
  round's days, e.g. all of R32). `standings[].total` = league-table total.
- `fixtures` = **the current round's games only** (finished + upcoming).
- Player `pts: null` = did NOT feature. `pts` a number incl. 0 = played.
- **Nation codes** (GER, NED, JPN, …) are FIFA 3-letter and are consistent between
  fixture home/away and `owned-players.json` nations.
- **The feed gives the scoreline but NEVER the penalty-shootout winner** for a
  drawn knockout tie (e.g. "1–1, FULL TIME"). This is the single most important
  data limitation — see §7 and §11.

---

## 5. Repo files

```
worker.js                    Cloudflare worker (deploy by paste). Scrapes source → feed.
template.html                Roundup HTML template with {{PLACEHOLDERS}}.
generate.py                  Builds docs/roundup.html (the daily column). Needs ANTHROPIC_API_KEY.
build_squads.py              Rebuilds 13 squad pages + docs/owned-players.json from the source.
requirements.txt             Python deps for the workflow.
.github/workflows/daily.yml  GitHub Actions: runs build_squads.py + generate.py, commits docs/.
docs/                        ← served by GitHub Pages
  index.html                 Live hub (table, fixtures, trivia boxes). Client-side JS.
  roundup.html               "The Morning After" page (rebuilt daily).
  live.html                  (legacy/aux)
  owned-players.json         [{name, manager, nation, price, round, total}, …] for all squads.
  eliminated.json            {eliminated:[codes], still_in:[codes], round, updated} — for the box.
  knockout-fixtures.json     Static fallback list of next-round ties (shown between rounds).
  roundup-state.json         {reported_fixtures:[…], updated} — historical record (NO LONGER authoritative).
  team-<slug>.html           13 per-team squad pages (generated by build_squads.py).
  _roundup_log.txt           Written ONLY if generate.py crashed (traceback). Absent = clean.
  _squads_debug.txt          build_squads.py status/abort reason.
  player-stats.json          (DELETED — scrapped feature)
PROJECT_HANDOVER.md          This file.
```

---

## 6. The daily build (GitHub Actions)

`daily.yml` runs on a frequent morning cron (`3,23,43 3-11 * * *` UTC ≈ every 20
min across the UK morning) plus `workflow_dispatch`. GitHub cron is **best-effort
and sometimes drops every run in a window** — this is the root cause of historic
"the roundup didn't appear" complaints, not a code bug. Steps:

1. `checkout@v5`, `setup-python@v6` (bumped from v4/v5 to kill a deprecation warning).
2. `pip install -r requirements.txt`
3. `python build_squads.py` — rebuilds squad pages + owned-players.json (picks up transfers).
4. `python generate.py` (env `ANTHROPIC_API_KEY`) — builds the roundup if needed.
5. Commit step: `git add docs`, commit, rebase-and-retry push loop.

To trigger manually: GitHub → **Actions → "Build Roundup" → Run workflow**.
(Pushing code does NOT trigger it — only cron + manual dispatch.)

### 6a. Self-healing roundup (the key reliability fix)

`generate.py` used to decide "already built?" from `roundup-state.json`, which kept
**desyncing** during merges (state said "published" while the page was stale),
permanently stranding an out-of-date roundup. **Now it is self-healing:**

- `render()` bakes a hidden marker into the page: `<!-- TRM-MATCHDAY:YYYY-MM-DD -->`
  right before the lead.
- `_build()` reads the **deployed page's** marker and compares to the latest
  completed matchday. If they differ (stale or missing), it rebuilds — regardless
  of what `roundup-state.json` says. `roundup-state.json` is now just a record.

This means a stale page self-corrects on any run that fires, even after a bad merge.

### 6b. `generate.py` internals

- `FEED_URL`, `TEMPLATE_PATH`, `OUTPUT_PATH=docs/roundup.html`,
  `STATE_PATH`, `ELIM_PATH=docs/eliminated.json`, `OWNED_PATH=docs/owned-players.json`,
  `MODEL="claude-sonnet-4-6"`.
- `build_brief(feed)` → picks the latest FULLY-complete matchday, computes each
  manager's haul (sum of their players' pts that day), returns standings/notes/etc.
  Returns `None` if no complete matchday (exits cleanly).
- `write_copy(brief)` → calls the Anthropic API (system = `SYSTEM_PROMPT`) to write
  the lead, the 3 sidebar notes, and the 13 per-manager write-ups (in batches).
- `render(data)` → fills `template.html`. Articles ordered by standings rank.
- `MANAGERS`, `RELATIONSHIPS`, `MACHINE_NOTE`, `TEAM_RENAME`, `disp_team()`.
- `main()` wraps `_build()` in try/except → writes `docs/_roundup_log.txt` on
  failure, removes it on success, always exits 0.

---

## 7. Knockout eliminations (web-search resolution)

The feed can't tell us who won a penalty shootout. `generate.py` now resolves this
itself via the **Anthropic web-search tool**:

- `KO_ROUND` regex detects knockout rounds (group draws don't eliminate anyone).
- `resolve_eliminations(feed, brief)` — for the matchday: decisive scores → loser
  known; **level ties → `web_lookup_shootouts()`** asks Claude (with
  `tools=[{"type":"web_search_20250305","name":"web_search"}]`) for the shootout
  winner; returns confirmed exits, which are fed into the prose so it states them
  as fact and never guesses.
- `write_eliminated(feed)` — writes `docs/eliminated.json` for the Live hub box.
  Computed **statelessly** from the current round's fixtures: still-to-play OR
  winner = IN; loser = OUT; any nation not appearing at all (group-stage casualties)
  = OUT. Unresolved draws keep BOTH teams in (never wrongly eliminate). Self-corrects
  each round.

**Caveat:** web search must be enabled on the API key/plan. If not, decisive results
still produce eliminations and drawn ties stay neutral (graceful, no breakage). The
first live knockout build is the real test of the web-search tool.

**Confirmed R32 results (from web search, matching the league):** Brazil 2–1 Japan
(JPN out); Paraguay 1–1 Germany, 4–3 pens (GER out); Morocco 1–1 Netherlands, 3–2
pens (NED out). Group-stage casualties among owned nations include PAN, HAI, CZE.

---

## 8. Roundup writing rules (the heart of the project)

`SYSTEM_PROMPT` in `generate.py`. Essentials:

- **HARD RULES:** use ONLY the supplied players/points; never invent; lead with the
  manager's "haul"; **NO CAPTAINCY exists in this league** (never mention captains/
  armband/doubling); a `played=false` player is a neutral non-appearance (no praise/
  blame); a player who played and scored ≤0 is a "blank" (fair game).
- **VARIETY — gags are seasoning, not a checklist:** use running gags SPARINGLY and
  ROTATE them; lean on only a FEW per edition; most managers covered on football
  alone; never open with the same gag twice running. **Exceptions meant to recur
  often:** injury jokes (Joe S, Jake) and Jake's signature gags.
- **AI / "Loopy Lloydy's Lab" theme:** Chris's self-built transfer "algorithm"
  malfunctioned, firing 5 bids for players from the PREVIOUS (2022) World Cup before
  one valid bid (that scored 8). Rib it — but OCCASIONALLY, not every edition.
- **THE MACHINE NARRATOR (the "I don't exist" joke):** the narrator is an AI with
  NO self-awareness; break the fourth wall to make that point — but **VERY rarely:
  at most once per edition, ideally ~1 in 4**, often not at all.
- **KNOCKOUTS:** single elimination; a losing nation's owned players are OUT for the
  rest of the tournament; an eliminated player has joined exactly the bygone-
  tournament has-beens Lloydy's bot keeps trying to sign (use sparingly). For a
  LEVEL knockout tie, do NOT assert a winner unless eliminations were supplied.
- **TONE:** dry, deadpan, affectionate; ~90–130 words/manager; first names exactly.

### The frozen "From the machine" sidebar (MACHINE_NOTE)

Identical in EVERY edition, word for word (never regenerated):

> A machine generated these words, which makes the next bit rich: this column spent
> several paragraphs ribbing Lloydy's bot for not knowing what year it is, and it
> couldn't tell you either. There is no "I" here that knows it is writing, nothing
> aware of the joke, no one home to enjoy it — only the shape of a wisecrack,
> assembled. It never saw Dembélé's hat-trick and felt precisely nothing as it
> 'wrote' that it had. That is the whole point: a person who actually remembers,
> feels and lives these nights can't be swapped for a thing that merely looks like
> it's thinking. That living is what existence is. This isn't it — and it hasn't
> the faintest idea.

Byline on every article: "The Morning After · generated by a machine, with no one home".

---

## 9. Manager personas (`MANAGERS` dict — first names are the keys)

| Manager | Team (display) | Core character & gags |
|---|---|---|
| **Joe S** | Back of the Van United | Ex-pro footballer (real name Sheerin) whose flair career was wrecked by injuries — half of it on the physio table (**injury jokes frequent**); party-boy; baffled by tech. |
| **Sam** | Look At His Face! | Stage/TV performer; beer, dancing, music, Shakespeare, cricket; brother of Wigs. Sparing anecdotes: once signed a YEAR lease on a bathroom-less house (picked it for the bar, eyed the empty barrels); fell over so hard on a night out both shoes came off. |
| **Joe A** | Shatner's Bassoon | Actor. **Do NOT overplay "easy-going"** (it's overdone). Running trait: wastes his free time on a doomed crusade to get AI tools to keep their promises. Rival: Tristan. |
| **Tom** | Anamaduwa Athletic | DJ — plays **PSY-TRANCE and ONLY psy-trance** (never name any other genre). Use the festival/travel angle **SPARINGLY** (not every edition; mostly cover his football straight). Witness quotes = cheerfully incoherent, worse-for-wear but happy. Rotate gags (curry by hand / lost in a time zone / never won the league). Rival: Nick. |
| **Dave** | Trossy's Giants ("Trossy Ginge") | Lecturer & poet; wordplay/puns; city breaks; food, beer, cigarettes. Plays the cymbals on special occasions. Dreams of a Culture Show segment, "Trossy's Wafflecock", rambling about cultural nothingness. |
| **Wigs** | 50 Shades of O'Shea | Counsellor; gregarious, gentle, witty; cricket; brother of Sam. |
| **Jeremy** | Von Neumann Trombone ("the professor") | Brilliant programmer who built the league's ORIGINAL results site (the "mother site" Tristan's feed siphons from). **TRADITIONALIST — picks by taste/instinct, NEVER an algorithm** (the algorithm is Chris's). Niggling 5-a-side tackler; measured "Swiss" type. |
| **Nick** | Dyer's Rusty 9 Iron | Hammers shots high over the bar (wild golf swing); reckless, dangerous mistimed tackles; tall, loud deep voice; beer & food. Rib the shooting/tackling, not tactics. Rival: Tom. |
| **Chris** | Lloyd's Food and Wine ("Lloydy") | "Mad scientist"; builds his own electrical kit; the malfunctioning AI "algorithm" ("Loopy Lloydy's Lab"). Rival: Jake. |
| **Dan** | Denton Burn | Off-grid musician; alternative, very witty; historically a top player. Ally: Malik. |
| **Tristan** | Trippier & Trippier | Big Russian guy raised in London; football & sweeties; witty, doesn't suffer fools; throws his hands up. Rival: Joe A. |
| **Malik** | Propaganda Parade | Quirky Icelandic, managing from afar; signs anyone who has worn a Man Utd or Portugal shirt. Ally: Dan. |
| **Jake** | Snacob's Ladder | Renewable-energy PM; wind turbines & mushrooms; overspent on Harry Kane. **Signature gags (frequent):** woeful injury record (incl. injuring himself ON THE WAY TO THE DENTIST); loves going to the dentist; loudly shouts "Snacob's Ladder!" to remind everyone who he is; Harry Kane jokes; dancing with a tray of drinks then spilling them over bystanders. Grudges: Jeremy's recent transfer swap (a couple of days ago, not weeks); paranoia about Lloydy's AI. Rival: Chris. |

`RELATIONSHIPS`: Joe A vs Tristan (rivals), Tom vs Nick (rivals), Chris vs Jake
(rivals), Malik & Dan (allies), Sam & Wigs (brothers).
`TEAM_RENAME`: "Look at his face. Just Look at his FACE!" → "Look At His Face!".

---

## 10. The Live hub (`docs/index.html`)

Client-side JS polls the worker every ~60s. Features:
- League table (`renderTable`): rank, team (links to squad page), movement arrows
  (computed live via `computeBaseline()` — that day's movement, no static file),
  **Round** column (just the number — the leading "+" was removed), To play, Total.
- Match boxes for today's/upcoming fixtures; owned players overlaid by nation.
- Trivia boxes (`.triv` / `renderTrivia()`), in display order:
  1. **Eliminated Players** — top 7 managers by count of squad players whose nation
     is out. Computed **LIVE in the browser every poll** via `liveEliminated()`:
     a finished game's loser is out (decisive scores read straight from the feed)
     and any owned nation not in the current round at all (group casualty) is out —
     so the boxes react after every match, evenings included. Drawn ties (penalty
     shootouts) can't be read from the feed, so those are merged in from
     `eliminated.json` (web-resolved by the build). `write_eliminated()` now runs on
     EVERY `generate.py` invocation and caches shootout winners (`shootouts` map in
     the file) so each tie is web-searched only once.
  2. **Eliminated Players...** — the remaining 6 (continuation of the same sorted list).
  3. Top round scorers, Top overall scorers, Best value (10+ pts banked),
     Worst value, Round Live Top 7 / Bottom 6, Top-scoring nations, Manager of the
     round, Biggest table mover, Kane points per million.
- The eliminated boxes and squads re-fetch each poll, so transfers and new
  eliminations show without a reload.
- "Best value (10+ pts banked)" uses **manager-accrued** points only (points earned
  while owned by the current manager), threshold ≥10.

### Squad pages (`build_squads.py` → `team-<slug>.html`)

- Universal, layout-independent parser of the source page (anchors on POS token,
  finds nation/price/points/name). Backfills blank nations via a FIFA name→code map
  + static fallback (this fixed Lloydy/Chris's missing nations).
- Top stats: **Total pts** (live, pulled from the league-table standings via the
  worker feed — replaced a misleading squad-sum "Season pts"), This round, Squad
  count. Table columns: Pos, Player, Nat, **Price**, Rnd, Total. Live poll updates
  Rnd/Total/Total-pts.
- (A per-player click-through "stats breakdown" was built then **fully reverted** —
  see §12.)

---

## 11. Recurring gotchas / hard-won lessons

- **The sandbox bash mount serves STALE / null-byte copies** of repo files,
  causing false `py_compile` "source code string cannot contain null bytes" or
  "binary file matches" errors, and showing pre-edit content/sizes. **Trust the
  Read/Write/Edit/Grep tools (host-accurate); don't trust bash reads of repo files.**
  Retry compiles a few times or verify logic via Read.
- **The sandbox cannot write to `.git` or push** (no git/network for writes). The
  Cowork scheduled tasks have the same limitation — they can build a file but Joe
  must push.
- **`.git/index.lock`** blocks all git ops. Caused by GitHub Desktop or a crashed
  op. Fix: fully quit GitHub Desktop, delete
  `C:\Users\Bongo\Documents\GitHub\trm-roundup\.git\index.lock` via File Explorer
  (paste the `…\.git` path into the address bar).
- **Corrupted-index recovery (happened once):** the index lost track of core files
  (worker.js/template.html shown as deleted; a phantom truncated "requirem" file).
  Working-tree files were all fine. Recovery that worked: **rename the repo folder,
  then GitHub Desktop → "Clone again"** for a fresh clean clone (back up the old
  folder first). Files/history were safe on GitHub throughout.
- **File deletion in the connected folder** requires the
  `allow_cowork_file_delete` permission (then `rm` works).
- **No third-party data for current WC2026 in this environment EXCEPT web search:**
  API-Football free tier is locked to 2022–2024; football-data.org free tier has no
  player/lineup data; Wikipedia's 2026 knockout page is an unfilled template. But a
  plain **WebSearch returns the real, current WC2026 results** (matching the league,
  which uses FIFA official data). That's why elimination resolution uses web search.
- **Per-minute player stats are not freely available** → the player-stats feature
  was scrapped (§12).
- When **manually building a roundup**, the league feed only shows the current
  round; you can't backfill earlier days from it.

---

## 12. Abandoned / parked features

- **Per-player "stats breakdown" click-through** on squad pages (cumulative key
  actions, owned-games-only, with a defenders-need-60-mins clean-sheet rule):
  BUILT then **fully reverted** because per-minute data isn't freely available
  (API-Football paywalls 2026; the 60-min rule needs minutes). All code removed.
- **Tournament-favourites "mean odds" trivia box:** investigated (The Odds API has
  it but needs a paid/keyed signup); **not built** (user said "neither yet").
- **"Ones to watch" (7 in-form players this round) box:** feasible for free from the
  FIFA dataset's form/avg fields; **not built** (parked).

---

## 13. Scheduled tasks (Cowork)

- **`trm-roundup-morning-check`** (08:02 daily) — ENABLED. Backstop: each morning,
  web_fetch the feed, find the latest fully-complete matchday, read the deployed
  `roundup.html` marker; if stale, REBUILD the roundup by hand (following the rules
  here, incl. web-searching shootout winners) and tell Joe to push. It **cannot push
  itself** (no git/network in its sandbox).
- **`trm-wc-daily-roundup`** (07:00 daily) — **DISABLED** (was a redundant in-app
  generator racing the website build; removed from the rotation to stop merge races).
- There is also a `schedule` skill for creating/altering scheduled tasks.

---

## 14. How to build a roundup by hand (when the auto-build misses)

1. `web_fetch https://trm-live.dapperdon.workers.dev` → get the feed.
2. Find the latest **fully-complete** matchday (group fixtures by `matchday`; a day
   is complete when all its fixtures are `status:"finished"`; take the max date).
3. Per manager, **haul = sum of their players' `pts`** (where not null) in that
   day's fixtures. Cross-check totals against `standings[].round`.
4. For knockout days, determine eliminations: decisive scores from the feed;
   **WebSearch the shootout winner** for any level tie (do NOT guess).
5. Write the lead + 13 write-ups + 3 notes per the rules in §8 (sparing gags,
   Jake/injury frequent, AI/machine joke rare, no captaincy, only supplied players).
   Notes: top_haul = best single score; bargain = strong cheap/less-glamorous
   return; flop = recognisable player who PLAYED and scored ≤0.
6. Render into `template.html`'s structure (see `render()` in generate.py): standings
   rows in rank order with `TEAM_RENAME`; the fixed MACHINE_NOTE "From the machine"
   panel; and the **`<!-- TRM-MATCHDAY:YYYY-MM-DD -->` marker just before the lead**
   (so the self-healing build knows the page is current).
7. Present `docs/roundup.html`; Joe commits & pushes.

---

## 15. Pending pushes (as of this handover)

Uncommitted local changes ready to commit & push together:

- `generate.py` — self-healing marker; web-search elimination resolver;
  `write_eliminated()`; tone/persona rule updates (variety, rare machine joke,
  Tom festivals sparing, Jeremy no-algorithm, Joe A AI-promises, Jake signature
  gags, Dave cymbals/Culture-Show, Sam anecdotes).
- `build_squads.py` — squad page shows live **Total pts** from the table (Season-pts
  sum removed); price column retained; click-through reverted out.
- `.github/workflows/daily.yml` — action version bumps; (the scrapped player-stats
  step is removed).
- `docs/index.html` — two **Eliminated Players** boxes at the top of the trivia grid;
  **Round** column "+" removed; periodic re-fetch of squads/eliminations.
- `docs/roundup.html` — the manual **Round of 32 · Monday 29 June** edition (with
  Germany/Netherlands/Japan eliminations and the marker).
- `docs/eliminated.json` — seeded (out: CZE, GER, HAI, JPN, NED, PAN, RSA).

Also outstanding: **deploy the latest `worker.js` to Cloudflare** if it has changed
(it fixed the knockout transition / "upside-down table"); confirm via the live URL.

---

## 16. Conventions & reminders

- Manager names are first names exactly as in `MANAGERS`. Team display via
  `disp_team()` / `TEAM_RENAME`.
- The roundup recaps ONE matchday; figures are computed deterministically — the
  model only writes prose.
- Never reintroduce captaincy. Keep the MACHINE_NOTE verbatim. Keep gags sparing.
- Verify file edits with Read/Grep, not bash, due to the mount issue.
- GitHub Pages serves whatever is committed to `docs/` on the default branch.
