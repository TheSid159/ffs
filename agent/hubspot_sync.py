"""HubSpot CRM v3 integration: creates/updates a Contact + Company for
each lead this tool drafts an outreach email for (tagging both with an
"Outreach Status" property, `Contacted` by default), and checks a
Company's Outreach Status before including a lead in a report so a
company already marked `Declined` can be excluded from future runs.
Deliberately narrower than "ever contacted" — a `No Response` company
should still be able to resurface for a later follow-up; only an explicit
`Declined` excludes it. See CLAUDE.md's "HubSpot sync" section for the
full design and credential-path rationale.

Also sets the Company's "Channel Methods Do Not Call" property to "Yes"
by default on every Company this tool creates/updates — the user's own
instruction: every lead here already has a specific trigger this tool
found (that's what makes it a lead) and/or a possible warm-path
introduction, so none of them should get a vanilla cold call from the
sales team; outreach should go through the drafted email (or a warm
intro) instead. Company-only — Contact has no equivalent field for this.

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
import time
import urllib.error
import urllib.parse
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

# Confirmed against the user's live account (a real run's PATCH failed with
# PROPERTY_DOESNT_EXIST on the original best guess, "channel_methods_do_not_call"
# — HubSpot did not auto-slugify the display label "Channel Methods Do Not
# Call" the way it did for Outreach Status; the real internal name is
# "do_not_call"). Override via --hubspot-no-call-property if a different
# account generated something else.
DEFAULT_NO_COLD_CALL_PROPERTY = "do_not_call"
# "do_not_call" is a boolean/checkbox property in the user's account, not an
# enumeration — a real run's PATCH failed with INVALID_OPTION on "Yes"
# ("not one of the allowed options: [\"true\", \"false\", \"\"]"). HubSpot
# boolean properties always take the literal strings "true"/"false", never
# a display label, regardless of what the property's UI toggle is labeled.
NO_COLD_CALL_YES_VALUE = "true"


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


def find_owner_id_by_email(email: str, api_key: str) -> Optional[str]:
    """Look up a HubSpot user's numeric Owner ID by their login email via
    the Owners API (GET /crm/v3/owners?email=...) — this is what
    "Company owner" (the standard `hubspot_owner_id` property) needs as
    its value, not an email or name string. Returns None if no owner with
    that email exists in this account, or the Private App's token doesn't
    have the scope for it (`crm.objects.owners.read` — check this if
    owner-assignment silently never happens)."""
    if not email:
        return None
    query = urllib.parse.urlencode({"email": email})
    data = _request("GET", f"/crm/v3/owners?{query}", api_key)
    results = data.get("results") or []
    return results[0]["id"] if results else None


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
    no_cold_call_property: Optional[str] = DEFAULT_NO_COLD_CALL_PROPERTY,
    owner_id: Optional[str] = None,
) -> str:
    """Create or update a Company by domain, setting its Outreach Status
    and (unless `no_cold_call_property` is falsy) its Channel Methods Do
    Not Call property to "Yes" — every lead reaching this point already
    has a specific trigger and/or warm path, so none should get a vanilla
    cold call; see the module docstring. If `owner_id` is given (a numeric
    HubSpot Owner ID — see find_owner_id_by_email()), also sets the
    standard "Company owner" property (`hubspot_owner_id`) to it — used
    to auto-assign a lead's Company to whichever Elevate teammate has the
    warm-path connection, see sync_lead(). Returns the HubSpot object ID."""
    existing = find_company_by_domain(domain, api_key, outreach_property)
    properties = {"name": name, "domain": domain, outreach_property: status}
    if no_cold_call_property:
        properties[no_cold_call_property] = NO_COLD_CALL_YES_VALUE
    if owner_id:
        properties["hubspot_owner_id"] = owner_id
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


def create_note(body_html: str, api_key: str) -> str:
    """Create a HubSpot Note (an Engagement) with `body_html` as its
    content — HubSpot's Notes UI renders basic HTML (bold, links, line
    breaks), which is why the caller builds simple HTML rather than plain
    text. `hs_timestamp` is epoch milliseconds, the established convention
    for the Notes/Engagements API specifically (HubSpot's newer custom
    datetime properties accept ISO-8601 too, but Notes predates that and
    has always used ms). Returns the note's HubSpot object ID — not yet
    associated with anything, see associate_note_with_object()."""
    properties = {"hs_note_body": body_html, "hs_timestamp": int(time.time() * 1000)}
    created = _request("POST", "/crm/v3/objects/notes", api_key, {"properties": properties})
    return created["id"]


def associate_note_with_object(note_id: str, object_type: str, object_id: str, api_key: str) -> None:
    """Link a Note to a Company or Contact (`object_type` is "companies"
    or "contacts") via the same v4 "default association" endpoint used
    for Contact-Company — same reasoning: auto-picks the correct
    association type, no need to hardcode a numeric type ID that could
    be wrong (e.g. NOTE-to-COMPANY and NOTE-to-CONTACT use different IDs
    in HubSpot's older v3 associations API)."""
    path = f"/crm/v4/objects/notes/{note_id}/associations/default/{object_type}/{object_id}"
    _request("PUT", path, api_key)


def sync_lead(
    lead: dict,
    contact,
    api_key: str,
    outreach_property: str = DEFAULT_OUTREACH_PROPERTY,
    status: str = CONTACTED_VALUE,
    no_cold_call_property: Optional[str] = DEFAULT_NO_COLD_CALL_PROPERTY,
    owner_id: Optional[str] = None,
    note_body_html: Optional[str] = None,
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

    `owner_id` (a numeric HubSpot Owner ID, already resolved by the
    caller — see push_leads_to_hubspot()) auto-assigns the Company to
    whichever Elevate teammate has a warm-path connection there. None
    means no assignment happens — the record is left unowned (or keeps
    its existing owner, on an update), never guessed.

    The Contact upsert is best-effort: if it fails (e.g. the Outreach
    Status property doesn't exist yet on the Contact object, only on
    Company), that's reported via "contact_error" rather than raised —
    the Company upsert above already succeeded and is a real, useful
    result on its own, and shouldn't be discarded just because the
    Contact side isn't fully set up. See push_leads_to_hubspot(), which
    still counts this as a success as long as the Company synced.

    `note_body_html` (built by the caller — see bd_agent._build_hubspot_note_body())
    is attached as a HubSpot Note on the Company (and Contact, if one was
    created) — so a salesperson opening the record sees exactly why this
    tool surfaced it and the drafted email, not just a status label. Also
    best-effort: a note failure is reported via "note_error", never
    raised, since the Company/Contact upserts already succeeded on their
    own regardless of whether the note attaches.

    Returns {"company_id": ...}, optionally with "contact_id",
    "contact_error", "note_id", and/or "note_error".
    """
    domain = lead.get("company_domain")
    if not domain:
        return {}
    company_name = lead.get("company_name") or domain
    result = {
        "company_id": upsert_company(
            domain, company_name, api_key, outreach_property, status, no_cold_call_property, owner_id
        )
    }

    email = getattr(contact, "email", None) if contact else None
    if email:
        contact_name = getattr(contact, "name", None) or ""
        first, _, last = contact_name.partition(" ")
        try:
            contact_id = upsert_contact(email, first or None, last or None, api_key, outreach_property, status)
            result["contact_id"] = contact_id
            associate_contact_with_company(contact_id, result["company_id"], api_key)
        except HubSpotAPIError as exc:
            result["contact_error"] = str(exc)

    if note_body_html:
        try:
            note_id = create_note(note_body_html, api_key)
            associate_note_with_object(note_id, "companies", result["company_id"], api_key)
            if result.get("contact_id"):
                associate_note_with_object(note_id, "contacts", result["contact_id"], api_key)
            result["note_id"] = note_id
        except HubSpotAPIError as exc:
            result["note_error"] = str(exc)

    return result


def push_leads_to_hubspot(
    enriched_leads: list,
    api_key: str,
    outreach_property: str = DEFAULT_OUTREACH_PROPERTY,
    no_cold_call_property: Optional[str] = DEFAULT_NO_COLD_CALL_PROPERTY,
    owner_emails_by_index: Optional[dict] = None,
    note_bodies_by_index: Optional[dict] = None,
) -> tuple:
    """Sync every lead in `enriched_leads` (a list of (lead, contact)
    tuples, same shape used throughout this tool) to HubSpot. Returns
    (success_count, [(lead, error_message), ...], [(lead, contact_error), ...],
    [(lead, note_error), ...]) — one lead's sync failing doesn't stop the
    rest from being attempted, same pattern as
    email_drafts.push_drafts_for_report(). The third and fourth lists are
    soft warnings only (see sync_lead()'s "contact_error"/"note_error") —
    those leads are still counted as successes, since their Company
    record synced fine; only the Contact/Note side didn't.

    `owner_emails_by_index` is `{lead_index: hubspot_login_email}` (see
    warm_connections.owner_emails_for_warm_paths()) — each unique email is
    resolved to a numeric Owner ID via find_owner_id_by_email() at most
    once per call (cached), and a lookup failure or unknown email just
    means no owner gets assigned for that lead, never an error that stops
    the sync.

    `note_bodies_by_index` is `{lead_index: html_body}` (see
    bd_agent._build_hubspot_note_body()) — attached as a HubSpot Note on
    that lead's Company (+ Contact, if any); a lead with no entry simply
    gets no note."""
    successes = 0
    failures = []
    contact_warnings = []
    note_warnings = []
    owner_emails_by_index = owner_emails_by_index or {}
    note_bodies_by_index = note_bodies_by_index or {}
    owner_id_cache: dict = {}
    for i, (lead, contact) in enumerate(enriched_leads):
        owner_id = None
        owner_email = owner_emails_by_index.get(i)
        if owner_email:
            if owner_email not in owner_id_cache:
                try:
                    owner_id_cache[owner_email] = find_owner_id_by_email(owner_email, api_key)
                except HubSpotAPIError:
                    owner_id_cache[owner_email] = None
            owner_id = owner_id_cache[owner_email]
        try:
            result = sync_lead(
                lead, contact, api_key, outreach_property,
                no_cold_call_property=no_cold_call_property,
                owner_id=owner_id,
                note_body_html=note_bodies_by_index.get(i),
            )
            if result:
                successes += 1
                if result.get("contact_error"):
                    contact_warnings.append((lead, result["contact_error"]))
                if result.get("note_error"):
                    note_warnings.append((lead, result["note_error"]))
        except HubSpotAPIError as exc:
            failures.append((lead, str(exc)))
    return successes, failures, contact_warnings, note_warnings
