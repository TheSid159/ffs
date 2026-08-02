# agent

Business development lead-finder for an imaging CRO. It runs **three
independent searches**, each producing its own report, kept deliberately
separate because their cost/scope profiles differ:

### 1. Conference search (Claude web research — costs API usage)

Uses Claude with web search to find business-development leads in a given
cancer type (default: bladder cancer) across eleven signal types: positive
Phase II trial results at named conferences, conference agenda/keynote/
late-breaking-abstract highlights (checked against a curated list of
major worldwide oncology/urology/immuno-oncology meetings, plus theranostics/
molecular-imaging and clinical-ops meetings like SNMMI, EANM, WMIC, RSNA,
DIA, and SCOPE Summit — smaller regional/subspecialty meetings aren't
pre-listed, but are actively searched for when they fall in the same
window as a major meeting already in scope), biotech funding rounds,
CEO/CMO/CSO leadership changes, new trial registrations on
ClinicalTrials.gov and international equivalents, FDA/EMA regulatory
designations, End-of-Phase 2/regulatory-meeting milestones, trial
expansions to new countries/sites, protocol amendments adding imaging
requirements, imaging-role hiring signals, and public vendor-switch
signals.

### 2. Trial signals search (free, deterministic — no LLM, no API cost)

Three sources, none of them using Claude or costing anything, so this is
safe to run as often as you like (daily, even hourly):

- **ClinicalTrials.gov** — a Phase 1 (or Phase 1/2) trial in your
  indication with a primary completion date 60-90 days out; one whose
  status just completed; a new, standalone Phase 2 trial filed by a
  sponsor who's already on record (tracked locally across runs) as having
  run a Phase 1 trial in this indication; and a trial (any phase) that has
  added new sites/locations since a previous run (also tracked locally
  across runs).
- **SEC EDGAR** — recent 8-K/10-Q filings from public biotechs mentioning
  Phase 1-to-Phase-2 transition language (e.g. "End-of-Phase 1 meeting",
  "Top-line Phase 1 data", "Initiation of Phase 2 trial").
- **Press-release RSS** (PR Newswire / Business Wire / GlobeNewswire) —
  press releases mentioning your indication, "Phase 1", and either
  "topline" or "Phase 2".

All are exact-match against real structured data or real filing/press-release
text — never Claude's interpretation of what it found. Pass
`--no-ctgov`/`--no-secedgar`/`--no-prwire` to skip any one of the three.

### 3. Phase transition search (Claude deep web search — costs API usage)

A narrower, deeper version of the same signal the trial-signals search
checks for (a company moving from Phase 1 to Phase 2), but instead of
three fixed sources, Claude searches the open web — LinkedIn, biotech/pharma
news sites, company blogs, hospital/university press, and anywhere else it
finds — for the last N days (default 60). Two things make this different
from a simple web search:

- If Claude finds the same underlying event described by more than one
  source (say, an SEC filing and a press release both about the same
  Phase 2 initiation), it reports that as **one lead**, not two — every
  source it found gets listed together.
- The outreach email opening isn't a fixed template — Claude drafts it
  itself per lead, synthesized from everything it found about that
  specific company, so it can reference the real, specific facts rather
  than generic language.

Before the paid deep search runs, this button automatically runs the free
trial-signals check first and tells Claude what it already found — so
Claude focuses its (paid) search budget on finding what those three fixed
sources missed, rather than re-confirming the same leads. Those free
findings show up in the phase-transition report too, in their own
"already found for free" section, alongside whatever Claude found on its
own.

Costs API usage like the conference search, but is a separate search
(separate button/subcommand, separate report, separate history) since it's
a different kind of research task — one signal hunted broadly, rather than
eleven signals hunted at named conferences.

### All three searches

- Look up a verified CEO/CMO contact for each company via **Hunter.io**,
  gated on a minimum confidence score (default 90/100) — a low-confidence
  guess is never reported as a confirmed contact.
