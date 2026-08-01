"""Non-UI logic for gui.py, kept separate so it can be tested without a
display or Tkinter installed (Tkinter itself needs a real display/window
manager to test meaningfully, unlike this module's plain functions).
"""

import argparse
import json
from pathlib import Path

import bd_agent
import seen_leads

CONFIG_PATH = Path(__file__).parent / "gui_config.json"


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
    conference = form.get("conference", "").split() or ["ASCO GU"]
    year = [int(y) for y in form.get("year", "").split()] or [2025, 2026]
    hunter_min_confidence = int(form.get("hunter_min_confidence", "").strip() or 90)

    return argparse.Namespace(
        conference=conference,
        year=year,
        indication=form.get("indication", "").strip() or "bladder cancer",
        phase=form.get("phase", "").strip() or "Phase II",
        sender_name=form.get("sender_name", "").strip() or "[Your Name]",
        sender_title=form.get("sender_title", "").strip() or "[Your Title]",
        sender_company=form.get("sender_company", "").strip() or "Elevate Imaging",
        output=form.get("output", "").strip() or "leads_report.md",
        hunter_api_key=form.get("hunter_api_key", "").strip() or None,
        hunter_min_confidence=hunter_min_confidence,
        hunter_delay_ms=4000,
        seen_file="seen_leads.json",
        no_dedup=False,
    )


def run_pipeline(args: argparse.Namespace) -> Path:
    """Same steps as bd_agent.main(), but returns the report path instead
    of just printing it, and takes an already-built Namespace (no argv)."""
    raw_response = bd_agent.run_research(args)
    preamble, leads, excluded = bd_agent.parse_research_output(raw_response)

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
    return out_path
