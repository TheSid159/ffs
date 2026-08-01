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

import conferences
import hunter_contacts
import seen_leads

MODEL = "claude-opus-5"
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def build_prompt(args: argparse.Namespace) -> str:
    conference_list = " and ".join(args.conference)
    years = ", ".join(str(y) for y in args.year)
    return f"""\
You are a business development research assistant for an imaging Contract \
Research Organization (CRO) called "{args.sender_company}". The CRO provides \
imaging services (central image review, endpoint assessment, imaging \
biomarkers) to biotech and pharmaceutical sponsors running clinical trials.

Task — using web search, find business-development leads in {args.indication} \
across EIGHT signal categories. A lead is any biotech/pharma company activity \
that could be a reason to introduce {args.sender_company} as an imaging \
vendor. Search for as many categories as your search budget allows; note in \
your PART 1 summary if you had to skip or under-search any category.

CATEGORY 1 — "trial_result": {args.phase} clinical trial results in \
{args.indication} presented at the {conference_list} annual meeting(s) in \
{years}. Only include trials with a POSITIVE primary result (met its primary \
endpoint, or the presenters/company described the result as positive, \
clinically meaningful, or practice-changing). For each: trial name/identifier \
(e.g. NCT number), drug/investigational agent name, a one- to two-sentence \
efficacy summary, the conference name/year, presentation date, abstract \
title, abstract/presentation number, and a direct URL to the abstract or \
presentation (conference abstract library, ASCO Meeting Library, ESMO \
congress resource library, AUA abstract archive) or a company press release \
URL if that's all you can find.

CATEGORY 2 — "conference_highlight": for the same {conference_list} \
meeting(s) in {years} (plus, if relevant, other major oncology/urology \
meetings from this list — use it to judge what counts as "major", don't \
limit yourself only to conferences the user explicitly named for this \
category):
{conferences.format_meeting_list()}
Look for published agendas, keynote speaker announcements, and late-breaking \
abstract titles in {args.indication} — company/trial activity that's \
upcoming or newly announced, not necessarily already presented with a result \
yet. Include the headline/title, conference name, date if known, and a \
direct URL to the agenda page, program listing, or announcement.

CATEGORY 3 — "funding": biotech or pharmaceutical companies working in \
{args.indication} that have recently secured funding (venture round, IPO, \
grant, or partnership/licensing deal with an upfront payment). Include the \
funding type/amount if reported, and a URL to the announcement or press \
release.

CATEGORY 4 — "leadership_change": companies working in {args.indication} \
that recently appointed a new CEO, CMO, or CSO. This is directly useful for \
BD outreach — a new executive is a natural reason to (re-)introduce \
{args.sender_company}. Include the person's name, new title, and a URL to \
the announcement.

CATEGORY 5 — "new_registration": newly registered {args.phase} trials in \
{args.indication} on ClinicalTrials.gov or an international equivalent \
registry, even if no results exist yet — this surfaces sponsors before \
their trial reaches a conference. Registries to check:
{conferences.format_registry_list()}
Include the registry name, the registry's trial ID (e.g. an NCT number), and \
a direct URL to the registry entry.

CATEGORY 6 — "regulatory_designation": FDA or EMA designations (Breakthrough \
Therapy, Fast Track, Priority Review, Orphan Drug, EMA PRIME, etc.) recently \
granted to a company's asset in {args.indication}. These often precede a \
company finalizing a pivotal trial's design — including its imaging \
endpoints — and selecting vendors for it. Include the designation type, the \
asset/trial it applies to, and a URL to the announcement.

CATEGORY 7 — "regulatory_milestone": End-of-Phase 2 meetings, Type B/C \
meetings, or other major regulatory-agency interactions recently reported \
for a company's {args.indication} program. These usually mean a pivotal \
trial's design is being finalized around now. Include what was reported and \
a URL.

CATEGORY 8 — "trial_expansion": an existing {args.indication} trial that \
recently expanded to new countries or added sites. Multi-region/multi-site \
trials are where centralized, standardized imaging review becomes valuable \
versus relying on inconsistent local site reads — this is a strong direct \
signal. Include what expanded and a URL.

For every lead in every category, also try to identify:
   - The sponsoring biotech or pharmaceutical company, and its primary \
website domain (e.g. "protaratx.com" — no "https://" or "www.")
   - The name and title of the company's CEO or CMO, ONLY if you happen to \
encounter it naturally while researching (e.g. named in a press release, or \
as a quoted spokesperson — for a "leadership_change" lead this is usually \
the lead itself). Do not spend extra search effort specifically hunting for \
this — a dedicated, verified contact lookup happens separately after your \
research, so this field is a bonus, not a requirement.

Two more things worth actively noting, folded into the existing \
result_summary/signal_detail text rather than as separate fields — both are \
strong, specific signals of imaging-CRO fit, stronger than the category \
alone:
   - For "trial_result" and "new_registration" leads: if the trial's \
endpoint explicitly uses a standardized imaging assessment criterion (e.g. \
RECIST 1.1, iRECIST, PCWG3, Lugano), say so — these criteria typically \
require central/blinded independent imaging review. Also note if this \
appears to be the company's first pivotal/registrational trial (as opposed \
to an earlier-phase trial) — companies often only engage an external \
imaging vendor once a trial has to hold up to regulators.
   - For "funding" leads: note if the proceeds are specifically said to \
fund a pivotal/registrational trial, not just general runway.

Also list any items you reviewed but excluded, and why (e.g. result was not \
clearly positive, no commercial sponsor, wrong indication or phase, funding \
round too old/stale to be a timely lead).

Do not fabricate anything — results, names, dates, funding amounts, \
registry IDs, or URLs, in any category. Omit a field (use null) rather than \
guess it.

Output format — TWO parts, in this exact order:

PART 1 — a short prose section (a few sentences) noting your search scope, \
which categories you covered, and any caveats (e.g. if a conference doesn't \
cover this indication, a category came up empty, or you ran low on search \
budget).

PART 2 — after all prose, output exactly one fenced code block starting \
with ```json and ending with ```, containing a single JSON object with \
this exact shape and nothing else inside the fence:

{{
  "leads": [
    {{
      "signal_type": "trial_result" | "conference_highlight" | "funding" | "leadership_change" | "new_registration" | "regulatory_designation" | "regulatory_milestone" | "trial_expansion",
      "company_name": "...",
      "company_domain": "..." or null,
      "trial_name": "..." or null,
      "drug_asset_name": "..." or null,
      "conference_name": "..." or null,
      "presentation_date": "..." or null,
      "result_summary": "..." or null,
      "abstract_title": "..." or null,
      "abstract_number": "..." or null,
      "abstract_url": "..." or null,
      "abstract_url_note": "..." or null,
      "signal_detail": "..." or null,
      "registry_name": "..." or null,
      "registry_id": "..." or null,
      "contact_name": "..." or null,
      "contact_title": "..." or null
    }}
  ],
  "excluded": [
    {{"company_name": "...", "reason": "..."}}
  ]
}}

Notes on fields shared across categories: "abstract_url"/"abstract_url_note" \
double as the general "source URL" field for every category (press release, \
registry entry, or agenda page — not literally always an abstract). \
"abstract_title" doubles as a general headline field. "signal_detail" is a \
one- to two-sentence plain-English description of the signal, required for \
every category except "trial_result" (which uses "result_summary" instead; \
"signal_detail" can be null there). "registry_name"/"registry_id" are only \
for "new_registration" leads.

If you cannot find any qualifying leads in a category, leave it out of the \
"leads" array and explain why in PART 1 rather than inventing results.
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


SIGNAL_LABELS = {
    "trial_result": "Trial Result",
    "conference_highlight": "Conference Highlight",
    "funding": "Funding",
    "leadership_change": "Leadership Change",
    "new_registration": "New Trial Registration",
    "regulatory_designation": "Regulatory Designation",
    "regulatory_milestone": "Regulatory Milestone",
    "trial_expansion": "Trial Expansion",
}


def _lead_title(lead: dict) -> str:
    """Best-effort short title for a lead, across all signal types."""
    return (
        lead.get("drug_asset_name")
        or lead.get("trial_name")
        or lead.get("abstract_title")
        or (lead.get("signal_detail") or "")[:80]
        or ""
    )


def draft_email(lead: dict, contact, args: argparse.Namespace):
    """Render the fixed-template outreach email. Returns (subject, body).

    Only call this for "trial_result" leads — the opening ("I read with
    interest your recent paper... Congratulations on this exciting result")
    is a hard, verbatim CRO requirement written for referencing a presented
    trial result, and doesn't fit a funding round, leadership change, or new
    registry filing. Don't loosen/repurpose it for other signal types;
    render_report() skips drafting for those instead.
    """
    contact_name = (contact.name if contact and contact.name else None) or lead.get("contact_name")
    if contact and contact.email and contact_name:
        salutation = f"Dear {contact_name},"
    elif contact_name:
        salutation = f"Dear Dr. {_last_name(contact_name)},"
    else:
        salutation = "Hello,"

    abstract_ref = f'"{lead.get("abstract_title") or lead.get("trial_name") or "your recent presentation"}"'
    if lead.get("abstract_number"):
        abstract_ref += f' (Abstract #{lead["abstract_number"]})'

    date_clause = f' on {lead["presentation_date"]}' if lead.get("presentation_date") else ""
    conference = lead.get("conference_name") or "the conference"
    asset = lead.get("drug_asset_name") or lead.get("trial_name") or "this asset"

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
    conference_list = " and ".join(args.conference)
    years = ", ".join(str(y) for y in args.year)

    lines = [
        f"# {args.indication.title()} — BD Leads for {args.sender_company}",
        "",
        f"**Scope searched:** {conference_list} ({years}), {args.phase} — trial results, "
        f"conference highlights, funding, leadership changes, new trial registrations, "
        f"regulatory designations/milestones, and trial expansions.",
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
        signal_type = (lead.get("signal_type") or "trial_result").strip().lower()
        label = SIGNAL_LABELS.get(signal_type, "Lead")
        title = _lead_title(lead)
        lines += ["", f"## [{label}] {lead.get('company_name') or 'Unknown company'} — {title}", ""]

        if signal_type in ("trial_result", "conference_highlight"):
            lines.append(
                f"**Trial:** {lead.get('trial_name') or ''} | "
                f"**Conference:** {lead.get('conference_name') or ''} | "
                f"**Result:** {lead.get('result_summary') or lead.get('signal_detail') or ''}"
            )
        else:
            detail = lead.get("signal_detail") or ""
            if signal_type == "new_registration" and lead.get("registry_name"):
                reg_id = f" ({lead['registry_id']})" if lead.get("registry_id") else ""
                detail = f"{detail} — registered on {lead['registry_name']}{reg_id}" if detail else f"Registered on {lead['registry_name']}{reg_id}"
            lines.append(f"**Signal:** {detail}")
        lines.append("")

        source_label = "Abstract" if signal_type in ("trial_result", "conference_highlight") else "Source"
        if lead.get("abstract_url"):
            note = f" — {lead['abstract_url_note']}" if lead.get("abstract_url_note") else ""
            lines.append(f"**{source_label}:** [{lead.get('abstract_title') or 'link'}]({lead['abstract_url']}){note}")
        else:
            lines.append(f"**{source_label}:** no direct link found")
        lines.append("")

        if contact and contact.email:
            title_part = f", {contact.title}" if contact.title else ""
            lines.append(
                f"**Contact:** {contact.name or lead.get('contact_name') or 'Unknown'}{title_part} — "
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

        if signal_type == "trial_result":
            subject, body = draft_email(lead, contact, args)
            lines.append("**Draft email:**")
            lines.append("")
            lines.append(f"> Subject: {subject}")
            lines.append(">")
            for paragraph in body.split("\n\n"):
                lines.append("> " + paragraph.replace("\n", "\n> "))
                lines.append(">")
        else:
            lines.append(
                "_No draft email generated — the fixed outreach template's opening "
                '("I read with interest your recent paper... Congratulations on this '
                f'exciting result") is written specifically for a {SIGNAL_LABELS["trial_result"].lower()} '
                "lead and doesn't fit this signal type. Draft this one manually, or ask "
                "for a dedicated template for this signal type._"
            )
        lines.append("")
        lines.append("---")

    if excluded:
        lines += ["", "## Reviewed but NOT included (and why)", ""]
        for item in excluded:
            lines.append(f"- **{item.get('company_name') or 'Unknown'}** — {item.get('reason') or ''}")

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
            title = _lead_title(lead)
            lines.append(f"- **{lead.get('company_name') or 'Unknown'}** — {title} (first seen {first_seen})")

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
