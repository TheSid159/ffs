"""Non-UI logic for gui.py, kept separate so it can be tested without a
display or Tkinter installed (Tkinter itself needs a real display/window
manager to test meaningfully, unlike this module's plain functions).
"""

import argparse
import json
import sys
from pathlib import Path

import bd_agent
import clinicaltrials_gov
import conference_dates
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


def build_args(form: dict) -> argparse.Namespace:
    """Build the Namespace bd_agent's pipeline functions expect from a plain
    dict of form values (as collected from GUI fields). Raises ValueError on
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
        no_ctgov=False,
    )


def run_pipeline(args: argparse.Namespace) -> Path:
    """Same steps as bd_agent.main(), but returns the report path instead
    of just printing it, and takes an already-built Namespace (no argv)."""
    raw_response = bd_agent.run_research(args)
    preamble, leads, excluded = bd_agent.parse_research_output(raw_response)

    if not args.no_ctgov:
        print("\nChecking ClinicalTrials.gov for Phase 1 trials nearing/past primary completion...")
        ctgov_leads = clinicaltrials_gov.find_leads(args.indication)
        if ctgov_leads:
            print(f"[Found {len(ctgov_leads)} lead(s) via ClinicalTrials.gov]")
        leads = leads + ctgov_leads

    out_path = Path(args.output)

    if not leads and not excluded:
        out_path.write_text(raw_response, encoding="utf-8")
        print("\n\n[Warning: could not parse structured lead data — saved the raw response instead]")
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

    report = bd_agent.render_report(
        preamble, enriched, excluded, args, hunter_enabled=bool(args.hunter_api_key), repeat_leads=repeat_leads
    )
    out_path.write_text(report, encoding="utf-8")

    csv_path = out_path.with_suffix(".csv")
    csv_path.write_text(bd_agent.render_csv(enriched, args), encoding="utf-8", newline="")
    print(f"\nSaved CSV to: {csv_path.resolve()}")

    return out_path
