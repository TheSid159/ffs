"""Direct integration with the ClinicalTrials.gov API v2 (free, no API key)
for four BD signals that are better found by an exact structured query
than by asking Claude to search the web for them: an early-phase trial
closing in on its primary completion date (a lead-time signal — sponsors
start planning their next phase, imaging vendor included, well before the
readout), an early-phase trial whose status just flipped to COMPLETED (the
same signal, caught from the other side once it's actually happened), a
new, standalone Phase 2 trial filed by a sponsor who's already on record as
running a Phase 1 trial in this indication (see sponsor_phase_history.py for
the cross-run tracking this one needs), and a trial that has added new
sites/locations since a previous run recorded its site list (see
trial_site_history.py — multi-region/multi-site trials are where
centralized imaging review beats inconsistent local site reads).

Unlike bd_agent.py's Claude-driven research, this module does no LLM
interpretation: every lead it returns matched a literal ClinicalTrials.gov
filter, so results are exact-match and reproducible run to run, and it never
"finds" a trial that doesn't actually meet the criteria. Errors (network
down, API shape changed, corporate firewall) are caught and return an empty
list rather than raising — a caller shouldn't lose the whole report because
this one supplementary source was unreachable.

Paid databases (BioPharmCatalyst/AlphaSense/Citeline) from the original
proposal are deliberately not built — see CLAUDE.md for the scoping
decision. SEC EDGAR filings and PR Newswire/Business Wire/GlobeNewswire RSS
are separate modules (sec_edgar.py, pr_wire_feeds.py).
"""

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

import sponsor_phase_history
import trial_site_history

API_BASE = "https://clinicaltrials.gov/api/v2/studies"
REQUEST_TIMEOUT_SECONDS = 20

# filter.phase=PHASE1 matches any study whose phases list includes Phase 1 —
# that covers pure Phase 1 studies AND combined "Phase 1/Phase 2" studies
# (which list both PHASE1 and PHASE2), while excluding pure Phase 2 studies.
# That's exactly the "Phase 1 or Phase 1/Phase 2" scope the BD proposal asked
# for, using the API's own OR-matching semantics rather than a manual filter.
EARLY_PHASE_FILTER = "PHASE1"


