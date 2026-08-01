#!/usr/bin/env python3
"""
Business development lead-finder for an imaging CRO.

Searches recent conference presentations for trials matching a given phase,
indication, and result criteria, using Claude with web search. Claude
returns structured lead data (company, trial, abstract link); this script
then looks up a verified CEO/CMO contact via Hunter.io (gated on a minimum
confidence score, so a low-confidence guess is never reported as confirmed)
and renders a preliminary outreach email per lead from a fixed template.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export HUNTER_API_KEY=...          # optional — omit to skip contact lookup
    python bd_agent.py --conference "ASCO GU" ASCO ESMO AUA --year 2025 2026 \
        --indication "bladder cancer" --phase "Phase II" \
        --sender-name "Dr. Darren Brennan" --sender-title "Medical Director" \
        --sender-company "Elevate Imaging" \
        --output leads_report.md
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import anthropic

import hunter_contacts
import seen_leads

MODEL = "claude-opus-5"
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def build_prompt(args: argparse.Namespace) -> str:
    conferences = " and ".join(args.conference)
    years = ", ".join(str(y) for y in args.year)
    return f"""\
You are a business development research assistant for an imaging Contract \
Research Organization (CRO) called "{args.sender_company}". The CRO provides \
imaging services (central image review, endpoint assessment, imaging \
biomarkers) to biotech and pharmaceutical sponsors running clinical trials.

Task — do the following, using web search:

1. Search for {args.phase} clinical trial results in {args.indication} that \
were presented at the {conferences} annual meeting(s) in {years}. Only \
include trials with a POSITIVE primary result (met its primary endpoint, or \
the presenters/company described the result as positive, clinically \
meaningful, or practice-changing).

2. For each qualifying trial, identify:
   - Trial name / identifier (e.g. NCT number) and drug/investigational agent name
   - The sponsoring biotech or pharmaceutical company, and its primary \
website domain (e.g. "protaratx.com" — no "https://" or "www.")
   - A one- to two-sentence summary of the efficacy result and why it's positive
   - The conference name/year and, if available, the exact presentation \
date, abstract title, and abstract/presentation number
   - A direct URL to the abstract or presentation if you can find one \
(conference abstract library, ASCO Meeting Library, ESMO congress resource \
library, AUA abstract archive); otherwise a company press release URL \
announcing the data, noted as such
   - The name and title of the company's CEO or CMO, ONLY if you happen to \
encounter it naturally while researching the trial (e.g. named in a press \
release, or as a quoted spokesperson). Do not spend extra search effort \
specifically hunting for this — a dedicated, verified contact lookup \
happens separately after your research, so this field is a bonus, not a \
requirement.

3. Also list any trials/companies you reviewed but excluded, and why (e.g. \
result was not clearly positive, no commercial sponsor, wrong indication or \
phase).

Do not fabricate anything — trial results, names, dates, abstract numbers, \
or URLs. Omit a field (use null) rather than guess it.

Output format — TWO parts, in this exact order:

