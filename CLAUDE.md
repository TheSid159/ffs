# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A single-purpose business development tool for an imaging CRO ("Elevate
Imaging"). It uses the Claude API with the web search tool to find recent
clinical trials with positive results at named oncology/urology
conferences, looks up a verified CEO/CMO contact for each company via
Hunter.io, and drafts an outreach email per lead from a fixed template.
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

Five modules, no application framework:

- **`agent/bd_agent.py`** — the pipeline, in four stages:
  1. `build_prompt()` renders one research prompt from the CLI args
     (conference list, years, indication, phase). It asks Claude to
     research trials via web search and return a short prose preamble
     followed by **one fenced ` ```json ` block** containing structured
     lead data (company, domain, trial, abstract link, and — only if
     stumbled upon incidentally — a contact name/title). Claude is
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
     the structured lead data plus whatever contact Hunter confirmed.
     `render_report()` assembles the final Markdown.

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

### Hunter.io requires a browser-like User-Agent

`hunter_contacts._get()` sets an explicit `User-Agent` header on every
request. This is load-bearing, not cosmetic: Hunter's front end sits behind
Cloudflare, and Python's default `Python-urllib/x.y` User-Agent gets
fingerprinted and blocked (Cloudflare error 1010) before the request ever
reaches Hunter's actual API — this was misread as a plan/rate-limit problem
before the real cause was found. Don't strip this header when touching
`_get()`.

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
