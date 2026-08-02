"""Non-UI logic for gui.py, kept separate so it can be tested without a
display or Tkinter installed (Tkinter itself needs a real display/window
manager to test meaningfully, unlike this module's plain functions).
"""

import argparse
import json
import sys
from pathlib import Path

import bd_agent
import conference_dates
import email_drafts
import seen_leads


def app_dir() -> Path:
    """Directory to store persistent app files in.

    Under a PyInstaller one-file build, `__file__` resolves to the
    temporary `_MEIxxxxx` extraction folder that's deleted when the process
    exits — using it for a config path would silently lose saved settings
    on every single run. `sys.executable` is the actual .exe location in a
    frozen build; `__file__` is correct for a plain `python gui.py` run.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


CONFIG_PATH = app_dir() / "gui_config.json"
CONFERENCE_DATES_CACHE_PATH = app_dir() / "conference_dates_cache.json"


def upcoming_meetings_banner_text(within_days: int = 45) -> str:
    """Plain-text summary of upcoming meetings for the GUI banner, reading
    only the local cache — no network call, so this is safe to call on
    every launch. Returns "" if there's no cache yet or nothing's upcoming
    (the caller decides whether to show a "no cache yet" hint instead)."""
    upcoming = conference_dates.upcoming_meetings(CONFERENCE_DATES_CACHE_PATH, within_days=within_days)
    if not upcoming:
        return ""
    lines = []
    for m in upcoming:
        when = f"in {m['days_until']} day(s)" if m["days_until"] > 0 else "now"
        city = f", {m['city']}" if m.get("city") else ""
        lines.append(f"{m.get('name', 'Unknown meeting')} starts {when} ({m.get('start_date')}{city})")
    return "Upcoming: " + " | ".join(lines)


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


class QueueWriter:
    """File-like object so background-thread output can reach a Tkinter UI
    thread safely — Tkinter widgets may only be touched from the main
    thread, but a queue.Queue is safe to write to from anywhere."""

    def __init__(self, q):
        self.q = q

    def write(self, text: str) -> None:
        if text:
            self.q.put(("text", text))

    def flush(self) -> None:
        pass


def _outbox_fields(form: dict) -> dict:
    """Shared outbox-drafts fields, read the same way by all three
    build_*_args() functions below — one shared set of GUI fields applies
    to whichever search is run, mirroring how sender_name/hunter_api_key
    etc. are already shared."""
    return dict(
        outbox_email=form.get("outbox_email", "").strip() or None,
        outbox_app_password=form.get("outbox_app_password", "").strip() or None,
        outbox_imap_host=form.get("outbox_imap_host", "").strip() or None,
        outbox_imap_port=993,
        outbox_drafts_folder=form.get("outbox_drafts_folder", "").strip() or "Drafts",
    )


def build_conference_args(form: dict) -> argparse.Namespace:
    """Build the Namespace for the conference search (bd_agent.run_research()
    and friends) from a plain dict of GUI form values. Raises ValueError on
    bad numeric input so the caller can show a clean error dialog."""
    # Split on commas, not whitespace — several real conference names contain a
    # space themselves (e.g. "ASCO GU"), and whitespace-splitting silently
    # broke a single "ASCO GU" entry into two separate conferences ("ASCO",
    # "GU"), changing what was actually searched without any visible error.
    conference = [c.strip() for c in form.get("conference", "").split(",") if c.strip()] or ["ASCO GU"]
    year = [int(y) for y in form.get("year", "").split()] or [2025, 2026]
    hunter_min_confidence = int(form.get("hunter_min_confidence", "").strip() or 90)
    indication = form.get("indication", "").strip() or "bladder cancer"

    # Leaving the Output file field blank auto-names the report from the
    # search parameters (e.g. "bladder_cancer_ASCO_GU_2025_2026_leads_report.md")
    # instead of always overwriting the same generic "leads_report.md" —
    # typing an exact filename still overrides this.
    output_field = form.get("output", "").strip()
    output = output_field or str(app_dir() / (bd_agent.default_output_basename(indication, conference, year) + ".md"))

    return argparse.Namespace(
        conference=conference,
        year=year,
        indication=indication,
        phase=form.get("phase", "").strip() or "Phase II",
        sender_name=form.get("sender_name", "").strip() or "[Your Name]",
        sender_title=form.get("sender_title", "").strip() or "[Your Title]",
        sender_company=form.get("sender_company", "").strip() or "Elevate Imaging",
        output=output,
        hunter_api_key=form.get("hunter_api_key", "").strip() or None,
        hunter_min_confidence=hunter_min_confidence,
        hunter_delay_ms=4000,
        seen_file=str(app_dir() / "seen_leads.json"),
        no_dedup=False,
        **_outbox_fields(form),
    )


def build_trial_signals_args(form: dict) -> argparse.Namespace:
    """Build the Namespace for the trial-signals search (bd_agent.
    run_trial_signals_search()) from a plain dict of GUI form values. No
    conference/year/phase fields — those only apply to the conference
    search, a separate, independently-run pipeline (see CLAUDE.md)."""
    hunter_min_confidence = int(form.get("hunter_min_confidence", "").strip() or 90)
    indication = form.get("indication", "").strip() or "bladder cancer"

    output_field = form.get("output", "").strip()
    output = output_field or str(app_dir() / (bd_agent.default_trial_signals_basename(indication) + ".md"))

    return argparse.Namespace(
        indication=indication,
        sender_name=form.get("sender_name", "").strip() or "[Your Name]",
        sender_title=form.get("sender_title", "").strip() or "[Your Title]",
        sender_company=form.get("sender_company", "").strip() or "Elevate Imaging",
        output=output,
        hunter_api_key=form.get("hunter_api_key", "").strip() or None,
        hunter_min_confidence=hunter_min_confidence,
        hunter_delay_ms=4000,
        # Kept separate from the conference search's seen-file/state — these
        # are independent searches over disjoint signal types.
        seen_file=str(app_dir() / "trial_signals_seen_leads.json"),
        sponsor_history_file=str(app_dir() / "sponsor_phase_history.json"),
        no_dedup=False,
        no_ctgov=False,
        no_secedgar=False,
        no_prwire=False,
        **_outbox_fields(form),
    )


def build_phase_transition_args(form: dict) -> argparse.Namespace:
    """Build the Namespace for the phase-transition deep search (bd_agent.
    run_phase_transition_search()) from a plain dict of GUI form values.
    Like trial-signals, no conference/year/phase fields — but unlike
    trial-signals, this one costs API usage (Claude-driven)."""
    hunter_min_confidence = int(form.get("hunter_min_confidence", "").strip() or 90)
    indication = form.get("indication", "").strip() or "bladder cancer"
    days = int(form.get("phase_transition_days", "").strip() or 60)

    output_field = form.get("output", "").strip()
    output = output_field or str(app_dir() / (bd_agent.default_phase_transition_basename(indication) + ".md"))

    return argparse.Namespace(
        indication=indication,
        days=days,
        sender_name=form.get("sender_name", "").strip() or "[Your Name]",
        sender_title=form.get("sender_title", "").strip() or "[Your Title]",
        sender_company=form.get("sender_company", "").strip() or "Elevate Imaging",
        output=output,
        hunter_api_key=form.get("hunter_api_key", "").strip() or None,
        hunter_min_confidence=hunter_min_confidence,
        hunter_delay_ms=4000,
        # Kept separate from the other two searches' seen-files/state — all
        # three are independent searches over disjoint signal types.
        seen_file=str(app_dir() / "phase_transition_seen_leads.json"),
        # Same default filename as trial-signals — run first as a free
        # pre-check before the deep search (see run_phase_transition_pipeline()),
        # and the sponsor-tracking data is the same underlying fact store
        # regardless of which search triggers the check.
        sponsor_history_file=str(app_dir() / "sponsor_phase_history.json"),
        no_dedup=False,
        **_outbox_fields(form),
    )


def _finalize_and_write(
    preamble: str, leads: list, excluded: list, args: argparse.Namespace, raw_response=None, known_leads=None
) -> Path:
    """Shared dedup -> Hunter enrich -> render -> write tail for all three
    GUI pipelines below — mirrors bd_agent.py's `_finalize_and_write()` but
    returns the report path instead of just printing it (see gui.py/
    gui_logic.py's module-split rationale in CLAUDE.md). `known_leads` is
    only ever set by the phase-transition pipeline."""
    out_path = Path(args.output)

    if not leads and not excluded:
        if raw_response is not None:
            out_path.write_text(raw_response, encoding="utf-8")
            print("\n\n[Warning: could not parse structured lead data — saved the raw response instead]")
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
        return out_path

    seen_path = Path(args.seen_file)
    repeat_leads = []
    if args.no_dedup:
        new_leads = leads
    else:
        seen = seen_leads.load_seen(seen_path)
        new_leads, repeat_leads = seen_leads.split_new_and_repeats(leads, seen)
        seen_leads.save_seen(seen_path, seen)
        if repeat_leads:
            print(f"\n[{len(repeat_leads)} of {len(leads)} lead(s) already appeared in a previous report — skipping them.]")

    if args.hunter_api_key:
        print(f"\n\nLooking up {len(new_leads)} contact(s) via Hunter.io...")
    enriched = bd_agent.enrich_contacts(
        new_leads, args.hunter_api_key, args.hunter_min_confidence, args.hunter_delay_ms / 1000
    )

    failed = [(lead, contact) for lead, contact in enriched if contact and contact.source.startswith("error")]
    if failed:
        print(
            f"\n[Warning: Hunter.io lookup FAILED for {len(failed)} of {len(new_leads)} "
            f"companies. First error: {failed[0][1].source}]"
        )

    if args.outbox_email:
        print(f"\nCreating draft emails in {args.outbox_email}...")
        successes, draft_failures = email_drafts.push_drafts_for_report(enriched, args, bd_agent.draft_email)
        print(f"[{successes} draft(s) created in {args.outbox_email} — sitting unsent, review before sending]")
        if draft_failures:
            print(f"\n[Warning: {len(draft_failures)} draft(s) FAILED to create — first error: {draft_failures[0][1]}]")

    report = bd_agent.render_report(
        preamble,
        enriched,
        excluded,
        args,
        hunter_enabled=bool(args.hunter_api_key),
        repeat_leads=repeat_leads,
        known_leads=known_leads,
    )
    out_path.write_text(report, encoding="utf-8")

    csv_path = out_path.with_suffix(".csv")
    csv_path.write_text(bd_agent.render_csv(enriched, args), encoding="utf-8", newline="")
    print(f"\nSaved CSV to: {csv_path.resolve()}")

    return out_path


def run_conference_pipeline(args: argparse.Namespace) -> Path:
    """Same steps as bd_agent._run_conferences_cli(), but returns the report
    path instead of just printing it, and takes an already-built Namespace
    (no argv)."""
    raw_response = bd_agent.run_research(args)
    preamble, leads, excluded = bd_agent.parse_research_output(raw_response)
    return _finalize_and_write(preamble, leads, excluded, args, raw_response=raw_response)


def run_trial_signals_pipeline(args: argparse.Namespace) -> Path:
    """Same steps as bd_agent._run_trial_signals_cli(), but returns the
    report path instead of just printing it."""
    leads = bd_agent.run_trial_signals_search(args)
    return _finalize_and_write("", leads, [], args, raw_response=None)


def run_phase_transition_pipeline(args: argparse.Namespace) -> Path:
    """Same steps as bd_agent._run_phase_transitions_cli(), but returns the
    report path instead of just printing it. Runs the free trial-signals
    check first and passes its findings to the deep search as context, so
    Claude doesn't spend paid search budget rediscovering them — see
    bd_agent._run_phase_transitions_cli()."""
    args.no_ctgov = False
    args.no_secedgar = False
    args.no_prwire = False
    known_leads = bd_agent.run_trial_signals_search(args)

    raw_response = bd_agent.run_phase_transition_search(args, known_leads)
    preamble, leads, excluded = bd_agent.parse_research_output(raw_response)
    return _finalize_and_write(preamble, leads, excluded, args, raw_response=raw_response, known_leads=known_leads)
