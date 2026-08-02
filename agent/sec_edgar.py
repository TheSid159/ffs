"""SEC EDGAR full-text search integration (free, no API key) for BD signals
from public micro/small-cap biotech 8-K/10-Q filings — the proposal's second
data source. Like clinicaltrials_gov.py, this does no LLM interpretation:
every lead matched a literal keyword phrase in a real, citable SEC filing.

The SEC's fair-access policy requires a descriptive User-Agent identifying
the requester (a bare/default User-Agent gets a 403) — built from
`--sender-company`/`--sender-name` rather than hardcoded, so it's still
correct if someone else runs this tool under their own CRO's name.

Not yet verified against a live response — this dev sandbox's network
policy blocks efts.sec.gov outright (same restriction hit while building
clinicaltrials_gov.py), so testing here used `unittest.mock.patch` on
`sec_edgar._get()` with a fabricated response shaped from the EDGAR
full-text search API's documented schema (confirmed via its OpenAPI spec
and real example responses, not guessed) — a real run should be checked
once by the user before relying on it.
"""

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://efts.sec.gov/LATEST/search-index"
REQUEST_TIMEOUT_SECONDS = 20

# Verbatim keyword phrases from the BD proposal this module implements. Each
# is run as its own EDGAR full-text query rather than ANDed together in one
# query — requiring every phrase in the same filing would be far too strict,
# since a company would only ever use one or two of these at a time.
ALERT_PHRASES = [
    "End-of-Phase 1 meeting",
    "EOP1",
    "Top-line Phase 1 data",
    "Positive Phase 1b safety",
    "Initiation of Phase 2 trial",
    "FDA alignment on Phase 2 trial design",
]

FORMS = "8-K,10-Q"

# SEC's fair-access policy asks requesters to stay well under 10 req/s
# aggregate across www.sec.gov + efts.sec.gov — a small pause between our
# handful of sequential phrase queries costs almost nothing and keeps this
# tool a good citizen of a free government service.
REQUEST_DELAY_SECONDS = 0.5


def _get(params: dict, user_agent: str) -> dict:
    query = urllib.parse.urlencode(params)
    url = f"{API_BASE}?{query}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _hit_to_lead(hit: dict, matched_phrase: str) -> dict:
    """Map one EDGAR full-text search hit to bd_agent's lead dict shape.
    Real field names confirmed from the API's OpenAPI spec and a real
    example response: `_source.ciks` (array of filer CIKs), `_source.
    display_names` (e.g. "IonQ, Inc. (IONQ) (CIK 0001824920)"),
    `_source.form`, `_source.file_date`, `_source.adsh` (accession number,
    with dashes).
    """
    source = hit.get("_source") or {}
    ciks = source.get("ciks") or []
    display_names = source.get("display_names") or []
    company_name = (display_names[0].split(" (")[0].strip() if display_names else None) or "Unknown company"
    adsh = source.get("adsh") or ""
    accession_no_dashes = adsh.replace("-", "")
    cik_raw = ciks[0] if ciks else None
    cik = str(int(cik_raw)) if cik_raw and cik_raw.isdigit() else cik_raw
    filing_url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{adsh}-index.htm"
        if cik and adsh
        else None
    )
    form = source.get("form") or "SEC filing"
    file_date = source.get("file_date") or "an unknown date"

    return {
        "signal_type": "sec_filing_signal",
        "company_name": company_name,
        "company_domain": None,
        "trial_name": None,
        "drug_asset_name": None,
        "signal_detail": (
            f'{form} filed {file_date} matched the phrase "{matched_phrase}" — '
            "a possible Phase 1/Phase 2 transition signal."
        ),
        "abstract_url": filing_url,
        "abstract_url_note": f"{form}, filed {file_date}",
        "abstract_title": f"{company_name} {form} filing",
        "registry_name": None,
        "registry_id": adsh or None,
        "contact_name": None,
        "contact_title": None,
    }


def find_leads(sender_company: str, sender_name: str, within_days: int = 30) -> list:
    """8-K/10-Q filings from the last `within_days` days matching any of
    ALERT_PHRASES. Not filtered by indication — EDGAR full-text search has
    no therapeutic-area field, and ANDing a phrase with a plain-English
    indication name (e.g. "bladder cancer") would silently miss filings
    that name the drug/program instead of the indication. Same
    anti-fabrication tradeoff as elsewhere in this tool: better to surface
    a few possibly-off-topic hits for the user to skim than silently miss
    a real one by over-filtering.

    Each phrase query fails independently — one bad request doesn't lose
    the rest — and the whole function returns whatever it collected rather
    than raising, consistent with clinicaltrials_gov.py.
    """
    user_agent = f"{sender_company} BD Research Tool ({sender_name})"
    today = dt.date.today()
    start = today - dt.timedelta(days=within_days)

    leads = []
    seen_hit_ids = set()
    for i, phrase in enumerate(ALERT_PHRASES):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        try:
            data = _get(
                {
                    "q": f'"{phrase}"',
                    "forms": FORMS,
                    "dateRange": "custom",
                    "startdt": start.isoformat(),
                    "enddt": today.isoformat(),
                },
                user_agent,
            )
        except (OSError, json.JSONDecodeError):
            continue

        for hit in (data.get("hits") or {}).get("hits") or []:
            hit_id = hit.get("_id")
            if hit_id in seen_hit_ids:
                continue
            seen_hit_ids.add(hit_id)
            leads.append(_hit_to_lead(hit, phrase))
    return leads
