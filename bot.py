#!/usr/bin/env python3
"""
Daily Gherkin bot
-----------------
Picks one entry per round from the bank, fetches any live data (Deezer preview),
builds days/current.json, then commits and pushes to GitHub Pages.

Run daily: python3 bot.py
Dry run:   python3 bot.py --dry-run
"""

import argparse
import json
import os
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

REPO_DIR  = Path(__file__).parent
BANK_DIR  = REPO_DIR / "bank"
DAYS_DIR  = REPO_DIR / "days"
STATE_FILE = REPO_DIR / "state.json"

DAYS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_state():
    if STATE_FILE.exists():
        return load_json(STATE_FILE)
    return {"day": 0, "used": {}}

def save_state(state):
    save_json(STATE_FILE, state)

def pick_entry(entries, used_ids, exclude_ids=None):
    """
    Pick a random unused entry.
    If all entries have been used, reset the used list (cycle repeats).
    exclude_ids: additional ids to avoid this round (scheduling constraints).
    """
    available = [e for e in entries if e["id"] not in used_ids]
    if not available:
        used_ids.clear()
        available = entries[:]

    if exclude_ids:
        filtered = [e for e in available if e["id"] not in exclude_ids]
        if filtered:
            available = filtered  # only apply exclusion if it doesn't empty the pool

    return random.choice(available)


# ---------------------------------------------------------------------------
# Live data fetchers
# ---------------------------------------------------------------------------

def get_deezer_preview(search_query):
    """Return the best Deezer preview URL for a search query."""
    try:
        r = requests.get(
            "https://api.deezer.com/search",
            params={"q": search_query, "limit": 10},
            timeout=10,
        )
        r.raise_for_status()
        for track in r.json().get("data", []):
            if track.get("preview"):
                return {
                    "preview_url": track["preview"],
                    "deezer_id": track["id"],
                    "title": track["title"],
                    "artist": track["artist"]["name"],
                }
    except Exception as e:
        print(f"  [warn] Deezer fetch failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_day(day_num, state):
    used = state.setdefault("used", {})

    banks = {
        k: load_json(BANK_DIR / f"{k}.json")["entries"]
        for k in ("career_path", "starting11", "earworm",
                  "guess_the_year", "pin_the_pickle", "filmography")
    }

    # Filmography scheduling constraint: don't play Bale (id=9) and
    # Crowe (id=10) on consecutive days (both open with Thor: Love and Thunder).
    film_exclude = set()
    last_film_id = used.get("filmography_last_id")
    if last_film_id in (9, 10):
        film_exclude = {9, 10} - {last_film_id}

    day = {"day": day_num, "date": str(date.today())}

    # --- Career Path ---
    career_used = used.setdefault("career_path", [])
    entry = pick_entry(banks["career_path"], career_used)
    career_used.append(entry["id"])
    day["career"] = {
        "answer":          entry["answer"],
        "display":         entry["display"],
        "wikipedia_title": entry["wikipedia_title"],
        "clubs":           entry["clubs"],  # list of {name, apps, goals}
    }

    # --- Starting 11 ---
    s11_used = used.setdefault("starting11", [])
    entry = pick_entry(banks["starting11"], s11_used)
    s11_used.append(entry["id"])
    day["starting11"] = {
        "answer":       entry["answer"],
        "display":      entry["display"],
        "match":        entry["match"],
        "lineup_hint":  entry["lineup_hint"],
    }

    # --- Earworm ---
    ew_used = used.setdefault("earworm", [])
    entry = pick_entry(banks["earworm"], ew_used)
    ew_used.append(entry["id"])
    deezer = get_deezer_preview(entry["deezer_search"])

    # Download the MP3 so it never expires in the browser
    local_mp3 = None
    if deezer and deezer["preview_url"]:
        try:
            mp3_path = DAYS_DIR / "earworm.mp3"
            r = requests.get(deezer["preview_url"], timeout=20)
            r.raise_for_status()
            mp3_path.write_bytes(r.content)
            local_mp3 = "days/earworm.mp3"
            print(f"  [earworm] Downloaded MP3 ({len(r.content)//1024}KB)")
        except Exception as e:
            print(f"  [warn] MP3 download failed: {e}")

    day["earworm"] = {
        "display":      entry["display"],
        "preview_url":  local_mp3 or (deezer["preview_url"] if deezer else None),
        "deezer_id":    deezer["deezer_id"] if deezer else None,
    }
    if not deezer:
        print(f"  [warn] No Deezer preview found for: {entry['deezer_search']}")

    # --- Guess the Year ---
    gty_used = used.setdefault("guess_the_year", [])
    entry = pick_entry(banks["guess_the_year"], gty_used)
    gty_used.append(entry["id"])
    day["guess_the_year"] = {
        "answer": entry["answer"],
        "clues":  entry["clues"],
    }

    # --- Pin the Pickle ---
    ptp_used = used.setdefault("pin_the_pickle", [])
    entry = pick_entry(banks["pin_the_pickle"], ptp_used)
    ptp_used.append(entry["id"])
    day["pin_the_pickle"] = {
        "question":      entry["question"],
        "location_name": entry["location_name"],
        "lat":           entry["lat"],
        "lng":           entry["lng"],
    }

    # --- Filmography ---
    film_used = used.setdefault("filmography", [])
    entry = pick_entry(banks["filmography"], film_used, exclude_ids=film_exclude)
    film_used.append(entry["id"])
    used["filmography_last_id"] = entry["id"]
    day["filmography"] = {
        "answer":  entry["answer"],
        "display": entry["display"],
        "films":   entry["films"],
    }

    return day


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(*args, check=True):
    result = subprocess.run(["git", "-C", str(REPO_DIR)] + list(args),
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  [git error] {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()

def push_to_github(day_num, day_date):
    print("Pushing to GitHub...")
    git("add", "days/current.json", f"days/day{day_num}.json", "state.json")
    git("commit", "-m", f"Day {day_num} — {day_date}")
    git("push")
    print("  Pushed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and print but don't save state or push")
    args = parser.parse_args()

    state = load_state()
    day_num = state["day"] + 1

    print(f"Building day {day_num} ({date.today()})...")
    day_config = build_day(day_num, state)

    print()
    print(f"  Career path:    {day_config['career']['display']}")
    print(f"  Starting 11:    {day_config['starting11']['display']} "
          f"({day_config['starting11']['match']['home']} vs "
          f"{day_config['starting11']['match']['away']} "
          f"{day_config['starting11']['match']['date'][-4:]})")
    print(f"  Earworm:        {day_config['earworm']['display']} "
          f"{'✓' if day_config['earworm']['preview_url'] else '✗ NO PREVIEW'}")
    print(f"  Guess the year: {day_config['guess_the_year']['answer']}")
    print(f"  Pin the pickle: {day_config['pin_the_pickle']['question'][:60]}...")
    print(f"  Filmography:    {day_config['filmography']['display']}")
    print()

    if args.dry_run:
        print("[dry-run] Not saving state or pushing.")
        return

    # Persist
    state["day"] = day_num
    day_file = DAYS_DIR / f"day{day_num}.json"
    save_json(day_file, day_config)
    save_json(DAYS_DIR / "current.json", day_config)
    save_state(state)
    print(f"Saved {day_file} and days/current.json")

    push_to_github(day_num, day_config["date"])

if __name__ == "__main__":
    main()
