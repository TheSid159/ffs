# agent

Business development lead-finder for an imaging CRO. It uses Claude with
web search to:

1. Find recent Phase II trials in a given cancer type (default: bladder
   cancer) presented at ASH/ASCO with **positive** results.
2. Identify the sponsoring biotech/pharma company and its CEO/CMO contact.
3. Draft a preliminary outreach email for each lead.

## Setup

1. Get an Anthropic API key from https://console.anthropic.com (Settings →
   API Keys), or run `ant auth login` if you have the Anthropic CLI.
2. Install dependencies:
   ```bash
   cd agent
   pip install -r requirements.txt
   ```
3. Set your API key:
   ```bash
   cp .env.example .env   # then edit .env with your real key
   export ANTHROPIC_API_KEY=sk-ant-...   # or `set -a; source .env; set +a`
   ```

## Run

```bash
python bd_agent.py \
  --conference ASH ASCO \
  --year 2025 2026 \
  --indication "bladder cancer" \
  --phase "Phase II" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --output leads_report.md
```

This prints progress to the terminal and writes a Markdown report
(`leads_report.md` by default, plain text/Markdown — nothing is sent) with
one section per lead: the trial result, the company contact (only real,
found contacts — it will never invent an email), and a draft outreach email
that always opens with:

> Dear [contact name], I read with interest your recent paper, "[abstract
> title]" (Abstract #[abstract number]), at [meeting name] on [presentation
> date]. Congratulations on this exciting result. Given this, I wanted to
> introduce our imaging CRO, Elevate Imaging, as a potential imaging vendor
> as you progress [drug/asset name] through its next stage of development.

The script never sends anything — you review and send each draft yourself.

Each run costs API usage (a few dollars per run at typical depth, since it
does many web searches over an extended research task).

## Next steps to consider

- Point it at other indications/phases by changing the flags.
- Wire the output into a CRM or spreadsheet instead of a flat Markdown file.
- Add a step that cross-checks trial results against ClinicalTrials.gov.

