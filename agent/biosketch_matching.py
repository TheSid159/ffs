"""Common-ground matching: cross-references your (and teammates') public
biographical background — current affiliation, city/region, taken from a
LinkedIn "Profile.csv" export — against publicly stated information about
the people behind a lead (a trial's Principal Investigator, a company
officer, a conference presenter), so cold outreach can open with real
common ground ("I trained at the same institution", "we're both in the
Worcester area") even when no direct LinkedIn connection exists yet.

Deliberately a *separate*, lower-confidence tier from warm_connections.py's
WarmPath — a shared university or city is not a personal connection, just
a possible conversation opener, and this module never claims otherwise.
render_report() renders CommonGround matches under their own "possible
common ground (unverified)" heading, distinct from "Warm path", the same
way CLAUDE.md already ruled out blending a lower-confidence, name-only
match into the same list as a direct warm-path match.

Same anti-fabrication/exact-match discipline as warm_connections.py:
- No fuzzy/semantic matching. Affiliation matching is case-insensitive
  substring matching between two pieces of text that are each already a
  real, publicly-stated institution name — one parsed from the user's own
  LinkedIn export, one from a lead's `related_people` (itself gated on
  "only if a source states it publicly", see bd_agent.py's prompts and
  clinicaltrials_gov.py's Overall Official field). Never a guessed or
  inferred affiliation on either side.
- No numeric "closeness" score — same reasoning as warm_connections.py's
  "No invented connection-strength score": there's no data here that would
  make a richer score meaningful, so every match is reported as a plain
  fact and left for a human to judge.

Data sources for "the other side" of the match (a lead's `related_people`):
free for ClinicalTrials.gov leads (a trial's Overall Official is
structured API data — see clinicaltrials_gov.py), and an additional
instruction folded into the three existing paid Claude prompts for
Claude-driven leads — no extra API budget, since it rides along in the
same call, and Claude is told to only fill it in when a source states it
publicly, same discipline as every other field in those prompts.

Affiliation extraction from free-text headlines/summaries is a best-effort
heuristic (`_extract_affiliations()`), not a guaranteed complete parse —
same posture as pr_wire_feeds.py's `_guess_company_name()`. It only
recognizes phrases containing a known institution-type word (university,
hospital, institute, etc.) split out from surrounding text on common
LinkedIn headline separators ("at", "with", commas, dashes) — narrow by
design, so it doesn't need a full name-entity model and won't confidently
mis-extract something that isn't actually an institution name.
"""

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROFILE_HEADER_PREFIX = "First Name,Last Name"

# Words that mark a text segment as naming an institution, not just any
# capitalized phrase — deliberately narrow (misses some real institutions
# that don't use one of these words) in exchange for never confidently
# mis-tagging an unrelated phrase as an affiliation.
_INSTITUTION_MARKERS = (
    "university", "college", "medical school", "school of medicine",
    "hospital", "medical center", "health system", "institute",
    "clinic", "cancer center",
)

_SEGMENT_SEPARATORS = (" at ", " with ", " for ", " - ", ",", "|", "•", "\n")


@dataclass
class Biosketch:
    owner: str
    full_name: str
    headline: str
    summary: str
    city: Optional[str]
    region: Optional[str]
    affiliations: list = field(default_factory=list)


@dataclass
class CommonGround:
    owner: str
    kind: str  # "affiliation" | "location"
    detail: str  # the matched institution name, or "City, Region"
    person_name: Optional[str]  # the related person on the lead's side, if known
    person_role: Optional[str]


def _extract_affiliations(text: str) -> list:
    """Best-effort pull of institution-like phrases out of free text (a
    LinkedIn headline or summary) — see the module docstring."""
    if not text:
        return []
    segments = [text]
    for sep in _SEGMENT_SEPARATORS:
        next_segments = []
        for segment in segments:
            next_segments.extend(segment.split(sep))
        segments = next_segments

    found = []
    for segment in segments:
        segment = segment.strip()
        if segment and any(marker in segment.lower() for marker in _INSTITUTION_MARKERS):
            if segment not in found:
                found.append(segment)
    return found


def _parse_geo(geo_location: str) -> tuple:
    """"Worcester, Massachusetts, United States" -> ("Worcester", "Massachusetts").
    Best-effort: LinkedIn's Geo Location free-text format isn't formally
    documented, so this only handles the common "City, Region[, Country]"
    shape and returns (None, None) for anything it can't confidently split."""
    if not geo_location:
        return None, None
    parts = [p.strip() for p in geo_location.split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], None
    return None, None


