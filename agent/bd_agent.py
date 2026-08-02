#!/usr/bin/env python3
"""
Business development lead-finder for an imaging CRO.

Four independent subcommands (see CLAUDE.md for why they're split):

- "conferences": Claude-driven web research, using web search, across the
  two conference-anchored signal types (trial results, conference agenda/
  highlight activity) at named conferences. Costs real API usage.
- "trial-signals": free, deterministic checks against ClinicalTrials.gov,
  SEC EDGAR filings, and press-release RSS feeds — no LLM, no API cost, so
  it can be run far more often than any of the three paid searches below.
- "phase-transitions": a narrower, deeper Claude-driven web search focused
  on just one signal (a Phase 1-to-Phase 2 transition), with full open-web
  reach (LinkedIn, biotech news, blogs, hospital/university press). Costs
  real API usage.
- "signal-sweep": Claude-driven web research across the other nine BD
  signal types (funding, leadership changes, new registrations, regulatory
  designations/milestones, trial expansions, protocol amendments, hiring
  signals, vendor-switch signals) — not tied to any conference, meant to
  run on its own regular cadence. Costs real API usage.

Any subcommand's structured lead data is then looked up for a verified
CEO/CMO contact via Hunter.io (gated on a minimum confidence score, so a
low-confidence guess is never reported as confirmed) and rendered into a
preliminary outreach email per lead from a fixed template.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    export HUNTER_API_KEY=...          # optional — omit to skip contact lookup

    python bd_agent.py conferences --conference "ASCO GU" ASCO ESMO AUA --year 2025 2026 \
        --indication "bladder cancer" --phase "Phase II" \
        --sender-name "Dr. Darren Brennan" --sender-title "Medical Director" \
        --sender-company "Elevate Imaging"

    python bd_agent.py trial-signals --indication "bladder cancer" \
        --sender-name "Dr. Darren Brennan" --sender-title "Medical Director" \
        --sender-company "Elevate Imaging"
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import anthropic

import clinicaltrials_gov
import conferences
import email_drafts
import hunter_contacts
import pr_wire_feeds
import sec_edgar
import seen_leads

MODEL = "claude-opus-5"
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

# Claude Opus 5 API pricing (confirmed against platform.claude.com/docs/en/pricing
# at the time this was written) — used only to print an approximate per-run cost
# from the response's own usage numbers, not an official bill. Check
# console.anthropic.com for exact billing; re-check this page if pricing changes.
OPUS_5_INPUT_PER_MTOK = 5.00
OPUS_5_OUTPUT_PER_MTOK = 25.00
WEB_SEARCH_PER_1000_SEARCHES = 10.00


def _estimate_run_cost(usage) -> tuple:
    """Approximate $ cost and web-search-request count from a Message's
    `usage` object. Cache read/write costs are included for completeness
    even though this pipeline doesn't currently use prompt caching (so
    they'll normally be 0) — see CLAUDE.md if that changes."""
    input_cost = usage.input_tokens / 1_000_000 * OPUS_5_INPUT_PER_MTOK
    output_cost = usage.output_tokens / 1_000_000 * OPUS_5_OUTPUT_PER_MTOK
    cache_read_cost = (usage.cache_read_input_tokens or 0) / 1_000_000 * OPUS_5_INPUT_PER_MTOK * 0.1
    cache_write_cost = (usage.cache_creation_input_tokens or 0) / 1_000_000 * OPUS_5_INPUT_PER_MTOK * 1.25
    search_requests = usage.server_tool_use.web_search_requests if usage.server_tool_use else 0
    search_cost = search_requests / 1000 * WEB_SEARCH_PER_1000_SEARCHES
    total = input_cost + output_cost + cache_read_cost + cache_write_cost + search_cost
    return total, search_requests


FILENAME_UNSAFE_RE = re.compile(r'[<>:"/\\|?*]')


def _sanitize_filename_part(text: str) -> str:
    """Make a string safe to use inside a filename on Windows (the target
    platform) as well as macOS/Linux — spaces become underscores and
    characters Windows forbids in filenames are stripped."""
    return FILENAME_UNSAFE_RE.sub("", text.strip().replace(" ", "_"))


def default_output_basename(indication: str, conference: list, year: list) -> str:
    """Build a descriptive default output filename (no extension) from the
    search parameters, e.g. "bladder_cancer_ASCO_GU_2025_2026_leads_report"
    — so re-running with different parameters doesn't silently overwrite an
    unrelated earlier report under the same generic "leads_report" name."""
    indication_part = _sanitize_filename_part(indication) or "leads"
    conference_part = "_".join(_sanitize_filename_part(c) for c in conference)
    year_part = "_".join(str(y) for y in year)
    parts = [p for p in (indication_part, conference_part, year_part) if p]
    return "_".join(parts) + "_leads_report"


def default_trial_signals_basename(indication: str) -> str:
    """Default output filename (no extension) for a trial-signals search,
    e.g. "bladder_cancer_trial_signals_report" — kept separate from
    default_output_basename() (used by the conference search) so the two
    independent searches never share a default filename and overwrite each
    other's report."""
    indication_part = _sanitize_filename_part(indication) or "leads"
    return f"{indication_part}_trial_signals_report"


def default_phase_transition_basename(indication: str) -> str:
    """Default output filename (no extension) for a phase-transition deep
    search, e.g. "bladder_cancer_phase_transition_report" — kept separate
    from the other default-basename functions so all independent searches
    never share a default filename and overwrite each other's report."""
    indication_part = _sanitize_filename_part(indication) or "leads"
    return f"{indication_part}_phase_transition_report"


def default_signal_sweep_basename(indication: str) -> str:
    """Default output filename (no extension) for a signal-sweep search,
    e.g. "bladder_cancer_signal_sweep_report" — kept separate from the
    other default-basename functions so all independent searches never
    share a default filename and overwrite each other's report."""
    indication_part = _sanitize_filename_part(indication) or "leads"
    return f"{indication_part}_signal_sweep_report"


