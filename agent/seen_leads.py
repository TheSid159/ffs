"""Local dedup so repeated runs don't keep resurfacing the same leads.

Keeps a small JSON file of leads already shown in a previous report,
keyed on the most stable fields available (company domain + abstract
number/trial name), since the model's exact wording for company/trial
names can vary slightly between runs even for the same underlying trial.

This is a lightweight, local stopgap — the real source of truth for
"already contacted" / "declined" will eventually be HubSpot once that
integration exists. This file only prevents showing you the same research
result twice; it says nothing about outreach status.
"""

import json
from datetime import date
from pathlib import Path


def dedup_key(lead: dict) -> str:
    """Build a stable identifier for a lead, preferring fields least likely
    to vary between runs (company_domain, abstract_number) over free-text
    fields the model rephrases run to run (company_name, trial_name)."""
    domain = (lead.get("company_domain") or "").strip().lower()
    abstract_number = (lead.get("abstract_number") or "").strip().lower()
    trial_name = (lead.get("trial_name") or "").strip().lower()
    company_name = (lead.get("company_name") or "").strip().lower()

    if domain and abstract_number:
        return f"{domain}|{abstract_number}"
    if domain and trial_name:
        return f"{domain}|{trial_name}"
    return f"{company_name}|{trial_name}"


def load_seen(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(path: Path, seen: dict) -> None:
    path.write_text(json.dumps(seen, indent=2, sort_keys=True), encoding="utf-8")


def split_new_and_repeats(leads: list, seen: dict) -> tuple:
    """Return (new_leads, repeat_leads). Updates `seen` in place — new leads
    are recorded with today's date, repeats get their `last_seen` bumped."""
    today = date.today().isoformat()
    new_leads, repeat_leads = [], []
    for lead in leads:
        key = dedup_key(lead)
        existing = seen.get(key)
        if existing:
            existing["last_seen"] = today
            repeat_leads.append((lead, existing.get("first_seen", "unknown date")))
        else:
            seen[key] = {
                "company_name": lead.get("company_name"),
                "trial_name": lead.get("trial_name"),
                "first_seen": today,
                "last_seen": today,
            }
            new_leads.append(lead)
    return new_leads, repeat_leads
