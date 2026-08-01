# agent

Business development lead-finder for an imaging CRO. It:

1. Uses Claude with web search to find recent Phase II trials in a given
   cancer type (default: bladder cancer) presented at named conferences
   with **positive** results, and returns them as structured data.
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
4. Set your API key(s):
   ```bash
   cp .env.example .env   # then edit .env with your real key(s)
   export ANTHROPIC_API_KEY=sk-ant-...
   export HUNTER_API_KEY=...             # optional
   # or: set -a; source .env; set +a
   ```

## Run

```bash
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

### Windows — easier alternative to typing commands each time

Copy `run_windows.bat.example` to `run_windows.bat` in this same folder,
open it in Notepad, replace the two placeholder API key lines with your
real keys, and edit the `--conference` / `--year` / etc. line if you want
different search parameters. Save it. From then on, just **double-click
`run_windows.bat`** in File Explorer to run the tool — no terminal typing
required. The window stays open at the end so you can read the result
before closing it. (`run_windows.bat` is gitignored since it holds your
real keys — never commit it.)

This prints progress to the terminal and writes a Markdown report
(`leads_report.md` by default, plain text/Markdown — nothing is sent) with
one section per lead: the trial result, an abstract/press-release link, the
verified contact (or an explicit "not confirmed" / "not publicly
available" — it will never invent an email or a confidence score), and a
draft outreach email that always opens with:

> Dear [contact name], I read with interest your recent paper, "[abstract
> title]" (Abstract #[abstract number]), at [meeting name] on [presentation
> date]. Congratulations on this exciting result. Given this, I wanted to
> introduce our imaging CRO, Elevate Imaging, as a potential imaging vendor
> as you progress [drug/asset name] through its next stage of development.

The script never sends anything — you review and send each draft yourself.

Each run costs API usage (a few dollars per run at typical depth, since it
does many web searches over an extended research task) plus Hunter.io usage
(one lookup per lead — check your Hunter plan's monthly search limit).

## Next steps to consider

- Point it at other indications/phases by changing the flags.
- Wire the output into HubSpot instead of a flat Markdown file.
- Add a step that cross-checks trial results against ClinicalTrials.gov.