def build_prompt(args: argparse.Namespace) -> str:
    conference_list = " and ".join(args.conference)
    years = ", ".join(str(y) for y in args.year)
    return f"""\
You are a business development research assistant for an imaging Contract \
Research Organization (CRO) called "{args.sender_company}". The CRO provides \
imaging services (central image review, endpoint assessment, imaging \
biomarkers) to biotech and pharmaceutical sponsors running clinical trials.

Task — using web search, find business-development leads in {args.indication} \
across TWO signal categories, both tied to the specific conference(s) named \
below (nine other, non-conference-anchored signal categories — funding, \
leadership changes, new trial registrations, regulatory designations/ \
milestones, trial expansions, protocol amendments, hiring signals, and \
vendor-switch signals — are covered by a separate, more regularly-run \
"signal sweep" search, not this one; see CLAUDE.md for why they're split). \
A lead is any biotech/pharma company activity that could be a reason to \
introduce {args.sender_company} as an imaging vendor. Search for both \
categories as thoroughly as your search budget allows; note in your PART 1 \
summary if you had to skip or under-search either one.

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
direct URL to the agenda page, program listing, or announcement. Also check \
imaging-science and clinical-operations meetings, where sponsors presenting \
early-phase imaging biomarker data in {args.indication} are strong \
prospects — they're actively generating imaging endpoints even before a \
pivotal trial:
{conferences.format_imaging_clinops_meeting_list()}
Additionally — do NOT maintain or assume a fixed list of every regional or \
subspecialty meeting, there are too many to enumerate — but when a major \
meeting from the lists above is in scope for this search and is coming up \
soon, actively search for smaller regional or subspecialty meetings \
happening in roughly the same window (a few weeks before or after). \
Companies sometimes present early data or make announcements at a smaller \
regional meeting shortly ahead of a major congress. Only include one you \
actually find evidence of — don't invent a plausible-sounding regional \
meeting name.

For every lead in either category, also try to identify:
   - The sponsoring biotech or pharmaceutical company, and its primary \
website domain (e.g. "protaratx.com" — no "https://" or "www.")
   - The name and title of the company's CEO or CMO, ONLY if you happen to \
encounter it naturally while researching (e.g. named in a press release, or \
as a quoted spokesperson). Do not spend extra search effort specifically \
hunting for this — a dedicated, verified contact lookup happens separately \
after your research, so this field is a bonus, not a requirement.

One more thing worth actively noting, folded into the existing \
result_summary/signal_detail text rather than as a separate field — a \
strong, specific signal of imaging-CRO fit, stronger than the category \
alone: for "trial_result" leads, if the trial's endpoint explicitly uses a \
standardized imaging assessment criterion (e.g. RECIST 1.1, iRECIST, PCWG3, \
Lugano), say so — these criteria typically require central/blinded \
independent imaging review. Also note if this appears to be the company's \
first pivotal/registrational trial (as opposed to an earlier-phase trial) — \
companies often only engage an external imaging vendor once a trial has to \
hold up to regulators.

Also list any items you reviewed but excluded, and why (e.g. result was not \
clearly positive, no commercial sponsor, wrong indication or phase).

Do not fabricate anything — results, names, dates, or URLs, in either \
category. Omit a field (use null) rather than guess it.

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
      "signal_type": "trial_result" | "conference_highlight",
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
      "contact_name": "..." or null,
      "contact_title": "..." or null
    }}
  ],
  "excluded": [
    {{"company_name": "...", "reason": "..."}}
  ]
}}

Notes on fields shared across categories: "abstract_url"/"abstract_url_note" \
double as the general "source URL" field for either category (press release \
or agenda page — not literally always an abstract). "abstract_title" \
doubles as a general headline field. "signal_detail" is a one- to \
two-sentence plain-English description of the signal, required for \
"conference_highlight" ("trial_result" uses "result_summary" instead; \
"signal_detail" can be null there).

If you cannot find any qualifying leads in a category, leave it out of the \
"leads" array and explain why in PART 1 rather than inventing results.
"""


def _format_known_leads_for_prompt(
    known_leads: list,
    source_label: str = "The free automated check (ClinicalTrials.gov, SEC EDGAR, press-release RSS)",
) -> str:
    """Render another search's already-found leads as a short bulleted
    block to inject into a research prompt, so Claude doesn't spend budget
    rediscovering them. `source_label` names where these leads came from —
    used both by build_phase_transition_prompt() (the free trial-signals
    pre-check) and build_signal_sweep_prompt() (phase-transitions' own
    recent history, the closest thing available to a pre-check when both
    sides are paid Claude searches — see that function's docstring)."""
    if not known_leads:
        return f"{source_label} found nothing this run — search freely, nothing to avoid duplicating."
    lines = [f"{source_label} already found the following before you started searching:", ""]
    for lead in known_leads:
        company = lead.get("company_name") or "Unknown company"
        detail = lead.get("signal_detail") or ""
        lines.append(f"- {company} — {detail}")
    return "\n".join(lines)


def build_phase_transition_prompt(
    args: argparse.Namespace,
    known_leads: Optional[list] = None,
    signal_sweep_leads: Optional[list] = None,
) -> str:
    """The phase-transition deep-search prompt — deliberately narrower in
    scope than build_prompt() (one signal, not two) but much broader in
    source reach: it explicitly encourages LinkedIn, biotech news sites,
    company blogs, and hospital/university press, not just named
    conferences or the trial-signals search's three fixed APIs
    (ClinicalTrials.gov/SEC EDGAR/press-release RSS). See CLAUDE.md for why
    this is a third, separate, paid search rather than folded into either
    existing one.

    `known_leads` (from run_trial_signals_search(), run for free right
    before this) is injected so Claude doesn't spend paid search budget
    rediscovering what the free check already found — see
    _format_known_leads_for_prompt(). `signal_sweep_leads` is the
    signal-sweep search's own recent history (read from its seen-leads
    file, not a fresh paid run — see _run_phase_transitions_cli()): two of
    its nine categories (new_registration, regulatory_milestone) can
    describe the same underlying event this search's Phase-1-to-Phase-2
    signal covers, so it gets its own, separate known-leads block.
    """
    known_leads_block = _format_known_leads_for_prompt(known_leads or [])
    signal_sweep_block = _format_known_leads_for_prompt(
        signal_sweep_leads or [],
        source_label="The signal-sweep search's own recent history (a separate, previously-run "
        "search covering funding, leadership changes, new registrations, regulatory milestones, "
        "and other non-conference-anchored BD signals)",
    )

    return f"""\
You are a business development research assistant for an imaging Contract \
Research Organization (CRO) called "{args.sender_company}". The CRO provides \
imaging services (central image review, endpoint assessment, imaging \
biomarkers) to biotech and pharmaceutical sponsors running clinical trials.

Task — using web search, find companies in {args.indication} that are \
transitioning, or have very recently transitioned, from a Phase 1 to a \
Phase 2 trial, within roughly the last {args.days} days. A separate, \
free, automated process already checks three fixed sources for this same \
signal (ClinicalTrials.gov, SEC EDGAR 8-K/10-Q filings, and a handful of \
press-release RSS feeds) and was just run before you started — your job \
here is to go further and deeper than those fixed sources can: search \
LinkedIn (company pages, executive posts), biotech/pharma news sites (e.g. \
Endpoints News, Fierce Biotech, BioPharma Dive, STAT News), company blogs \
and press pages, hospital/university press releases, investor-update \
pages, and conference-presentation summaries — not limited to a fixed \
list of domains. Search as broadly as your budget allows.

{known_leads_block}

{signal_sweep_block}

Do NOT spend search budget re-confirming or re-reporting any of the leads \
listed above (from either source) — treat them as already covered. Only \
include one of them in your own "leads" output if you find something \
genuinely new and valuable about it that the source couldn't have (e.g. an \
additional corroborating source, a materially fuller narrative, or a \
specific contact) — and if you do, say so explicitly in "signal_detail" \
(e.g. "Also independently found by the automated ClinicalTrials.gov/SEC \
EDGAR/press-release check; adding here because..."). Otherwise, focus your \
search entirely on finding companies/signals those sources missed.

For every company/trial you find:

1. Before writing it up, actively search for every source you can find \
describing it — don't stop at the first hit.
2. If multiple sources describe the SAME underlying event (for example, an \
SEC 8-K filing and a press release both announcing the same Phase 2 \
initiation, or a LinkedIn post repeating a news article), report it as ONE \
lead, not multiple — list every corroborating source URL you found for it \
in "source_urls", and synthesize the "signal_detail" and "email_opening" \
fields from all of them together. Never report the same underlying event \
as two separate leads just because you found it via two different sources.
3. Write "email_opening" as a 2-4 sentence paragraph that could open a cold \
outreach email, referencing the specific, real, verifiable facts you found \
(e.g. "I saw that Acme Biotech recently announced initiation of its Phase 2 \
trial for [asset], following positive Phase 1 results presented at [event]. \
Congratulations on this milestone."). This must be grounded only in facts \
you actually found in a real source — never invent a quote, a date, a \
number, or a detail that isn't present in something you found via search.

Anti-fabrication rules (same discipline as always): never invent a contact \
name, email address, date, or fact. If you can't verify something, leave \
the field null or say so in "signal_detail" rather than guessing. Only \
include a lead if you found at least one real, citable source for it — if \
you searched and found nothing qualifying, say so in your summary rather \
than inventing a plausible-sounding company or trial.

Don't spend search budget hunting for contact emails or names — that's a \
separate, more reliable step (Hunter.io) after this one.

Return a short prose summary of your search (what you searched, what you \
found, any notable gaps or things you excluded and why), followed by \
exactly one fenced ```json code block with this exact shape:

```json
{{
  "leads": [
    {{
      "signal_type": "phase_transition_deep_signal",
      "company_name": "...",
      "company_domain": "..." or null,
      "trial_name": "..." or null,
      "drug_asset_name": "..." or null,
      "signal_detail": "..." (1-2 sentence factual summary for the report, synthesized from all sources),
      "email_opening": "..." (the 2-4 sentence drafted opening described above),
      "source_urls": ["...", "..."] (every corroborating source URL, at least one),
      "contact_name": null,
      "contact_title": null
    }}
  ],
  "excluded": [
    {{"company_name": "...", "reason": "..."}}
  ]
}}
```
"""


