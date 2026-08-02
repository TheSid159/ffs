# agent

Business development lead-finder for an imaging CRO. It runs **two
independent searches**, each producing its own report, kept deliberately
separate because one costs real API money and the other doesn't:

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
  status just completed; and a new, standalone Phase 2 trial filed by a
  sponsor who's already on record (tracked locally across runs) as having
  run a Phase 1 trial in this indication.
- **SEC EDGAR** — recent 8-K/10-Q filings from public biotechs mentioning
  Phase 1-to-Phase-2 transition language (e.g. "End-of-Phase 1 meeting",
  "Top-line Phase 1 data", "Initiation of Phase 2 trial").
- **Press-release RSS** (PR Newswire / Business Wire / GlobeNewswire) —
  press releases mentioning your indication, "Phase 1", and either
  "topline" or "Phase 2".

All are exact-match against real structured data or real filing/press-release
text — never Claude's interpretation of what it found. Pass
`--no-ctgov`/`--no-secedgar`/`--no-prwire` to skip any one of the three.

### Both searches

- Look up a verified CEO/CMO contact for each company via **Hunter.io**,
  gated on a minimum confidence score (default 90/100) — a low-confidence
  guess is never reported as a confirmed contact.
- Render a preliminary outreach email per lead from a template (the
  `trial_result` opening is a **fixed**, hand-specified template; every
  other signal type gets a Claude-Code-drafted starting point, flagged in
  the report for review).
- Remember what they've already shown you (a separate `seen_leads.json`-style
  file per search, created automatically next to the report) so re-running
  doesn't resurface the same leads every time.

## Setup

1. Get an Anthropic API key from https://console.anthropic.com (Settings →
   API Keys), or run `ant auth login` if you have the Anthropic CLI. Only
   needed for the conference search — the trial-signals search doesn't use
   Claude at all.
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

There are **two independent buttons**: **Search Conferences** (needs your
Anthropic API key) and **Search Trial Signals** (free, no Anthropic key
needed). Click either one, watch progress in the shared log window, then
use the matching **Open Conference Report** / **Open Trial Signals
Report** button when it's done. Running one never affects the other's
report or history.

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

Two subcommands, matching the two searches above:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # only needed for "conferences"
export HUNTER_API_KEY=...              # optional, both subcommands

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
```

`--output` is optional on both — leave it out and the report/CSV are named
from your search parameters (e.g.
`bladder_cancer_ASCO_GU_ASCO_ESMO_AUA_2025_2026_leads_report.md` for the
conference search, `bladder_cancer_trial_signals_report.md` for the
trial-signals search), so re-running with different parameters — or
running the other search — won't silently overwrite an unrelated earlier
report. Pass `--output some_name.md` to pick your own name instead. Same
behavior in the GUI — leave the "Output file" field blank to auto-name.

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
Release Signal]` — each with the detail, a source link, the verified
contact (or an explicit "not confirmed" / "not publicly available" — it
will never invent an email or a confidence score), and a draft outreach
email. `[Trial Result]` leads always open with:

> Dear [contact name], I read with interest your recent paper, "[abstract
> title]" (Abstract #[abstract number]), at [meeting name] on [presentation
> date]. Congratulations on this exciting result. Given this, I wanted to
> introduce our imaging CRO, Elevate Imaging, as a potential imaging vendor
> as you progress [drug/asset name] through its next stage of development.

That exact wording was hand-specified and is treated as fixed. Every other
signal type gets a draft too, but with an opening written by Claude Code
as a starting point rather than hand-specified the same way — the report
flags those with a note to review the wording before relying on it.

The script never sends anything — you review and send each draft yourself.

Only the conference search costs API usage (typically a few dollars, since
it does many web searches over an extended research task) — the
trial-signals search is free. Both may use Hunter.io (one lookup per lead
— check your Hunter plan's monthly search limit). An approximate cost for
the conference search just completed — based on its actual token and
search usage, not a guess — prints at the end of the progress log in both
the GUI and CLI. It's an estimate, not an official bill; check
console.anthropic.com for exact billing.

## Next steps to consider

- Point either search at other indications via the GUI or CLI flags.
- Wire the output into HubSpot instead of a flat Markdown file.
- Add a company-name-scoped lookup across the trial-signals sources
  (today they're indication-scoped only).
