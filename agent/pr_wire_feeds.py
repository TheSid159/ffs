"""RSS monitoring of PR Newswire / Business Wire / GlobeNewswire (free, no
API key, no LLM) for the BD proposal's third data source: small biotechs
often announce a Phase 1 completion or Phase 2 initiation via press release
before ClinicalTrials.gov is updated to match.

No LLM interpretation: entries are kept only if their title/description
literally contains "phase 1" plus either "topline" or "phase 2", alongside
the target indication — the same anti-fabrication posture as
clinicaltrials_gov.py and sec_edgar.py.

IMPORTANT — the three DEFAULT_FEED_URLS below could not be verified with a
live fetch: this dev sandbox's network policy blocks all three wire
services' domains outright (same restriction hit throughout this session),
and their own feed-listing pages returned 403s to automated fetches too. Each
URL was found via web search of the services' own documented feed-URL
patterns (not guessed from scratch), but "found via search" is weaker
evidence than a live 200 response with real entries. If a feed 404s or
comes back empty on every run, that's the first thing to check — see
`find_leads()`'s per-feed warning, which is deliberately NOT silent here
(unlike clinicaltrials_gov.py's error handling) precisely because these
URLs are less trustworthy than a documented API. Edit DEFAULT_FEED_URLS
directly if a service changes its feed URLs.
"""

import datetime as dt
import email.utils
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

REQUEST_TIMEOUT_SECONDS = 20

# Best-effort, not verified live — see module docstring.
DEFAULT_FEED_URLS = [
    "https://www.prnewswire.com/rss/health-latest-news/biotechnology-list.rss",
    "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkpaGVlYXg==",
    "https://www.globenewswire.com/JSWidgetFeed/industry/4573-Biotechnology/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Biotechnology",
]

ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Common press-release verbs used to split "Company Name Announces/Reports
# X" headlines into a best-effort company name. Not reliable enough to
# treat as a confirmed field — see _guess_company_name()'s docstring.
_HEADLINE_VERB_RE = re.compile(
    r"\s+(Announces|Reports|Provides|Presents|Reveals|Receives|Completes|Initiates|Doses|to Present|to Report)\b",
    re.IGNORECASE,
)


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        return resp.read()


def _parse_pubdate(text: str):
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_entries(xml_bytes: bytes) -> list:
    """Return [{"title", "link", "description", "pub_date"}] from either an
    RSS 2.0 (<item>) or Atom (<entry>) feed. Raises xml.etree.ElementTree.ParseError
    if the content isn't valid XML at all (caller decides how to handle)."""
    root = ET.fromstring(xml_bytes)
    entries = []

    items = root.findall(".//item")
    if items:
        for item in items:
            entries.append(
                {
                    "title": (item.findtext("title") or "").strip(),
                    "link": (item.findtext("link") or "").strip(),
                    "description": (item.findtext("description") or "").strip(),
                    "pub_date": _parse_pubdate(item.findtext("pubDate") or ""),
                }
            )
        return entries

    atom_entries = root.findall(f".//{ATOM_NS}entry")
    for entry in atom_entries:
        link_elem = entry.find(f"{ATOM_NS}link")
        link = link_elem.get("href") if link_elem is not None else ""
        summary = entry.findtext(f"{ATOM_NS}summary") or entry.findtext(f"{ATOM_NS}content") or ""
        updated = entry.findtext(f"{ATOM_NS}updated") or entry.findtext(f"{ATOM_NS}published") or ""
        entries.append(
            {
                "title": (entry.findtext(f"{ATOM_NS}title") or "").strip(),
                "link": (link or "").strip(),
                "description": summary.strip(),
                "pub_date": _parse_pubdate(updated),
            }
        )
    return entries


def _guess_company_name(title: str) -> str:
    """Best-effort company name from a press-release headline (e.g. "Acme
    Biotech Announces Positive Phase 1 Data" -> "Acme Biotech"). This is a
    heuristic over free text, not a confirmed field the way ClinicalTrials.gov's
    sponsor name is — render_report()/render_csv() show the full headline
    too, so a bad guess here doesn't hide the real company name from the user.
    """
    match = _HEADLINE_VERB_RE.search(title)
    if match:
        return title[: match.start()].strip()
    if "," in title:
        return title.split(",")[0].strip()
    return title.strip() or "Unknown company"


def _matches(text: str, indication: str) -> bool:
    text = text.lower()
    if indication.strip().lower() not in text:
        return False
    if "phase 1" not in text:
        return False
    return "topline" in text or "phase 2" in text


def _entry_to_lead(entry: dict) -> dict:
    title = entry["title"] or "Untitled press release"
    return {
        "signal_type": "press_release_signal",
        "company_name": _guess_company_name(title),
        "company_domain": None,
        "trial_name": None,
        "drug_asset_name": None,
        "signal_detail": f"Press release: {title}",
        "abstract_url": entry["link"] or None,
        "abstract_url_note": "Company name is a best-effort guess from the headline — verify from the full text.",
        "abstract_title": title,
        "registry_name": None,
        "registry_id": None,
        "contact_name": None,
        "contact_title": None,
    }


def find_leads(indication: str, within_days: int = 14, feed_urls=None) -> list:
    """Press-release leads from the last `within_days` days across
    `feed_urls` (default DEFAULT_FEED_URLS) whose title/description mentions
    `indication`, "phase 1", and either "topline" or "phase 2".

    Unlike clinicaltrials_gov.py/sec_edgar.py, a feed that fails to fetch or
    parse prints a warning instead of failing silently — see the module
    docstring for why these particular URLs warrant that.
    """
    urls = feed_urls if feed_urls is not None else DEFAULT_FEED_URLS
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=within_days)

    leads = []
    for url in urls:
        try:
            raw = _fetch(url)
        except (OSError, urllib.error.HTTPError) as exc:
            print(f"[Warning: could not fetch press-release feed {url} — {exc}]", file=sys.stderr)
            continue

        try:
            entries = _parse_entries(raw)
        except ET.ParseError as exc:
            print(f"[Warning: could not parse press-release feed {url} — {exc}]", file=sys.stderr)
            continue

        for entry in entries:
            pub_date = entry["pub_date"]
            if pub_date is not None:
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=dt.timezone.utc)
                if pub_date < cutoff:
                    continue
            haystack = f"{entry['title']} {entry['description']}"
            if _matches(haystack, indication):
                leads.append(_entry_to_lead(entry))

    return leads