def build_signal_sweep_prompt(args: argparse.Namespace, known_leads: Optional[list] = None) -> str:
    """The signal-sweep prompt — the nine BD signal types split out of
    build_prompt() (see CLAUDE.md for the full split rationale): unlike
    trial_result/conference_highlight, none of these nine are tied to a
    named conference's timing, so batching them into the conference
    search's own irregular, conference-driven cadence under-checked them.
    This search runs on its own schedule (meant to be more regular — e.g.
    weekly — than the conference search), independent of when any
    conference happens to fall, which is why it takes its own `--days`
    look-back window rather than the conference search's `--year` scoping.

    `known_leads` is phase-transitions' own recently-seen leads (read from
    its seen-leads file by _run_signal_sweep_cli(), not a fresh paid run)
    — injected because two of the nine categories here (new_registration,
    regulatory_milestone) can describe the same underlying event
    phase-transitions' Phase-1-to-Phase-2 signal already covers, and unlike
    the free trial-signals pre-check, there's no zero-cost way to check one
    paid Claude search against another before running it.
    """
    known_leads_block = _format_known_leads_for_prompt(
        known_leads or [],
        source_label="The phase-transitions search's own recent history (a separate, previously-run "
        "search for Phase 1-to-Phase 2 transition signals)",
    )

    return f"""\
You are a business development research assistant for an imaging Contract \
Research Organization (CRO) called "{args.sender_company}". The CRO provides \
imaging services (central image review, endpoint assessment, imaging \
biomarkers) to biotech and pharmaceutical sponsors running clinical trials.

Task — using web search, find business-development leads in {args.indication} \
from the last {args.sweep_days} days, across NINE signal categories. None of \
these are tied to a specific conference or named meeting — this is a \
general, open-web sweep meant to run on a regular cadence (e.g. weekly), \
independent of conference timing (a separate search covers the two \
conference-anchored signal types — trial results and conference agenda/ \
highlight activity — on its own, conference-driven schedule). A lead is any \
biotech/pharma company activity that could be a reason to introduce \
{args.sender_company} as an imaging vendor. Search for as many categories as \
your search budget allows; note in your PART 1 summary if you had to skip or \
under-search any category.

{known_leads_block}

Do NOT spend search budget re-confirming or re-reporting any of the leads \
listed above — treat them as already covered. Only include one of them in \
your own "leads" output if you find something genuinely new and valuable \
about it (e.g. an additional corroborating source, a materially fuller \
narrative, or a specific contact) — and if you do, say so explicitly in \
"signal_detail" (e.g. "Also independently found by the phase-transitions \
search; adding here because...").

CATEGORY 1 — "funding": biotech or pharmaceutical companies working in \
{args.indication} that have recently secured funding — Series B/C+ venture \
rounds and IPOs are the strongest version of this signal, since imaging-heavy \
oncology trials are expensive and this kind of raise often precedes an \
imaging-vendor RFP by a few months, but also include grants and partnership/ \
licensing deals with an upfront payment. Include the funding type/amount if \
reported, and a URL to the announcement or press release. Also note in \
signal_detail if the proceeds are specifically said to fund a pivotal/ \
registrational trial, not just general runway.

CATEGORY 2 — "leadership_change": companies working in {args.indication} \
that recently appointed a new CEO, CMO, or CSO. This is directly useful for \
BD outreach — a new executive is a natural reason to (re-)introduce \
{args.sender_company}. Include the person's name, new title, and a URL to \
the announcement.

CATEGORY 3 — "new_registration": newly registered trials in \
{args.indication} on ClinicalTrials.gov or an international equivalent \
registry, even if no results exist yet — this surfaces sponsors before \
their trial reaches a conference. Registries to check:
{conferences.format_registry_list()}
Include the registry name, the registry's trial ID (e.g. an NCT number), and \
a direct URL to the registry entry. Also note in signal_detail if the \
trial's endpoint explicitly uses a standardized imaging assessment criterion \
(e.g. RECIST 1.1, iRECIST, PCWG3, Lugano) — these typically require central/ \
blinded independent imaging review — and whether this appears to be the \
company's first pivotal/registrational trial.

CATEGORY 4 — "regulatory_designation": FDA or EMA designations (Breakthrough \
Therapy, Fast Track, Priority Review, Orphan Drug, EMA PRIME, etc.) recently \
granted to a company's asset in {args.indication}. These often precede a \
company finalizing a pivotal trial's design — including its imaging \
endpoints — and selecting vendors for it. Include the designation type, the \
asset/trial it applies to, and a URL to the announcement.

CATEGORY 5 — "regulatory_milestone": End-of-Phase 2 meetings, Type B/C \
meetings, or other major regulatory-agency interactions recently reported \
for a company's {args.indication} program. These usually mean a pivotal \
trial's design is being finalized around now. Include what was reported and \
a URL.

CATEGORY 6 — "trial_expansion": an existing {args.indication} trial that \
recently expanded to new countries or added sites. Multi-region/multi-site \
trials are where centralized, standardized imaging review becomes valuable \
versus relying on inconsistent local site reads — this is a strong direct \
signal. Include what expanded and a URL.

CATEGORY 7 — "protocol_amendment": a protocol amendment to an existing \
{args.indication} trial that adds or changes an imaging-related requirement \
(e.g. adding an imaging endpoint, switching imaging assessment criteria, \
adding central/blinded independent review). Check registry version/amendment \
history where visible (e.g. ClinicalTrials.gov's "Study Record Versions" \
tab), not just the current listing. This can mean a new imaging need has \
emerged, or that a current imaging vendor isn't working out — a time-\
sensitive signal, though don't speculate about a specific vendor by name. \
Include what changed and a URL.

CATEGORY 8 — "hiring_signal": a company in {args.indication} publicly \
hiring for an imaging-specific clinical role (e.g. "Director of Imaging", \
"Clinical Operations Lead, Imaging", "Imaging Biomarker Lead") — a fairly \
strong tell that an imaging-heavy trial is coming, since this role usually \
manages an imaging CRO relationship rather than replacing one. Include the \
job title, company, and a URL to the posting.

CATEGORY 9 — "vendor_switch_signal": a company in {args.indication} \
publicly describing imaging data delays, quality-control issues, or \
dissatisfaction with a current imaging vendor on one of their trials — in a \
press release, LinkedIn post, or conference talk. This is a strong, direct \
pain-point signal, but it is also the category most likely to not exist for \
a given search, and the most sensitive: only include it if you find an \
actual, citable public statement, never a rumor or inference, and never \
name a specific competing vendor unless the source itself already does so \
explicitly and publicly. If in doubt, leave it out rather than risk \
repeating something unverified about a real company. Include what was said \
and a URL to the source.

For every lead in every category, also try to identify:
   - The sponsoring biotech or pharmaceutical company, and its primary \
website domain (e.g. "protaratx.com" — no "https://" or "www.")
   - The name and title of the company's CEO or CMO, ONLY if you happen to \
encounter it naturally while researching (e.g. named in a press release, or \
as a quoted spokesperson — for a "leadership_change" lead this is usually \
the lead itself). Do not spend extra search effort specifically hunting for \
this — a dedicated, verified contact lookup happens separately after your \
research, so this field is a bonus, not a requirement.

Also list any items you reviewed but excluded, and why (e.g. no commercial \
sponsor, wrong indication, or too old/stale to be a timely lead).

Do not fabricate anything — names, dates, funding amounts, registry IDs, or \
URLs, in any category. Omit a field (use null) rather than guess it.

Return a short prose summary of your search (what you searched, which \
categories you covered, and any caveats), followed by exactly one fenced \
```json code block with this exact shape:

```json
{{
  "leads": [
    {{
      "signal_type": "funding" | "leadership_change" | "new_registration" | "regulatory_designation" | "regulatory_milestone" | "trial_expansion" | "protocol_amendment" | "hiring_signal" | "vendor_switch_signal",
      "company_name": "...",
      "company_domain": "..." or null,
      "trial_name": "..." or null,
      "drug_asset_name": "..." or null,
      "abstract_title": "..." or null,
      "abstract_url": "..." or null,
      "abstract_url_note": "..." or null,
      "signal_detail": "...",
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
```

Notes on fields: "abstract_url"/"abstract_url_note" double as the general \
"source URL" field (press release, registry entry, agenda page). \
"abstract_title" doubles as a general headline field. "signal_detail" is \
required for every category here. "registry_name"/"registry_id" are only \
for "new_registration" leads.

If you cannot find any qualifying leads in a category, leave it out of the \
"leads" array and explain why in PART 1 rather than inventing results.
"""