def load_biosketch_csv(csv_path: Path, owner: str = "") -> Optional[Biosketch]:
    """Parse a LinkedIn "Profile.csv" export (Settings & Privacy -> Data
    privacy -> Get a copy of your data -> Profile). Returns None if the
    file doesn't look like a real Profile.csv export — a whole-run failure
    over one malformed biosketch would be worse than just skipping it, so
    unlike warm_connections.load_linkedin_connections() this doesn't raise;
    see load_biosketches_dir(), which is the only real caller."""
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith(PROFILE_HEADER_PREFIX)), None)
    if header_index is None:
        return None
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    row = next(reader, None)
    if row is None:
        return None

    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    headline = (row.get("Headline") or "").strip()
    summary = (row.get("Summary") or "").strip()
    city, region = _parse_geo((row.get("Geo Location") or "").strip())

    affiliations = []
    for phrase in _extract_affiliations(headline) + _extract_affiliations(summary):
        if phrase not in affiliations:
            affiliations.append(phrase)

    return Biosketch(
        owner=owner,
        full_name=f"{first} {last}".strip(),
        headline=headline,
        summary=summary,
        city=city,
        region=region,
        affiliations=affiliations,
    )


def load_biosketches_dir(dir_path: Path) -> list:
    """Load every `*.csv` file in `dir_path` as one person's LinkedIn
    Profile.csv export, tagged with that file's name (minus extension) as
    `owner` — mirrors warm_connections.load_connections_dir() exactly,
    including its per-file-failure posture: a missing directory returns
    [] (this whole feature is opt-in), and one bad/non-Profile.csv file
    prints a warning and is skipped rather than stopping the others from
    loading."""
    if not dir_path.is_dir():
        return []
    biosketches = []
    for csv_path in sorted(dir_path.glob("*.csv")):
        try:
            biosketch = load_biosketch_csv(csv_path, owner=csv_path.stem)
        except OSError as exc:
            print(f"[biosketch_matching: could not load {csv_path.name} — {exc}]")
            continue
        if biosketch is None:
            print(f"[biosketch_matching: {csv_path.name} doesn't look like a LinkedIn Profile.csv export — skipped]")
            continue
        biosketches.append(biosketch)
    return biosketches


def _affiliation_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a) and bool(b) and (a in b or b in a)


def find_common_ground(biosketches: list, related_people: list) -> list:
    """Compare every biosketch against every related person on one lead
    (`related_people` — each a dict with `name`/`role`/`affiliation`/
    `location`, see bd_agent.py's prompts and clinicaltrials_gov.py),
    returning every CommonGround match found. Affiliation matching is
    case-insensitive substring matching (not fuzzy) between two real,
    already-public institution names; location matching is an exact
    city+region match. No score, no ranking — every match is a plain,
    separately-listed fact, left for a human to judge which (if any) is
    worth opening with."""
    matches = []
    for person in related_people or []:
        person_affiliation = (person.get("affiliation") or "").strip()
        person_city, person_region = _parse_geo((person.get("location") or "").strip())

        for bio in biosketches:
            if person_affiliation:
                for bio_affiliation in bio.affiliations:
                    if _affiliation_match(bio_affiliation, person_affiliation):
                        matches.append(
                            CommonGround(
                                owner=bio.owner,
                                kind="affiliation",
                                detail=bio_affiliation,
                                person_name=person.get("name"),
                                person_role=person.get("role"),
                            )
                        )
                        break

            if bio.city and person_city and bio.city.lower() == person_city.lower():
                matches.append(
                    CommonGround(
                        owner=bio.owner,
                        kind="location",
                        detail=f"{bio.city}, {bio.region}" if bio.region else bio.city,
                        person_name=person.get("name"),
                        person_role=person.get("role"),
                    )
                )
    return matches


def find_common_ground_for_leads(biosketches: list, leads: list) -> dict:
    """Convenience wrapper for a whole report run — `{lead_index:
    [CommonGround, ...]}` for every lead with at least one match, same
    shape as warm_connections.find_warm_paths_for_leads(). A lead with no
    `related_people` (most leads — this field is only populated when a
    source publicly names someone) is simply absent from the result."""
    if not biosketches:
        return {}
    results = {}
    for i, lead in enumerate(leads):
        related_people = lead.get("related_people")
        if not related_people:
            continue
        matches = find_common_ground(biosketches, related_people)
        if matches:
            results[i] = matches
    return results
