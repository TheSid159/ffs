# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A single-purpose business development tool for an imaging CRO ("Elevate
Imaging"). It runs **four independent searches**, each its own report,
kept deliberately separate because their cost/scope profiles — and
cadences — differ:

1. **Conference search** — uses the Claude API with the web search tool to
   find business-development leads across the **two conference-anchored
   signal types** (see "Conference-anchored signal types" below) at named
   oncology/urology conferences and beyond. This is a paid path (Claude +
   web search usage per run — see "Per-run cost estimate" below), meant to
   run whenever a relevant conference is coming up — an irregular cadence.
2. **Trial signals search** — free, deterministic checks against
   ClinicalTrials.gov, SEC EDGAR filings, and press-release RSS feeds (see
   "Trial signals search" below). No LLM call, no API cost, so it's meant
   to be run far more often than any of the three paid searches — daily or
   even hourly, with zero budget impact.
3. **Phase transition search** — a narrower, deeper Claude-driven search
   (see "Phase transition deep search" below) focused on just one signal
   (a Phase 1-to-Phase 2 transition) but with full open-web reach —
   LinkedIn, biotech news sites, blogs, hospital/university press — not
   limited to the trial-signals search's three fixed sources. Also a paid
   path, and deliberately kept separate from the conference search since
   it's a different kind of research task (one narrow signal hunted
   broadly, vs. named-conference-anchored signals).
4. **Signal sweep search** — uses the Claude API with the web search tool
   to find business-development leads across the **nine non-conference-
   anchored signal types** (see "Signal sweep search" below) — funding,
   leadership changes, new registrations, regulatory designations/
   milestones, trial expansions, protocol amendments, hiring signals, and
   vendor-switch signals. Also a paid path, but — unlike the conference
   search — not tied to any conference's timing, so it's meant to run on
   its own regular cadence (e.g. weekly) instead of an irregular,
   conference-driven one. Split out of the original eleven-signal
   conference search specifically because batching these nine into the
   conference search's own irregular cadence under-checked them (see
   "Signal sweep search" below).

Every search's leads get a verified CEO/CMO contact lookup via Hunter.io,
and — for `trial_result` leads only — a hand-specified outreach email
template (every other signal type gets a drafted starting point instead,
flagged for review — either a Claude-Code-authored Python template, or, for
`phase_transition_deep_signal` leads specifically, a narrative Claude
itself synthesizes per-lead at research time from every corroborating
source it found). There is no application server or database. There are
two front ends over the same four pipelines: a Tkinter desktop GUI
(`gui.py`, the primary one — the end user is non-technical and
terminal/PowerShell friction was a repeated, significant source of real
problems), with one button per search, and a CLI (`bd_agent.py`) with one
subcommand per search, for scripting. Each writes its own Markdown report
(plus a CSV).

## Commands

```bash
cd agent
pip install -r requirements.txt
python gui.py   # primary interface — prompts for API keys in the window, no env vars needed
```

CLI equivalent (used for scripting, or when developing without a display) —
four independent subcommands:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # from console.anthropic.com — needed for "conferences"/"phase-transitions"/"signal-sweep"
export HUNTER_API_KEY=...             # optional, from hunter.io/api-keys — omit to skip contact lookup

# Paid: Claude-driven web research across the 2 conference-anchored signal types at named conferences.
python bd_agent.py conferences \
  --conference "ASCO GU" ASCO ESMO AUA \
  --year 2025 2026 \
  --indication "bladder cancer" \
  --phase "Phase II" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging"

# Free: ClinicalTrials.gov + SEC EDGAR + press-release RSS, no LLM, no API cost.
python bd_agent.py trial-signals \
  --indication "bladder cancer" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging"

# Paid: Claude-driven deep web search for one signal — Phase 1-to-Phase 2 transition.
python bd_agent.py phase-transitions \
  --indication "bladder cancer" \
  --days 60 \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging"

# Paid: Claude-driven web research across the other 9 (non-conference-anchored) signal types.
# Meant to run on its own regular cadence (e.g. weekly), independent of conference timing.
python bd_agent.py signal-sweep \
  --indication "bladder cancer" \
  --days 30 \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging"
