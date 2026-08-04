"""Warm-path lead scoring: cross-references your own LinkedIn connections
against a target company's employee roster, so cold BD outreach can start
from wherever a personal connection already exists — not necessarily the
company's most senior person. A strong connection to anyone there can
become an internal-referral path to the actual decision-maker, so matching
is done across every employee at the target company, not just a presumed
"the CEO/CMO is the target" shortcut. (Design arrived at in conversation
with the user; see CLAUDE.md for the fuller rationale.)

The data source is your own LinkedIn connections export — LinkedIn: Settings
& Privacy -> Data privacy -> "Get a copy of your data" -> Connections. This
is YOUR data, which LinkedIn explicitly provides for download; reading a
CSV file you already have permission to have is not scraping, and this
module never talks to linkedin.com at all. Scraping LinkedIn directly (or
having an agent browse it) is explicitly out of scope — it violates
LinkedIn's Terms of Service and they actively block it technically; see the
"Legitimate alternatives only" note below for what this deliberately does
NOT attempt.

What this deliberately does NOT do: no numeric "connection strength" score
is invented. LinkedIn's CSV export exposes only the connection's current
company/title and the date you connected — no interaction history, no
mutual-connection count, nothing that would make a richer score meaningful.
Inventing one anyway would break this tool's anti-fabrication discipline
(see CLAUDE.md's "Anti-fabrication is load-bearing"). Every match is
reported as a plain fact — this person is a 1st-degree connection, in this
role, connected since this date — and left for a human to judge which path
is actually warmest, the same restraint hunter_contacts.py already applies
to Hunter's own confidence scores.

Matching is intentionally exact (after light legal-suffix normalization),
not fuzzy — a wrong match here surfaces a stranger as a false "warm path"
into a company, which is worse than silently missing a real one.
"""

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CONNECTIONS_HEADER_PREFIX = "First Name,Last Name"

_LEGAL_SUFFIXES = (
    ", inc.", ", inc", " inc.", " inc",
    ", llc", " llc",
    ", ltd.", " ltd.", ", ltd", " ltd",
    " corporation", " corp.", " corp",
    " co.", " company",
)


@dataclass
class Connection:
    first_name: str
    last_name: str
    company: str
    position: Optional[str]
    connected_on: Optional[str]
    profile_url: Optional[str]
    owner: str = ""  # whose connections export this came from — see load_connections_dir()

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class WarmPath:
    connection: Connection
    matched_company_name: str  # the target-company name this was matched against, as given by the caller

    @property
    def owner(self) -> str:
        return self.connection.owner


def load_linkedin_connections(csv_path: Path, owner: str = "") -> list:
    """Parse a LinkedIn "Connections.csv" export.

    LinkedIn prepends several lines of boilerplate notes before the actual
    header row ("First Name,Last Name,URL,Email Address,Company,Position,
    Connected On"), so this scans for that header rather than assuming
    line 1 is it — a fixed line-offset would silently break the moment
    LinkedIn changes how many note lines it prepends, the same class of
    bug as pr_wire_feeds.py's stale-feed-URL lesson (see CLAUDE.md).
    Raises ValueError if the header can't be found at all, rather than
    silently returning zero connections and looking like "no matches."

    `owner` tags every Connection loaded from this file — see
    load_connections_dir(), which is how multiple Elevate teammates'
    exports get combined into one pool while still tracking whose
    connection each match actually is.
    """
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith(CONNECTIONS_HEADER_PREFIX)), None)
    if header_index is None:
        raise ValueError(
            f"Could not find the '{CONNECTIONS_HEADER_PREFIX},...' header row in {csv_path} — "
            "is this a real LinkedIn connections export (Settings & Privacy -> Data privacy -> "
            "Get a copy of your data -> Connections)?"
        )

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    connections = []
    for row in reader:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        company = (row.get("Company") or "").strip()
        if not company or not (first or last):
            continue  # no employer on file, or a blank/malformed row — nothing to match on
        connections.append(
            Connection(
                first_name=first,
                last_name=last,
                company=company,
                position=(row.get("Position") or "").strip() or None,
                connected_on=(row.get("Connected On") or "").strip() or None,
                profile_url=(row.get("URL") or "").strip() or None,
                owner=owner,
            )
        )
    return connections


