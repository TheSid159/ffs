#!/usr/bin/env python3
"""
Business development lead-finder for an imaging CRO.

Searches recent ASH / ASCO conference presentations for trials matching a
given phase, indication, and result criteria, identifies the sponsoring
biotech/pharma company and its CEO/CMO contact, and drafts a preliminary
outreach email for each lead.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python bd_agent.py --conference ASH ASCO --year 2025 2026 \
        --indication "bladder cancer" --phase "Phase II" \
        --sender-name "Dr. Darren Brennan" --sender-title "Medical Director" \
        --sender-company "Elevate Imaging" \
        --output leads_report.md
"""

import argparse
import sys
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"


def build_prompt(args: argparse.Namespace) -> str:
    conferences = " and ".join(args.conference)
    years = ", ".join(str(y) for y in args.year)
    return f"""\
You are a business development research assistant for an imaging Contract \
Research Organization (CRO) called "{args.sender_company}". The CRO provides \
imaging services (central image review, endpoint assessment, imaging \
biomarkers) to biotech and pharmaceutical sponsors running clinical trials.

Task — do the following, in order, using web search:

1. Search for {args.phase} clinical trial results in {args.indication} that \
were presented at the {conferences} annual meeting(s) in {years}. \
Only include trials with a POSITIVE primary result (met its primary \
endpoint, or the presenters/company described the result as positive, \
clinically meaningful, or practice-changing).

2. For each qualifying trial, identify:
   - Trial name / identifier (e.g. NCT number) and drug/investigational agent name
   - The sponsoring biotech or pharmaceutical company
   - A one- to two-sentence summary of the efficacy result and why it's positive
   - The conference, year, and abstract/presentation number if available

3. For each company found, search for its current CEO and/or Chief Medical \
Officer (CMO), and any publicly available business-development or investor \
contact email (company press releases, IR pages, LinkedIn, or the company \
"contact us" page often have this). If no direct email is publicly \
available, say so explicitly rather than guessing one.

4. For each lead, draft a short, professional preliminary outreach email \
from {args.sender_name}, {args.sender_title} at {args.sender_company}. The \
email MUST open with exactly this template, with the bracketed placeholders \
filled in from your research (do not paraphrase or restructure this opening \
— fill in the placeholders and keep the wording and sentence structure as-is):

    Dear [contact name],

    I read with interest your recent paper, "[abstract title]" (Abstract \
#[abstract number]), at [meeting name] on [presentation date]. \
Congratulations on this exciting result.

    Given this, I wanted to introduce our imaging CRO, {args.sender_company}, \
as a potential imaging vendor as you progress [drug/asset name] through its \
next stage of development.

If the contact's name is unknown, use "Dr. [Last Name]" if you have a last \
name, otherwise "Hello" instead of "Dear [contact name]". If the exact \
presentation date isn't available, use the conference dates or omit that \
clause gracefully. If the abstract number isn't available, reference the \
abstract title alone. Never fabricate a name, date, or abstract number — \
omit what you can't verify rather than guessing.

After that opening, add 2-4 more sentences that:
   - Briefly state what {args.sender_company} does (central imaging review / \
endpoint adjudication / imaging biomarkers for oncology trials)
   - Suggest a short call, with no hard sell
   - Close with a professional sign-off from {args.sender_name}, \
{args.sender_title}, {args.sender_company}

Address the email to the CMO if identified, otherwise a general BD/IR contact.

Output format — Markdown, one section per lead, in this exact structure:

## [Company Name] — [Trial/Drug Name]

**Trial:** [name/NCT] | **Conference:** [conf, year] | **Result:** [1-2 sentence summary]

**Contact:** [Name, Title] — [email or "not publicly available"]

**Draft email:**

> Subject: [subject line]
>
> [email body]

---

If you cannot find any qualifying trials, say so plainly rather than \
inventing results. Do not fabricate contact emails — only report ones found \
via search, and clearly flag when a contact could not be confirmed.
"""


def run(args: argparse.Namespace) -> str:
    client = anthropic.Anthropic()
    prompt = build_prompt(args)

    full_text_parts = []
    print("Researching trials and drafting leads (this can take a few minutes)...\n", file=sys.stderr)

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
    args = parser.parse_args()

    report = run(args)

    out_path = Path(args.output)
    out_path.write_text(report, encoding="utf-8")
    print(f"\n\nSaved report to {out_path.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