```

There is no test suite, linter, or build step — `python -m py_compile
bd_agent.py hunter_contacts.py seen_leads.py clinicaltrials_gov.py
sec_edgar.py pr_wire_feeds.py sponsor_phase_history.py trial_site_history.py
warm_connections.py hubspot_sync.py gui_logic.py gui.py`
is the only pre-flight check currently used before committing changes
(`gui.py` itself imports `tkinter`, which may not be installed in a
headless dev environment — a `py_compile` syntax check still passes
without it, but `gui_logic.py` is where the real, importable-and-testable
logic lives; see Architecture below). Every CLI flag has a default (see
`--help`), so all four of `bd_agent.py conferences`, `bd_agent.py
trial-signals`, `bd_agent.py phase-transitions`, and `bd_agent.py
signal-sweep` also run with no other arguments for a smoke test. When
editing `parse_research_output()`, `draft_email()`, `render_report()`, or
anything in `gui_logic.py`, sanity check with fabricated lead dicts and
`unittest.mock.patch` on `bd_agent.run_research`/`run_phase_transition_search`/
`run_signal_sweep_search` (no network calls needed) rather than only
testing via a full paid run — see the git history around the Hunter.io
integration and GUI commits for the pattern. The same applies to the
trial-signals sources: `unittest.mock.patch` on `clinicaltrials_gov._get`,
`sec_edgar._get`, and `pr_wire_feeds._fetch`.

## Architecture

Sixteen modules, no application framework:

- **`agent/bd_agent.py`** — all four pipelines, plus everything shared
  between them (Hunter enrichment, email drafting, report/CSV rendering):
  1. `build_prompt()` renders one research prompt from the CLI args
     (conference list, years, indication, phase). It asks Claude to
     research leads via web search across the **two conference-anchored
     signal types** (see "Conference-anchored signal types" below) and
     return a short prose preamble followed by **one fenced ` ```json `
     block** containing structured lead data. Claude is explicitly told
     *not* to spend search budget hunting for contacts; that's a separate,
     more reliable step now. `build_signal_sweep_prompt()` is its sibling
     for the other **nine, non-conference-anchored signal types** (see
     "Signal sweep search" below) — same two-part output shape, different
     categories and no conference/year framing.
  2. `_stream_claude_research(prompt, max_uses, progress_message)` is the
     shared Claude web-search streaming call — sends a prompt to
     `claude-opus-5` via `client.messages.stream(...)` with the server-side
     `web_search_20260209` tool, streams text to stdout for progress
     visibility, prints the usage/cost line, and handles
     `anthropic.APIConnectionError`. All three Claude-driven pipelines call
     this same helper — only the prompt/budget/progress-message differ:
     `run_research()` (conference search, `build_prompt()`, `max_uses=40` —
     brought down from an earlier 90 once the conference search was
     trimmed to two signal types, see "Signal sweep search" below),
     `run_phase_transition_search()`
     (phase-transition search, `build_phase_transition_prompt()`,
     `max_uses=60`), and `run_signal_sweep_search()` (signal-sweep search,
     `build_signal_sweep_prompt()`, `max_uses=70` — nine categories need
     real budget of their own, even though none needs phase-transitions'
     per-lead narrative-synthesis depth).
  3. `parse_research_output()` extracts the trailing JSON block via regex
     (`JSON_FENCE_RE`) into `(preamble, leads, excluded)`. Prompt-agnostic,
     so all three Claude-driven pipelines' output is parsed by the same
     function. If parsing fails, the caller falls back to writing Claude's
     raw text instead of crashing — see `_run_conferences_cli()`/
     `_run_phase_transitions_cli()`/`_run_signal_sweep_cli()`.
  4. `run_trial_signals_search()` is the free path's equivalent entry
     point: it aggregates `clinicaltrials_gov.find_leads()`,
     `sec_edgar.find_leads()`, and `pr_wire_feeds.find_leads()` — no LLM
     involved at all — each independently toggleable
     (`--no-ctgov`/`--no-secedgar`/`--no-prwire`) and each failing
     independently, so one unreachable source never loses the other two's
     leads.
  5. `enrich_contacts()` calls `hunter_contacts.find_contact()` per lead
     (from any of the four pipelines), then `draft_email()` renders the
     outreach email using the structured lead data plus whatever contact
     Hunter confirmed. For `signal_type == "trial_result"` leads this is
     the **hard-specified, verbatim** template (see "The fixed email
     opening" below); for `phase_transition_deep_signal` leads it's
     `lead["email_opening"]` — text Claude itself synthesized at research
     time from every corroborating source it found (see "Phase transition
     deep search" below), not a Python template at all; every other
     signal type gets a fixed Python-templated opening drafted by Claude
     Code, flagged for review. `render_report()` assembles the final
     Markdown, branching its header text on whether `args` has
     `.conference` (conference search), `.days` (phase-transition search),
     `.sweep_days` (signal-sweep search — deliberately a different
     Namespace attribute name than phase-transitions' `.days`, even though
     both are CLI-exposed as `--days`, so these two branches can't
     collide), or none of the three (trial-signals search) since the four
     Namespaces differ — see `build_arg_parser()`/`_run_conferences_cli()`/
     `_run_trial_signals_cli()`/`_run_phase_transitions_cli()`/
     `_run_signal_sweep_cli()`.

  The four searches are independent CLI subcommands
  (`bd_agent.py conferences ...` / `bd_agent.py trial-signals ...` /
  `bd_agent.py phase-transitions ...` / `bd_agent.py signal-sweep ...`, see
  `build_arg_parser()`) and independent GUI buttons (see `gui.py` below) —
  deliberately never merged into one flow, so the free trial-signals search
  can be run as often as wanted without ever touching any paid search's API
  budget, and the three paid searches (different research tasks/cadences —
  two signals at named conferences on an irregular cadence, one signal
  hunted across the open web, nine signals hunted across the open web on a
  regular cadence) stay separately costed and separately reportable. Each
  writes to its own auto-named report/CSV pair (`default_output_basename()`
  / `default_trial_signals_basename()` / `default_phase_transition_basename()`
  / `default_signal_sweep_basename()`) and its own dedup state
  (`seen_leads.json` / `trial_signals_seen_leads.json` /
  `phase_transition_seen_leads.json` / `signal_sweep_seen_leads.json` by
  default), so running one never overwrites or contaminates another's
  history.

### Conference-anchored signal types

`build_prompt()` asks Claude to categorize every lead with a `signal_type`:
`trial_result` (positive Phase II result at a named conference — the
original, only signal type before this tool grew any others) and
`conference_highlight` (agenda/keynote/late-breaking-abstract activity at a
major oncology/urology meeting, grounded against the curated list in
`agent/conferences.py` so Claude isn't searching blind for what counts as
"major" — also covers imaging-science/clinical-ops meetings like SNMMI,
RSNA, DIA, and SCOPE Summit, since sponsors presenting early-phase imaging
biomarker data there are prospects even before a pivotal trial). These are
the only two signal types this search covers — see "Signal sweep search"
below for the other nine, which aren't tied to any conference's timing and
were split out for that reason.

`agent/conferences.py`'s curated lists intentionally stay at the "major
meeting" level (oncology/urology, immuno-oncology, and theranostics/
molecular-imaging meetings like SNMMI and EANM) — there are too many
regional/subspecialty meetings to enumerate and maintain a static list of.
Instead, `CATEGORY 2` of the prompt tells Claude to actively search for
smaller regional meetings that fall in the same timeframe as whichever
major meeting(s) are actually in scope for a given run (e.g. a small
regional breast cancer meeting happening the same month as ESMO), with an
explicit "only include one you actually find evidence of — don't invent a
plausible-sounding regional meeting name" guardrail, same anti-fabrication
discipline as everywhere else in this prompt.

For `trial_result` leads specifically, the prompt also asks Claude to fold
two extra observations into the existing `result_summary` text rather than
adding dedicated fields: whether the trial's endpoint explicitly uses a
standardized imaging criterion (RECIST 1.1, iRECIST, PCWG3, Lugano, etc. —
these typically require central/blinded independent imaging review, a
direct signal of fit) and whether it looks like the company's first
pivotal/registrational trial (companies often only engage an external
imaging vendor once a trial has to hold up to regulators). These stay as
prose notes, not booleans, since they're inherently soft inferences Claude
is making, not verified facts. (`new_registration`, in the signal-sweep
search, gets the same two notes for the same reason — see below.)

Several JSON fields are deliberately reused across signal types instead of
adding a parallel field per type, a pattern that holds across every search
in this tool, not just this one: `abstract_url`/`abstract_url_note` double
as the general "source URL" (press release, registry entry, agenda page),
and `abstract_title` doubles as a general headline. `signal_detail` is the
plain-English description for every type except `trial_result` (which uses
`result_summary` instead).

**`draft_email()` drafts an email for every signal type, across every
search**, via `_opening_and_transition()`, but only the `trial_result`
opening ("I read with interest your recent paper... Congratulations on
this exciting result") is a verbatim, hard CRO requirement (see "The fixed
email opening" below) — never loosen or paraphrase it. Every other signal
type's opening was drafted by Claude Code at the user's explicit request as
a starting point, NOT hand-specified the same way (the one exception is
`phase_transition_deep_signal`, which Claude itself synthesizes per-lead at
research time — see "Phase transition deep search" below); `render_report()`
marks the drafted ones with an inline note ("drafted by Claude Code...
review the wording") so the distinction is visible in the report itself,
not just in this file. If the user gives exact wording for a given signal
type later (the same way they did for `trial_result`), update
`_opening_and_transition()` and drop that type's review note.

`seen_leads.dedup_key()` prefixes every key with `signal_type` — without
it, two different signal types for the same company (e.g. a funding lead
and a leadership-change lead) would both fall back to the same
`company_name|` key and the second would be wrongly treated as a repeat of
the first.

### Signal sweep search (paid, Claude-driven, nine signal types)

`build_signal_sweep_prompt()` + `run_signal_sweep_search()` are the fourth
search — the nine signal types that used to live in the conference
search's own eleven-category prompt, split out into their own subcommand
after the user pointed out a cadence mismatch: the conference search only
makes sense to run irregularly, whenever a relevant conference is coming
up, but these nine signal types (funding, leadership changes, new trial
registrations, regulatory designations/milestones, trial expansions,
protocol amendments, hiring signals, vendor-switch signals) aren't tied to
conference timing at all — they happen year-round, and batching them into
the conference search's own irregular cadence meant they were only ever
checked whenever a conference happened to be searched for, under-checking
them the rest of the year. Splitting them into their own search lets this
one run on its own, more regular cadence (e.g. weekly) independent of
conference timing — see "What this repository is" above for the fuller
architecture discussion that led here.

The nine signal types themselves are unchanged from their original
descriptions when they lived in the conference search: `funding`
(financing rounds, IPOs, grants, licensing deals — Series B/C+ rounds and
IPOs are the strongest version since imaging-heavy oncology trials are
expensive and this often precedes an imaging-vendor RFP by a few months;
also noting if proceeds are earmarked for a pivotal/registrational trial
specifically), `leadership_change` (new CEO/CMO/CSO), `new_registration` (a
newly registered trial on ClinicalTrials.gov or an international
equivalent — also listed in `agent/conferences.py` — surfacing a sponsor
before their trial ever reaches a conference; also gets the same
imaging-criterion/first-pivotal-trial notes as `trial_result` does in the
conference search, folded into `signal_detail`), `regulatory_designation`
(FDA/EMA designations like Breakthrough Therapy, Fast Track, Priority
Review, Orphan Drug, EMA PRIME), `regulatory_milestone` (End-of-Phase 2 or
Type B/C meeting outcomes — usually means a pivotal trial's design,
including imaging endpoints, is being finalized around now),
`trial_expansion` (an existing trial expanding to new countries/sites —
multi-region trials are where centralized imaging review becomes valuable
versus inconsistent local site reads), `protocol_amendment` (an amendment
adding or changing an imaging-related requirement on an existing trial —
often means a new imaging need has emerged, or a current vendor isn't
working out, though the prompt explicitly tells Claude not to speculate
about a specific vendor by name), `hiring_signal` (a company publicly
hiring for an imaging-specific clinical role like "Director of Imaging" —
usually means they're about to manage an imaging CRO relationship, not
insource it away), and `vendor_switch_signal` (a public, citable statement
— press release, LinkedIn post, conference talk — describing imaging data
delays or QC issues with a current vendor; the prompt treats this as the
most sensitive category and explicitly forbids naming a specific competing
vendor unless the source itself already does so, and says to leave it out
entirely rather than repeat an unverified claim about a real company).
`registry_name`/`registry_id` are `new_registration`-only, same as before
the split.

`vendor_switch_signal`'s opening deliberately does NOT reference the
complaint/pain-point content itself (e.g. never says anything like "I
heard you're having imaging delays with your current vendor") — repeating
a public complaint about a competitor back to the prospect would read as
opportunistic, not professional. It opens generically about running
imaging-intensive trials instead. Don't "improve" this by making it more
specific to the signal_detail; that specificity is exactly what it's
avoiding.

**Cross-dedup with phase-transitions, not just within itself.** Two of
these nine categories (`new_registration`, `regulatory_milestone`) can
describe the same underlying event as phase-transitions' Phase-1-to-Phase-2
signal, and both are paid Claude searches — unlike the free trial-signals
pre-check phase-transitions already gets, there's no zero-cost way to
re-run one paid search just to see what the other already found. Instead,
`_run_signal_sweep_cli()`/`run_signal_sweep_pipeline()` read
phase-transitions' *persisted* `phase_transition_seen_leads.json` via
`seen_leads.recent_entries(path, within_days)` (a thin, `company_name`
+ `trial_name`-only read of the dedup state, not that search's full
findings) and inject the recent ones into the prompt as "already covered,
don't duplicate" context via `_format_known_leads_for_prompt()` (now
generalized with a `source_label` parameter so both this search and
phase-transitions can use it with different framing text). This is
symmetric: `_run_phase_transitions_cli()`/`run_phase_transition_pipeline()`
do the same read in the other direction, against
`signal_sweep_seen_leads.json`, so whichever search runs second in a given
week is aware of the other's very recent findings. Both reads default to
the two searches' own default seen-file names/locations — a user who
customizes `--seen-file` for one breaks the other's ability to find it, an
accepted, documented limitation rather than adding yet another CLI flag
for "the other search's seen-file path."

`--days` (default 30, stored as `args.sweep_days` — see below) scopes the
look-back window, giving a weekly-run search enough buffer without
resurfacing stale news indefinitely; shorter than phase-transitions'
default 60 since this search is meant to run more often. Contact hunting
is out of scope here too, same as every other Claude-driven search in this
tool — that's Hunter.io's job.

**`args.sweep_days`, not `args.days`.** Both phase-transitions and
signal-sweep expose the same `--days` flag to the user, but the
signal-sweep subparser stores it under a different Namespace attribute
(`dest="sweep_days"`) specifically so `render_report()`'s
`hasattr(args, "days")` / `hasattr(args, "sweep_days")` branches can't
collide with each other — see `build_arg_parser()`.

Not yet verified against a live run — built and tested here with
`unittest.mock.patch` on `bd_agent._stream_claude_research()` using a
fabricated single-lead response, following the same fabricated-data
testing discipline as the rest of this tool; a real run should be checked
once by the user before relying on it, same as every other new paid search
added to this tool.

### Auto-named output files

`bd_agent.default_output_basename(indication, conference, year)` (conference
search) and `bd_agent.default_trial_signals_basename(indication)`
(trial-signals search) each build a descriptive default filename (e.g.
`bladder_cancer_ASCO_GU_2025_2026_leads_report` /
`bladder_cancer_trial_signals_report`) instead of a fixed generic name —
re-running with different search parameters no longer silently overwrites
an unrelated earlier report, and it's the direct fix for the real
"Permission denied: leads_report.md" case (the file locked because a
previous same-named report was still open elsewhere) as much as the
friendlier error message is. Kept as two separate functions, not one
shared one, so the two independent searches never compute the same
default filename and clobber each other's report. `--output`/the GUI's
Output file field still lets the user pick an exact name when they want
one; the auto-name only kicks in when it's left unset (CLI) or blank (GUI
— the field's default value is `""`, not a placeholder string,
specifically so this triggers on first launch too). `render_csv()`'s
output filename is derived from the same path
(`out_path.with_suffix(".csv")`), so it inherits the descriptive name for
free.

### CSV export

`render_csv()` writes the same leads as `render_report()`'s Markdown, one
row per lead, to a `.csv` file with the same basename as `--output`
(`leads_report.csv` next to `leads_report.md` by default) — both
`bd_agent.py`'s `_finalize_and_write()` and `gui_logic.py`'s
`_finalize_and_write()` write it right after the Markdown report.
Only covers the current run's actionable leads, not the excluded/repeat
sections (those are informational, not leads to act on). Contact-status
logic is intentionally duplicated in condensed form in
`_contact_csv_fields()` rather than sharing code with `render_report()`'s
Markdown contact block — the Markdown version's wording (e.g. the ⚠️
lookup-FAILED explanation) is specific/verbose by design and a shared
helper would have to either lose that detail or leak CSV-cell-length prose
into the report. Both follow the same underlying decision tree (confirmed
/ lookup failed / below-threshold / found-but-unconfirmed / not publicly
available) — if that branching logic changes, update both places.
Written with `newline=""` on `Path.write_text()` (Python 3.10+) since the
`csv` module's own line-terminator handling and the platform's text-mode
newline translation would otherwise double up `\r\n` into `\r\r\n` on
Windows, this tool's target platform.

- **`agent/hunter_contacts.py`** — Hunter.io API client (stdlib `urllib`,
  no extra dependency). `find_contact(domain, contact_name, api_key,
  min_confidence)`: if a contact name is already known, uses Hunter's
  Email Finder; otherwise Domain Search, filtered to CEO/CMO-titled
  results by substring match on `position`. Returns a `Contact`
  dataclass whose `.email` is only populated if Hunter's confidence score
  met `min_confidence` (default 90) — a below-threshold match still
  returns `.confidence` so the report can show "found but unconfirmed"
  rather than silently discarding it. Network/parsing errors are caught
  and returned as `Contact(..., source="error: ...")` rather than raising,
  so one bad lookup doesn't abort the whole run.

Contact lookup is **optional** — if `HUNTER_API_KEY` isn't set,
`enrich_contacts()` returns `None` for every contact and the report says
so explicitly rather than silently omitting the caveat.

### Phase transition deep search (paid, Claude-driven, one signal)

`build_phase_transition_prompt()` + `run_phase_transition_search()` are the
third search added to this tool, added after the user watched the free
trial-signals search work correctly and asked for a more "agentic" version
of the same signal
(Phase 1-to-Phase 2 transition) with full open-web reach — LinkedIn,
biotech/pharma news sites, company blogs, hospital/university press —
rather than the trial-signals search's three fixed sources. Explicitly
approved as a paid, token-costing path by the user ("I know this will
cost tokens-thats OK").

**The free trial-signals search runs first, automatically, as a pre-check
before the paid deep search** — per the user's explicit suggestion ("I
would suggest that it runs the free first, then the paid"). `_run_phase_transitions_cli()`
(and `gui_logic.run_phase_transition_pipeline()`) calls
`run_trial_signals_search(args)` before `run_phase_transition_search()`,
and the resulting leads are passed into `build_phase_transition_prompt()`
via `_format_known_leads_for_prompt()` as a "here's what a free check
already found — don't spend budget re-confirming these" context block.
This is Python-level sequencing, not Claude's own judgment: the free check
costs nothing and runs first regardless of what it finds, and Claude is
instructed to only re-report one of these leads if it can add something
genuinely new (an additional source, a fuller narrative, a contact) —
noting so explicitly in `signal_detail` if it does. The free check's own
leads are shown in the phase-transition report as a separate, informational
"Already found by the free trial-signals check" section (no contacts/
emails — that's the trial-signals report's job) even when Claude's own
`leads` list ends up empty, via `_finalize_and_write()`'s `known_leads`
param. This pre-check is stateless from the phase-transition run's
perspective — it doesn't touch `trial_signals_seen_leads.json` (an
independent later run of the actual "trial-signals" search would still
see these as new) — but it does share `--sponsor-history-file` with the
trial-signals search (same default filename), since that's the same
underlying sponsor-tracking fact store regardless of which search
triggers the check.

**Also cross-checked against the signal-sweep search's recent history**
(added when signal-sweep was built — see "Signal sweep search" below for
the full rationale): unlike the trial-signals pre-check above, this isn't
a fresh free run — both phase-transitions and signal-sweep are paid Claude
searches, so there's no zero-cost way to re-run one just to see what the
other already found. Instead `_run_phase_transitions_cli()`/
`run_phase_transition_pipeline()` read signal-sweep's *persisted*
`signal_sweep_seen_leads.json` via `seen_leads.recent_entries()` and pass
the recent entries into `build_phase_transition_prompt()`'s
`signal_sweep_leads` parameter, rendered as its own, separate
`_format_known_leads_for_prompt()` block (distinct from the trial-signals
block above) — relevant because two of signal-sweep's nine categories
(`new_registration`, `regulatory_milestone`) can describe the same
underlying event this search's Phase-1-to-Phase-2 signal covers.

Two things make this prompt different from every other Claude call in
this tool:

1. **Cross-source dedup is Claude's job, not Python's.** The prompt
   explicitly instructs: if multiple sources (an SEC 8-K, a press release,
   a LinkedIn post) describe the same underlying transition event, report
   it as ONE lead with every source URL listed in `source_urls`, not as
   separate leads. This has to happen at generation time — deciding "is
   this the same story" needs actual understanding of the content, which
   is exactly what Python string/ID matching (as used for `seen_leads.py`'s
   *cross-run* dedup) can't do. `seen_leads.py` still does its normal
   cross-run dedup afterward — this is a different, complementary kind of
   dedup (within-one-run, across-sources).
2. **The email opening is drafted by Claude at research time, not by a
   Python template.** Every other signal type's opening is either the
   hard-specified `trial_result` template or a fixed Python string
   Claude Code wrote once during development (see `_opening_and_transition()`).
   For `phase_transition_deep_signal`, the prompt asks Claude to write a
   2-4 sentence `email_opening` per lead, synthesized from whatever
   sources it actually found — since the whole point of this signal is
   combining multiple sources into one coherent narrative, that has to be
   generated per-lead, not templated. `_opening_and_transition()` just
   uses `lead["email_opening"]` directly. `render_report()`'s review-note
   for this type is worded differently from the other Python-templated
   types for the same reason — see the `signal_type ==
   "phase_transition_deep_signal"` branch there.

Other notable choices: `--days` (default 60) scopes the search window,
matching the user's own example ("Bladder cancer last 60 days"). Contact
hunting is still explicitly told to stay out of scope (that's Hunter.io's
job). `max_uses=60` for the web-search tool — narrower than
signal-sweep's 70 (one signal, not nine categories) but still generous,
since "search broadly" is the entire point here.
`seen_leads.dedup_key()` keys this signal type on `drug_asset_name`/
`trial_name` rather than `signal_detail` — the free-text detail is a fresh
Claude-synthesized summary every run (even more likely to be reworded
between runs than other free-text signal types), where the asset/trial
name Claude extracted is a real proper noun and more stable.
`render_report()` shows every URL in `source_urls` as a bulleted list
(instead of the single-link "Abstract"/"Source" line every other signal
type gets) specifically so the user can see how well-corroborated a lead
is.

This was the second Claude-driven search added to the tool (after the
conference search) but is a separate subcommand/button rather than a mode
of either existing search, so every search's cost/scope profile stays
legible at a glance: conferences (paid, 2 conference-anchored signals,
named conferences), trial-signals (free, 4 fixed sources), phase-transitions
(paid, 1 signal, open web), signal-sweep (paid, 9 non-conference-anchored
signals, open web, regular cadence — added later, see "Signal sweep
search" above). Not yet verified against a live run — built and tested
here with `unittest.mock.patch` on `bd_agent.run_phase_transition_search()`
using a fabricated multi-source lead, following the same fabricated-data
testing discipline as the rest of this tool; a real run should be checked
once by the user before relying on it, same as every other new source
added this session.

### Trial signals search (free, no LLM)

`bd_agent.run_trial_signals_search()` is the entire trial-signals
pipeline: three independent, stateless-or-lightly-stateful sources, each
its own module, each returning lead dicts in the same shape the Claude
pipeline uses (`company_name`, `signal_detail`, `abstract_url`,
`registry_name`/`registry_id`, etc.) so they flow through the existing
`render_report()`/`render_csv()`/`draft_email()` code unchanged. None of
these three sources involves an LLM call, so unlike the conference search
there is no `raw_response` to fall back to if something goes wrong — see
`_finalize_and_write()`'s `raw_response=None` path.

- **`agent/clinicaltrials_gov.py`** — direct integration with the
  ClinicalTrials.gov API v2 (`https://clinicaltrials.gov/api/v2/studies`,
  free, no API key, stdlib `urllib` like `hunter_contacts.py`). Unlike the
  rest of the pipeline, this does **no LLM interpretation at all**: it
  queries ClinicalTrials.gov directly with a literal filter and only
  returns trials that actually matched, so its leads are exact-match and
  reproducible run to run rather than Claude's summary of what it found
  searching the web. Four signal types: `trial_milestone_approaching`
  (a Phase 1 or Phase 1/2 trial in the target indication, `RECRUITING` or
  `ACTIVE_NOT_RECRUITING`, with a primary completion date 60-90 days out —
  a lead-time signal that the sponsor is about to plan its next phase,
  imaging vendor included, well before any result becomes public),
  `trial_recently_completed` (same phase scope, status `COMPLETED`, with a
  completion date in roughly the last 14 days — the same signal caught
  from the other side once it's actually happened), and
  `phase2_filing_by_returning_sponsor` (a new, standalone Phase 2 trial
  filed by a sponsor already on record as having run a Phase 1 trial in
  this indication — see `sponsor_phase_history.py` below for the
  cross-run tracking this one needs), and `trial_site_expansion` (a trial
  that has added new sites/locations since a previous run recorded its
  site list — see `trial_site_history.py` below; deliberately not
  phase-scoped like the other three, since site expansion is a meaningful
  BD signal at any phase, not just Phase 1 — multi-region/multi-site
  trials are exactly where centralized imaging review beats inconsistent
  local site reads, the same rationale as the conference search's
  `trial_expansion` signal type, just sourced from structured
  ClinicalTrials.gov data instead of Claude web search). `find_leads()` is
  called from `bd_agent.run_trial_signals_search()`. `filter.phase=PHASE1` is
  deliberately the only phase filter used for the first two lookups —
  ClinicalTrials.gov's phase filter is OR-matching against a study's
  *phases list*, so filtering on `PHASE1` alone already includes combined
  "Phase 1/Phase 2" studies (which list both) while excluding pure Phase 2
  studies, which is exactly the "Phase 1 or Phase 1/Phase 2" scope wanted —
  no need for a `PHASE1_PHASE2` filter value (it doesn't exist).
  `find_recently_completed()` filters on `AREA[CompletionDate]RANGE[...]`,
  not `LastUpdatePostDate` — the actual completion date is a much more
  direct proxy for "just transitioned to Completed" than an unrelated
  metadata edit would be, since the v2 API doesn't expose discrete
  status-change events. `find_returning_sponsor_new_phase2()` deliberately
  filters its Phase 2 query down to studies whose `phases` list is
  *exactly* `["PHASE2"]` — excluding combined Phase 1/2 studies, which are
  already covered by the other two signals and aren't a sponsor
  "graduating" to a standalone next phase the way this trigger means.
  `seen_leads.dedup_key()` keys the first three signal types on
  `registry_id` (the NCT number) the same way it already did for
  `new_registration`, rather than falling through to the generic
  free-text-detail fallback — the detail text embeds a completion-date
  estimate that could drift slightly between runs even for the identical
  trial, where the NCT number never does. `trial_site_expansion` keys on
  `registry_id` *plus* the trial's total site count at the time of that
  lead (`site_expansion_snapshot`, set in `find_site_expansion()`) —
  unlike the other three, the same trial can legitimately fire this
  signal again in a later run once it adds further sites on top of ones
  already reported, and a plain `registry_id` key would wrongly treat
  that second, larger expansion as a repeat of the first. Not yet
  verified against a live API response — this dev sandbox's network
  policy blocks `clinicaltrials.gov` outright, so all testing here used
  `unittest.mock.patch` on `clinicaltrials_gov._get()` with a fabricated
  study JSON shaped from the v2 API's documented schema; the query
  parameter names and Essie `AREA[]RANGE[]` date syntax are confirmed from
  ClinicalTrials.gov's own documentation, but a real run should be checked
  once by the user before relying on it.

- **`agent/sponsor_phase_history.py`** — the cross-run state
  `find_returning_sponsor_new_phase2()` needs: a small local JSON file
  (`sponsor_phase_history.json`, gitignored, mirrors `seen_leads.py`'s
  pattern) mapping indication -> sponsor name -> the Phase 1 NCT IDs seen
  for that sponsor so far. Every run first records every Phase 1 sponsor
  it finds (persisting immediately, not just in memory), then checks any
  new pure-Phase-2 filing against that record — so a sponsor's Phase 1
  trial (found in one run, possibly months ago) and its later Phase 2
  filing (found in a different run) get correlated correctly, which is
  the entire point of this module existing as stateful tracking rather
  than a single stateless query like the rest of `clinicaltrials_gov.py`.

- **`agent/trial_site_history.py`** — the cross-run state
  `find_site_expansion()` needs, mirroring `sponsor_phase_history.py`'s
  pattern exactly but tracking a trial's site list instead of a sponsor's
  Phase 1 trials: a small local JSON file (`trial_site_history.json`,
  gitignored) mapping indication -> NCT ID -> the site list (facility +
  city + country, since the v2 API exposes no persistent per-location ID
  to key on) recorded for that trial as of the last run. Every run records
  each trial's current site list (persisting immediately) and, if that
  trial already had a recorded, non-empty site list from a previous run,
  checks whether any of its current sites weren't in that recording — new
  ones are the signal. A trial's first sighting only establishes the
  baseline (nothing to diff against yet), and a trial whose prior
  recording had *no* sites at all is also excluded from firing on its
  first populated sighting — that's ClinicalTrials.gov filling in location
  data late, not a real expansion event.

- **`agent/sec_edgar.py`** — direct integration with the SEC EDGAR
  full-text search API (`https://efts.sec.gov/LATEST/search-index`, free,
  no API key, stdlib `urllib`). Searches recent 8-K/10-Q filings for the
  BD proposal's verbatim alert phrases (`ALERT_PHRASES`: "End-of-Phase 1
  meeting", "EOP1", "Top-line Phase 1 data", "Positive Phase 1b safety",
  "Initiation of Phase 2 trial", "FDA alignment on Phase 2 trial design"),
  one EDGAR query per phrase rather than ANDing them all together (a
  company would only ever use one or two of these at a time, so requiring
  all of them in one filing would be far too strict). Not filtered by
  indication — EDGAR full-text search has no therapeutic-area field, and
  ANDing a phrase with a plain-English indication name would silently
  miss filings that name the drug/program instead; same
  better-to-surface-a-few-off-topic-hits tradeoff as everywhere else in
  this tool. SEC's fair-access policy requires a descriptive `User-Agent`
  identifying the requester (a bare/default one gets a 403) — built from
  `--sender-company`/`--sender-name` rather than hardcoded, so it's still
  correct if someone else runs this tool under their own CRO's name.
  `_hit_to_lead()`'s field mapping (`_source.ciks`, `_source.display_names`,
  `_source.form`, `_source.file_date`, `_source.adsh`) and the filing URL
  construction (`.../Archives/edgar/data/{cik}/{accession_no_no_dashes}/{adsh}-index.htm`)
  are confirmed from the API's OpenAPI spec and a real example response
  found via web search, not guessed. This dev sandbox itself can't reach
  `efts.sec.gov` (network policy blocks it, like `clinicaltrials.gov`), so
  it was built and tested here with `unittest.mock.patch` on
  `sec_edgar._get()` — but **the user has since confirmed a real run finds
  actual leads** (2 filings matched on the first live try), so this one's
  proven working, not just plausible.

- **`agent/pr_wire_feeds.py`** — RSS/Atom monitoring of PR Newswire /
  Business Wire / GlobeNewswire (free, no API key, stdlib
  `xml.etree.ElementTree`, no new dependency) for the proposal's third
  data source: small biotechs often announce a Phase 1 completion or
  Phase 2 initiation via press release before ClinicalTrials.gov catches
  up. Entries are kept only if their title/description literally contains
  the target indication, "phase 1", and either "topline" or "phase 2" —
  same no-LLM-interpretation posture as the other two sources. Company
  name is a **best-effort heuristic** parsed from the headline
  (`_guess_company_name()`, e.g. splitting before "Announces"/"Reports"),
  not a confirmed field the way ClinicalTrials.gov's sponsor name is —
  the report always shows the full headline too so a bad guess never
  hides the real name.

  `DEFAULT_FEED_URLS` was the weakest-verified part of this whole session's
  work (this dev sandbox's network policy blocks all three wire services'
  domains outright, so none could be fetched live from here) — one entry
  turned out wrong in practice and has since been fixed with the user's
  help: the original GlobeNewswire URL used the `JSWidgetFeed` path, which
  is actually a JavaScript embed snippet (`<script>GetWidget(...)</script>`),
  not a feed at all — it returned HTML/JS that failed XML parsing
  (`not well-formed (invalid token)`). A real user run surfaced this
  immediately via `find_leads()`'s per-feed warning (deliberately NOT
  silent here, unlike `clinicaltrials_gov.py`/`sec_edgar.py`, precisely
  because these URLs started out less trustworthy than a documented API),
  and the user then confirmed the correct pattern by fetching
  `https://www.globenewswire.com/rss/list` and testing candidate URLs
  directly: GlobeNewswire's real feed path is `RssFeed`, not `JSWidgetFeed`
  — e.g. `.../RssFeed/industry/4573-Biotechnology/feedTitle/...` — now the
  current `DEFAULT_FEED_URLS` entry, confirmed against multiple live 200
  responses with real RSS 2.0 content (also tested and confirmed working:
  `4577-Pharmaceuticals` and `4535-Medical%20Equipment`, not used by
  default since they're a looser fit for an oncology/urology imaging CRO).
  PR Newswire's and Business Wire's URLs have not thrown a parse warning in
  the user's runs so far, which is reasonable evidence they're fetching
  real feeds too, though genuine relevant hits from either haven't been
  confirmed yet the way GlobeNewswire's and SEC EDGAR's have. If a feed
  ever 404s or comes back empty on every run, that's still the first thing
  to check — fix by editing `DEFAULT_FEED_URLS` directly, following the
  `RssFeed`-not-`JSWidgetFeed` lesson above.

  This was scoped down from a larger proposal (ClinicalTrials.gov API +
  SEC EDGAR 8-K/10-Q filings + PR Newswire/Business Wire/GlobeNewswire RSS
  + paid databases like BioPharmCatalyst/AlphaSense/Citeline, plus tracking
  NDA/BLA-stage triggers like BICR re-reads and sNDA/sBLA filings) built in
  two passes: ClinicalTrials.gov's first two signals shipped alone first
  (proven end-to-end before adding more), then SEC EDGAR, PR-wire RSS, and
  the sponsor cross-referencing all followed once that pattern was
  validated. Paid databases (BioPharmCatalyst/AlphaSense/Citeline) were
  explicitly excluded per the user's instruction, and the NDA/BLA-stage
  triggers (BICR re-reads, exploratory endpoint analyses, sNDA/sBLA
  filings) remain deferred — see Known gaps.

- **`agent/seen_leads.py`** — local dedup so re-running doesn't resurface
  the same leads. `dedup_key()` deliberately prefers `company_domain` +
  `abstract_number` over `company_name`/`trial_name`: Claude's exact
  wording for those free-text fields varies between runs even for the
  identical underlying trial (observed directly — same enGene/LEGEND
  trial came back with two differently-phrased company names and trial
  descriptions across two consecutive runs), so keying on them would
  silently fail to dedup. `main()` calls `split_new_and_repeats()` before
  Hunter enrichment (repeats never consume a Hunter lookup) and
  `render_report()` lists skipped repeats in their own section rather than
  dropping them silently. State lives in `--seen-file` (default
  `seen_leads.json`, gitignored — it's per-user run state, not code).

- **`agent/gui_logic.py` / `agent/gui.py`** — the GUI is split specifically
  so most of it is testable without a display: `gui_logic.py` has zero
  Tkinter import and holds everything meaningful. Since the four searches
  are independent (see "What this repository is" above), this module has
  **one build-args function and one pipeline function per search**, not
  one shared set: `build_conference_args()` / `run_conference_pipeline()`
  for the conference search, `build_trial_signals_args()` /
  `run_trial_signals_pipeline()` for the trial-signals search,
  `build_phase_transition_args()` / `run_phase_transition_pipeline()` for
  the phase-transition search, `build_signal_sweep_args()` /
  `run_signal_sweep_pipeline()` for the signal-sweep search. All four
  pipeline functions funnel through a shared private `_finalize_and_write()`
  (dedup -> Hunter enrich -> render -> write — the only part that's
  actually identical across all four) and mirror `bd_agent.py`'s own
  `_run_conferences_cli()`/`_run_trial_signals_cli()`/
  `_run_phase_transitions_cli()`/`_run_signal_sweep_cli()`/
  `_finalize_and_write()`, just returning the report path instead of only
  printing it. `QueueWriter` is a file-like object for routing
  `print()`/stdout output into a `queue.Queue`, used by all four.

  `gui.py` is a thin Tkinter shell with **four independent "Search ..."
  buttons and four independent "Open ... Report" buttons** (one pair per
  search), sharing one log window and one "New Search" reset button.
  Clicking any search button disables *all four* run buttons until it
  finishes (`_set_run_buttons_state()`) — there's only one background
  thread slot in use at a time, and Tkinter widgets aren't thread-safe to
  touch from more than one thread regardless. Only the three paid searches'
  buttons require an Anthropic API key (`on_run_conferences()`,
  `on_run_phase_transitions()`, and `on_run_signal_sweep()` all check for
  it; `on_run_trial_signals()` deliberately does not, since that search has
  no LLM call at all) — this is the one behavioral difference that
  actually matters between the four search handlers, so don't add an
  Anthropic-key check to `on_run_trial_signals()` "for consistency." All
  four handlers redirect `sys.stdout`/`sys.stderr` to a shared
  `QueueWriter` before running their pipeline on a background thread
  (network calls would otherwise freeze the window), and only touch
  Tkinter widgets from `_poll_log_queue()` on the main thread via
  `after()` — never from a background thread directly. API keys and
  last-used field values persist in `gui_config.json` (gitignored) so
  they're entered once, not per run. This module split is deliberate, not
  incidental — when changing pipeline behavior, prefer editing the
  relevant `gui_logic.py` function (testable here) over inlining logic
  into `gui.py` (only testable by the user, on their own machine, since
  this sandbox has no Tkinter/display).

  **Layout: a capped-height scrollable top area, with the Progress log
  always guaranteed its own space below it.** Every field/section added
  to `gui.py` over this tool's growth (four searches, API keys, Outbox,
  HubSpot) was originally just `pack()`-ed straight onto the root window
  top to bottom, which meant the Progress log — the one thing that needs
  to stay visible *while a search is running* — kept getting squeezed
  smaller, then off the bottom of the fixed-size window entirely, as more
  sections were added; a real problem the user hit directly ("dashboard
  is so full now I can't see if there's any progress"). The fix: a
  `tk.Canvas` + `ttk.Scrollbar` wraps everything except the log (mouse-
  wheel-scrollable, capped at a fixed height), packed with `side="top"`;
  the log frame is packed separately with `side="bottom", fill="both",
  expand=True` — so the log always claims all remaining vertical space
  no matter how much content is above it, and the *scrollable* area
  absorbs any overflow instead. **Outbox and HubSpot fields were also
  moved out of the main dashboard entirely**, into a separate `Toplevel`
  "Outbox / HubSpot Settings..." window opened on demand (`open_settings_dialog()`)
  — both are occasional-setup, opt-in fields most runs never touch, so
  they no longer cost permanent space on the screen the user looks at
  every run. `self.outbox_*_var`/`self.hubspot_*_var` `StringVar`s are
  still created in `__init__()` (not lazily inside the dialog) since
  `_current_form()` needs to read them on every search regardless of
  whether the settings window has ever been opened this session. Not yet
  verified against a real Windows display — this sandbox has no
  Tkinter/display at all (not even importable), so only `py_compile`
  could check this change; the user should confirm the scrolling/dialog
  actually behave as intended on their own machine.

  **Batch mode: run several searches in one pass.** Each of the four
  search frames got an "Include in batch run" checkbox
  (`self.conf_batch_var`/`trial_batch_var`/`phase_batch_var`/`sweep_batch_var`)
  and a new "Run Selected Searches" button (`on_run_selected()`) runs every
  ticked one, one after another, on the same background thread/stdout
  redirect the individual "Search ..." buttons already use — added at the
  user's request to avoid clicking and waiting for each search
  individually. `_batch_search_defs()` is a small per-search table (build-
  args function, pipeline function, whether it needs the Anthropic key,
  its "done" queue-message kind, its Open-report button) that both
  `on_run_selected()` and `_run_selected_in_background()` iterate over,
  instead of four near-duplicate copies of the run logic. One search
  failing doesn't stop the batch — the exception is logged and the loop
  moves to the next selected search, same "don't lose the others" posture
  as every other multi-source loop in this tool. `self._batch_running`
  suppresses `_poll_log_queue()`'s normal per-search button re-enable
  (each individual `..._done`/`error` queue kind would otherwise re-enable
  all four run buttons the moment the *first* search in the batch
  finishes) until a new `"batch_done"` kind fires at the very end. The
  four individual "Search ..." buttons are unchanged and still run just
  one search immediately, exactly as before — batch mode is strictly
  additive.

  **"New Search" now blanks every field instead of resetting to the
  sample defaults** (e.g. "ASCO GU", "bladder cancer") shown on first
  launch — the user's own request, since silently reverting to sample
  values reads as though a new search inherited leftover parameters from
  the last one. `on_new_search()` now sets every `field_vars` entry to
  `""` rather than iterating `FIELDS`' `default` column; those defaults
  still exist and still act as the very first launch's placeholder text
  (from `self.config_data.get(key, default)` in `_build_ui()`), just not
  as a value "New Search" reverts to later.

  **This blank reset exposed a real, pre-existing bug**: every
  `build_*_args()` function in `gui_logic.py` silently substituted the
  sample launch values (`conference=["ASCO GU"]`, `year=[2025, 2026]`,
  `indication="bladder cancer"`, `phase="Phase II"`,
  `days`/`sweep_days=60`/`30`) whenever the corresponding field was blank.
  Harmless before this change — every field always had pre-filled sample
  text, so the fallback was dead code — but a real run left the
  Conference field blank after "New Search" and the tool silently ran
  "ASCO GU" anyway with no indication anything had been substituted (the
  user's exact report: "started referencing ASCO GU even though the
  conference was blank"). Fixed by making these search-defining fields
  (conference, year, indication, phase, the two days fields) required —
  each `build_*_args()` now raises `ValueError` with a specific message
  ("Indication is required.", etc.) instead of silently substituting, and
  `gui.py`'s `except ValueError as exc: messagebox.showerror(...,
  str(exc))` surfaces that specific message rather than a generic
  hardcoded one (previously discarded the real exception entirely).
  `sender_name`/`sender_title`/`sender_company`/`hunter_min_confidence`
  deliberately keep their fallback behavior — their defaults are either an
  obvious, unmistakable placeholder (`"[Your Name]"`) or a genuinely
  reasonable setting (`hunter_min_confidence=90`, `sender_company=
  "Elevate Imaging"`, correct for this tool's one real user near-always),
  not a different, unrelated search topic silently swapped in.

  **A running-time indicator** (`self.status_var`, a label above the
  Progress log) — the user's own request, so a long paid search visibly
  shows it's still working rather than looking frozen. `_start_timer()`/
  `_stop_timer()`/`_tick_timer()` are hooked into `_set_run_buttons_state()`
  itself (called at exactly the moments any run starts/ends, single-search
  or batch) rather than duplicated at every call site: disabling the run
  buttons starts a `self.after(1000, ...)` tick loop showing "Running...
  Ns elapsed"; re-enabling them stops it and shows "Finished — took Ns".

  `build_conference_args()`'s `conference` field splits on **commas**, not
  whitespace. It used to split on whitespace, which silently broke a
  single-entry conference name containing its own space — typing "ASCO GU"
  (one conference) into the field produced `["ASCO", "GU"]` (two
  conferences), changing what was actually searched with no visible
  error; a user's pasted report showing "Scope searched: ASCO and GU"
  instead of "ASCO GU" was the tell. The `year` field still splits on
  whitespace deliberately — years never contain internal spaces, so
  that's not the same bug. `build_trial_signals_args()` doesn't need
  either field.

- **`agent/conference_dates.py`** — separate, small Claude + web search call
  (not part of the main lead research) that looks up *actual confirmed*
  dates for the meetings listed in `agent/conferences.py`, and caches them
  in `conference_dates_cache.json` (gitignored, per-user like
  `gui_config.json`). Deliberately **not** run automatically — only when
  the user clicks "Refresh Dates" in the GUI — since it's a real, billed
  API call each time; the whole point is a manual, on-demand reminder
  system, not a background job. `upcoming_meetings()` reads the cache
  (no network call) to find meetings starting within a window (default 45
  days) of today, and `gui_logic.upcoming_meetings_banner_text()` turns
  that into the banner text shown at the top of the GUI on every launch.
  If there's no cache yet, the banner tells the user to click "Refresh
  Dates" rather than guessing a date — same anti-fabrication discipline as
  the rest of the tool, just applied to conference timing instead of lead
  data. `conferences.py`'s `typical_timing` field (e.g. `"May/June"`) is
  only ever a rough hint inside the main research prompt; it is never used
  as a stand-in for a real date in the reminder banner.

### `gui.py` can be packaged into a standalone Windows `.exe`

`ElevateImaging-BD-Agent.spec` (committed) drives `pyinstaller
ElevateImaging-BD-Agent.spec` — see the README's "Build a standalone .exe"
section for the user-facing steps. This **must be built on Windows**;
PyInstaller doesn't cross-compile. `build/` and `dist/` are gitignored
(generated) but the `.spec` is committed so the build config is
reproducible without retyping flags.

**`gui_logic.app_dir()` exists specifically for this.** Under a PyInstaller
one-file build, `__file__` resolves to the temporary `_MEIxxxxx`
extraction folder, which is deleted when the process exits — using it for
`CONFIG_PATH` would silently lose every saved setting on every single run
(a real bug caught and fixed before shipping, verified by mocking
`sys.frozen`/`sys.executable`, since this sandbox can't launch a real
Windows `.exe` to observe it directly). `app_dir()` checks
`getattr(sys, "frozen", False)` and uses `Path(sys.executable).parent`
instead when true. `gui_logic.build_conference_args()`/
`build_trial_signals_args()`'s default `output`/`seen_file`/
`sponsor_history_file` paths all route through `app_dir()` for the same
reason — a bundled exe's working directory isn't reliably its own folder
depending on how it's launched, but `sys.executable` always is.

### The Anthropic SDK's "Connection error." is deliberately generic

`anthropic.APIConnectionError`'s message is hardcoded to `"Connection
error."` regardless of cause — but the SDK raises it via `raise
APIConnectionError(request=request) from err`, so the real httpx/network
exception (DNS failure, TLS interception by antivirus/corporate proxy,
connection refused, etc.) is preserved on `.__cause__`. Same class of bug
as the Hunter.io Cloudflare block below: a vague error hid the real cause
until surfaced explicitly. `run_research()` catches
`anthropic.APIConnectionError` and re-raises a `RuntimeError` that includes
`exc.__cause__`'s text plus the likely real-world causes (internet down,
firewall/VPN, antivirus HTTPS interception) — don't let that collapse back
to just re-raising or printing the bare SDK exception.

### Per-run cost estimate

`bd_agent._estimate_run_cost(usage)` turns a `Message.usage` object into an
approximate dollar figure, printed to stderr at the end of every
`run_research()` call (and `conference_dates.run_lookup()`, which imports
it from `bd_agent` rather than duplicating the pricing constants — both
calls run on the same model, `claude-opus-5`). Covers input/output tokens
at Opus 5 rates ($5/$25 per MTok), web search at $10/1,000 searches, and
cache read/write at their standard multipliers (0.1x / 1.25x) even though
this pipeline doesn't currently set `cache_control` anywhere, so those
terms are normally 0 — included for completeness in case caching is added
later. This is explicitly labeled an estimate in the printed message, not
a claim of exact billing — re-verify `OPUS_5_INPUT_PER_MTOK` /
`OPUS_5_OUTPUT_PER_MTOK` / `WEB_SEARCH_PER_1000_SEARCHES` against
platform.claude.com/docs/en/pricing if Anthropic's pricing changes, rather
than trusting these hardcoded constants indefinitely.

### Hunter.io requires a browser-like User-Agent

`hunter_contacts._get()` sets an explicit `User-Agent` header on every
request. This is load-bearing, not cosmetic: Hunter's front end sits behind
Cloudflare, and Python's default `Python-urllib/x.y` User-Agent gets
fingerprinted and blocked (Cloudflare error 1010) before the request ever
reaches Hunter's actual API — this was misread as a plan/rate-limit problem
before the real cause was found. Don't strip this header when touching
`_get()`.

### Hunter.io Domain Search plan limit

The Domain Search call in `hunter_contacts.find_contact()` passes
`"limit": 10`, not a higher number. Hunter's free/starter plans reject
`domain-search` requests above their per-plan cap with `HTTP 400
pagination_error: The search results are limited to 10 email addresses on
your current plan.` — this surfaced in a real report as a ⚠️ lookup-FAILED
line for three companies. If a paid-plan user ever needs more candidates
per domain to pick the best CEO/CMO match from, this would need to become
a configurable value rather than a hardcoded 10, not just bumped back up.

### Outbox drafts (optional, all four searches)

`agent/email_drafts.py` is an opt-in extra step, run at the end of
`_finalize_and_write()` (both `bd_agent.py`'s and `gui_logic.py`'s copies)
right after Hunter enrichment, right before rendering the report: if
`--outbox-email` (CLI) / the GUI's "Outbox" fields are filled in, it
creates one real, **unsent** draft email per new lead directly in that
mailbox's Drafts folder via IMAP (stdlib `imaplib`/`email`, no new
dependency) — added at the user's request to push drafts into a mailbox
connected to HubSpot for logging/tracking, while explicitly stopping short
of actually sending ("can you send them so they sit unsent in that
outbox?" — the user's own words). This does **not** change the tool's core
"never sends anything" design (see "The script never sends anything" in
the README) — a draft sitting in an outbox still requires a human to open
it and hit send.

Key decisions:

- **IMAP, not a provider-specific API.** IMAP APPEND to the Drafts folder
  with a username + app password works across Gmail, Microsoft 365/
  Outlook, and most generic providers, without needing OAuth setup or a
  provider-specific SDK — important since the user wasn't sure which
  platform hosts `imogene@elevateimaging.ai` at the time this was built
  (still true as of this writing; see below).
- **The "To:" field is only ever set from a Hunter-confirmed contact
  email** (`contact.email`) — never a guessed or unconfirmed address, the
  same anti-fabrication discipline as everywhere else in this tool. A
  lead without a confirmed contact still gets a draft (subject/body ready
  to go), just with no recipient filled in — a human fills it in before
  sending, the same way they'd fill in any other missing piece of a draft.
- **Entirely optional and validated up front.** `validate_outbox_args()`
  (in `bd_agent.py`, imported directly by `gui.py` so both surfaces share
  one check) fails fast with a clear error if `--outbox-email` is set but
  `--outbox-app-password`/`--outbox-imap-host` aren't — before any Hunter
  lookups or report writing happens, not partway through pushing drafts
  for a whole report. If `--outbox-email` is never set at all, nothing
  outbox-related runs — zero behavior change from before this feature
  existed.
- **Only for new leads.** `push_drafts_for_report()` is called with
  `enriched` (post-dedup — repeats already filtered out by
  `seen_leads.split_new_and_repeats()`), so re-running a search doesn't
  create duplicate drafts for leads already drafted in a previous run.
  **Credentials are collected the same way as the Anthropic/Hunter keys** —
  a masked GUI field (`gui_config.json`, gitignored) or `--outbox-app-password`/
  `OUTBOX_APP_PASSWORD` env var for the CLI — never something to paste into
  chat.

**Not yet verified against a live mailbox** — as of this writing the user
hadn't confirmed which platform hosts `imogene@elevateimaging.ai` (Google
Workspace, Microsoft 365, or something else), so this was built and tested
entirely with `unittest.mock.patch` on `email_drafts.imaplib.IMAP4_SSL`.
Once the platform is known: Gmail needs `imap.gmail.com` + an App Password
(requires 2-Step Verification) + Drafts folder `"[Gmail]/Drafts"`; Microsoft
365/Outlook needs `outlook.office365.com` + an app password + Drafts folder
`"Drafts"` (the default). A real run should be checked once before relying
on it, same as every other new source added this session.

### `dict.get(key, default)` is not None-safe

`.get(key, default)` only substitutes `default` when `key` is **absent** —
if the key is present with an explicit JSON `null` (which the research
prompt deliberately asks Claude to use for unknown fields, e.g. "abstract_number":
null), `.get()` returns `None` itself, and that `None` then renders
literally as the text "None" in the report and in drafted emails. This hit
real leads in production (Protara and Bicycle Therapeutics both showed
`**Abstract:** [None](url)` and a "Dear ... your recent paper, "None""
email opening). Every lead-field lookup in `draft_email()` and
`render_report()` uses `lead.get(key) or default` instead — `or` correctly
falls through on both an absent key and an explicit `None`. When adding a
new lead field anywhere in `bd_agent.py`, use this pattern, not
`.get(key, default)`.

### The fixed email opening

The draft email's opening paragraph is a hard requirement embedded verbatim
in the prompt (see the `build_prompt()` docstring block) — it must always
open with "Dear [contact name], I read with interest your recent paper,
'[abstract title]' (Abstract #[abstract number]), at [meeting name] on
[presentation date]. Congratulations on this exciting result...". Do not
loosen or paraphrase this instruction when editing the prompt; it's a
specific formatting requirement from the CRO, not a style suggestion the
model is free to vary.

### Anti-fabrication is load-bearing

The prompt explicitly instructs the model to never invent contact emails,
names, dates, or abstract numbers, and to say "not publicly available" /
"not confirmed" rather than guess. This has been observed working in
practice (the model has both refused to search a conference outside its
scope rather than fabricate results, and left contacts marked "not
confirmed" when its search budget ran out mid-task). Any prompt edit should
preserve this behavior rather than optimize for always returning a
complete-looking report.

### Warm-path connection matching (built and wired into all four searches)

`agent/warm_connections.py` — cross-references the user's (and team's)
LinkedIn connections against a target company's employee roster, so
outreach can start from wherever a personal connection already exists
rather than always going in cold to whoever looks like the decision-maker.
Design arrived at in conversation with the user (not this session's own
back-and-forth — an earlier planning conversation the user pasted in):

- **No LinkedIn scraping, by anyone, ever.** Scraping LinkedIn (directly,
  or by having an agent browse it) violates LinkedIn's Terms of Service
  and they actively block it technically — this was the first thing ruled
  out. The only LinkedIn data this module touches is the user's own
  connections list, exported through LinkedIn's own official data-export
  tool (Settings & Privacy -> Data privacy -> "Get a copy of your data" ->
  Connections) — the user's own data, which LinkedIn explicitly provides
  for download. `warm_connections.py` never makes a network call to
  linkedin.com at all; it only ever reads a CSV file the user already has.
- **Hunter.io does not do this.** The user already has a Hunter account/API
  key wired into this tool, and asked directly whether Hunter offers
  relationship-mapping — it doesn't. Hunter's Domain Search/Discover find
  emails and B2B company/contact data; it has no visibility into who
  already knows whom. (Sales Navigator's TeamLink feature and paid
  relationship-mapping tools like Clay/Apollo/Crossbeam do this natively,
  and are worth considering as an alternative to a custom-built matcher —
  noted for the user, not evaluated further here.)
- **The decision-maker isn't necessarily the target.** A strong personal
  connection to anyone at the target company can become an internal
  referral path to the actual decision-maker — often a better entry point
  than a cold approach straight to the top title. So matching is done
  across the target company's whole employee roster, not just a presumed
  CEO/CMO contact, and `render_report()`/whatever surfaces this later
  should not silently sort by seniority.
- **No invented connection-strength score.** LinkedIn's connections export
  gives only: current company, current title, and the date connected —
  no interaction history, no message count, no mutual-connection count.
  Any richer "connection strength" score would be fabricated, which
  breaks this tool's anti-fabrication discipline (see "Anti-fabrication is
  load-bearing" above). `find_warm_paths()` reports plain facts (this
  person is a 1st-degree connection, in this role, connected since this
  date) and leaves judgment to the human — the same restraint
  `hunter_contacts.py` already applies to Hunter's own confidence scores.
- **Matching is exact, not fuzzy** (`_normalize_company()` only strips
  common legal suffixes like ", Inc."/" LLC" and normalizes case/
  punctuation) — a wrong fuzzy match would surface a stranger at a
  similarly-named company as a false warm path into the real target,
  which is worse than missing a real match.
- `load_linkedin_connections()` scans for the real "First Name,Last
  Name,..." header row rather than assuming it's line 1 — LinkedIn
  prepends several lines of boilerplate notes to the export, and a fixed
  line-offset would silently break the moment LinkedIn changes how many
  note lines it prepends (same class of bug as `pr_wire_feeds.py`'s
  stale-feed-URL lesson). Raises `ValueError` if the header truly can't be
  found, rather than silently returning zero connections and looking
  indistinguishable from "no matches."
- `find_warm_paths_for_leads(connections, leads)` is the convenience entry
  point for a whole run — takes this run's lead dicts (any signal type,
  from any search) and returns `{lead_index: [WarmPath, ...]}` for every
  lead with at least one match.

**Multiple Elevate teammates' connections, not just the user's own.**
`load_connections_dir(dir_path)` loads every `*.csv` file in a directory,
tagging each `Connection` with `owner` (the filename stem — e.g.
`Sarah.csv` -> `owner="Sarah"`), so the whole team's networks combine into
one pool: the best warm path into a target company isn't necessarily the
user's personal connection (see the "decision-maker isn't necessarily the
target" point above — same reasoning extends to *whose* network the path
comes from, not just which person at the target company it lands on). A
missing directory returns `[]` (this feature is entirely opt-in — no
folder, no matching, nothing else changes) and one malformed CSV in the
directory doesn't stop the others from loading (a warning is printed for
it, matching `pr_wire_feeds.py`'s per-source-failure posture).

**Wired into `_finalize_and_write()` in both `bd_agent.py` and
`gui_logic.py`**, right before `render_report()`: connections are loaded
from `--linkedin-connections-dir` (CLI; default `linkedin_connections`,
env `LINKEDIN_CONNECTIONS_DIR`) or, in the GUI, always
`app_dir() / "linkedin_connections"` — deliberately **no GUI field** for
this one, since the setup step is "drop your team's LinkedIn exports into
that folder, named after each person" rather than something to type a
path for. `render_report()` takes a new `warm_paths` dict
(`{lead_index: [WarmPath, ...]}`) and renders a **"Warm path:"** bullet
list per matching lead, between the Contact block and the Draft email —
each line shows whose connection it is ("Sarah's connection: Bob Smith,
Director of Regulatory Affairs — connected since 02 Jan 2023"), never
just "a connection exists," so the human doing outreach knows exactly who
to ask for an intro.

**Deliberately still company-name-text matching only, not a Hunter/Claude
employee-roster cross-check.** The user asked whether Hunter or Claude
could pull a full employee roster for a target company and cross-reference
it against the team's connections by name — considered and intentionally
not built: the only way to link a roster (from Hunter/Claude, which has no
LinkedIn URL or email in common with the connections export) to a LinkedIn
connection is matching on **name alone**, and common names create real
false-positive risk ("the John Smith at the company" isn't necessarily
"your John Smith") — the same anti-fabrication principle that already
governs `_normalize_company()`'s deliberately-non-fuzzy matching above. The
existing company-name-in-the-connections-export approach doesn't have this
problem, since it only ever matches a connection against their own
self-reported current employer, not a separately-sourced roster. If
roster-based name matching is wanted later, it should be a clearly
separate, lower-confidence "possible match — unverified" tier, never
blended into the same list as a direct match.

Tested with fabricated multi-owner CSV data (including a full mocked run
of `_run_trial_signals_cli()` proving the "Warm path" section renders
correctly, only for matching leads, with the right owner attribution) —
see the git history for the test pattern. Also validated directly against
the user's real LinkedIn export (612 real connections parsed with no
errors; spot-checked matches against real companies, including 15 warm
paths to a competing imaging CRO already in the user's network).

**HubSpot Company-owner auto-assignment from a warm path.** When
HubSpot sync is also enabled and a lead has a warm-path match,
`hubspot_sync.upsert_company()` sets HubSpot's standard "Company owner"
property (`hubspot_owner_id`) to whichever Elevate teammate has the
connection — the record lands in HubSpot already assigned to the right
person, not just flagged in the report. Requires an **explicit** mapping
from a connections-CSV owner label to that person's real HubSpot login
email — `warm_connections.load_owner_emails()` reads it from
`owners.json` in the same `linkedin_connections` folder (e.g.
`{"Sarah": "sarah@elevateimaging.com"}`) — the CSV filename stem
("Sarah") is just a display label, not necessarily a real login email,
and guessing one from the other risks assigning a lead to the wrong
person's HubSpot account; same identity-guessing risk already ruled out
for roster-based name matching above. Missing `owners.json`, or a label
not listed in it, just means no owner gets auto-assigned for that
person's connections — never an error.

If a lead has warm-path matches from more than one teammate, only the
**first** match's owner is used (`owner_emails_for_warm_paths()`) —
HubSpot only supports one owner per record, and there's no connection-
strength score to prefer one teammate's match over another's, so
first-found (alphabetical by connections-CSV filename) is the least-
arbitrary deterministic choice available.

`hubspot_sync.find_owner_id_by_email()` resolves each teammate's email to
HubSpot's numeric Owner ID via the Owners API (`GET /crm/v3/owners?email=...`)
— `hubspot_owner_id` needs that numeric ID as its value, not an email or
name string. `push_leads_to_hubspot()` resolves and caches each unique
email at most once per run, and a lookup failure or unmapped email just
means no owner assignment for that lead — never blocks the rest of the
sync. **The Private App's token may need the `crm.objects.owners.read`
scope added** for this to work — not yet confirmed against the user's
account; if owner-assignment silently never happens, check that scope
first.

Note this required reordering `_finalize_and_write()` in both
`bd_agent.py` and `gui_logic.py`: warm-path matching now happens
*before* the HubSpot Declined-check/sync block (it used to happen only
right before `render_report()`), so a lead's owner-email lookup is
available in time for the HubSpot sync step that needs it.

Tested with `unittest.mock.patch`: owner-email loading/mapping, first-
match-wins tie-breaking, Owner-ID resolution and caching, and a full
mocked `_run_trial_signals_cli()` run proving a warm path correctly
drives `hubspot_owner_id` end to end. Not yet confirmed against a live
account.

**Related, already covered — don't duplicate:** the user separately asked
about a "competitor dissatisfaction" module (public complaints, negative
reviews, social posts about a competitor) from the same planning
conversation; this already exists as the `vendor_switch_signal` signal
type in the signal-sweep search (see "Signal sweep search" above) —
confirmed with the user, no new module needed for this.

### Common-ground matching (built, all four searches, opt-in via a `biosketches` folder)

`agent/biosketch_matching.py` is a second, deliberately lower-confidence
sibling to `warm_connections.py` — same job (give cold outreach a real
reason to open with something other than "Dear Sir/Madam"), different
data: instead of a verified 1st-degree LinkedIn connection, this looks for
a shared *background* between you (or a teammate) and a specific named
person tied to a lead — a trial's Principal Investigator, a company
officer, a conference presenter — same institution, same city. Built after
the user asked to extend warm-path matching beyond LinkedIn using "filings,
meetings, public available info," specifically naming university/city/
county overlap, and dropped in their own LinkedIn `Profile.csv` export to
seed it.

**Deliberately a separate, lower-confidence tier, never blended into
`warm_paths`.** A shared university or city is not a personal connection —
it's a possible conversation opener at best, and could be a coincidence.
`render_report()` renders it under its own **"Possible common ground
(unverified — confirm before using)"** heading, distinct from "Warm path",
and the HubSpot note (`_build_hubspot_note_body()`) does the same. This is
the same principle CLAUDE.md already established for roster-based name
matching in the warm-connections section above ("a clearly separate,
lower-confidence 'possible match — unverified' tier, never blended into
the same list as a direct match") — just applied to a second, related
feature.

**The HubSpot note always shows this section, even when empty** — the
user's own request. A note that just omits the section when there's no
match is indistinguishable from the check never having run at all; a note
opened days later gives no way to tell "checked, nothing found" from
"never checked." `_build_hubspot_note_body()`'s `else` branch renders
`"Possible common ground: none found."` explicitly rather than skipping
the section — deliberately asymmetric with `render_report()`'s Markdown
report (which still omits the section when empty, unchanged), since the
report is read as one continuous document where an absent section already
reads clearly as "nothing here," while a HubSpot note is read standalone,
out of that context.

**Data source, side A (you/your team): a LinkedIn `Profile.csv` export**
(Settings & Privacy -> Data privacy -> Get a copy of your data -> Profile
— a different export file than Connections.csv, which `warm_connections.py`
already uses). `load_biosketches_dir()` mirrors
`warm_connections.load_connections_dir()` exactly: every `*.csv` in a
`biosketches` subfolder of `--linkedin-connections-dir` (so a team folder
that already has `Sarah.csv`/`Darren.csv` connections exports just gets a
`biosketches/Sarah.csv`/`biosketches/Darren.csv` sibling, no new CLI flag
or GUI field — same "drop files in a folder" pattern, wired the same way
into both `bd_agent.py`'s and `gui_logic.py`'s `_finalize_and_write()`).
`_extract_affiliations()` pulls institution-like phrases out of the
Headline/Summary free text (e.g. "Associate Professor at University of
Massachusetts Medical School" -> "University of Massachusetts Medical
School") — a best-effort heuristic gated on a fixed list of institution-
type words (university, hospital, institute, etc.) split out on common
LinkedIn headline separators ("at", "with", commas, dashes), same
narrow-by-design posture as `pr_wire_feeds.py`'s `_guess_company_name()`.
City/region come from the Geo Location field via `_parse_geo()` (e.g.
"Worcester, Massachusetts, United States" -> city="Worcester",
region="Massachusetts").

**Data source, side B (the lead's people): a new `related_people` field
on lead dicts**, populated two ways at no extra Claude *call* (though see
the cost note below):
- **Free, for ClinicalTrials.gov leads** — a trial's Overall Official
  (typically its Principal Investigator) is structured API data
  (`contactsLocationsModule.overallOfficials`: `name`/`affiliation`/
  `role`), pulled by `clinicaltrials_gov._overall_officials_to_related_people()`
  from the same study payload the free trial-signals search already
  fetches — genuinely free, no extra request. Always `"confidence":
  "stated"` since it's registry data, not something pieced together; no
  `location` (the API ties an official to an institution, not a personal
  city). Field names confirmed from documented ClinicalTrials.gov API v2
  schema, but — like the rest of `clinicaltrials_gov.py` — not yet
  verified against a live response from this sandbox (network policy
  blocks `clinicaltrials.gov` outright); if a real run shows this coming
  back empty for trials that do list an Overall Official on the website,
  check this field path first.
- **For the three Claude-driven searches** — `RELATED_PEOPLE_PROMPT_BLOCK`
  (a single shared instruction, injected into `build_prompt()`,
  `build_phase_transition_prompt()`, and `build_signal_sweep_prompt()` so
  the wording can't drift between them) asks Claude to identify a named
  individual connected to a lead and actively search further for their
  public background — not just note it if it happens to already be in the
  source that produced the lead. This rides along in the same paid call,
  but unlike the CEO/CMO `contact_name`/`contact_title` fields right above
  it in each prompt ("bonus only, don't spend search budget hunting for
  this"), this instruction explicitly *does* authorize spending a slice of
  the existing search budget once a person is named — a deliberately
  different, looser instruction than everywhere else in these prompts,
  per the user's own explicit call ("allow claude to be creative when
  looking for links — we can firm them up with some DD on our end"). To
  make that workable without loosening this tool's anti-fabrication
  discipline, `related_people` carries its own `"confidence": "stated" |
  "inferred"` per person — "stated" only when a source directly says the
  detail, "inferred" for a real but imperfect match (e.g. a same-named
  profile that's very likely, not certainly, the right person). Claude is
  told not to omit a plausible lead just for being uncertain — mark it
  "inferred" instead, since nothing here gets used unconfirmed anyway
  (`biosketch_matching.py` only ever surfaces a labeled, unverified
  "possible common ground" line). The one line that doesn't move: never
  invent a person who doesn't exist, or state a fact not actually found
  somewhere — same as every other field in these prompts. Because this can
  cost a few extra searches within the existing per-call budget
  (`max_uses=40`/`60`/`70` — see "Per-run cost estimate" above), those
  budgets were deliberately left unchanged rather than bumped
  preemptively; if real runs show the core research getting under-
  searched because of it, that's the first knob to revisit.

**Matching itself is exact/substring only, same discipline as
`warm_connections.py`'s company-name matching — no fuzzy/semantic
matching, no numeric closeness score.** `find_common_ground()` does
case-insensitive substring matching between two already-public
institution-name strings (one from a biosketch, one from a lead's
`related_people`) for affiliation matches, and an exact city+region string
match for location matches. Every match is a separate, plain fact
(`CommonGround(owner, kind, detail, person_name, person_role)`) — no
ranking, no "best" match picked, same "leave judgment to the human"
restraint as `warm_connections.py`'s `WarmPath` and Hunter's own
confidence scores.

Tested with `unittest.mock.patch` and the user's real uploaded LinkedIn
`Profile.csv` export (not committed to the repo): affiliation/geo parsing
against the real headline ("Associate Professor at University of
Massachusetts Medical School" correctly split into institution
"University of Massachusetts Medical School" and location "Worcester,
Massachusetts"), `load_biosketches_dir()`'s per-file-failure posture
(skips a non-Profile.csv file, keeps loading the rest), affiliation and
location matching (including a deliberately non-matching third person to
confirm no false positive), `render_report()`'s and
`_build_hubspot_note_body()`'s common-ground sections, and a full mocked
`_run_trial_signals_cli()` run proving a ClinicalTrials.gov Overall
Official correctly flows all the way through to the rendered report. Not
yet confirmed against a live ClinicalTrials.gov response, and the
Claude-prompt side (`related_people` from the three paid searches) hasn't
been exercised against a real paid run yet either — same "built and
mock-tested here, confirm on a real run" posture as every other new
source added this session.

### HubSpot sync (built, all four searches, opt-in via `--hubspot-api-key`)

`agent/hubspot_sync.py` implements the design from CLAUDE.md's original
"Known gaps" entry: when a lead is drafted, create/update it as a Company
(+ Contact, if Hunter confirmed an email, associated to that Company) in
HubSpot with an "Outreach Status" property set to `Contacted`; before
writing the report, exclude leads whose company is already marked
`Declined` in HubSpot (deliberately narrower than "ever contacted" — a
`No Response` company should still be able to resurface for a later
follow-up — see `is_company_declined()`/`split_declined()`).

**Every Company also gets "Channel Methods Do Not Call" set to `Yes`, by
default and unconditionally** (Company object only — Contact has no
equivalent field). The user's own reasoning: every lead this tool
surfaces already has a specific trigger (that's the definition of a lead
here — a funding round, a trial result, whatever the signal was) and/or a
possible warm-path introduction, so none of them should get a vanilla
cold call from the sales team; outreach should go through the drafted
email or a warm intro instead. `upsert_company()`/`sync_lead()`/
`push_leads_to_hubspot()` all take a `no_cold_call_property` parameter
(default `DEFAULT_NO_COLD_CALL_PROPERTY`) — pass an empty string to
disable it for a given call, or override the property name via
`--hubspot-no-call-property` (CLI/env `HUBSPOT_NO_CALL_PROPERTY`) or the
GUI's Settings dialog. Unlike `outreach_status`, HubSpot did **not**
auto-slugify the display label "Channel Methods Do Not Call" into
`channel_methods_do_not_call` the way it did for "Outreach Status" ->
`outreach_status` — that original best guess was confirmed wrong by a
real run's `PATCH` failing with `PROPERTY_DOESNT_EXIST` (all 4 leads in
that run failed to sync at all, since HubSpot rejects the whole
properties payload atomically when one property name is invalid — see
`test_hubspot_connection.py`'s "double-check its internal name" note).
The user found the real internal name in their account's property
settings: **`do_not_call`**, now `DEFAULT_NO_COLD_CALL_PROPERTY`. Lesson
for next time a property's internal name is needed: don't assume
HubSpot's auto-slugify pattern holds for every property — some display
labels apparently don't slugify to what you'd expect, so confirm via
`test_hubspot_connection.py` or the property's own detail page rather
than guessing from the label.

**The property's value is also account-specific, not just its name.**
`NO_COLD_CALL_YES_VALUE` was originally `"Yes"` — a second real run then
failed with `INVALID_OPTION` ("Yes was not one of the allowed options:
[\"true\", \"false\", \"\"]"), because `do_not_call` is a boolean/checkbox
property in this account, not an enumeration with display-label options.
HubSpot boolean properties always take the literal string `"true"`/
`"false"` over the API, regardless of what the checkbox itself is labeled
in the UI — now `NO_COLD_CALL_YES_VALUE = "true"`. Same lesson as the
property-name mixup above, just one level deeper: confirm both the
property's internal name *and* its value type/options before assuming a
human-readable label works over the API.

**Only ever fires for leads that carry a `company_domain`** — which the
free trial-signals search's three sources never populate
(`clinicaltrials_gov.py`/`sec_edgar.py`/`pr_wire_feeds.py` all hardcode
`"company_domain": None`, since none of those three structured sources
exposes a company website, only a name). Only the three Claude-driven
searches (conferences, phase-transitions, signal-sweep) populate it,
since Claude is explicitly asked to find each company's domain. Confirmed
directly against a real trial-signals run: two real SEC EDGAR leads came
back with `company_domain: None`, so HubSpot sync silently did nothing
for either — expected given the gating, but worth knowing before assuming
a run "didn't work." (Hunter contact lookup has the exact same gate —
`hunter_contacts.find_contact()` also requires a domain — so trial-signals
leads never get a confirmed contact either, regardless of whether a
Hunter key is set.) `agent/test_hubspot_connection.py` is a small,
standalone script (prompts for the token interactively, not part of any
pipeline) for verifying the token/property/API path work against a real
account without needing a domain-bearing lead or spending any Anthropic
budget.

Entirely optional, same posture as `email_drafts.py`'s outbox integration:
only runs if `--hubspot-api-key` is set (env: `HUBSPOT_API_KEY`); nothing
changes if it's never provided. Wired into `_finalize_and_write()` in both
`bd_agent.py` and `gui_logic.py`, right after the seen-leads dedup step
(Declined-check, before Hunter enrichment — so a declined company never
consumes a Hunter lookup either) and right after outbox drafts (the actual
sync push, since by then `enriched` has the Hunter contact each Contact
record should carry).

**Credential path: a HubSpot "Legacy private app" access token, not a
Private App (deprecated) or a Project-based/OAuth app.** HubSpot's app
creation UI changed in 2026 — "Private Apps" as a standalone menu item is
gone, replaced by three options: **Legacy app**, **Project**, and **MCP
auth app**. Only "Legacy app" -> "Private App" still issues a simple,
permanent static access token (`pat-...`) suitable for this tool's stdlib-
`urllib` REST-call pattern (same shape as `hunter_contacts.py`'s API key —
no OAuth flow, no refresh tokens). "Project" apps use OAuth instead and
don't hand back a reusable static token the same way — real added
complexity (a local CLI, an OAuth redirect flow, token refresh handling)
for no benefit to a standalone script like this one. "MCP auth app" is for
connecting an AI agent directly to HubSpot over Model Context Protocol —
a different integration shape entirely (this Claude Code session talking
to HubSpot live), not a credential `bd_agent.py` itself can use later,
unattended, when the user runs a report.

**`--hubspot-outreach-property` (env: `HUBSPOT_OUTREACH_PROPERTY`) defaults
to `outreach_status`** — HubSpot's auto-generated internal name for a
property labeled "Outreach Status" when there's no naming collision on the
account. The user's actual Contact and Company properties can end up with
different internal names (HubSpot generates them independently per
object), so this default is a starting point, not a confirmed value — the
same one flag is used for both objects, on the assumption they match.
**Confirmed correct for the Company object** via a real run of
`test_hubspot_connection.py` against the user's live account (`outreach_status`
took the write with no error, real HubSpot object ID returned). Still
unconfirmed for the Contact object specifically — Contact property setup
was never fully completed during the user's HubSpot walkthrough, though
`sync_lead()`'s best-effort handling (see above) means this can't block
the Company side regardless.

The API endpoint shapes (CRM v3 objects search/create/update, and the v4
"default association" shorthand for linking a Contact to a Company) are
confirmed against HubSpot's own developer-docs conventions, which have
been stable for years. This dev sandbox's own network policy still blocks
`api.hubapi.com` outright (like every other external API touched this
session), so all of this was built and mock-tested here with
`unittest.mock.patch` on `hubspot_sync._request()` — but **the user has
since confirmed a real run works**: `test_hubspot_connection.py` against
their live account created a real Company (HubSpot ID `57257808228`) with
`outreach_status` set correctly and `is_company_declined()` returning the
expected `False` — so the Company-side path (search/create/update,
auth, the property name) is proven working, not just plausible. The
full search-pipeline integration (`split_declined()`/`push_leads_to_hubspot()`
end to end, and the Contact side) hasn't been separately confirmed on a
live run yet — the one real run so far only exercised leads with no
`company_domain` (see above), so HubSpot sync never actually fired during
it.

**Every synced lead also gets a HubSpot Note — "why this landed here."**
Requested directly by the user after looking at a populated HubSpot
Company record and asking for "some how to have all the relevant search
info ... as a clickable/launchable note/document. That way someone looks
at it, immediately can see the relevant info." `hubspot_sync.create_note()`
+ `associate_note_with_object()` create a Note (HubSpot's Engagement
type, `POST /crm/v3/objects/notes` with `hs_note_body`/`hs_timestamp` —
`hs_timestamp` is epoch milliseconds, the established convention for the
Notes API specifically, confirmed via web search) and attach it to the
Company (and Contact, if one was created) via the same v4 "default
association" endpoint already used for Contact-Company linking — auto-
picks the correct association type rather than hardcoding a numeric type
ID, which differs between NOTE-to-COMPANY and NOTE-to-CONTACT in HubSpot's
older v3 associations API.

`bd_agent._build_hubspot_note_body(lead, contact, warm_path_matches, args,
other_candidates=None)` composes the note body as simple HTML (bold,
links, line breaks — what HubSpot's Notes UI actually renders), covering
the same ground as `render_report()`'s per-lead Markdown block condensed
into one note: the signal type/company/detail, the source URL(s) (every
URL for `phase_transition_deep_signal`, same as the report), the primary
contact Hunter confirmed (or "not publicly available"), any warm-path
connection, and the full drafted email subject+body — so opening the
record in HubSpot shows the trigger and a ready-to-send draft immediately,
not just a status label. Every piece of lead/contact/company text is run
through `html.escape()` before being embedded, since it's all free text
Claude or Hunter returned and could in principle contain characters that
would otherwise break the note's HTML.

**Other Hunter-confirmed contacts, not just the one picked for outreach —
the user's own follow-up request** ("any Hunter confirmed employee can get
pulled in?"). `hunter_contacts.find_all_candidates(domain, api_key,
min_confidence=90) -> list[Contact]` returns every candidate Hunter's
Domain Search has on file for a domain, not filtered down to a single
CEO/CMO match the way `find_contact()` is — same confidence gating
(`.email` only populated at/above `min_confidence`, otherwise shown as
"not confirmed (score/100)" rather than silently dropped, same
anti-fabrication posture as everywhere else in this tool). This costs one
*extra* Hunter Domain Search call per lead beyond the existing
`find_contact()` call, since Hunter doesn't expose a way to reuse one
call's result for two purposes — so it only fires when both
`--hunter-api-key` and `--hubspot-api-key` are set (see
`_finalize_and_write()`'s `note_bodies_by_index` loop in both
`bd_agent.py` and `gui_logic.py`), and only once per lead, not per Hunter
usage generally. `_build_hubspot_note_body()` excludes whoever's already
shown as the primary contact from this "other contacts" list so the same
person never appears twice in one note.

**Best-effort, same posture as the Contact-side sync.** `sync_lead()`
gained a `note_body_html` parameter; note creation/association is wrapped
in its own try/except reporting `note_error` rather than raising — a
Notes-API failure never discards an already-successful Company/Contact
upsert. `push_leads_to_hubspot()` now returns a 4-tuple, `(successes,
failures, contact_warnings, note_warnings)` — anywhere this function is
called (`bd_agent.py`, `gui_logic.py`, and any test) needs updating for
the extra return value. A lead with no `note_body_html` entry in
`note_bodies_by_index` never touches the Notes API at all — this stays a
strict opt-in add-on to HubSpot sync, not a required part of it.

Tested with `unittest.mock.patch` on `hubspot_sync._request()`:
`create_note()`/`associate_note_with_object()` in isolation, `sync_lead()`
creating a note and associating it with both Company and Contact, note
failure being best-effort (Company/Contact still succeed and are still
counted a success), `push_leads_to_hubspot()` threading
`note_bodies_by_index` through and reporting `note_warnings`, and
confirming the Notes API is never called when no note body is supplied.
Also tested `find_all_candidates()`'s confidence gating and
`_build_hubspot_note_body()`'s HTML escaping (including an explicit
`<script>` XSS-shaped input) and primary-contact dedup. Not yet confirmed
against a live account — same posture as the rest of HubSpot sync's
unverified pieces above.

### Known gaps (not yet implemented)

- `email_drafts.py`'s outbox-drafts feature (see its own section above)
  hasn't been confirmed against a real mailbox yet — the user hadn't
  determined which platform hosts `imogene@elevateimaging.ai` as of this
  writing. Built and tested with `unittest.mock.patch` on `imaplib.IMAP4_SSL`.
- Paid biotech intelligence databases (BioPharmCatalyst, AlphaSense,
  Citeline/Trialtrove) were explicitly excluded from the trial-signals
  search per the user's instruction — free/deterministic sources only.
- NDA/BLA-stage triggers from the original proposal are still not built:
  Blinded Independent Central Review (BICR) re-read requests (triggered by
  SEC filings mentioning "FDA Type B meeting feedback"/"Pre-NDA meeting
  summary"), ad hoc exploratory endpoint analyses (triggered by "pivotal
  trial met primary endpoint, secondary endpoint ongoing" language), and
  supplemental filings (sNDA/sBLA, triggered by expanded-access or Phase
  3b extension protocol updates on ClinicalTrials.gov). These would need
  their own alert-phrase lists in `sec_edgar.py` and/or new
  `clinicaltrials_gov.py` queries, following the same pattern as the
  signals already built, if prioritized later.
- None of the four searches can be scoped to a specific company by name
  today — all four only take `--indication`. A "everything about Company
  X" lookup would be a natural extension of the same pattern (add a
  `company` query param alongside `indication` to each of
  `clinicaltrials_gov.py`/`sec_edgar.py`/`pr_wire_feeds.py`, or just ask
  for it directly in `build_phase_transition_prompt()`/
  `build_signal_sweep_prompt()`) but isn't built.
- Cross-search dedup is only as good as each pair's actual check.
  Phase-transitions checks trial-signals for free (a live pre-run, see
  "Phase transition deep search" above) and checks/is checked by
  signal-sweep via each other's persisted seen-leads state (see "Signal
  sweep search" above) — but the conference search isn't cross-checked
  against anything, and trial-signals isn't cross-checked against
  signal-sweep or the conference search either (e.g. `regulatory_milestone`
  could plausibly surface in both signal-sweep and, someday, a
  ClinicalTrials.gov-sourced signal). Only the two overlaps that were
  actually flagged as likely (trial-signals/phase-transitions,
  phase-transitions/signal-sweep) have been addressed; a fuller N-way
  cross-check isn't built.
- `pr_wire_feeds.DEFAULT_FEED_URLS` is the least-verified piece of this
  whole trial-signals search — see its module docstring and the
  dedicated callout in the `agent/pr_wire_feeds.py` section above. If a
  feed URL goes stale, `find_leads()` will print a fetch/parse warning
  rather than fail silently; fix by editing `DEFAULT_FEED_URLS` directly.
- Hunter.io's Domain Search only surfaces contacts Hunter has already
  crawled/indexed for that domain — smaller biotechs with thin public web
  presence may still come back with no confirmed contact. This is a data
  availability limit, not a bug; the report should say "not confirmed"
  rather than papering over it.
- HubSpot sync is built (see "HubSpot sync" above) but not yet verified
  against a live account, and `--hubspot-outreach-property` assumes
  Contact and Company share the same internal name — correct this once
  the user confirms both. Actual email sending and reply tracking are
  still intended to go through Outlook (desktop, via `pywin32` COM
  automation — no new credentials needed) with HubSpot's native
  inbox-connection feature handling conversation logging, not custom code.
- `agent/warm_connections.py` is now wired into all four report pipelines
  (see "Warm-path connection matching" above), including HubSpot
  Company-owner auto-assignment from a warm path (see the "HubSpot
  Company-owner auto-assignment" callout at the end of that section) —
  requires the `crm.objects.owners.read` scope on the Private App token,
  not yet confirmed against the user's account.
- `run_windows.bat.example` (copy to `run_windows.bat`, fill in real keys,
  gitignored) is a secondary CLI-launcher path, superseded by `gui.py` as
  the primary interface — added after `set` env vars in PowerShell
  repeatedly failed to persist across windows/copy-paste in practice.
  Batch files need **unquoted** `set VAR=value` — quotes become part of
  the value and silently break auth. Kept around for scripting/automation
  use, not because it's the recommended path.
- Manual file-by-file patching via "right-click → Edit → paste" (used
  before `gui.py` existed, to get fixes onto the user's machine without a
  git-based update flow) turned out fragile in practice — Notepad's Save
  As silently appends `.txt` unless "Save as type" is explicitly set to
  "All Files", and this bit the user more than once (`run_windows.bat`,
  `seen_leads.py`). If a similar situation recurs, prefer pointing at a
  fresh full-repo download over incremental single-file patches.
- Both `.bat` launchers call `py` (the Windows Python Launcher), not
  `python`. `python.org`'s installer always places `py.exe` in
  `C:\Windows` — which is already on `PATH` — regardless of whether "Add
  python.exe to PATH" was checked, specifically to route around a real
  gotcha hit in practice: installing/reinstalling Python updates `PATH` in
  the registry, but `explorer.exe` (what actually launches a double-clicked
  `.bat`) caches its own environment and won't see that update until the
  user logs off/on or reboots — so a freshly-installed Python can work fine
  from a brand-new terminal while `run_gui.bat` still fails with Windows'
  "Python was not found... Microsoft Store" message. `py` sidesteps the
  whole class of problem instead of asking the user to reboot.

## Generated output is not tracked

`agent/leads_report.md` and `agent/run.log` (or whatever `--output` path is
used) are gitignored — they're per-run artifacts, not part of the
committed codebase. Don't unignore them; if a report needs to be preserved,
save it outside `agent/` or as an explicitly named file the user asks to
commit.
