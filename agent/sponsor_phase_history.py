"""Cross-run tracking of which sponsors have run a Phase 1 trial in a given
indication, so a later run can recognize when one of those same sponsors
files a brand-new Phase 2 trial — the BD proposal's "new Phase 2 filing by
a sponsor that previously ran a Phase 1 trial" trigger.

This needs state that persists between runs, unlike clinicaltrials_gov.py's
other two signals (each a single stateless query): the Phase 1 trial and
its later Phase 2 filing are typically found weeks or months apart, in two
different runs. Mirrors seen_leads.py's pattern — a small local JSON file,
loaded/updated/saved once per run — but tracks sponsors, not leads.
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


def _sponsor_name(study: dict):
    protocol = study.get("protocolSection") or {}
    sponsor_collab = protocol.get("sponsorCollaboratorsModule") or {}
    return (sponsor_collab.get("leadSponsor") or {}).get("name")


def record_phase1_sponsors(history: dict, indication: str, phase1_studies: list, today_iso: str) -> None:
    """Add/update every sponsor found in `phase1_studies` (raw
    ClinicalTrials.gov study dicts, as returned by clinicaltrials_gov._get())
    under `indication` in `history`, in place. Safe to call every run —
    already-recorded sponsors/trials are just refreshed, not duplicated.
    """
    bucket = history.setdefault(indication.strip().lower(), {})
    for study in phase1_studies:
        sponsor = _sponsor_name(study)
        nct_id = (study.get("protocolSection") or {}).get("identificationModule", {}).get("nctId")
        if not sponsor or not nct_id:
            continue
        key = sponsor.strip().lower()
        entry = bucket.setdefault(key, {"display_name": sponsor, "phase1_nct_ids": [], "last_updated": today_iso})
        if nct_id not in entry["phase1_nct_ids"]:
            entry["phase1_nct_ids"].append(nct_id)
        entry["display_name"] = sponsor
        entry["last_updated"] = today_iso


def find_returning_sponsors(history: dict, indication: str, phase2_studies: list) -> list:
    """Return the subset of `phase2_studies` (raw study dicts) whose sponsor
    already has at least one recorded Phase 1 trial in `indication` —
    including one recorded earlier in this same call to
    record_phase1_sponsors(), so a sponsor's Phase 1 and its new Phase 2
    filing can be correlated even the first time both happen to show up in
    the same run, not only across separate runs.
    """
    bucket = history.get(indication.strip().lower(), {})
    matches = []
    for study in phase2_studies:
        sponsor = _sponsor_name(study)
        if sponsor and sponsor.strip().lower() in bucket:
            matches.append(study)
    return matches