def load_connections_dir(dir_path: Path) -> list:
    """Load every `*.csv` file in `dir_path` as one person's LinkedIn
    connections export, tagged with that file's name (minus extension) as
    `owner` — e.g. a file named `Sarah.csv` produces Connections with
    `owner="Sarah"`. This is how multiple Elevate Imaging teammates'
    exports combine into one pool: the best warm path into a target
    company isn't necessarily the user's own connection, so every
    teammate who drops their export into this folder gets checked too
    (see CLAUDE.md's "Warm-path connection matching" for the fuller
    rationale).

    A missing directory returns an empty list — this whole feature is
    opt-in, so "no folder yet" just means no warm-path matching this run,
    not an error. One bad/malformed file doesn't stop the others from
    loading: a warning is printed for it (matching pr_wire_feeds.py's
    per-feed-failure posture) and the rest still load normally.
    """
    if not dir_path.is_dir():
        return []
    connections = []
    for csv_path in sorted(dir_path.glob("*.csv")):
        try:
            connections += load_linkedin_connections(csv_path, owner=csv_path.stem)
        except (ValueError, OSError) as exc:
            print(f"[warm_connections: could not load {csv_path.name} — {exc}]")
    return connections


def _normalize_company(name: str) -> str:
    """Loose normalization for matching only (never for display): lowercase,
    strip common legal suffixes and trailing punctuation, so "Acme Bio,
    Inc." and "Acme Bio" match without any fuzzy/edit-distance guessing."""
    normalized = (name or "").lower().strip()
    for suffix in _LEGAL_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.strip(" .,")


def find_warm_paths(connections: list, target_company_name: str) -> list:
    """Every connection whose current Company (from the LinkedIn export)
    matches `target_company_name`, across the WHOLE roster — deliberately
    not limited to a presumed decision-maker. Returns a WarmPath per match;
    an empty list means no known warm path, not "cold outreach is fine" —
    it just means this data source didn't find one."""
    target_normalized = _normalize_company(target_company_name)
    if not target_normalized:
        return []
    return [
        WarmPath(connection=c, matched_company_name=target_company_name)
        for c in connections
        if _normalize_company(c.company) == target_normalized
    ]


def find_warm_paths_for_leads(connections: list, leads: list) -> dict:
    """Convenience wrapper for a whole report run: given the connections
    list and this run's leads (bd_agent's lead dicts, any signal type —
    each has `company_name`), return {lead_index: [WarmPath, ...]} for
    every lead with at least one match. Leads with no `company_name` or no
    match are simply absent from the returned dict, not present with an
    empty list — keeps callers from having to filter twice."""
    results = {}
    for i, lead in enumerate(leads):
        company_name = lead.get("company_name")
        if not company_name:
            continue
        matches = find_warm_paths(connections, company_name)
        if matches:
            results[i] = matches
    return results


def load_owner_emails(dir_path: Path) -> dict:
    """Load `{owner_label: hubspot_login_email}` from `owners.json` in
    `dir_path` (the same folder as the connections CSVs), if present —
    e.g. `{"Sarah": "sarah@elevateimaging.com"}`.

    This mapping is deliberately explicit, never guessed: a connections
    CSV's filename stem ("Sarah") is just a display label, not
    necessarily that person's real HubSpot login email, and inferring one
    from the other risks assigning a lead to the wrong person's HubSpot
    account — the same identity-guessing risk already ruled out for
    roster-based name matching (see the module docstring). A missing
    file, or a label not listed in it, just means no HubSpot owner gets
    auto-assigned for that person's connections — never an error.
    """
    path = dir_path / "owners.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def owner_emails_for_warm_paths(warm_paths: dict, owner_emails: dict) -> dict:
    """Given `find_warm_paths_for_leads()`'s `{lead_index: [WarmPath, ...]}`
    and `load_owner_emails()`'s `{owner_label: email}`, return
    `{lead_index: email}` for every lead whose *first* warm-path match's
    owner has a known email — used to auto-assign HubSpot's Company owner
    to whichever teammate has the connection (see hubspot_sync.py).

    Only the first match is used deliberately: HubSpot only supports one
    owner per record, and there's no connection-strength score to prefer
    one teammate's match over another's (see "No invented connection-
    strength score" above) — first-found (alphabetical by connections
    CSV filename, per load_connections_dir()) is the least-arbitrary
    deterministic choice available. A lead whose matching owner isn't in
    `owner_emails` is simply absent from the result, not present with a
    `None` — the caller shouldn't attempt an assignment for it at all.
    """
    result = {}
    for i, matches in warm_paths.items():
        if not matches:
            continue
        owner_label = matches[0].owner
        email = owner_emails.get(owner_label)
        if email:
            result[i] = email
    return result
