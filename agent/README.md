# agent

Business development lead-finder for an imaging CRO. It:

1. Uses Claude with web search to find business-development leads in a given
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
   signals — and returns them all as structured data.
2. Looks up a verified CEO/CMO contact for each company via **Hunter.io**,
   gated on a minimum confidence score (default 90/100) — a low-confidence
   guess is never reported as a confirmed contact.
3. Renders a preliminary outreach email per lead from a **fixed template**
   (defined in Python, not left to the model to reproduce), always opening
   with a reference to the specific trial result.
4. Remembers what it's already shown you (`seen_leads.json`, created
   automatically next to the report) so re-running doesn't resurface the
   same leads every time.

## Setup

1. Get an Anthropic API key from https://console.anthropic.com (Settings →
   API Keys), or run `ant auth login` if you have the Anthropic CLI.
2. (Optional, recommended) Get a Hunter.io API key from
   https://hunter.io/api-keys for verified contact lookup. Without one, the
   report still generates but contacts are unverified — only what Claude
   happened to find during research.
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
commit it) and pre-filled next time, so you only type them once. Click
**Run**, watch progress in the window, then **Open report** when it's done.

At the top of the window is a **Conference Calendar** banner that flags
any major meeting starting within the next 45 days, so you know when it's
worth running a targeted search ahead of a specific conference. It only
shows real, confirmed dates — click **Refresh Dates** to look them up
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

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export HUNTER_API_KEY=...             # optional

python bd_agent.py \
  --conference "ASCO GU" ASCO ESMO AUA \
  --year 2025 2026 \
  --indication "bladder cancer" \
  --phase "Phase II" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --hunter-min-confidence 90 \
  --output leads_report.md
```

On Windows, `run_windows.bat.example` is a template for a double-clickable
version of this same command (copy to `run_windows.bat`, fill in your keys
with **no quotes** around them, edit the search-parameter line if wanted).
The GUI above is the easier option for most day-to-day use; this is here
for scripting/automation.

## What you get

Either path writes **two files**: a Markdown report (`leads_report.md` by
default — nothing is ever sent) and a CSV (`leads_report.csv`, same name,
next to it) with one row per lead for tracking in a spreadsheet or
importing elsewhere.

The Markdown report has one section per lead, labeled by signal type
(`[Trial Result]`, `[Conference Highlight]`, `[Funding]`, `[Leadership
Change]`, `[New Trial Registration]`, `[Regulatory Designation]`,
`[Regulatory Milestone]`, `[Trial Expansion]`, `[Protocol Amendment]`,
`[Hiring Signal]`, `[Vendor-Switch Signal]`): the detail, a source link,
the verified contact (or an explicit "not confirmed" / "not publicly
available" — it will never invent an email or a confidence score), and a
draft outreach email. `[Trial Result]` leads always open with:

> Dear [contact name], I read with interest your recent paper, "[abstract
> title]" (Abstract #[abstract number]), at [meeting name] on [presentation
> date]. Congratulations on this exciting result. Given this, I wanted to
> introduce our imaging CRO, Elevate Imaging, as a potential imaging vendor
> as you progress [drug/asset name] through its next stage of development.

That exact wording was hand-specified and is treated as fixed. The other
ten signal types get a draft too, but with an opening written by Claude
Code as a starting point rather than hand-specified the same way — the
report flags those with a note to review the wording before relying on it.

The script never sends anything — you review and send each draft yourself.

Each run costs API usage (typically a few dollars, since it does many web
searches over an extended research task) plus Hunter.io usage (one lookup
per lead — check your Hunter plan's monthly search limit). An approximate
cost for the run just completed — based on its actual token and search
usage, not a guess — prints at the end of the progress log in both the GUI
and CLI. It's an estimate, not an official bill; check
console.anthropic.com for exact billing.

## Next steps to consider

- Point it at other indications/phases via the GUI or CLI flags.
- Wire the output into HubSpot instead of a flat Markdown file.
- Add a step that cross-checks trial results against ClinicalTrials.gov.
