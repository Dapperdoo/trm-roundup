# TRM World Cup After-Hours — self-hosted site (free / zero cost)

A free, always-on website that posts a daily, playful "all-night party" roundup of your
13-manager World Cup fantasy league. It rebuilds itself every morning from the live data on
`trm-fantasy.onrender.com` — no server of your own to babysit, and no help needed from
whoever built the original site.

**How it works:** GitHub runs a small script each morning → the script reads the league pages
→ Google's Gemini AI writes the column → the page is saved and published by GitHub Pages at a
public link you can share with the group.

**What it costs: nothing.** GitHub Pages is free, and the AI write-ups use **Google Gemini's
free API tier — no credit card required**. One short request per day sits far inside the free
limits.

---

## What's in this folder

```
generate.py                 the daily generator (uses free Gemini)
template.html               the page design
requirements.txt            python packages
.github/workflows/daily.yml the daily schedule
docs/                       where the live page gets written (starts with a placeholder)
README.md                   this file
```

---

## One-time setup (about 15 minutes, no coding, no payment)

### 1. Get a free Google Gemini API key
- Go to **aistudio.google.com**, sign in with a Google account.
- Click **Get API key → Create API key**. Copy it (starts with `AIza...`). Keep it private.
- No credit card, no billing setup. That's it.

### 2. Put these files in a GitHub repo
- Create a free account at **github.com**.
- Click **New repository** → name it e.g. `trm-after-hours` → **Public** → Create.
- On the repo page click **Add file → Upload files**, then drag in everything from this
  folder (keep the folder structure — uploading the whole folder preserves `.github/` and
  `docs/`). Commit.

### 3. Add your key as a secret
- In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
- Name: `GEMINI_API_KEY`  ·  Value: paste your key  ·  Add secret.

### 4. Turn on GitHub Pages
- **Settings → Pages**.
- Under **Source** choose **Deploy from a branch**.
- Branch: `main`, folder: `/docs` → Save.
- After a minute your public address appears here: `https://<your-username>.github.io/trm-after-hours/`

### 5. Build it for the first time
- Go to the **Actions** tab → if prompted, click to enable workflows.
- Choose **Build After-Hours** on the left → **Run workflow** → Run.
- Give it a minute; it fetches the data, writes the column, and publishes. Refresh your
  Pages link and the roundup should be live.

That's it. From now on it refreshes automatically every morning, for free.

---

## Changing things

- **Time of day:** edit the `cron` line in `.github/workflows/daily.yml`. It's in **UTC**.
  `0 7 * * *` = 07:00 UTC. (8am GMT in winter = `0 8 * * *`; in British Summer Time, 8am GMT
  is 07:00 local clock — pick the UTC time you want.) You can also list several times, e.g.
  add `- cron: "0 19 * * *"` for an evening refresh.
- **Tone or personalities:** edit the `MANAGERS` profiles or `SYSTEM_PROMPT` near the top of
  `generate.py`.
- **Look of the page:** edit `template.html` (it's plain HTML/CSS).
- **Run it on demand:** Actions tab → Build After-Hours → Run workflow.

## Good to know

- The Gemini free tier needs no card and doesn't expire, but Google can change its limits and
  may use free-tier inputs to improve their models — fine for a fun football league. One run a
  day uses a tiny fraction of the daily allowance.
- GitHub may start the scheduled run a few minutes late at busy times — normal.
- The schedule keeps running as long as the rep