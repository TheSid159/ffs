# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A single-purpose business development tool for an imaging CRO ("Elevate
Imaging"). It uses the Claude API with the web search tool to find
business-development leads across five signal types (see "Five lead signal
types" below) at named oncology/urology conferences and beyond, looks up a
verified CEO/CMO contact for each company via Hunter.io, and — for
trial-result leads only — drafts an outreach email from a fixed template.
There is no application server or database. There are two front ends over
the same pipeline: a Tkinter desktop GUI (`gui.py`, the primary one — the
end user is non-technical and terminal/PowerShell friction was a repeated,
significant source of real problems) and a CLI (`bd_agent.py`) for
scripting. Both write a Markdown report.

## Commands

```bash
cd agent
pip install -r requirements.txt
python gui.py   # primary interface — prompts for API keys in the window, no env vars needed
```

CLI equivalent (used for scripting, or when developing without a display):

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # from console.anthropic.com
export HUNTER_API_KEY=...             # optional, from hunter.io/api-keys — omit to skip contact lookup

python bd_agent.py \
  --conference "ASCO GU" ASCO ESMO AUA \
  --year 2025 2026 \
  --indication "bladder cancer" \
  --phase "Phase II" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --output leads_report.md
```

There is no test suite, linter, or build step — `python -m py_compile
bd_agent.py hunter_contacts.py seen_leads.py gui_logic.py gui.py` is the
only pre-flight check currently used before committing changes (`gui.py`
itself imports `tkinter`, which may not be installed in a headless dev
environment — a `py_compile` syntax check still passes without it, but
`gui_logic.py` is where the real, importable-and-testable logic lives; see
Architecture below). Every CLI flag has a default (see `--help`), so
`bd_agent.py` also runs with no arguments for a smoke test. When editing
`parse_research_output()`, `draft_email()`, `render_report()`, or anything
in `gui_logic.py`, sanity check with fabricated lead dicts and
`unittest.mock.patch` on `bd_agent.run_research` (no network calls needed)
rather than only testing via a full paid run — see the git history around
the Hunter.io integration and GUI commits for the pattern.

## Architecture

Six modules, no application framework:

- **`agent/bd_agent.py`** — the pipeline, in four stages:
  1. `build_prompt()` renders one research prompt from the CLI args
     (conference list, years, indication, phase). It asks Claude to
     research leads via web search across **five signal types** (see below)
     and return a short prose preamble followed by **one fenced
     ` ```json ` block** containing structured lead data. Claude is
     explicitly told *not* to spend search budget hunting for contacts;
     that's a separate, more reliable step now.
  2. `run_research()` sends that prompt to `claude-opus-5` via
     `client.messages.stream(...)` with the server-side
     `web_search_20260209` tool (`max_uses=30`), streaming text to stdout
     for progress visibility.
  3. `parse_research_output()` extracts the trailing JSON block via regex
     (`JSON_FENCE_RE`) into `(preamble, leads, excluded)`. If parsing
     fails, the caller falls back to writing Claude's raw text instead of
     crashing — see `main()`.
  4. `enrich_contacts()` calls `hunter_contacts.find_contact()` per lead,
     then `draft_email()` renders the outreach email **entirely in
     Python** from a fixed string template (not asked of the model) using
     the structured lead data plus whatever contact Hunter confirmed —
     **only for `signal_type == "trial_result"` leads** (see "Five lead
     signal types" below for why). `render_report()` assembles the final
     Markdown.

### Eleven lead signal types

`build_prompt()` asks Claude to categorize every lead with a `signal_type`:
`trial_result` (positive Phase II result at a named conference — the
original, only signal type before this was added), `conference_highlight`
(agenda/keynote/late-breaking-abstract activity at a major oncology/urology
meeting, grounded against the curated list in `agent/conferences.py` so
Claude isn't searching blind for what counts as "major" — also covers
imaging-science/clinical-ops meetings like SNMMI, RSNA, DIA, and SCOPE
Summit, since sponsors presenting early-phase imaging biomarker data there
are prospects even before a pivotal trial), `funding` (financing rounds,
IPOs, grants, licensing deals — Series B/C+ rounds and IPOs are the
strongest version since imaging-heavy oncology trials are expensive and
this often precedes an imaging-vendor RFP by a few months; also noting if
proceeds are earmarked for a pivotal/registrational trial specifically),
`leadership_change` (new CEO/CMO/CSO), `new_registration` (a newly
registered trial on ClinicalTrials.gov or an international equivalent —
also listed in `agent/conferences.py` — surfacing a sponsor before their
trial ever reaches a conference), `regulatory_designation` (FDA/EMA
designations like Breakthrough Therapy, Fast Track, Priority Review,
Orphan Drug, EMA PRIME), `regulatory_milestone` (End-of-Phase 2 or Type
B/C meeting outcomes — usually means a pivotal trial's design, including
imaging endpoints, is being finalized around now), `trial_expansion` (an
existing trial expanding to new countries/sites — multi-region trials are
where centralized imaging review becomes valuable versus inconsistent
local site reads), `protocol_amendment` (an amendment adding or changing
an imaging-related requirement on an existing trial — often means a new
imaging need has emerged, or a current vendor isn't working out, though
the prompt explicitly tells Claude not to speculate about a specific
vendor by name), `hiring_signal` (a company publicly hiring for an
imaging-specific clinical role like "Director of Imaging" — usually means
they're about to manage an imaging CRO relationship, not insource it
away), and `vendor_switch_signal` (a public, citable statement — press
release, LinkedIn post, conference talk — describing imaging data delays
or QC issues with a current vendor; the prompt treats this as the most
sensitive category and explicitly forbids naming a specific competing
vendor unless the source itself already does so, and says to leave it out
entirely rather than repeat an unverified claim about a real company).

For `trial_result` and `new_registration` leads specifically, the prompt
also asks Claude to fold two extra observations into the existing
`result_summary`/`signal_detail` text rather than adding dedicated fields:
whether the trial's endpoint explicitly uses a standardized imaging
criterion (RECIST 1.1, iRECIST, PCWG3, Lugano, etc. — these typically
require central/blinded independent imaging review, a direct signal of fit)
and whether it looks like the company's first pivotal/registrational trial
(companies often only engage an external imaging vendor once a trial has
to hold up to regulators). These stay as prose notes, not booleans, since
they're inherently soft inferences Claude is making, not verified facts.

Several JSON fields are deliberately reused across signal types instead of
adding a parallel field per type: `abstract_url`/`abstract_url_note` double
as the general "source URL" (press release, registry entry, agenda page),
and `abstract_title` doubles as a general headline. `signal_detail` is the
plain-English description for every type except `trial_result`.
`registry_name`/`registry_id` are `new_registration`-only.

**`draft_email()` drafts an email for every signal type**, via
`_opening_and_transition()`, but only the `trial_result` opening ("I read
with interest your recent paper... Congratulations on this exciting
result") is a verbatim, hard CRO requirement (see "The fixed email
opening" below) — never loosen or paraphrase it. The other ten openings
were drafted by Claude Code at the user's explicit request as a starting
point, NOT hand-specified the same way; `render_report()` marks those with
an inline note ("drafted by Claude Code... review the wording") so the
distinction is visible in the report itself, not just in this file. If the
user gives exact wording for a given signal type later (the same way they
did for `trial_result`), update `_opening_and_transition()` and drop that
type's review note.

`vendor_switch_signal`'s opening deliberately does NOT reference the
complaint/pain-point content itself (e.g. never says anything like "I
heard you're having imaging delays with your current vendor") — repeating
a public complaint about a competitor back to the prospect would read as
opportunistic, not professional. It opens generically about running
imaging-intensive trials instead. Don't "improve" this by making it more
specific to the signal_detail; that specificity is exactly what it's
avoiding.

`seen_leads.dedup_key()` prefixes every key with `signal_type` — without
it, two different signal types for the same company (e.g. a funding lead
and a leadership-change lead) would both fall back to the same
`company_name|` key and the second would be wrongly treated as a repeat of
the first.

### CSV export

`render_csv()` writes the same leads as `render_report()`'s Markdown, one
row per lead, to a `.csv` file with the same basename as `--output`
(`leads_report.csv` next to `leads_report.md` by default) — both `main()`
and `gui_logic.run_pipeline()` write it right after the Markdown report.
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
  Tkinter import and holds everything meaningful (`build_args()` translates
  a plain dict of form strings into the same `argparse.Namespace` the CLI
  builds, `run_pipeline()` mirrors `bd_agent.main()`'s body but returns the
  report path instead of just printing it, `QueueWriter` is a file-like
  object for routing `print()`/stdout output into a `queue.Queue`).
  `gui.py` is a thin Tkinter shell: it redirects `sys.stdout`/`sys.stderr`
  to a `QueueWriter` before running the pipeline **on a background
  thread** (network calls would otherwise freeze the window), and only
  touches Tkinter widgets from `_poll_log_queue()` on the main thread via
  `after()` — never from the background thread directly, since Tkinter
  widgets aren't thread-safe. API keys and last-used field values persist
  in `gui_config.json` (gitignored) so they're entered once, not per run.
  This module split is deliberate, not incidental — when changing pipeline
  behavior, prefer editing `gui_logic.run_pipeline()` / `build_args()`
  (testable here) over inlining logic into `gui.py` (only testable by the
  user, on their own machine, since this sandbox has no Tkinter/display).

  `build_args()`'s `conference` field splits on **commas**, not whitespace.
  It used to split on whitespace, which silently broke a single-entry
  conference name containing its own space — typing "ASCO GU" (one
  conference) into the field produced `["ASCO", "GU"]` (two conferences),
  changing what was actually searched with no visible error; a user's
  pasted report showing "Scope searched: ASCO and GU" instead of "ASCO GU"
  was the tell. The `year` field still splits on whitespace deliberately —
  years never contain internal spaces, so that's not the same bug.

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
instead when true. `gui_logic.build_args()`'s default `output`/`seen_file`
paths also route through `app_dir()` for the same reason — a bundled exe's
working directory isn't reliably its own folder depending on how it's
launched, but `sys.executable` always is.

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

### Known gaps (not yet implemented)

- Hunter.io's Domain Search only surfaces contacts Hunter has already
  crawled/indexed for that domain — smaller biotechs with thin public web
  presence may still come back with no confirmed contact. This is a data
  availability limit, not a bug; the report should say "not confirmed"
  rather than papering over it.
- Output is a flat Markdown file; no HubSpot integration yet. Planned
  design (not yet built): when a lead is drafted, create/update it as a
  Contact + Company in HubSpot with an "Outreach Status" property
  (`Contacted` initially); before future research runs, exclude companies
  already marked `Declined` in HubSpot (deliberately narrower than "ever
  contacted" — a `No Response` company should still be able to resurface
  for a later follow-up). Actual email sending and reply tracking are
  intended to go through Outlook (desktop, via `pywin32` COM automation —
  no new credentials needed) with HubSpot's native inbox-connection
  feature handling conversation logging, not custom code.
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

## Generated output is not tracked

`agent/leads_report.md` and `agent/run.log` (or whatever `--output` path is
used) are gitignored — they're per-run artifacts, not part of the
committed codebase. Don't unignore them; if a report needs to be preserved,
save it outside `agent/` or as an explicitly named file the user asks to
commit.