def _stream_claude_research(prompt: str, max_uses: int, progress_message: str) -> str:
    """Shared Claude web-search streaming call used by every Claude-driven
    search in this tool (currently run_research() for the conference search
    and run_phase_transition_search() for the phase-transition deep search)
    — only the prompt, search budget, and progress message differ between
    callers; the streaming/cost-printing/error-handling logic is identical.
    """
    client = anthropic.Anthropic()
    full_text_parts = []
    print(f"{progress_message}\n", file=sys.stderr)

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "high"},
            tools=[
                {
                    "type": "web_search_20260209",
                    "name": "web_search",
                    "max_uses": max_uses,
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

            usage = final_message.usage
            cost, search_requests = _estimate_run_cost(usage)
            print(
                f"\n\n[Usage: {usage.input_tokens:,} input tokens, "
                f"{usage.output_tokens:,} output tokens, {search_requests} web "
                f"search(es) — approx. cost ${cost:.2f}. This is an estimate from "
                "this response's own token counts, not an official bill — check "
                "console.anthropic.com for exact billing.]",
                file=sys.stderr,
            )
    except anthropic.APIConnectionError as exc:
        # The SDK's own message is a hardcoded, generic "Connection error." —
        # it deliberately wraps (via `raise ... from err`) the real httpx/network
        # exception in __cause__, which is where the actually useful detail is
        # (DNS failure, TLS/proxy interception, connection refused, etc.). Same
        # class of bug as the Hunter.io Cloudflare block: a vague error hid the
        # real cause until it was surfaced explicitly.
        cause = f" Underlying error: {exc.__cause__}" if exc.__cause__ else ""
        raise RuntimeError(
            "Could not reach the Anthropic API (api.anthropic.com)."
            + cause
            + " This is almost always your internet connection, a corporate "
            "firewall/VPN blocking that address, or antivirus/security software "
            "intercepting HTTPS traffic — not a problem with your API key. Try "
            "a different network (e.g. a phone hotspot) to check, or ask your "
            "IT/network admin if api.anthropic.com is blocked."
        ) from exc

    return "".join(full_text_parts)


def run_research(args: argparse.Namespace) -> str:
    """Send the conference-search research prompt to Claude and return the
    full streamed response. max_uses=40 — down from an earlier 90 now that
    build_prompt() covers only the two conference-anchored categories
    (trial_result, conference_highlight); the other nine moved to
    run_signal_sweep_search() below, each with its own budget."""
    prompt = build_prompt(args)
    return _stream_claude_research(
        prompt, max_uses=40, progress_message="Researching trials (this can take a few minutes)..."
    )


def run_phase_transition_search(
    args: argparse.Namespace,
    known_leads: Optional[list] = None,
    signal_sweep_leads: Optional[list] = None,
) -> str:
    """Send the phase-transition deep-search prompt to Claude and return the
    full streamed response. See build_phase_transition_prompt() for what
    makes this different from run_research(): one narrow signal, but full
    open-web search reach (LinkedIn, biotech news, blogs, hospital/
    university press — not limited to named conferences or a fixed source
    list), with Claude itself responsible for cross-source dedup and
    drafting a synthesized email opening per lead. `known_leads` are the
    free trial-signals search's findings (run first — see
    _run_phase_transitions_cli()); `signal_sweep_leads` is the signal-sweep
    search's own recent history — both passed through so Claude doesn't
    spend budget rediscovering them."""
    prompt = build_phase_transition_prompt(args, known_leads, signal_sweep_leads)
    return _stream_claude_research(
        prompt,
        max_uses=60,
        progress_message="Searching broadly for Phase 1-to-Phase 2 transition signals (this can take a few minutes)...",
    )


def run_signal_sweep_search(args: argparse.Namespace, known_leads: Optional[list] = None) -> str:
    """Send the signal-sweep research prompt to Claude and return the full
    streamed response. See build_signal_sweep_prompt() for what these nine
    signal types are and why they're a separate search from the conference
    search (CLAUDE.md has the full split rationale). max_uses=70 — between
    the conference search's 40 (two categories, but grounded against a
    curated conference list, so less blind searching) and phase-transitions'
    60 (one category, but full cross-source narrative synthesis): nine
    categories need real budget of their own, even though none needs
    phase-transitions' depth-per-lead."""
    prompt = build_signal_sweep_prompt(args, known_leads)
    return _stream_claude_research(
        prompt,
        max_uses=70,
        progress_message="Sweeping the open web for funding, leadership, regulatory, and other BD signals "
        "(this can take a few minutes)...",
    )


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


def run_trial_signals_search(args: argparse.Namespace) -> list:
    """Aggregate every free, deterministic (no-LLM) trial-signal lead:
    ClinicalTrials.gov (4 signals — see clinicaltrials_gov.py), SEC EDGAR
    8-K/10-Q filings, and press-release RSS feeds. This is the whole point
    of splitting it from the conference search (run_research(), above): none
    of these three sources costs API money or uses Claude at all, so this
    can be run as often as wanted — daily, hourly — independent of the
    conference search's cost. Each source is independently toggleable via
    --no-ctgov/--no-secedgar/--no-prwire and fails independently: one
    source being unreachable never loses leads from the other two.
    """
    leads = []

    if not args.no_ctgov:
        print("Checking ClinicalTrials.gov for trial milestones, completions, returning-sponsor filings, and site expansions...", file=sys.stderr)
        ctgov_leads = clinicaltrials_gov.find_leads(
            args.indication, Path(args.sponsor_history_file), Path(args.site_history_file)
        )
        print(f"[Found {len(ctgov_leads)} lead(s) via ClinicalTrials.gov]", file=sys.stderr)
        leads += ctgov_leads

    if not args.no_secedgar:
        print("Checking SEC EDGAR for Phase 1/Phase 2 transition language in recent 8-K/10-Q filings...", file=sys.stderr)
        sec_leads = sec_edgar.find_leads(args.sender_company, args.sender_name)
        print(f"[Found {len(sec_leads)} lead(s) via SEC EDGAR]", file=sys.stderr)
        leads += sec_leads

    if not args.no_prwire:
        print("Checking PR Newswire/Business Wire/GlobeNewswire for Phase 1/Phase 2 press releases...", file=sys.stderr)
        pr_leads = pr_wire_feeds.find_leads(args.indication)
        print(f"[Found {len(pr_leads)} lead(s) via press-release feeds]", file=sys.stderr)
        leads += pr_leads

    return leads


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
    "protocol_amendment": "Protocol Amendment",
    "hiring_signal": "Hiring Signal",
    "vendor_switch_signal": "Vendor-Switch Signal",
    "trial_milestone_approaching": "Trial Milestone Approaching",
    "trial_recently_completed": "Trial Recently Completed",
    "phase2_filing_by_returning_sponsor": "New Phase 2 Filing (Returning Sponsor)",
    "trial_site_expansion": "Trial Site Expansion",
    "sec_filing_signal": "SEC Filing Signal",
    "press_release_signal": "Press Release Signal",
    "phase_transition_deep_signal": "Phase Transition (Deep Search)",
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


def _opening_and_transition(lead: dict, args: argparse.Namespace) -> tuple:
    """Return (opening_paragraph, transition_paragraph) for a lead's signal
    type. Only the "trial_result" case is a hard, verbatim CRO requirement
    ("I read with interest your recent paper... Congratulations on this
    exciting result" — see CLAUDE.md "The fixed email opening") — never
    loosen or paraphrase it. The other ten were drafted by Claude Code at
    the user's request as a starting point and are NOT hard-specified the
    same way; treat them as adjustable until the user signs off on exact
    wording, the same way they did for trial_result.
    """
    signal_type = (lead.get("signal_type") or "trial_result").strip().lower()
    company = lead.get("company_name") or "your company"
    asset = lead.get("drug_asset_name") or lead.get("trial_name") or "this asset"
    intro = f"I wanted to introduce our imaging CRO, {args.sender_company}, as a potential imaging vendor"

    if signal_type == "conference_highlight":
        headline = lead.get("abstract_title") or "your upcoming presentation"
        conference = lead.get("conference_name") or "the conference"
        date_clause = f" on {lead['presentation_date']}" if lead.get("presentation_date") else ""
        opening = f'I saw that "{headline}" is on the agenda at {conference}{date_clause}. Congratulations on the recognition.'
        transition = f"Given this, {intro} as you prepare for {conference} and progress {asset} through its next stage of development."

    elif signal_type == "funding":
        detail = lead.get("signal_detail") or "your recent funding"
        opening = f"I read about {company}'s recent funding — {detail} Congratulations on the milestone."
        transition = f"Given this, {intro} as you progress {asset} through its next stage of development with this new funding."

    elif signal_type == "leadership_change":
        new_title = lead.get("contact_title") or "your new role"
        opening = f"I read that you've recently joined {company} as {new_title}. Congratulations on the appointment."
        transition = f"Given this, {intro} as you build out {company}'s clinical development strategy."

    elif signal_type == "new_registration":
        registry = lead.get("registry_name") or "the registry"
        opening = f"I saw that {company} has registered a new {args.phase} trial for {asset} on {registry}. Congratulations on advancing to this stage."
        transition = f"Given this, {intro} as you plan imaging assessment for this trial."

    elif signal_type == "regulatory_designation":
        detail = lead.get("signal_detail") or "this regulatory designation"
        opening = f"I read that {asset} recently received a regulatory designation — {detail} Congratulations on this important milestone."
        transition = f"Given this, {intro} as you accelerate {asset}'s development plan."

    elif signal_type == "regulatory_milestone":
        detail = lead.get("signal_detail") or "this regulatory milestone"
        opening = f"I read that {company} recently reached a regulatory milestone — {detail} Congratulations on reaching this stage."
        transition = f"Given this, {intro} as you finalize your pivotal trial design."

    elif signal_type == "trial_expansion":
        detail = lead.get("signal_detail") or "this trial expansion"
        opening = f"I saw that {company} recently expanded a trial — {detail} Congratulations on the expansion."
        transition = f"Given this, {intro} as you scale imaging assessment across these new sites."

    elif signal_type == "protocol_amendment":
        detail = lead.get("signal_detail") or "an imaging-related requirement"
        opening = f"I saw that {company} recently amended the protocol for {asset} — {detail}"
        transition = f"Given this, {intro} as you implement this updated imaging requirement."

    elif signal_type == "hiring_signal":
        detail = lead.get("signal_detail") or "an opening for an imaging-related role"
        opening = f"I saw that {company} recently posted an opening for an imaging-related role — {detail} Congratulations on the growth."
        transition = f"Given this, {intro} as you build out your imaging capabilities for upcoming trials."

    elif signal_type == "vendor_switch_signal":
        # Deliberately doesn't reference the complaint/pain-point content
        # itself — repeating a public complaint about a competitor back to
        # the prospect would read as opportunistic, not professional.
        opening = f"I understand {company} is running imaging-intensive trials in {args.indication}, and wanted to reach out."
        transition = f"{intro}, with reliable turnaround and rigorous quality control built into our process."

    elif signal_type == "trial_milestone_approaching":
        trial = lead.get("trial_name") or "your Phase 1 trial"
        opening = f"I saw via ClinicalTrials.gov that {company}'s Phase 1 trial ({trial}) has a primary completion date approaching in the next few months."
        transition = f"Given this, {intro} as you begin planning imaging needs for the next phase of development."

    elif signal_type == "trial_recently_completed":
        trial = lead.get("trial_name") or "your Phase 1 trial"
        opening = f"I saw via ClinicalTrials.gov that {company}'s Phase 1 trial ({trial}) recently completed. Congratulations on reaching this milestone."
        transition = f"Given this, {intro} as you plan the next phase of development."

    elif signal_type == "trial_site_expansion":
        trial = lead.get("trial_name") or "your trial"
        opening = f"I saw via ClinicalTrials.gov that {company}'s trial ({trial}) has recently expanded to new sites."
        transition = f"Given this, {intro} as you scale imaging assessment consistently across these new sites."

    elif signal_type == "phase2_filing_by_returning_sponsor":
        opening = (
            f"I saw via ClinicalTrials.gov that {company} has registered a new Phase 2 trial, having previously "
            f"run a Phase 1 trial in {args.indication}. Congratulations on advancing to this stage."
        )
        transition = f"Given this, {intro} as you plan imaging assessment for this next phase."

    elif signal_type == "sec_filing_signal":
        detail = lead.get("signal_detail") or "a recent SEC filing"
        opening = f"I read {company}'s recent SEC filing — {detail}"
        transition = f"Given this, {intro} as you plan your next stage of clinical development."

    elif signal_type == "press_release_signal":
        detail = lead.get("abstract_title") or "your recent press release"
        opening = f'I saw the press release "{detail}."'
        transition = f"Given this, {intro} as you plan your next stage of clinical development."

    elif signal_type == "phase_transition_deep_signal":
        # Unlike every other type, the opening here is drafted by Claude
        # itself as part of the research call (see build_phase_transition_prompt()),
        # not constructed in Python — the whole point of this signal type is
        # synthesizing a narrative across multiple corroborating sources,
        # which needs actual understanding, not a template. Still flagged
        # for review in render_report() like every non-trial_result type.
        opening = lead.get("email_opening") or f"I read about {company}'s recent progress in {args.indication}."
        transition = f"Given this, {intro} as you plan your next stage of clinical development."

    else:  # "trial_result" — the hard-specified template, do not alter
        abstract_ref = f'"{lead.get("abstract_title") or lead.get("trial_name") or "your recent presentation"}"'
        if lead.get("abstract_number"):
            abstract_ref += f' (Abstract #{lead["abstract_number"]})'
        date_clause = f" on {lead['presentation_date']}" if lead.get("presentation_date") else ""
        conference = lead.get("conference_name") or "the conference"
        opening = f"I read with interest your recent paper, {abstract_ref}, at {conference}{date_clause}. Congratulations on this exciting result."
        transition = f"Given this, {intro} as you progress {asset} through its next stage of development."

    return opening, transition


def draft_email(lead: dict, contact, args: argparse.Namespace):
    """Render the outreach email for a lead. Returns (subject, body).

    The opening/transition text varies by signal_type — see
    `_opening_and_transition()`. Everything else (salutation logic, company
    blurb, sign-off) is shared across all signal types.
    """
    contact_name = (contact.name if contact and contact.name else None) or lead.get("contact_name")
    if contact and contact.email and contact_name:
        salutation = f"Dear {contact_name},"
    elif contact_name:
        salutation = f"Dear Dr. {_last_name(contact_name)},"
    else:
        salutation = "Hello,"

    asset = lead.get("drug_asset_name") or lead.get("trial_name") or "this asset"
    opening, transition = _opening_and_transition(lead, args)

    body = (
        f"{salutation}\n\n"
        f"{opening}\n\n"
        f"{transition}\n\n"
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
    known_leads: Optional[list] = None,
) -> str:
    # render_report() is shared by all four searches (conference, trial-signals,
    # phase-transitions, signal-sweep — see CLAUDE.md), which are separate,
    # independently-run pipelines, so their args.Namespaces differ: only the
    # conference search's has .conference/.year/.phase; only the
    # phase-transitions search's has .days; only the signal-sweep search's
    # has .sweep_days (deliberately a different attribute name than
    # phase-transitions' .days, even though both are CLI-exposed as --days,
    # so these two branches can't collide with each other).
    if hasattr(args, "conference"):
        conference_list = " and ".join(args.conference)
        years = ", ".join(str(y) for y in args.year)
        scope_line = (
            f"**Scope searched:** {conference_list} ({years}), {args.phase} — trial results and "
            f"conference agenda/keynote/highlight activity (the other nine BD signal types are "
            f"covered by the separate signal-sweep search)."
        )
    elif hasattr(args, "days"):
        scope_line = (
            f"**Scope searched:** open web search (LinkedIn, biotech/pharma news sites, company "
            f"blogs, hospital/university press, and more) for Phase 1-to-Phase 2 transition signals "
            f"in the last {args.days} days — Claude-driven, costs API usage, broader source reach "
            f"than the trial-signals search below/above."
        )
    elif hasattr(args, "sweep_days"):
        scope_line = (
            f"**Scope searched:** open web search in the last {args.sweep_days} days for funding, "
            f"leadership changes, new trial registrations, regulatory designations/milestones, "
            f"trial expansions, protocol amendments, hiring signals, and vendor-switch signals — "
            f"Claude-driven, costs API usage, not tied to any specific conference or meeting."
        )
    else:
        scope_line = (
            "**Scope searched:** ClinicalTrials.gov (trial milestones approaching, recently "
            "completed trials, new Phase 2 filings by returning sponsors, trial site expansions), "
            "SEC EDGAR (8-K/10-Q filings), and press-release RSS feeds — no LLM research involved, "
            "free and deterministic, and independent of the other searches."
        )

    lines = [
        f"# {args.indication.title()} — BD Leads for {args.sender_company}",
        "",
        scope_line,
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

        if signal_type == "phase_transition_deep_signal" and lead.get("source_urls"):
            # Multiple corroborating sources, deliberately listed together
            # rather than picking just one — see build_phase_transition_prompt()'s
            # cross-source-dedup instruction: this lead already represents
            # one underlying event Claude found described in more than one
            # place, and showing every source lets the user judge how well
            # corroborated it is.
            lines.append("**Sources:**")
            for url in lead["source_urls"]:
                lines.append(f"- {url}")
        else:
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

        subject, body = draft_email(lead, contact, args)
        lines.append("**Draft email:**")
        if signal_type == "phase_transition_deep_signal":
            lines.append(
                "_(this opening was synthesized by Claude from the sources above during "
                "this search, not a fixed template — verify every fact against the source "
                "links before sending)_"
            )
        elif signal_type != "trial_result":
            lines.append(
                "_(this signal type's opening was drafted by Claude Code as a starting "
                "point, not hand-specified the way the trial-result template was — "
                "review the wording before relying on it)_"
            )
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

    if known_leads:
        # Only ever set by the phase-transition search — the free
        # trial-signals check run first as a pre-check (see
        # _run_phase_transitions_cli()). Shown for transparency, not as
        # full leads (no contact/email — that's the trial-signals report's
        # job); Claude was told not to re-report these unless it found
        # something genuinely new about them.
        lines += [
            "",
            "## Already found by the free trial-signals check (run first, not repeated as leads above)",
            "",
            "_These were found by the free ClinicalTrials.gov/SEC EDGAR/press-release check that runs "
            "before this deep search — Claude was told not to spend search budget re-reporting them "
            "unless it found something genuinely new. Run the trial-signals search separately for full "
            "details/contacts on these._",
            "",
        ]
        for lead in known_leads:
            title = _lead_title(lead)
            detail = lead.get("signal_detail") or ""
            lines.append(f"- **{lead.get('company_name') or 'Unknown'}** — {title}: {detail}")

    return "\n".join(lines)


CSV_FIELDNAMES = [
    "signal_type", "company_name", "company_domain", "headline", "detail",
    "source_url", "contact_name", "contact_title", "contact_email",
    "contact_confidence", "contact_status", "draft_subject", "draft_body",
]


def _contact_csv_fields(lead: dict, contact, args: argparse.Namespace) -> dict:
    """Contact-status fields for one CSV row. Mirrors the same decision
    tree as the Markdown contact block in render_report() (confirmed /
    lookup failed / below-threshold / found-but-unconfirmed / not publicly
    available), just condensed into short status strings for a spreadsheet
    cell instead of report prose — if that branching logic changes, check
    both places.
    """
    if contact and contact.email:
        return {
            "contact_name": contact.name or lead.get("contact_name") or "",
            "contact_title": contact.title or lead.get("contact_title") or "",
            "contact_email": contact.email,
            "contact_confidence": contact.confidence,
            "contact_status": "confirmed",
        }
    if contact and contact.source.startswith("error"):
        return {
            "contact_name": lead.get("contact_name") or "",
            "contact_title": lead.get("contact_title") or "",
            "contact_email": "",
            "contact_confidence": None,
            "contact_status": f"lookup failed: {contact.source}",
        }
    if contact and contact.confidence is not None:
        return {
            "contact_name": contact.name or lead.get("contact_name") or "",
            "contact_title": contact.title or lead.get("contact_title") or "",
            "contact_email": "",
            "contact_confidence": contact.confidence,
            "contact_status": "not confirmed (below threshold)",
        }
    if lead.get("contact_name"):
        return {
            "contact_name": lead["contact_name"],
            "contact_title": lead.get("contact_title") or "",
            "contact_email": "",
            "contact_confidence": None,
            "contact_status": "not confirmed",
        }
    return {
        "contact_name": "", "contact_title": "", "contact_email": "",
        "contact_confidence": None, "contact_status": "not publicly available",
    }


def render_csv(enriched_leads: list, args: argparse.Namespace) -> str:
    """CSV export of the same leads in render_report(), one row per lead —
    for tracking in a spreadsheet, or importing elsewhere later. Covers
    only this run's leads, not excluded/repeat leads (those are
    informational sections in the Markdown report, not actionable leads).
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()

    for lead, contact in enriched_leads:
        signal_type = (lead.get("signal_type") or "trial_result").strip().lower()
        subject, body = draft_email(lead, contact, args)
        row = {
            "signal_type": signal_type,
            "company_name": lead.get("company_name") or "",
            "company_domain": lead.get("company_domain") or "",
            "headline": _lead_title(lead),
            "detail": lead.get("result_summary") or lead.get("signal_detail") or "",
            "source_url": lead.get("abstract_url") or "; ".join(lead.get("source_urls") or []),
            "draft_subject": subject,
            "draft_body": body,
            **_contact_csv_fields(lead, contact, args),
        }
        writer.writerow(row)

    return buffer.getvalue()


def build_arg_parser() -> argparse.ArgumentParser:
    """Four independent subcommands (see CLAUDE.md): "conferences" is
    Claude-driven web research across the two conference-anchored signal
    types (trial results, conference highlights — costs real API usage,
    meant to run whenever a relevant conference is coming up);
    "trial-signals" is the free, deterministic ClinicalTrials.gov/SEC
    EDGAR/press-release-RSS check (no LLM, no API cost, safe to run daily
    or hourly); "phase-transitions" is a narrower Claude-driven deep web
    search focused only on Phase 1-to-Phase 2 transition signals, with much
    broader source reach than trial-signals' three fixed sources (LinkedIn,
    biotech news, blogs, hospital/university press) — also costs API
    usage; "signal-sweep" is Claude-driven web research across the other
    nine BD signal types (funding, leadership changes, new registrations,
    regulatory designations/milestones, trial expansions, protocol
    amendments, hiring signals, vendor-switch signals) — not tied to any
    conference, meant to run on its own regular cadence (e.g. weekly) —
    also costs API usage. Kept as four separate subcommands so the
    free-vs-paid boundary and each search's own cadence/cost story stay
    clear, and the free one can be run far more often without touching any
    paid budget.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--indication", default="bladder cancer", help="Cancer type / indication")
    common.add_argument("--sender-name", default="[Your Name]", help="Your name for the draft emails")
    common.add_argument("--sender-title", default="[Your Title]", help="Your title for the draft emails")
    common.add_argument("--sender-company", default="Elevate Imaging", help="Your CRO's name for the draft emails")
    common.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output markdown file path (default: auto-named from search parameters)",
    )
    common.add_argument(
        "--hunter-api-key",
        default=os.environ.get("HUNTER_API_KEY"),
        help="Hunter.io API key for verified contact lookup (env: HUNTER_API_KEY). Omit to skip contact lookup.",
    )
    common.add_argument(
        "--hunter-min-confidence",
        type=int,
        default=90,
        help="Minimum Hunter.io confidence score (0-100) required to report an email as confirmed (default: 90)",
    )
    common.add_argument(
        "--hunter-delay-ms",
        type=int,
        default=4000,
        help="Milliseconds to wait between Hunter.io API calls (default: 4000, i.e. 15/min — "
        "Hunter's stated free-tier limit). Lower this if your plan's actual limit is per-second, not per-minute.",
    )
    common.add_argument(
        "--no-dedup",
        action="store_true",
        help="Show every lead this run, even ones already recorded in --seen-file, and don't update it.",
    )
    common.add_argument(
        "--outbox-email",
        default=os.environ.get("OUTBOX_EMAIL"),
        help="Mailbox address to create real, unsent draft emails in (env: OUTBOX_EMAIL). Optional — "
        "omit to skip entirely (nothing is created, same as today). Never sends anything; only "
        "creates drafts in that mailbox's Drafts folder for a human to review and send.",
    )
    common.add_argument(
        "--outbox-app-password",
        default=os.environ.get("OUTBOX_APP_PASSWORD"),
        help="App password for --outbox-email (env: OUTBOX_APP_PASSWORD). Required if --outbox-email is set.",
    )
    common.add_argument(
        "--outbox-imap-host",
        default=os.environ.get("OUTBOX_IMAP_HOST"),
        help="IMAP server for --outbox-email, e.g. imap.gmail.com (Google Workspace/Gmail) or "
        "outlook.office365.com (Microsoft 365/Outlook). Required if --outbox-email is set.",
    )
    common.add_argument(
        "--outbox-imap-port",
        type=int,
        default=993,
        help="IMAP port for --outbox-email (default: 993, the standard IMAPS port — almost never needs changing).",
    )
    common.add_argument(
        "--outbox-drafts-folder",
        default=os.environ.get("OUTBOX_DRAFTS_FOLDER", "Drafts"),
        help='IMAP folder name to create drafts in (default: "Drafts"). Gmail accounts typically need '
        '"[Gmail]/Drafts" instead — check your account if drafts don\'t show up where expected.',
    )

    conf_parser = subparsers.add_parser(
        "conferences",
        parents=[common],
        help="Claude-driven web research across the two conference-anchored signal types (trial "
        "results, conference highlights) at named conferences — costs API usage. The other nine BD "
        "signal types are covered by signal-sweep.",
    )
    conf_parser.add_argument("--conference", nargs="+", default=["ASH", "ASCO"], help="Conference(s) to search (default: ASH ASCO)")
    conf_parser.add_argument("--year", nargs="+", type=int, default=[2025, 2026], help="Year(s) to search (default: 2025 2026)")
    conf_parser.add_argument("--phase", default="Phase II", help="Trial phase")
    conf_parser.add_argument(
        "--seen-file",
        default="seen_leads.json",
        help="Path to the local dedup file (default: seen_leads.json, next to the report).",
    )

    trial_parser = subparsers.add_parser(
        "trial-signals",
        parents=[common],
        help="Free, deterministic checks (ClinicalTrials.gov, SEC EDGAR, press-release RSS) — no LLM, no API cost.",
    )
    trial_parser.add_argument(
        "--seen-file",
        default="trial_signals_seen_leads.json",
        help="Path to the local dedup file (default: trial_signals_seen_leads.json — kept separate "
        "from the conference search's seen-file since these are independent searches).",
    )
    trial_parser.add_argument(
        "--sponsor-history-file",
        default="sponsor_phase_history.json",
        help="Path to the local sponsor-tracking file (default: sponsor_phase_history.json) used to "
        "recognize when a sponsor that previously ran a Phase 1 trial files a new Phase 2 trial.",
    )
    trial_parser.add_argument(
        "--site-history-file",
        default="trial_site_history.json",
        help="Path to the local trial-site-tracking file (default: trial_site_history.json) used to "
        "recognize when a trial has added new sites/locations since a previous run.",
    )
    trial_parser.add_argument(
        "--no-ctgov",
        action="store_true",
        help="Skip the direct ClinicalTrials.gov checks (free, no API key, no LLM involved).",
    )
    trial_parser.add_argument(
        "--no-secedgar",
        action="store_true",
        help="Skip the SEC EDGAR 8-K/10-Q filing check (free, no API key, no LLM involved).",
    )
    trial_parser.add_argument(
        "--no-prwire",
        action="store_true",
        help="Skip the PR Newswire/Business Wire/GlobeNewswire RSS check (free, no API key, no LLM involved).",
    )

    phase_parser = subparsers.add_parser(
        "phase-transitions",
        parents=[common],
        help="Claude-driven deep web search (LinkedIn, biotech news, blogs, hospital/university press) "
        "for Phase 1-to-Phase 2 transition signals only — costs API usage, broader source reach than "
        "trial-signals' three fixed sources.",
    )
    phase_parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="How many days back to search for a transition signal (default: 60).",
    )
    phase_parser.add_argument(
        "--seen-file",
        default="phase_transition_seen_leads.json",
        help="Path to the local dedup file (default: phase_transition_seen_leads.json — kept separate "
        "from the other two searches' seen-files since these are independent searches).",
    )
    phase_parser.add_argument(
        "--sponsor-history-file",
        default="sponsor_phase_history.json",
        help="Path to the local sponsor-tracking file (default: sponsor_phase_history.json — same "
        "default as trial-signals, since this is run first as a free pre-check before the deep "
        "search and the sponsor-tracking data is shared between the two).",
    )
    phase_parser.add_argument(
        "--site-history-file",
        default="trial_site_history.json",
        help="Path to the local trial-site-tracking file (default: trial_site_history.json — same "
        "default as trial-signals, for the same free-pre-check reason as --sponsor-history-file).",
    )

    sweep_parser = subparsers.add_parser(
        "signal-sweep",
        parents=[common],
        help="Claude-driven web research across the nine non-conference-anchored signal types "
        "(funding, leadership changes, new registrations, regulatory designations/milestones, "
        "trial expansions, protocol amendments, hiring signals, vendor-switch signals) — costs API "
        "usage, meant to run on a regular cadence (e.g. weekly) independent of conference timing.",
    )
    sweep_parser.add_argument(
        "--days",
        dest="sweep_days",
        type=int,
        default=30,
        help="How many days back to search (default: 30 — enough buffer for a weekly-run search "
        "without resurfacing stale news indefinitely). Stored as args.sweep_days, not args.days, so "
        "render_report() can't confuse this search's Namespace with phase-transitions'.",
    )
    sweep_parser.add_argument(
        "--seen-file",
        default="signal_sweep_seen_leads.json",
        help="Path to the local dedup file (default: signal_sweep_seen_leads.json — kept separate "
        "from the other searches' seen-files since these are independent searches).",
    )

    return parser


