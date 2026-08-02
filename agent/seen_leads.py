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
from datetime import date, timedelta
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

    if signal_type in (
        "new_registration",
        "trial_milestone_approaching",
        "trial_recently_completed",
        "phase2_filing_by_returning_sponsor",
    ):
        # These are all ClinicalTrials.gov-sourced (see clinicaltrials_gov.py)
        # and always carry a real NCT number as registry_id — a far more stable
        # key than the free-text detail fallback below, which embeds a
        # completion-date estimate that could shift slightly between runs.
        registry_id = (lead.get("registry_id") or "").strip().lower()
        if domain and registry_id:
            return f"{signal_type}|{domain}|{registry_id}"
        return f"{signal_type}|{company_name}|{registry_id}"

    if signal_type == "trial_site_expansion":
        # Also ClinicalTrials.gov-sourced with a stable registry_id, but
        # unlike the four signals above, the *same* trial can legitimately
        # fire this signal again in a later run once it adds more sites on
        # top of the ones already reported — that's a genuinely new event,
        # not a repeat. So the key also folds in the site count at the time
        # of this event (see clinicaltrials_gov.find_site_expansion()'s
        # site_expansion_snapshot field); a second, larger expansion gets a
        # different key and correctly surfaces as new, while an identical
        # re-run (same trial, same site count) still dedups as a repeat.
        registry_id = (lead.get("registry_id") or "").strip().lower()
        snapshot = lead.get("site_expansion_snapshot")
        return f"{signal_type}|{registry_id}|{snapshot}"

    if signal_type == "sec_filing_signal":
        # registry_id is the filing's SEC accession number (adsh) — a stable
        # per-document ID, unlike the free-text detail fallback below.
        registry_id = (lead.get("registry_id") or "").strip().lower()
        return f"{signal_type}|{company_name}|{registry_id}"

    if signal_type == "phase_transition_deep_signal":
        # signal_detail here is a fresh Claude-synthesized summary across
        # however many sources it found *this run* — even more likely to be
        # reworded between runs than the other free-text types below, so
        # prefer drug_asset_name/trial_name (real proper nouns Claude
        # extracted, not prose it composed) over signal_detail.
        asset = (lead.get("drug_asset_name") or lead.get("trial_name") or "").strip().lower()
        if domain and asset:
            return f"{signal_type}|{domain}|{asset}"
        return f"{signal_type}|{company_name}|{asset}"

    # Every other signal type (funding, leadership_change, conference_highlight,
    # regulatory_designation, regulatory_milestone, trial_expansion,
    # protocol_amendment, hiring_signal, vendor_switch_signal, and
    # press_release_signal — RSS entries have no registry ID either): no
    # natural unique ID, so key on the free-text detail too — same variability
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


def recent_entries(path: Path, within_days: int) -> list:
    """Return a thin lead-shaped list — [{"company_name": ..., "signal_detail": ...}]
    — for every seen-leads entry at `path` last seen within `within_days`
    days. Used for lightweight, zero-cost cross-search awareness between two
    *paid* Claude searches (signal-sweep and phase-transitions — see
    bd_agent.build_signal_sweep_prompt()/build_phase_transition_prompt()):
    unlike the free trial-signals pre-check, there's no way to re-run the
    other paid search for free just to see what it already found, so this
    reads its persisted dedup state instead. Deliberately thin — a
    seen-leads entry only ever stores company_name/trial_name/first_seen/
    last_seen (see split_new_and_repeats()), not the original lead's full
    signal_detail — good enough to tell Claude "this company/trial was
    already reported by the other search recently," not a substitute for
    that search's own report. Missing or unreadable file returns []."""
    seen = load_seen(path)
    if not seen:
        return []
    cutoff = (date.today() - timedelta(days=within_days)).isoformat()
    entries = []
    for value in seen.values():
        if (value.get("last_seen") or "") >= cutoff:
            trial = value.get("trial_name")
            entries.append(
                {
                    "company_name": value.get("company_name"),
                    "signal_detail": f"previously reported (trial/asset: {trial})" if trial else "previously reported",
                }
            )
    return entries


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
