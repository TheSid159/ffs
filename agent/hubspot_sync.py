"""HubSpot CRM v3 integration: creates/updates a Contact + Company for
each lead this tool drafts an outreach email for (tagging both with an
"Outreach Status" property, `Contacted` by default), and checks a
Company's Outreach Status before including a lead in a report so a
company already marked `Declined` can be excluded from future runs.
Deliberately narrower than "ever contacted" — a `No Response` company
should still be able to resurface for a later follow-up; only an explicit
`Declined` excludes it. See CLAUDE.md's "HubSpot sync" section for the
full design and credential-path rationale.

Entirely optional, same posture as email_drafts.py's outbox integration:
only runs if `--hubspot-api-key` is set; the tool's core behavior is
unchanged if it's never provided.

Auth is a Legacy Private App access token (a static Bearer token — not a
Project/OAuth app, and not an MCP auth app; see CLAUDE.md for why). Uses
stdlib `urllib` only, no `hubspot-api-client` SDK dependency, consistent
with hunter_contacts.py's/sec_edgar.py's pattern in this tool.

Endpoint shapes (search/create/update via the CRM v3 objects API, and the
v4 "default association" shorthand for linking a Contact to a Company)
are confirmed against HubSpot's own developer-docs conventions, which have
been stable for years — but this dev sandbox's network policy blocks
api.hubapi.com outright (like every other external API touched this
session), so none of this has been exercised against a live account yet.
Built and tested here with `unittest.mock.patch` on `hubspot_sync._request()`
using fabricated responses; a real run should be checked once by the user
before relying on it, same as every other new integration added to this
tool.
"""

import json
import urllib.error
import urllib.request
from typing import Optional

API_BASE = "https://api.hubapi.com"
REQUEST_TIMEOUT_SECONDS = 20

# HubSpot auto-generates this from the property label "Outreach Status"
# when there's no naming collision on the account — used as the default so
# the tool works out of the box, but the user's actual internal name(s)
# (Contact and Company can differ) should be confirmed and passed via
# --hubspot-outreach-property if HubSpot generated something else.
DEFAULT_OUTREACH_PROPERTY = "outreach_status"
DECLINED_VALUE = "Declined"
CONTACTED_VALUE = "Contacted"


class HubSpotAPIError(Exception):
    """A non-2xx response from HubSpot, with their explanation attached —
    same pattern as hunter_contacts.HunterAPIError."""


def _request(method: str, path: str, api_key: str, body: Optional[dict] = None) -> dict:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HubSpotAPIError(f"HTTP {exc.code} on {method} {path} — {detail}") from None
    except urllib.error.URLError as exc:
        raise HubSpotAPIError(f"Could not reach {API_BASE} — {exc}") from None


def _search_by_property(
    object_type: str, property_name: str, value: str, api_key: str, extra_properties: Optional[list] = None
) -> Optional[dict]:
    """Return the first matching object's {"id": ..., "properties": {...}}
    for an exact-match ("EQ") search on `property_name`, or None if
    nothing matched. Exact match only, deliberately — the same
    "never fuzzy-match a company into existence" discipline as
    warm_connections.py's `_normalize_company()`."""
    body = {
        "filterGroups": [{"filters": [{"propertyName": property_name, "operator": "EQ", "value": value}]}],
        "properties": list({property_name, *(extra_properties or [])}),
        "limit": 1,
    }
    data = _request("POST", f"/crm/v3/objects/{object_type}/search", api_key, body)
    results = data.get("results") or []
    return results[0] if results else None


def find_company_by_domain(domain: str, api_key: str, outreach_property: str = DEFAULT_OUTREACH_PROPERTY) -> Optional[dict]:
    if not domain:
        return None
    return _search_by_property("companies", "domain", domain, api_key, extra_properties=["name", outreach_property])


def find_contact_by_email(email: str, api_key: str, outreach_property: str = DEFAULT_OUTREACH_PROPERTY) -> Optional[dict]:
    if not email:
        return None
    return _search_by_property(
        "contacts", "email", email, api_key, extra_properties=["firstname", "lastname", outreach_property]
    )


def is_company_declined(domain: str, api_key: str, outreach_property: str = DEFAULT_OUTREACH_PROPERTY) -> bool:
    """True only if the company exists in HubSpot AND its Outreach Status
    is exactly "Declined". A company HubSpot has never heard of, or one
    marked anything else (Contacted, No Response, blank), is never
    excluded — this only ever acts on an explicit Declined marking."""
    company = find_company_by_domain(domain, api_key, outreach_property)
    if not company:
        return False
    status = (company.get("properties") or {}).get(outreach_property)
    return (status or "").strip().lower() == DECLINED_VALUE.lower()


