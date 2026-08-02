"""Direct integration with the ClinicalTrials.gov API v2 (free, no API key)
for two BD signals that are better found by an exact structured query than
by asking Claude to search the web for them: an early-phase trial closing in
on its primary completion date (a lead-time signal — sponsors start planning
their next phase, imaging vendor included, well before the readout), and an
early-phase trial whose status just flipped to COMPLETED (the same signal,
caught from the other side once it's actually happened).

Unlike bd_agent.py's Claude-driven research, this module does no LLM
interpretation: every lead it returns matched a literal ClinicalTrials.gov
filter, so results are exact-match and reproducible run to run, and it never
"finds" a trial that doesn't actually meet the criteria. Errors (network
down, API shape changed, corporate firewall) are caught and return an empty
list rather than raising — a caller shouldn't lose the whole report because
this one supplementary source was unreachable.

Scope note: only these two triggers are implemented. The third proposed
trigger ("new Phase 2 filing by the same sponsor that ran an earlier Phase 1")
needs cross-referencing a sponsor's trial history across runs, which is a
meaningfully bigger feature (stateful sponsor tracking, not a single
stateless query) — deferred rather than built as a shallow approximation.
SEC EDGAR filings, PR Newswire/Business Wire/GlobeNewswire RSS, and paid
databases (BioPharmCatalyst/AlphaSense/Citeline) from the same proposal are
not built at all yet; see CLAUDE.md for the scoping decision.
"""

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

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


def find_leads(indication: str) -> list:
    """All ClinicalTrials.gov-sourced leads for one indication. Each
    sub-lookup fails independently (network issues on one don't lose the
    other), and both fail silently to an empty list rather than raising —
    see the module docstring for why."""
    return find_primary_completion_approaching(indication) + find_recently_completed(indication)