- Render a preliminary outreach email per lead (the `trial_result` opening
  is a **fixed**, hand-specified template; the phase-transition search's
  opening is drafted by Claude itself per lead at research time; every
  other signal type gets a Claude-Code-drafted starting point, flagged in
  the report for review).
- Remember what they've already shown you (a separate `seen_leads.json`-style
  file per search, created automatically next to the report) so re-running
  doesn't resurface the same leads every time.

### Optional: push drafts to an outbox mailbox

Fill in the **Outbox** fields (GUI) or `--outbox-email`/`--outbox-app-password`/
`--outbox-imap-host` (CLI) and every new lead also gets a real, **unsent**
draft email created directly in that mailbox's Drafts folder — useful if
that mailbox is connected to HubSpot (or another CRM) for logging. This
still doesn't send anything: a human opens the draft and hits send
themselves, same as reviewing the Markdown report. The "To:" field is only
filled in when Hunter confirmed a contact email; otherwise it's left blank
for you to fill in. Leave the Outbox fields blank to skip this entirely —
nothing changes from before.

You'll need: the mailbox address, an app password for it (not your regular
login password — check your email provider's settings for "app password"
or "app-specific password"), and its IMAP server address (e.g.
`imap.gmail.com` for Google Workspace/Gmail, `outlook.office365.com` for
Microsoft 365/Outlook). Gmail accounts also need the Drafts folder name
changed to `[Gmail]/Drafts`.

## Setup

1. Get an Anthropic API key from https://console.anthropic.com (Settings →
   API Keys), or run `ant auth login` if you have the Anthropic CLI. Needed
   for the conference search and the phase-transition search — the
   trial-signals search doesn't use Claude at all.
2. (Optional, recommended) Get a Hunter.io API key from
   https://hunter.io/api-keys for verified contact lookup. Without one, the
   report still generates but contacts are unverified — only what was found
   during research.
3. Install dependencies:
   ```bash
   cd agent
   pip install -r requirements.txt
   ```

## Run — desktop app (recommended, especially on Windows)

**Double-click `run_gui.bat`** (or run `python gui.py`). A window opens
where you paste in your API keys and fill in the search fields — no
terminal, no environment variables, no editing files. Your keys and last-
used settings are saved locally to `gui_config.json` (gitignored — never
commit it) and pre-filled next time, so you only type them once.

There are **three independent buttons**: **Search Conferences** and
**Search Phase Transitions** (both need your Anthropic API key) and
**Search Trial Signals** (free, no Anthropic key needed). Click any one,
watch progress in the shared log window, then use the matching
**Open ... Report** button when it's done. Running one never affects
another's report or history.

At the top of the window is a **Conference Calendar** banner that flags
any major meeting starting within the next 45 days, so you know when it's
worth running a targeted conference search ahead of a specific meeting. It
only shows real, confirmed dates — click **Refresh Dates** to look them up
(a small, separate API call, not run automatically) whenever you want an
update; the banner just reads the last lookup otherwise, at no extra cost.

## Build a standalone `.exe` (no Python needed after this, one-time step)

