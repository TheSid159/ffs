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
    fields the model rephrases run to run (company_name, trial_name).

    Always prefixed with signal_type: a company can have a funding lead AND
    a leadership-change lead at the same time, and without the prefix both
    would fall back to the same "company_name|" key and the second would be
    wrongly treated as a repeat of the first.
    """
    signal_type = (lead.get("signal_type") or "trial_result").strip().lower()
    domain = (lead.get("company_domain") or "").strip().lower()
    company_name = (lead.get("company_name") or "").strip().lower()

    if signal_type == "trial_result":
        abstract_number = (lead.get("abstract_number") or "").strip().lower()
        trial_name = (lead.get("trial_name") or "").strip().lower()
        if domain and abstract_number:
            return f"{signal_type}|{domain}|{abstract_number}"
        if domain and trial_name:
            return f"{signal_type}|{domain}|{trial_name}"
        return f"{signal_type}|{company_name}|{trial_name}"

    if signal_type in ("new_registration", "trial_milestone_approaching", "trial_recently_completed"):
        # The latter two are ClinicalTrials.gov-sourced (see clinicaltrials_gov.py)
        # and always carry a real NCT number as registry_id — a far more stable
        # key than the free-text detail fallback below, which embeds a
        # completion-date estimate that could shift slightly between runs.
        registry_id = (lead.get("registry_id") or "").strip().lower()
        if domain and registry_id:
            return f"{signal_type}|{domain}|{registry_id}"
        return f"{signal_type}|{company_name}|{registry_id}"

    # Every other signal type (funding, leadership_change, conference_highlight,
    # regulatory_designation, regulatory_milestone, trial_expansion,
    # protocol_amendment, hiring_signal, vendor_switch_signal): no natural
    # unique ID, so key on the free-text detail too — same variability
    # caveat as company_name/trial_name above applies (Claude may rephrase
    # between runs), but colliding two different signals at one company is
    # worse.
    detail = (lead.get("signal_detail") or lead.get("abstract_title") or "").strip().lower()
    if domain:
        return f"{signal_type}|{domain}|{detail}"
    return f"{signal_type}|{company_name}|{detail}"


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
