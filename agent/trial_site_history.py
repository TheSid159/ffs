"""Cross-run tracking of each trial's site (location) list, so a later run
can recognize when a trial has added new sites since we last saw it — the
`trial_site_expansion` signal in clinicaltrials_gov.py. Multi-region/
multi-site trials are where centralized imaging review beats inconsistent
local site reads (see CLAUDE.md's rationale for the conference search's
`trial_expansion` signal type) — this is the free, ClinicalTrials.gov-
sourced version of the same idea, using structured data instead of Claude
web search.

Mirrors sponsor_phase_history.py's pattern: a small local JSON file,
loaded/updated/saved once per run, tracking trials (by NCT ID) instead of
sponsors. A trial's site list is compared against what was recorded last
run; sites present now but not in that recording are the signal. The first
time a given trial is seen, there's nothing to diff against yet, so no
lead fires — only its site list is recorded as a baseline for a future run
to compare against (same "record now, check next time" shape as
sponsor_phase_history.py's Phase 1 sponsor tracking).
"""

import json
from pathlib import Path


def load_history(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_history(path: Path, history: dict) -> None:
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def site_keys(study: dict) -> list:
    """One stable string per site — facility+city+country, lowercased —
    since ClinicalTrials.gov doesn't expose a persistent per-location ID
    to key on directly."""
    locations = ((study.get("protocolSection") or {}).get("contactsLocationsModule") or {}).get("locations") or []
    keys = []
    for loc in locations:
        facility = (loc.get("facility") or "").strip().lower()
        city = (loc.get("city") or "").strip().lower()
        country = (loc.get("country") or "").strip().lower()
        if facility or city or country:
            keys.append(f"{facility}|{city}|{country}")
    return keys


def site_label(key: str) -> str:
    """Turn a site_keys() entry back into a human-readable label for report
    text — prefer the facility name, falling back to city, then country,
    whichever was actually present when the key was built."""
    facility, city, country = (key.split("|") + ["", ""])[:3]
    return (facility or city or country or "an unnamed site").title()


def record_and_find_new_sites(history: dict, indication: str, studies: list, today_iso: str) -> dict:
    """Update `history` in place with each study's current site list, and
    return {nct_id: [new site keys]} for every study that had a previously
    recorded, non-empty site list AND has at least one site now that wasn't
    in it. A study seen for the first time (or whose prior recording had no
    sites at all) is still recorded, but never appears in the returned
    dict — there's nothing meaningful to compare its first real sighting
    against, and treating "went from no location data to some" as an
    "expansion" would just be noise from ClinicalTrials.gov filling in
    location data late, not a real expansion event.
    """
    bucket = history.setdefault(indication.strip().lower(), {})
    expansions = {}
    for study in studies:
        nct_id = (study.get("protocolSection") or {}).get("identificationModule", {}).get("nctId")
        if not nct_id:
            continue
        current_sites = site_keys(study)
        entry = bucket.get(nct_id)
        if entry is not None:
            previous_sites = set(entry.get("site_keys") or [])
            new_sites = [s for s in current_sites if s not in previous_sites]
            if new_sites and previous_sites:
                expansions[nct_id] = new_sites
        bucket[nct_id] = {"site_keys": current_sites, "last_updated": today_iso}
    return expansions