def split_declined(leads: list, api_key: str, outreach_property: str = DEFAULT_OUTREACH_PROPERTY) -> tuple:
    """Return (kept_leads, declined_leads). A lead with no `company_domain`
    is always kept (nothing to check against). Per-domain results are
    cached within one call so multiple leads at the same company only cost
    one HubSpot lookup. A HubSpot API error for one domain fails open (the
    lead is kept, not silently dropped) — a transient HubSpot hiccup
    should never be the reason a real lead disappears from a report."""
    kept, declined = [], []
    checked: dict = {}
    for lead in leads:
        domain = lead.get("company_domain")
        if not domain:
            kept.append(lead)
            continue
        if domain not in checked:
            try:
                checked[domain] = is_company_declined(domain, api_key, outreach_property)
            except HubSpotAPIError:
                checked[domain] = False
        (declined if checked[domain] else kept).append(lead)
    return kept, declined


def upsert_company(
    domain: str,
    name: str,
    api_key: str,
    outreach_property: str = DEFAULT_OUTREACH_PROPERTY,
    status: str = CONTACTED_VALUE,
) -> str:
    """Create or update a Company by domain, setting its Outreach Status.
    Returns the HubSpot object ID."""
    existing = find_company_by_domain(domain, api_key, outreach_property)
    properties = {"name": name, "domain": domain, outreach_property: status}
    if existing:
        _request("PATCH", f"/crm/v3/objects/companies/{existing['id']}", api_key, {"properties": properties})
        return existing["id"]
    created = _request("POST", "/crm/v3/objects/companies", api_key, {"properties": properties})
    return created["id"]


def upsert_contact(
    email: str,
    first_name: Optional[str],
    last_name: Optional[str],
    api_key: str,
    outreach_property: str = DEFAULT_OUTREACH_PROPERTY,
    status: str = CONTACTED_VALUE,
) -> str:
    """Create or update a Contact by email, setting its Outreach Status.
    Returns the HubSpot object ID. Caller is responsible for only calling
    this with a Hunter-confirmed email — see sync_lead()."""
    existing = find_contact_by_email(email, api_key, outreach_property)
    properties = {"email": email, outreach_property: status}
    if first_name:
        properties["firstname"] = first_name
    if last_name:
        properties["lastname"] = last_name
    if existing:
        _request("PATCH", f"/crm/v3/objects/contacts/{existing['id']}", api_key, {"properties": properties})
        return existing["id"]
    created = _request("POST", "/crm/v3/objects/contacts", api_key, {"properties": properties})
    return created["id"]


def associate_contact_with_company(contact_id: str, company_id: str, api_key: str) -> None:
    """Link a Contact to a Company via HubSpot's v4 "default association"
    endpoint, which auto-picks the correct association type — no need to
    look up a numeric association type ID first, unlike the older v3
    associations API."""
    path = f"/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}"
    _request("PUT", path, api_key)


def sync_lead(
    lead: dict, contact, api_key: str, outreach_property: str = DEFAULT_OUTREACH_PROPERTY, status: str = CONTACTED_VALUE
) -> dict:
    """Push one lead (+ its Hunter-enriched `contact`, if any — a
    hunter_contacts.Contact or None) to HubSpot as a Company, plus a
    Contact if there's a confirmed email, associated to that Company.

    Gated on `lead.get("company_domain")` — a lead with no domain has
    nothing to key a Company record on, so it's skipped entirely (returns
    `{}`) rather than creating a domain-less, effectively orphaned Company
    record. The Contact's "To:" field is only ever set from
    `contact.email` when Hunter actually confirmed it — never a guessed
    name/address — same anti-fabrication discipline as email_drafts.py's
    outbox integration.

    Returns {"company_id": ...} or {"company_id": ..., "contact_id": ...}.
    """
    domain = lead.get("company_domain")
    if not domain:
        return {}
    company_name = lead.get("company_name") or domain
    result = {"company_id": upsert_company(domain, company_name, api_key, outreach_property, status)}

    email = getattr(contact, "email", None) if contact else None
    if email:
        contact_name = getattr(contact, "name", None) or ""
        first, _, last = contact_name.partition(" ")
        contact_id = upsert_contact(email, first or None, last or None, api_key, outreach_property, status)
        result["contact_id"] = contact_id
        associate_contact_with_company(contact_id, result["company_id"], api_key)
    return result


def push_leads_to_hubspot(enriched_leads: list, api_key: str, outreach_property: str = DEFAULT_OUTREACH_PROPERTY) -> tuple:
    """Sync every lead in `enriched_leads` (a list of (lead, contact)
    tuples, same shape used throughout this tool) to HubSpot. Returns
    (success_count, [(lead, error_message), ...]) — one lead's sync
    failing doesn't stop the rest from being attempted, same pattern as
    email_drafts.push_drafts_for_report()."""
    successes = 0
    failures = []
    for lead, contact in enriched_leads:
        try:
            if sync_lead(lead, contact, api_key, outreach_property):
                successes += 1
        except HubSpotAPIError as exc:
            failures.append((lead, str(exc)))
    return successes, failures