Turns the GUI into a single double-clickable `.exe` — no Python
installation, no `pip install`, on this machine or any other you copy it
to. This has to be built once **on Windows** (a build made on any other OS
won't run on Windows):

```
pip install -r requirements-build.txt
pyinstaller ElevateImaging-BD-Agent.spec
```

The finished file appears at `dist\ElevateImaging-BD-Agent.exe`. Move it
wherever you like (Desktop, Start Menu folder, etc.) — it keeps its saved
settings (`gui_config.json`) and reports right next to wherever that `.exe`
file itself lives, not wherever you happen to run it from. Re-run the same
`pyinstaller` command any time the underlying code changes, to rebuild it.

## Run — command line (alternative)

Three subcommands, matching the three searches above:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # needed for "conferences" and "phase-transitions"
export HUNTER_API_KEY=...              # optional, all three subcommands

# Conference search (costs API usage)
python bd_agent.py conferences \
  --conference "ASCO GU" ASCO ESMO AUA \
  --year 2025 2026 \
  --indication "bladder cancer" \
  --phase "Phase II" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --hunter-min-confidence 90

# Trial signals search (free, no LLM)
python bd_agent.py trial-signals \
  --indication "bladder cancer" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --hunter-min-confidence 90

# Phase transition deep search (costs API usage)
python bd_agent.py phase-transitions \
  --indication "bladder cancer" \
  --days 60 \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --hunter-min-confidence 90
```

`--output` is optional on all three — leave it out and the report/CSV are
named from your search parameters (e.g.
`bladder_cancer_ASCO_GU_ASCO_ESMO_AUA_2025_2026_leads_report.md` for the
conference search, `bladder_cancer_trial_signals_report.md` for the
trial-signals search, `bladder_cancer_phase_transition_report.md` for the
phase-transition search), so re-running with different parameters — or
running a different search — won't silently overwrite an unrelated
earlier report. Pass `--output some_name.md` to pick your own name
instead. Same behavior in the GUI — leave the "Output file" field blank to
auto-name.

On Windows, `run_windows.bat.example` is a template for a double-clickable
version of the conference-search command (copy to `run_windows.bat`, fill
in your keys with **no quotes** around them, edit the search-parameter
line if wanted). The GUI above is the easier option for most day-to-day
use; this is here for scripting/automation.

## What you get

Each search writes **two files** of its own: a Markdown report and a CSV
(same basename, `.csv` instead of `.md`) with one row per lead for
tracking in a spreadsheet or importing elsewhere. Nothing is ever sent.

The Markdown report has one section per lead, labeled by signal type —
conference search: `[Trial Result]`, `[Conference Highlight]`, `[Funding]`,
`[Leadership Change]`, `[New Trial Registration]`, `[Regulatory
Designation]`, `[Regulatory Milestone]`, `[Trial Expansion]`, `[Protocol
Amendment]`, `[Hiring Signal]`, `[Vendor-Switch Signal]`; trial-signals
search: `[Trial Milestone Approaching]`, `[Trial Recently Completed]`,
`[New Phase 2 Filing (Returning Sponsor)]`, `[SEC Filing Signal]`, `[Press
Release Signal]`; phase-transition search: `[Phase Transition (Deep
Search)]` — each with the detail, a source link (or, for phase-transition
leads that were corroborated by more than one source, every source
listed), the verified contact (or an explicit "not confirmed" / "not
publicly available" — it will never invent an email or a confidence
score), and a draft outreach email. `[Trial Result]` leads always open
with:

> Dear [contact name], I read with interest your recent paper, "[abstract
> title]" (Abstract #[abstract number]), at [meeting name] on [presentation
> date]. Congratulations on this exciting result. Given this, I wanted to
> introduce our imaging CRO, Elevate Imaging, as a potential imaging vendor
> as you progress [drug/asset name] through its next stage of development.

That exact wording was hand-specified and is treated as fixed. Every other
signal type gets a draft too: the phase-transition search's opening is
synthesized by Claude itself from the sources it found for that specific
lead (verify every fact against the source links before sending); every
other type's opening was written by Claude Code as a starting point rather
than hand-specified the same way — the report flags those with a note to
review the wording before relying on it.

The script never sends anything — you review and send each draft yourself.

Only the conference search and phase-transition search cost API usage
(typically a few dollars each, since they do many web searches over an
extended research task) — the trial-signals search is free. All three may
use Hunter.io (one lookup per lead — check your Hunter plan's monthly
search limit). An approximate cost for a paid search just completed —
based on its actual token and search usage, not a guess — prints at the
end of the progress log in both the GUI and CLI. It's an estimate, not an
official bill; check console.anthropic.com for exact billing.

## Next steps to consider

- Point any search at other indications via the GUI or CLI flags.
- Wire the output into HubSpot instead of a flat Markdown file.
- Add a company-name-scoped lookup (today all three searches are
  indication-scoped only).
