#!/usr/bin/env python3
"""Evening/night feed capture for The Morning After.

WHY THIS EXISTS
The only live view of the league is Tristan's relay, which runs on free hosting that
sleeps after ~15 min idle. The dawn build kept failing because it had to wake a COLD
relay at ~04:00 UTC and often couldn't in time, so generate.py's freshness gate timed
out and the roundup was stranded morning after morning.

This script flips the timing. It runs FREQUENTLY through the evening/night (see
.github/workflows/capture-feed.yml), when the relay is already warm from the live hub
and the games have just finished. Each run wakes + syncs the relay, pulls a genuinely
fresh feed, and — only if that feed cleanly reflects completed reality — COMMITS it to
docs/feed-latest.json. The morning build (generate.py: load_durable_feed) then reads
that durable file instead of gambling on waking a cold relay at dawn.

Stdlib only: no Anthropic key, no `pip install`, nothing to break. The capture job is
fast and cannot fail on a dependency.
"""
import os
import sys
import json
import time
import datetime
import urllib.request

FEED_URL = "https://trm-live.dapperdon.workers.dev"
SYNC_URL = "https://trm-fantasy.onrender.com/wc/sync"  # wakes the relay + forces a FIFA re-pull
OUT_PATH = "docs/feed-latest.json"
UA = "Mozilla/5.0 (compatible; TRM-Capture/1.0; +https://github.com)"


def poke_sync():
    """Fire a single /wc/sync (wakes the relay if asleep + forces a fresh FIFA pull).
    Best-effort; never raises."""
    try:
        req = urllib.request.Request(SYNC_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            r.read()
        return True
    except Exception as e:
        print(f"  /wc/sync poke failed: {e}", flush=True)
        return False


def get_feed():
    """Fetch the worker feed with a cache-buster so we get a fresh scrape, not an edge
    cache."""
    url = FEED_URL + "?cb=" + str(int(time.time()))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def age_minutes(feed):
    """How long ago the relay last synced FIFA, per the feed's own 'updated' stamp."""
    try:
        u = (feed.get("updated") or "").replace("Z", "")
        dt = datetime.datetime.fromisoformat(u)
        return max(0, int((datetime.datetime.utcnow() - dt).total_seconds() // 60))
    except Exception:
        return 9999


def stuck_fixtures(feed, grace_hours=3):
    """Fixtures whose kickoff is comfortably past yet still NOT 'finished' — the
    tell-tale of a stale, pre-games snapshot. A generous grace absorbs kickoff-time
    timezone offsets."""
    now = datetime.datetime.utcnow()
    out = []
    for f in feed.get("fixtures", []) or []:
        if f.get("status") == "finished":
            continue
        d = f.get("date")
        if not d:
            continue
        try:
            ko = datetime.datetime.strptime(f"{d} {f.get('kickoff') or '00:00'}", "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if now - ko >= datetime.timedelta(hours=grace_hours):
            out.append(f'{f.get("home")}-{f.get("away")}')
    return out


def finished_keys(feed):
    return {f'{f.get("home")}-{f.get("away")}'
            for f in feed.get("fixtures", []) or [] if f.get("status") == "finished"}


def main():
    feed = None
    for attempt in range(1, 4):
        poke_sync()
        time.sleep(20)  # allow a cold start + FIFA pull to land
        try:
            feed = get_feed()
        except Exception as e:
            print(f"  feed fetch failed (attempt {attempt}): {e}", flush=True)
            feed = None
            time.sleep(15)
            continue
        age, stuck = age_minutes(feed), stuck_fixtures(feed)
        if age <= 25 and not stuck:
            print(f"  fresh feed on attempt {attempt}: updated ~{age}m ago, nothing stuck.", flush=True)
            break
        print(f"  not clean yet (attempt {attempt}): age ~{age}m, {len(stuck)} past-kickoff "
              f"game(s) still 'upcoming'. Re-syncing...", flush=True)
        feed = None
        time.sleep(15)

    if feed is None:
        print("No clean feed captured this run — leaving the stored snapshot untouched.", flush=True)
        return

    new_keys = finished_keys(feed)
    if not new_keys:
        print("Feed clean but no finished fixtures yet — nothing worth storing.", flush=True)
        return

    old_keys = set()
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                old_keys = finished_keys(json.load(f))
        except Exception:
            old_keys = set()

    # Never overwrite a MORE-complete snapshot with a lesser one: a partial/regressed
    # relay response (strictly fewer finished games) is discarded so a good capture
    # can't be clobbered by a later flaky one.
    if new_keys < old_keys:
        print(f"Stored snapshot has more finished games ({len(old_keys)} > {len(new_keys)}) "
              "— keeping it.", flush=True)
        return

    feed["_captured"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(feed, f, separators=(",", ":"))
    print(f"Stored {OUT_PATH}: {len(new_keys)} finished fixtures, feed age ~{age_minutes(feed)}m.",
          flush=True)


if __name__ == "__main__":
    main()
