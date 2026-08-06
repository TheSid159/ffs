"""Free, structured integration with the EU Clinical Trials Information
System (CTIS) — the EU/EEA equivalent of ClinicalTrials.gov, used here to
surface newly registered/submitted trials the same way clinicaltrials_gov.py
does for the US registry. No LLM, no API key.

*** IMPORTANT — this is NOT an officially documented public API, unlike
ClinicalTrials.gov's v2 API. Read this before touching this module. ***

The European Medicines Agency (EMA) has stated CTIS "does not have a
machine-readable interface for the public, only an API for EU member
states." The endpoints this module calls — `POST /ctis-public-api/search`
and `GET /ctis-public-api/retrieve/{EUCT number}` on euclinicaltrials.eu —
are real, reachable, unauthenticated JSON endpoints that the public CTIS
search *website* itself uses internally (confirmed via web search
referencing third-party tooling built against them), not something EMA
publishes or supports for external use. Practical consequences:

  - It could change shape, start requiring authentication, or be shut off
    entirely at any time, with zero notice or changelog — the opposite of
    ClinicalTrials.gov's versioned, documented v2 API.
  - This dev sandbox's network policy blocks euclinicaltrials.eu outright
    (same as clinicaltrials.gov/api.hunter.io/api.hubapi.com elsewhere in
    this tool), so the request/response shape below could NOT be
    confirmed against a live response from here. The field names in
    `_trial_to_lead()` are a best-effort GUESS based on the EUCT-number
    scheme and adjacent tooling, not a confirmed schema — genuinely
    weaker footing than every other structured source in this tool.
  - Parsing is deliberately defensive: an unrecognized response shape
    returns [] with a visible warning (never a crash, never a silent
    empty result masquerading as "checked, nothing found") — same
    not-silent posture as pr_wire_feeds.py's per-feed failure handling,
    for the same reason (this is the least-trustworthy source here).

See CLAUDE.md's "EU CTIS integration" section for what to check
periodically to confirm this is still working, and for how to report a
confirmed real request/response shape if you get one from a live run —
this needs that the same way pr_wire_feeds.py's GlobeNewswire URL did.
"""

import json
import re
import urllib.error
import urllib.request
from typing import Optional

API_BASE = "https://euclinicaltrials.eu/ctis-public-api"
REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_PAGE_SIZE = 25

# A CTIS "EU Trial Number" (EUCT number) always looks like YYYY-NNNNNN-NN-NN
# (e.g. "2024-500123-45-00") — used to sanity-check that a field we guessed
# as the trial identifier actually looks like one, not just any string.
_EUCT_NUMBER_RE = re.compile(r"^\d{4}-\d{6}-\d{2}-\d{2}$")


def _post(path: str, body: dict) -> dict:
    url = f"{API_BASE}/{path}"
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Same reasoning as hunter_contacts.py/clinicaltrials_gov.py:
            # a bare urllib User-Agent is a common bot-block trigger.
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first_present(record: dict, *keys: str) -> Optional[str]:
    """Try several plausible field-name candidates in order, return the
    first non-empty one found — see the module docstring on why the exact
    field names couldn't be confirmed ahead of time."""
    for key in keys:
        value = record.get(key)
        if value:
            return value
    return None


def _euct_number_from(record: dict) -> Optional[str]:
    candidate = _first_present(
        record, "ctNumber", "euCtNumber", "trialNumber", "euctNumber", "number"
    )
    if candidate and _EUCT_NUMBER_RE.match(str(candidate).strip()):
        return str(candidate).strip()
    return None


def _trial_to_lead(record: dict) -> Optional[dict]:
    """Map one CTIS search-result record to bd_agent's lead dict shape.
    Returns None (never a half-filled placeholder lead) if the record
    doesn't even contain something that looks like a real EUCT number —
    same anti-fabrication discipline as everywhere else in this tool:
    better to skip a record we can't confidently parse than report a lead
    that might be wrong.
    """
    euct_number = _euct_number_from(record)
    if not euct_number:
        return None

    sponsor = _first_present(record, "sponsorName", "sponsor", "applicantName")
    title = _first_present(record, "title", "trialTitle", "shortTitle", "publicTitle")
    condition = _first_present(record, "condition", "medicalCondition", "therapeuticArea")
    status = _first_present(record, "status", "trialStatus", "overallStatus")

    detail_parts = [f"New/updated CTIS trial (EU/EEA registry) — EUCT {euct_number}."]
    if condition:
        detail_parts.append(f"Condition: {condition}.")
    if status:
        detail_parts.append(f"Status: {status}.")
    signal_detail = " ".join(detail_parts)

    return {
        "signal_type": "new_registration",
        "company_name": sponsor or "Unknown sponsor",
        "company_domain": None,
        "trial_name": title or euct_number,
        "drug_asset_name": None,
        "signal_detail": signal_detail,
        "abstract_url": f"https://euclinicaltrials.eu/ctis-public/view/{euct_number}",
        "abstract_url_note": None,
        "abstract_title": title,
        "registry_name": "EU CTIS",
        "registry_id": euct_number,
        "contact_name": None,
        "contact_title": None,
    }


def find_new_registrations(indication: str, page_size: int = DEFAULT_PAGE_SIZE) -> list:
    """Trials in CTIS matching `indication` in a free-text search. Every
    failure mode (network error, non-JSON response, unrecognized response
    shape) returns [] with a printed warning rather than raising or
    silently returning nothing that looks identical to "checked, no
    matches" — see the module docstring for why this source specifically
    needs to fail loud.
    """
    try:
        data = _post(
            "search",
            {
                "pagination": {"page": 1, "size": page_size},
                "searchCriteria": {"containAll": indication},
            },
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        print(
            f"[ctis_eu: search request failed — {exc}. This is an unofficial, undocumented "
            "endpoint that could have changed or been blocked — see CLAUDE.md's 'EU CTIS "
            "integration' section for how to check.]"
        )
        return []
    except json.JSONDecodeError as exc:
        print(
            f"[ctis_eu: response wasn't valid JSON — {exc}. The undocumented API may have "
            "changed shape — see CLAUDE.md's 'EU CTIS integration' section.]"
        )
        return []

    trials = None
    if isinstance(data, dict):
        trials = data.get("data") or data.get("results") or data.get("content")
    elif isinstance(data, list):
        trials = data
    if not isinstance(trials, list):
        print(
            "[ctis_eu: unexpected response shape from the CTIS search endpoint — skipping this "
            "source this run. The undocumented API may have changed — see CLAUDE.md's 'EU CTIS "
            "integration' section, and if you can see the real response shape (e.g. via your "
            "browser's Network tab on euclinicaltrials.eu), that's exactly what's needed to fix "
            "this properly.]"
        )
        return []

    leads = []
    for record in trials:
        if not isinstance(record, dict):
            continue
        lead = _trial_to_lead(record)
        if lead:
            leads.append(lead)
    return leads


def find_leads(indication: str) -> list:
    """Entry point matching the shape of clinicaltrials_gov.find_leads()/
    sec_edgar.find_leads()/pr_wire_feeds.find_leads() — called from
    bd_agent.run_trial_signals_search()."""
    return find_new_registrations(indication)
