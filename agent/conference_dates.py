"""Looks up actual confirmed dates for the meetings in conferences.py via a
small, separate Claude + web search call, and caches the result locally so
the GUI's "upcoming meeting" reminder banner can show real dates without a
network call on every launch.

Deliberately NOT run automatically — refreshed only when the user clicks
"Refresh conference dates" in the GUI, the same on-demand design as the
rest of this tool, since each refresh is a real, billed API call. Until a
refresh has been run at least once, there's no cache and no reminder banner
— this tool never estimates a date it hasn't actually looked up.
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import anthropic

import bd_agent
import conferences

MODEL = "claude-opus-5"
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _all_meeting_names() -> list:
    return [m["name"] for m in conferences.MAJOR_ONCOLOGY_MEETINGS + conferences.IMAGING_AND_CLINOPS_MEETINGS]


def build_prompt() -> str:
    names = "\n".join(f"   - {n}" for n in _all_meeting_names())
    current_year = date.today().year
    return f"""\
Using web search, find the actual confirmed dates for each of the \
following medical/scientific conferences, for {current_year} and \
{current_year + 1} — whichever of those two years' dates are officially \
confirmed and announced on the meeting's own website. Skip a year for a \
meeting entirely if its dates for that year aren't announced yet; do not \
estimate or guess a date from a "typical" past pattern.

{names}

Output exactly one fenced code block starting with ```json and ending \
with ```, containing a single JSON object with this exact shape and \
nothing else inside the fence:

{{
  "meetings": [
    {{
      "name": "...",
      "year": 2026,
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD" or null,
      "city": "..." or null,
      "source_url": "..." or null
    }}
  ]
}}
"""


def run_lookup() -> str:
    """Send the date-lookup prompt to Claude and return the full response."""
    client = anthropic.Anthropic()
    prompt = build_prompt()

    full_text_parts = []
    print("Looking up confirmed conference dates...\n", file=sys.stderr)

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=8000,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 15}],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    full_text_parts.append(event.delta.text)

            usage = stream.get_final_message().usage
            cost, search_requests = bd_agent._estimate_run_cost(usage)
            print(
                f"[Usage: {usage.input_tokens:,} input tokens, {usage.output_tokens:,} "
                f"output tokens, {search_requests} web search(es) — approx. cost "
                f"${cost:.2f}. Estimate only, not an official bill.]",
                file=sys.stderr,
            )
    except anthropic.APIConnectionError as exc:
        # Same generic-message issue as bd_agent.run_research() — surface
        # the real cause instead of the SDK's bare "Connection error."
        cause = f" Underlying error: {exc.__cause__}" if exc.__cause__ else ""
        raise RuntimeError(
            "Could not reach the Anthropic API (api.anthropic.com)."
            + cause
            + " This is almost always your internet connection, a corporate "
            "firewall/VPN blocking that address, or antivirus/security software "
            "intercepting HTTPS traffic — not a problem with your API key."
        ) from exc

    return "".join(full_text_parts)


def parse_lookup_output(text: str) -> list:
    matches = list(JSON_FENCE_RE.finditer(text))
    if not matches:
        return []
    try:
        data = json.loads(matches[-1].group(1))
    except json.JSONDecodeError:
        return []
    return data.get("meetings") or []


def refresh(cache_path: Path) -> list:
    """Run the lookup, cache the result with a timestamp, and return the
    parsed meeting list."""
    raw = run_lookup()
    meetings = parse_lookup_output(raw)
    cache_path.write_text(
        json.dumps(
            {"refreshed_at": datetime.now().isoformat(timespec="seconds"), "meetings": meetings},
            indent=2,
        ),
        encoding="utf-8",
    )
    return meetings


def load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def upcoming_meetings(cache_path: Path, within_days: int = 45) -> list:
    """Meetings from the cache starting within `within_days` of today (or
    already in progress), soonest first. Empty list if there's no cache yet
    or nothing qualifies — the caller decides how to present that."""
    cache = load_cache(cache_path)
    meetings = cache.get("meetings") or []
    today = date.today()
    upcoming = []
    for m in meetings:
        start = m.get("start_date")
        if not start:
            continue
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            continue
        end = m.get("end_date")
        try:
            end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else start_date
        except ValueError:
            end_date = start_date
        if end_date < today:
            continue
        days_until = (start_date - today).days
        if days_until <= within_days:
            upcoming.append({**m, "days_until": days_until})
    upcoming.sort(key=lambda m: m["days_until"])
    return upcoming