PART 1 — a short prose section (a few sentences) noting your search scope \
and any caveats (e.g. if a conference doesn't cover this indication, or you \
ran low on search budget).

PART 2 — after all prose, output exactly one fenced code block starting \
with ```json and ending with ```, containing a single JSON object with \
this exact shape and nothing else inside the fence:

{{
  "leads": [
    {{
      "company_name": "...",
      "company_domain": "..." or null,
      "trial_name": "...",
      "drug_asset_name": "...",
      "conference_name": "...",
      "presentation_date": "..." or null,
      "result_summary": "...",
      "abstract_title": "...",
      "abstract_number": "..." or null,
      "abstract_url": "..." or null,
      "abstract_url_note": "..." or null,
      "contact_name": "..." or null,
      "contact_title": "..." or null
    }}
  ],
  "excluded": [
    {{"company_name": "...", "reason": "..."}}
  ]
}}

If you cannot find any qualifying trials, return an empty "leads" array and \
explain why in PART 1 rather than inventing results.
"""


def run_research(args: argparse.Namespace) -> str:
    """Send the research prompt to Claude and return the full streamed response."""
    client = anthropic.Anthropic()
    prompt = build_prompt(args)

    full_text_parts = []
    print("Researching trials (this can take a few minutes)...\n", file=sys.stderr)

    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": "high"},
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": 30,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                sys.stdout.write(event.delta.text)
                sys.stdout.flush()
                full_text_parts.append(event.delta.text)

        final_message = stream.get_final_message()
        if final_message.stop_reason == "pause_turn":
            print(
                "\n\n[Note: hit the server-side tool-use pause limit; "
                "response may be incomplete. Re-run to continue.]",
                file=sys.stderr,
            )

    return "".join(full_text_parts)


def parse_research_output(text: str):
    """Split Claude's response into (prose_preamble, leads, excluded).

    Falls back to (text, [], []) if the JSON block is missing or malformed,
    so a bad response still produces a readable file instead of crashing.
    """
    matches = list(JSON_FENCE_RE.finditer(text))
    if not matches:
        return text, [], []
    block = matches[-1]
    preamble = text[: block.start()].strip()
    try:
        data = json.loads(block.group(1))
    except json.JSONDecodeError:
        return text, [], []
    return preamble, data.get("leads") or [], data.get("excluded") or []


def enrich_contacts(leads: list, api_key: Optional[str], min_confidence: int, delay_seconds: float = 4.0):
    """Return [(lead, Contact | None)] — Contact is None if lookup was skipped.

    Sleeps `delay_seconds` between successive Hunter.io calls to stay under
    their rate limit. Leads with no `company_domain` never hit the network
    (see hunter_contacts.find_contact) so they don't consume a delay.
    """
    enriched = []
    calls_made = 0
    for lead in leads:
        if not api_key:
            enriched.append((lead, None))
            continue
        if lead.get("company_domain") and calls_made > 0:
            time.sleep(delay_seconds)
        contact = hunter_contacts.find_contact(
            domain=lead.get("company_domain"),
            contact_name=lead.get("contact_name"),
            api_key=api_key,
            min_confidence=min_confidence,
        )
        if lead.get("company_domain"):
            calls_made += 1
        enriched.append((lead, contact))
    return enriched


# Trailing credentials/suffixes that show up in freely-researched name strings
# (e.g. "Jianmin Fang, Ph.D.") and must not be mistaken for a surname.
NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "ph.d.", "md", "m.d."}


def _last_name(full_name: str) -> str:
    """Best-effort surname extraction from a free-text name.

    Drops anything after the first comma (titles/credentials like ", Ph.D."
    almost always follow one), then strips trailing suffix tokens so
    "Jianmin Fang, Ph.D." yields "Fang", not "Ph.D." (a naive
    `.split()[-1]` grabs the credential instead of the name).
    """
    core = full_name.split(",")[0].strip()
    parts = [p for p in core.split() if p.lower().strip(".") not in {s.strip(".") for s in NAME_SUFFIXES}]
    return parts[-1] if parts else core or full_name.strip()


def draft_email(lead: dict, contact, args: argparse.Namespace):
    """Render the fixed-template outreach email. Returns (subject, body)."""
    contact_name = (contact.name if contact and contact.name else None) or lead.get("contact_name")
    if contact and contact.email and contact_name:
        salutation = f"Dear {contact_name},"
    elif contact_name:
        salutation = f"Dear Dr. {_last_name(contact_name)},"
    else:
        salutation = "Hello,"

    abstract_ref = f'"{lead.get("abstract_title", lead.get("trial_name", "your recent presentation"))}"'
    if lead.get("abstract_number"):
        abstract_ref += f' (Abstract #{lead["abstract_number"]})'

    date_clause = f' on {lead["presentation_date"]}' if lead.get("presentation_date") else ""
    conference = lead.get("conference_name", "the conference")
    asset = lead.get("drug_asset_name") or lead.get("trial_name", "this asset")

    body = (
        f"{salutation}\n\n"
        f"I read with interest your recent paper, {abstract_ref}, at {conference}"
        f"{date_clause}. Congratulations on this exciting result.\n\n"
        f"Given this, I wanted to introduce our imaging CRO, {args.sender_company}, "
        f"as a potential imaging vendor as you progress {asset} through its next "
        f"stage of development.\n\n"
        f"{args.sender_company} provides central image review, blinded "
        f"independent endpoint adjudication and imaging biomarker services for "
        f"oncology trials. If it would be useful, I would welcome a short "
        f"introductory call at your convenience — no obligation either way.\n\n"
        f"Best regards,\n\n{args.sender_name}\n{args.sender_title}, {args.sender_company}"
    )
    subject = f"Imaging CRO introduction — {asset}"
    return subject, body


def render_report(
    preamble: str,
    enriched_leads: list,
    excluded: list,
    args: argparse.Namespace,
    hunter_enabled: bool,
    repeat_leads: Optional[list] = None,
) -> str:
    conferences = " and ".join(args.conference)
    years = ", ".join(str(y) for y in args.year)

    lines = [
        f"# {args.indication.title()} — BD Leads for {args.sender_company}",
        "",
        f"**Scope searched:** {conferences} ({years}), {args.phase} trials with positive results.",
        "",
    ]
    if preamble:
        lines += [preamble, ""]
    if not hunter_enabled:
        lines += [
            "_Contact lookup via Hunter.io was skipped (no `HUNTER_API_KEY` "
            "provided) — contacts below are only what Claude found during "
            "research, unverified._",
            "",
        ]
    lines.append("---")

    for lead, contact in enriched_leads:
        title = lead.get("drug_asset_name") or lead.get("trial_name", "")
        lines += ["", f"## {lead.get('company_name', 'Unknown company')} — {title}", ""]
        lines.append(
            f"**Trial:** {lead.get('trial_name', '')} | "
            f"**Conference:** {lead.get('conference_name', '')} | "
            f"**Result:** {lead.get('result_summary', '')}"
        )
        lines.append("")

        if lead.get("abstract_url"):
            note = f" — {lead['abstract_url_note']}" if lead.get("abstract_url_note") else ""
            lines.append(f"**Abstract:** [{lead.get('abstract_title', 'link')}]({lead['abstract_url']}){note}")
        else:
            lines.append("**Abstract:** no direct link found")
        lines.append("")

        if contact and contact.email:
            title_part = f", {contact.title}" if contact.title else ""
            lines.append(
                f"**Contact:** {contact.name or lead.get('contact_name', 'Unknown')}{title_part} — "
                f"{contact.email} _(Hunter.io confidence: {contact.confidence}/100)_"
            )
        elif contact and contact.source.startswith("error"):
            lines.append(
                f"**Contact:** ⚠️ Hunter.io lookup FAILED for this company ({contact.source}) — "
                f"this is not the same as 'no contact found'; the lookup never completed. "
                f"Re-run once the issue is resolved before treating this as unconfirmed."
            )
        elif contact and contact.confidence is not None:
            lines.append(
                f"**Contact:** not confirmed — Hunter.io found a possible match at "
                f"{contact.confidence}/100 confidence, below the "
                f"{args.hunter_min_confidence}/100 threshold. Verify manually before sending."
            )
        elif lead.get("contact_name"):
            title_part = f", {lead['contact_title']}" if lead.get("contact_title") else ""
            lines.append(f"**Contact:** {lead['contact_name']}{title_part} — email not confirmed")
        else:
            lines.append("**Contact:** not publicly available")
        lines.append("")

        subject, body = draft_email(lead, contact, args)
        lines.append("**Draft email:**")
        lines.append("")
        lines.append(f"> Subject: {subject}")
        lines.append(">")
        for paragraph in body.split("\n\n"):
            lines.append("> " + paragraph.replace("\n", "\n> "))
            lines.append(">")
        lines.append("")
        lines.append("---")

    if excluded:
        lines += ["", "## Reviewed but NOT included (and why)", ""]
        for item in excluded:
            lines.append(f"- **{item.get('company_name', 'Unknown')}** — {item.get('reason', '')}")

    if repeat_leads:
        lines += [
            "",
            "## Already shown in a previous report (skipped here)",
            "",
            "_These matched a lead from an earlier run and were left out to avoid "
            "repeating leads you've already reviewed. Delete `seen_leads.json` if "
            "you want everything to resurface._",
            "",
        ]
        for lead, first_seen in repeat_leads:
            title = lead.get("drug_asset_name") or lead.get("trial_name", "")
            lines.append(f"- **{lead.get('company_name', 'Unknown')}** — {title} (first seen {first_seen})")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--conference", nargs="+", default=["ASH", "ASCO"], help="Conference(s) to search (default: ASH ASCO)")
    parser.add_argument("--year", nargs="+", type=int, default=[2025, 2026], help="Year(s) to search (default: 2025 2026)")
    parser.add_argument("--indication", default="bladder cancer", help="Cancer type / indication")
    parser.add_argument("--phase", default="Phase II", help="Trial phase")
    parser.add_argument("--sender-name", default="[Your Name]", help="Your name for the draft emails")
    parser.add_argument("--sender-title", default="[Your Title]", help="Your title for the draft emails")
    parser.add_argument("--sender-company", default="Elevate Imaging", help="Your CRO's name for the draft emails")
    parser.add_argument("--output", "-o", default="leads_report.md", help="Output markdown file path")
    parser.add_argument(
        "--hunter-api-key",
        default=os.environ.get("HUNTER_API_KEY"),
        help="Hunter.io API key for verified contact lookup (env: HUNTER_API_KEY). Omit to skip contact lookup.",
    )
    parser.add_argument(
        "--hunter-min-confidence",
        type=int,
        default=90,
        help="Minimum Hunter.io confidence score (0-100) required to report an email as confirmed (default: 90)",
    )
    parser.add_argument(
        "--hunter-delay-ms",
        type=int,
        default=4000,
        help="Milliseconds to wait between Hunter.io API calls (default: 4000, i.e. 15/min — "
        "Hunter's stated free-tier limit). Lower this if your plan's actual limit is per-second, not per-minute.",
    )
    parser.add_argument(
        "--seen-file",
        default="seen_leads.json",
        help="Path to the local dedup file (default: seen_leads.json, next to the report). "
        "Leads already recorded here are skipped in future runs.",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Show every lead this run, even ones already recorded in --seen-file, and don't update it.",
    )
    args = parser.parse_args()

    raw_response = run_research(args)
    preamble, leads, excluded = parse_research_output(raw_response)

    out_path = Path(args.output)

    if not leads and not excluded:
        out_path.write_text(raw_response, encoding="utf-8")
        print(
            "\n\n[Warning: could not parse structured lead data from the response — "
            "saved the raw response instead]",
            file=sys.stderr,
        )
        print(f"Saved report to {out_path.resolve()}", file=sys.stderr)
        return

    seen_path = Path(args.seen_file)
    repeat_leads = []
    if args.no_dedup:
        new_leads = leads
    else:
        seen = seen_leads.load_seen(seen_path)
        new_leads, repeat_leads = seen_leads.split_new_and_repeats(leads, seen)
        seen_leads.save_seen(seen_path, seen)
        if repeat_leads:
            print(
                f"[{len(repeat_leads)} of {len(leads)} lead(s) already appeared in a "
                f"previous report — skipping them. See {seen_path.resolve()}]",
                file=sys.stderr,
            )

    print(f"\n\nLooking up {len(new_leads)} contact(s) via Hunter.io..." if args.hunter_api_key else "", file=sys.stderr)
    enriched = enrich_contacts(new_leads, args.hunter_api_key, args.hunter_min_confidence, args.hunter_delay_ms / 1000)

    failed = [(lead, contact) for lead, contact in enriched if contact and contact.source.startswith("error")]
    if failed:
        print(
            f"[Warning: Hunter.io lookup FAILED (not just 'no match') for "
            f"{len(failed)} of {len(new_leads)} companies — see the ⚠️ lines in the "
            f"report. First error: {failed[0][1].source}]",
            file=sys.stderr,
        )

    report = render_report(
        preamble, enriched, excluded, args, hunter_enabled=bool(args.hunter_api_key), repeat_leads=repeat_leads
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"Saved report to {out_path.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