def _finalize_and_write(
    preamble: str,
    leads: list,
    excluded: list,
    args: argparse.Namespace,
    raw_response: Optional[str] = None,
    known_leads: Optional[list] = None,
) -> None:
    """Shared dedup -> Hunter enrich -> render -> write tail for all four
    CLI subcommands — only the research step before this differs between
    them. `raw_response` is only ever set by the conference/phase-transition/
    signal-sweep searches (the fallback text written if Claude's response
    couldn't be parsed as structured leads); the trial-signals search has no
    such raw text to fall back to. `known_leads` is only ever set by the
    phase-transition search — the free trial-signals findings it was given
    as context (see _run_phase_transitions_cli()) — shown in the report for
    transparency even when Claude's own leads list is empty.
    """
    out_path = Path(args.output)

    if not leads and not excluded:
        if raw_response is not None:
            out_path.write_text(raw_response, encoding="utf-8")
            print(
                "\n\n[Warning: could not parse structured lead data from the response — "
                "saved the raw response instead]",
                file=sys.stderr,
            )
        else:
            body = "No trial signals found in this run.\n"
            if known_leads:
                body = (
                    f"No new leads from the deep search this run. {len(known_leads)} lead(s) "
                    "were found by the free trial-signals check that ran first — see the "
                    "trial-signals report for those.\n"
                )
            out_path.write_text(
                f"# {args.indication.title()} — BD Leads for {args.sender_company}\n\n" + body,
                encoding="utf-8",
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

    if args.outbox_email:
        print(f"\nCreating draft emails in {args.outbox_email}...", file=sys.stderr)
        successes, draft_failures = email_drafts.push_drafts_for_report(enriched, args, draft_email)
        print(f"[{successes} draft(s) created in {args.outbox_email} — sitting unsent, review before sending]", file=sys.stderr)
        if draft_failures:
            print(
                f"[Warning: {len(draft_failures)} draft(s) FAILED to create — "
                f"first error: {draft_failures[0][1]}]",
                file=sys.stderr,
            )

    report = render_report(
        preamble,
        enriched,
        excluded,
        args,
        hunter_enabled=bool(args.hunter_api_key),
        repeat_leads=repeat_leads,
        known_leads=known_leads,
    )
    out_path.write_text(report, encoding="utf-8")
    print(f"Saved report to {out_path.resolve()}", file=sys.stderr)

    csv_path = out_path.with_suffix(".csv")
    csv_path.write_text(render_csv(enriched, args), encoding="utf-8", newline="")
    print(f"Saved CSV to {csv_path.resolve()}", file=sys.stderr)


def _run_conferences_cli(args: argparse.Namespace) -> None:
    if not args.output:
        args.output = default_output_basename(args.indication, args.conference, args.year) + ".md"

    raw_response = run_research(args)
    preamble, leads, excluded = parse_research_output(raw_response)
    _finalize_and_write(preamble, leads, excluded, args, raw_response=raw_response)


def _run_trial_signals_cli(args: argparse.Namespace) -> None:
    if not args.output:
        args.output = default_trial_signals_basename(args.indication) + ".md"

    leads = run_trial_signals_search(args)
    _finalize_and_write("", leads, [], args, raw_response=None)


def _run_phase_transitions_cli(args: argparse.Namespace) -> None:
    if not args.output:
        args.output = default_phase_transition_basename(args.indication) + ".md"

    # Run the free trial-signals check first (costs nothing) and pass its
    # findings into the paid deep search as context, so Claude doesn't spend
    # search budget rediscovering what a deterministic check already found —
    # see build_phase_transition_prompt(). This doesn't touch trial-signals'
    # own seen-file/report — it's a fresh, unrecorded check purely to inform
    # this run, not a substitute for actually running "trial-signals" — but
    # it does share --sponsor-history-file, since that's the same underlying
    # fact store regardless of which search triggers the check.
    args.no_ctgov = False
    args.no_secedgar = False
    args.no_prwire = False
    known_leads = run_trial_signals_search(args)

    # Also fold in the signal-sweep search's own recent history (read from
    # its persisted seen-leads file, not a fresh paid run — unlike
    # trial-signals, there's no free way to re-check a paid Claude search).
    # Two of signal-sweep's nine categories can describe the same
    # underlying event as this search's Phase-1-to-Phase-2 signal.
    signal_sweep_leads = seen_leads.recent_entries(Path("signal_sweep_seen_leads.json"), within_days=args.days)

    raw_response = run_phase_transition_search(args, known_leads, signal_sweep_leads)
    preamble, leads, excluded = parse_research_output(raw_response)
    _finalize_and_write(preamble, leads, excluded, args, raw_response=raw_response, known_leads=known_leads)


def _run_signal_sweep_cli(args: argparse.Namespace) -> None:
    if not args.output:
        args.output = default_signal_sweep_basename(args.indication) + ".md"

    # Fold in phase-transitions' own recent history (read from its
    # persisted seen-leads file, not a fresh paid run — see
    # build_signal_sweep_prompt() for why this search can't get the free
    # pre-check trial-signals gives phase-transitions).
    known_leads = seen_leads.recent_entries(Path("phase_transition_seen_leads.json"), within_days=args.sweep_days)

    raw_response = run_signal_sweep_search(args, known_leads)
    preamble, leads, excluded = parse_research_output(raw_response)
    _finalize_and_write(preamble, leads, excluded, args, raw_response=raw_response)


def validate_outbox_args(args: argparse.Namespace) -> Optional[str]:
    """Return an error message if --outbox-email is set but the other
    required IMAP details aren't, so the caller can fail fast and clearly
    rather than discovering it partway through pushing drafts for a whole
    report. Returns None if outbox drafts aren't configured at all, or are
    configured completely."""
    if not args.outbox_email:
        return None
    missing = [
        flag
        for flag, val in (
            ("--outbox-app-password", args.outbox_app_password),
            ("--outbox-imap-host", args.outbox_imap_host),
        )
        if not val
    ]
    if missing:
        return f"--outbox-email is set but {', '.join(missing)} is missing — both are required to create drafts."
    return None


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    outbox_error = validate_outbox_args(args)
    if outbox_error:
        parser.error(outbox_error)

    if args.command == "conferences":
        _run_conferences_cli(args)
    elif args.command == "trial-signals":
        _run_trial_signals_cli(args)
    elif args.command == "phase-transitions":
        _run_phase_transitions_cli(args)
    else:
        _run_signal_sweep_cli(args)


if __name__ == "__main__":
    main()