def _get(params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    url = f"{API_BASE}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _study_to_lead(study: dict, signal_type: str, signal_detail: str) -> dict:
    """Map one ClinicalTrials.gov v2 study object to bd_agent's lead dict
    shape, reusing the same field names (`signal_type`, `signal_detail`,
    `abstract_url`, etc.) so it flows through render_report()/render_csv()/
    draft_email() unchanged. `company_domain` is deliberately left unset —
    ClinicalTrials.gov gives a sponsor *name*, not a website domain, so
    Hunter.io lookup for these leads falls back to "not publicly available"
    the same way any lead without a domain already does elsewhere.
    """
    protocol = study.get("protocolSection") or {}
    identification = protocol.get("identificationModule") or {}
    status = protocol.get("statusModule") or {}
    sponsor_collab = protocol.get("sponsorCollaboratorsModule") or {}
    design = protocol.get("designModule") or {}

    nct_id = identification.get("nctId")
    lead_sponsor = (sponsor_collab.get("leadSponsor") or {}).get("name")
    phases = design.get("phases") or []

    return {
        "signal_type": signal_type,
        "company_name": lead_sponsor or "Unknown sponsor",
        "company_domain": None,
        "trial_name": identification.get("briefTitle") or nct_id,
        "drug_asset_name": None,
        "signal_detail": signal_detail,
        "abstract_url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else None,
        "abstract_url_note": f"Phase: {'/'.join(phases) if phases else 'not specified'}",
        "abstract_title": identification.get("briefTitle"),
        "registry_name": "ClinicalTrials.gov",
        "registry_id": nct_id,
        "contact_name": None,
        "contact_title": None,
    }


def find_primary_completion_approaching(indication: str, days_min: int = 60, days_max: int = 90) -> list:
    """Phase 1 / Phase 1-2 trials in `indication`, currently recruiting or
    active, whose primary completion date falls `days_min`-`days_max` days
    from today — a lead-time signal that a sponsor's next phase (and its
    imaging needs) is coming up for planning soon, well before any result
    is public.
    """
    today = dt.date.today()
    range_start = today + dt.timedelta(days=days_min)
    range_end = today + dt.timedelta(days=days_max)

    try:
        data = _get(
            {
                "query.cond": indication,
                "filter.phase": EARLY_PHASE_FILTER,
                "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
                "query.term": f"AREA[PrimaryCompletionDate]RANGE[{range_start.isoformat()},{range_end.isoformat()}]",
                "pageSize": 20,
                "format": "json",
            }
        )
    except (OSError, json.JSONDecodeError):
        return []

    leads = []
    for study in data.get("studies") or []:
        status = (study.get("protocolSection") or {}).get("statusModule") or {}
        completion_date = (status.get("primaryCompletionDateStruct") or {}).get("date") or "an unknown date"
        leads.append(
            _study_to_lead(
                study,
                signal_type="trial_milestone_approaching",
                signal_detail=f"Phase 1 trial's primary completion date is {completion_date} — approaching within {days_min}-{days_max} days.",
            )
        )
    return leads


def find_recently_completed(indication: str, within_days: int = 14) -> list:
    """Phase 1 / Phase 1-2 trials in `indication` whose status is COMPLETED
    and whose completion date falls within the last `within_days` days —
    used as a proxy for "just transitioned to Completed", since the API
    exposes the completion date itself but not a discrete status-change
    event/timestamp.
    """
    today = dt.date.today()
    range_start = today - dt.timedelta(days=within_days)

    try:
        data = _get(
            {
                "query.cond": indication,
                "filter.phase": EARLY_PHASE_FILTER,
                "filter.overallStatus": "COMPLETED",
                "query.term": f"AREA[CompletionDate]RANGE[{range_start.isoformat()},{today.isoformat()}]",
                "pageSize": 20,
                "format": "json",
            }
        )
    except (OSError, json.JSONDecodeError):
        return []

    leads = []
    for study in data.get("studies") or []:
        status = (study.get("protocolSection") or {}).get("statusModule") or {}
        completion_date = (status.get("completionDateStruct") or {}).get("date") or "recently"
        leads.append(
            _study_to_lead(
                study,
                signal_type="trial_recently_completed",
                signal_detail=f"Phase 1 trial completed on {completion_date} — likely planning its next phase now.",
            )
        )
    return leads


def find_returning_sponsor_new_phase2(indication: str, history_path, within_days: int = 30) -> list:
    """New, pure-Phase-2 trials (first posted in the last `within_days`
    days) in `indication` whose sponsor already has a recorded Phase 1 trial
    in this indication from a previous (or this same) run — the BD
    proposal's "new Phase 2 filing by a sponsor that previously ran a Phase
    1 trial" trigger. See sponsor_phase_history.py for the persistence.

    "Pure" Phase 2 deliberately excludes combined Phase 1/2 studies (which
    `filter.phase=PHASE2` alone would also match, per the OR-matching
    semantics documented at EARLY_PHASE_FILTER above) — those are already
    covered by find_primary_completion_approaching()/find_recently_completed(),
    and aren't a sponsor "graduating" to a standalone next phase the way
    this trigger means.
    """
    history = sponsor_phase_history.load_history(history_path)
    today = dt.date.today()

    try:
        phase1_data = _get(
            {"query.cond": indication, "filter.phase": "PHASE1", "pageSize": 100, "format": "json"}
        )
    except (OSError, json.JSONDecodeError):
        phase1_data = {}
    phase1_studies = phase1_data.get("studies") or []
    sponsor_phase_history.record_phase1_sponsors(history, indication, phase1_studies, today.isoformat())
    sponsor_phase_history.save_history(history_path, history)

    start = today - dt.timedelta(days=within_days)
    try:
        phase2_data = _get(
            {
                "query.cond": indication,
                "filter.phase": "PHASE2",
                "query.term": f"AREA[StudyFirstPostDate]RANGE[{start.isoformat()},{today.isoformat()}]",
                "pageSize": 20,
                "format": "json",
            }
        )
    except (OSError, json.JSONDecodeError):
        return []
    phase2_studies = phase2_data.get("studies") or []
    pure_phase2 = [
        s
        for s in phase2_studies
        if ((s.get("protocolSection") or {}).get("designModule") or {}).get("phases") == ["PHASE2"]
    ]

    returning = sponsor_phase_history.find_returning_sponsors(history, indication, pure_phase2)
    leads = []
    for study in returning:
        sponsor = ((study.get("protocolSection") or {}).get("sponsorCollaboratorsModule") or {}).get(
            "leadSponsor", {}
        ).get("name") or "Unknown sponsor"
        leads.append(
            _study_to_lead(
                study,
                signal_type="phase2_filing_by_returning_sponsor",
                signal_detail=(
                    f"{sponsor} has a new Phase 2 trial registered on ClinicalTrials.gov, and previously ran "
                    "a Phase 1 trial in this indication — likely progressing to its next stage."
                ),
            )
        )
    return leads


def find_site_expansion(indication: str, site_history_path) -> list:
    """Trials in `indication`, currently recruiting or active, that have
    added new sites/locations since a previous run recorded their site
    list — a signal that a trial is scaling up, and multi-region/
    multi-site trials are exactly where centralized imaging review beats
    inconsistent local site reads (see CLAUDE.md). Deliberately not
    phase-scoped like EARLY_PHASE_FILTER's other two lead-time signals in
    this module — site expansion is a meaningful BD signal at any phase,
    not just Phase 1. See trial_site_history.py for the cross-run site-list
    tracking this needs; a trial only produces a lead once it's been seen
    at least twice, since the first sighting has nothing to diff against.
    """
    today = dt.date.today()
    history = trial_site_history.load_history(site_history_path)

    try:
        data = _get(
            {
                "query.cond": indication,
                "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
                "pageSize": 100,
                "format": "json",
            }
        )
    except (OSError, json.JSONDecodeError):
        return []

    studies = data.get("studies") or []
    expansions = trial_site_history.record_and_find_new_sites(history, indication, studies, today.isoformat())
    trial_site_history.save_history(site_history_path, history)

    studies_by_nct_id = {}
    for study in studies:
        nct_id = (study.get("protocolSection") or {}).get("identificationModule", {}).get("nctId")
        if nct_id:
            studies_by_nct_id[nct_id] = study

    leads = []
    for nct_id, new_sites in expansions.items():
        study = studies_by_nct_id.get(nct_id)
        if not study:
            continue
        count = len(new_sites)
        preview = ", ".join(trial_site_history.site_label(key) for key in new_sites[:3])
        detail = (
            f"Trial has added {count} new site{'s' if count != 1 else ''} since it was last checked"
            f" (including {preview})"
            " — expanding multi-site trials are where centralized imaging review beats inconsistent local reads."
        )
        lead = _study_to_lead(study, signal_type="trial_site_expansion", signal_detail=detail)
        # Total current site count, not just the new ones — used by
        # seen_leads.dedup_key() so a *later, separate* expansion of the
        # same trial (a different site count) isn't wrongly treated as a
        # repeat of this one. See seen_leads.py's trial_site_expansion branch.
        lead["site_expansion_snapshot"] = len(trial_site_history.site_keys(study))
        leads.append(lead)
    return leads


def find_leads(indication: str, sponsor_history_path, site_history_path) -> list:
    """All ClinicalTrials.gov-sourced leads for one indication. Each
    sub-lookup fails independently (network issues on one don't lose the
    other), and all fail silently to an empty list rather than raising —
    see the module docstring for why."""
    return (
        find_primary_completion_approaching(indication)
        + find_recently_completed(indication)
        + find_returning_sponsor_new_phase2(indication, sponsor_history_path)
        + find_site_expansion(indication, site_history_path)
    )
