# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A single-purpose business development tool for an imaging CRO ("Elevate
Imaging"). It uses the Claude API with the web search tool to find recent
clinical trials with positive results at named oncology/urology
conferences, identify the sponsoring company's CEO/CMO contact, and draft
outreach emails per lead. There is no application server, frontend, or
database — it's one Python script run from the terminal that writes a
Markdown report.

## Commands

```bash
cd agent
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # from console.anthropic.com

python bd_agent.py \
  --conference ASCO GU ASCO ESMO AUA \
  --year 2025 2026 \
  --indication "bladder cancer" \
  --phase "Phase II" \
  --sender-name "Dr. Darren Brennan" \
  --sender-title "Medical Director" \
  --sender-company "Elevate Imaging" \
  --output leads_report.md
```

There is no test suite, linter, or build step — `python -m py_compile
bd_agent.py` is the only pre-flight check currently used before committing
changes. Every flag has a default (see `--help`), so the script also runs
with no arguments for a smoke test.

## Architecture

`agent/bd_agent.py` is the entire application:

- `build_prompt()` renders one large task prompt from the CLI args
  (conference list, years, indication, phase, sender identity). This is
  where all of the agent's behavior is defined — there is no separate
  orchestration logic; the whole multi-step task (search → identify
  companies → find contacts → draft emails) is delegated to a single
  Claude API call with the model doing its own multi-turn tool use
  internally.
- `run()` sends that prompt to `claude-opus-5` via
  `client.messages.stream(...)` with the server-side `web_search_20260209`
  tool (`max_uses=30`), adaptive thinking, and `effort: "high"`. Text
  deltas are streamed to stdout as they arrive and also collected into the
  final report string.
- `main()` parses args and writes the collected report to the `--output`
  path (default `leads_report.md`) as-is — the script does no
  post-processing or validation of Claude's output.

There is no loop, no manual tool-call handling, and no retry logic: the
web search tool executes server-side, and the script is a thin
wrapper around one streaming request.

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

- No persistence/dedup across runs — every invocation is independent, so
  repeated runs can resurface the same leads.
- Contact-finding (step 3 in the prompt) shares the same 30-search budget
  as trial discovery (step 1) and commonly runs out before finding
  contacts for every lead — a known limitation, not a bug to silently
  paper over.
- Output is a flat Markdown file; there's no CRM/spreadsheet integration.

## Generated output is not tracked

`agent/leads_report.md` and `agent/run.log` (or whatever `--output` path is
used) are gitignored — they're per-run artifacts, not part of the
committed codebase. Don't unignore them; if a report needs to be preserved,
save it outside `agent/` or as an explicitly named file the user asks to
commit.
