"""Hunter.io integration for finding verified company contacts.

Given a company domain (and optionally a known contact name), looks up a
CEO/CMO-titled email address via the Hunter.io API and gates it on a
minimum confidence score, so a low-confidence guess is never reported as a
confirmed contact.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

HUNTER_BASE_URL = "https://api.hunter.io/v2"
REQUEST_TIMEOUT_SECONDS = 20

# Position substrings (lowercase) used to pick a contact out of a Domain
# Search result when we don't already have a specific person's name.
TARGET_TITLE_KEYWORDS = (
    "chief medical officer",
    "cmo",
    "chief executive officer",
    "ceo",
    "president",
)


@dataclass
class Contact:
    name: Optional[str]
    title: Optional[str]
    email: Optional[str]
    confidence: Optional[int]
    source: str  # "email_finder" | "domain_search" | "none" | "error: ..."


class HunterAPIError(Exception):
    """A non-2xx response from Hunter.io, with their explanation attached.

    Hunter returns a JSON body describing *why* a request was rejected
    (e.g. plan/endpoint restrictions, invalid key) — a bare HTTP status
    code alone isn't enough to act on, so this carries that detail through.
    """


def _get(path: str, params: dict, api_key: str) -> dict:
    query = urllib.parse.urlencode({**params, "api_key": api_key})
    url = f"{HUNTER_BASE_URL}/{path}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip()
        try:
            errors = json.loads(body).get("errors") or []
            if errors:
                first = errors[0]
                detail = f"{first.get('id', 'error')}: {first.get('details') or first.get('code') or body}"
        except json.JSONDecodeError:
            pass
        raise HunterAPIError(f"HTTP {exc.code} on {path} — {detail}") from None


def find_contact(
    domain: Optional[str],
    contact_name: Optional[str],
    api_key: str,
    min_confidence: int = 90,
) -> Contact:
    """Look up a verified email for a company via Hunter.io.

    If `contact_name` ("First Last") is already known, uses Hunter's Email
    Finder to guess that specific person's address. Otherwise falls back to
    Domain Search and picks the highest-confidence CEO/CMO-titled contact
    Hunter has on file for that domain.

    The returned Contact only has `.email` populated if Hunter's confidence
    score met `min_confidence` — otherwise `.email` is None but `.confidence`
    still reports what Hunter found, so the caller can show "not confirmed"
    with the actual score rather than silently discarding it.
    """
    if not domain:
        return Contact(contact_name, None, None, None, "none")

    try:
        if contact_name and " " in contact_name.strip():
            first, _, last = contact_name.strip().partition(" ")
            data = _get(
                "email-finder",
                {"domain": domain, "first_name": first, "last_name": last},
                api_key,
            )
            found = data.get("data") or {}
            email = found.get("email")
            confidence = found.get("score")
            if email and confidence is not None and confidence >= min_confidence:
                return Contact(contact_name, None, email, confidence, "email_finder")
            return Contact(contact_name, None, None, confidence, "email_finder")

        data = _get("domain-search", {"domain": domain, "limit": 25}, api_key)
        candidates = (data.get("data") or {}).get("emails") or []

        best = None
        for candidate in candidates:
            position = (candidate.get("position") or "").lower()
            if any(kw in position for kw in TARGET_TITLE_KEYWORDS):
                confidence = candidate.get("confidence") or 0
                if best is None or confidence > (best.get("confidence") or 0):
                    best = candidate

        if best is None:
            return Contact(None, None, None, None, "domain_search")

        name = " ".join(filter(None, [best.get("first_name"), best.get("last_name")])) or None
        confidence = best.get("confidence")
        if confidence is not None and confidence >= min_confidence:
            return Contact(name, best.get("position"), best.get("value"), confidence, "domain_search")
        return Contact(name, best.get("position"), None, confidence, "domain_search")

    except (HunterAPIError, urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
        return Contact(contact_name, None, None, None, f"error: {exc}")
