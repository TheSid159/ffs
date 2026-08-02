#!/usr/bin/env python3
"""
Desktop GUI for bd_agent.py — no terminal, no environment variables.

Enter your API keys once; they're saved locally in gui_config.json (next to
this file, gitignored) and pre-filled every time after. Fill in the search
fields, then pick one of two independent searches:

- "Search Conferences" — Claude-driven web research (costs real API usage,
  needs an Anthropic API key).
- "Search Trial Signals" — free, deterministic checks against
  ClinicalTrials.gov, SEC EDGAR, and press-release RSS feeds (no LLM, no
  Anthropic key needed, safe to run as often as you like).

Each writes its own report and opens automatically when it's done.

Usage: python gui.py   (or double-click run_gui.bat)
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import conference_dates
from gui_logic import (
    CONFERENCE_DATES_CACHE_PATH,
    QueueWriter,
    build_conference_args,
    build_trial_signals_args,
    load_config,
    run_conference_pipeline,
    run_trial_signals_pipeline,
    save_config,
    upcoming_meetings_banner_text,
)


class App(tk.Tk):
    FIELDS = [
        # (config_key, label, default, width)
        ("conference", "Conference(s) (comma-separated) — Conference search only:", "ASCO GU", 40),
        ("year", "Year(s) (space-separated) — Conference search only:", "2025 2026", 40),
        ("indication", "Indication:", "bladder cancer", 40),
        ("phase", "Phase — Conference search only:", "Phase II", 40),
        ("sender_name", "Your name:", "Dr. Darren Brennan", 40),
        ("sender_title", "Your title:", "Medical Director", 40),
        ("sender_company", "Company:", "Elevate Imaging", 40),
        ("hunter_min_confidence", "Hunter min confidence (0-100):", "90", 10),
        ("output", "Output file (blank = auto-name from search below):", "", 40),
    ]

    def __init__(self):
        super().__init__()
        self.title("Elevate Imaging — BD Lead Finder")
        self.geometry("780x760")
        self.log_queue: "queue.Queue" = queue.Queue()
        self.config_data = load_config()
        self.conference_report_path = None
        self.trial_signals_report_path = None
        self.field_vars = {}
        self._build_ui()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        calendar_frame = ttk.LabelFrame(self, text="Conference Calendar")
        calendar_frame.pack(fill="x", **pad)
        self.calendar_var = tk.StringVar(value=self._initial_banner_text())
        ttk.Label(calendar_frame, textvariable=self.calendar_var, wraplength=580, justify="left").pack(
            side="left", fill="x", expand=True, **pad
        )
        self.refresh_dates_button = ttk.Button(
            calendar_frame, text="Refresh Dates", command=self.on_refresh_dates
        )
        self.refresh_dates_button.pack(side="right", **pad)

        keys_frame = ttk.LabelFrame(self, text="API Keys (saved locally, entered once)")
        keys_frame.pack(fill="x", **pad)

        ttk.Label(keys_frame, text="Anthropic API Key (Conference search only):").grid(row=0, column=0, sticky="w", **pad)
        self.anthropic_key_var = tk.StringVar(value=self.config_data.get("anthropic_api_key", ""))
        ttk.Entry(keys_frame, textvariable=self.anthropic_key_var, show="*", width=60).grid(row=0, column=1, **pad)

        ttk.Label(keys_frame, text="Hunter.io API Key (optional, both searches):").grid(row=1, column=0, sticky="w", **pad)
        self.hunter_key_var = tk.StringVar(value=self.config_data.get("hunter_api_key", ""))
        ttk.Entry(keys_frame, textvariable=self.hunter_key_var, show="*", width=60).grid(row=1, column=1, **pad)

        params_frame = ttk.LabelFrame(self, text="Search parameters")
        params_frame.pack(fill="x", **pad)

        for row, (key, label, default, width) in enumerate(self.FIELDS):
            ttk.Label(params_frame, text=label).grid(row=row, column=0, sticky="w", **pad)
            var = tk.StringVar(value=str(self.config_data.get(key, default)))
            ttk.Entry(params_frame, textvariable=var, width=width).grid(row=row, column=1, sticky="w", **pad)
            self.field_vars[key] = var

        conf_frame = ttk.LabelFrame(self, text="Conference search (Claude web research — costs API usage)")
        conf_frame.pack(fill="x", **pad)
        self.conf_run_button = ttk.Button(conf_frame, text="Search Conferences", command=self.on_run_conferences)
        self.conf_run_button.pack(side="left", **pad)
        self.conf_open_button = ttk.Button(
            conf_frame, text="Open Conference Report", command=self.open_conference_report, state="disabled"
        )
        self.conf_open_button.pack(side="left", **pad)

        trial_frame = ttk.LabelFrame(self, text="Trial signals search (ClinicalTrials.gov + SEC EDGAR + press releases — free, no LLM)")
        trial_frame.pack(fill="x", **pad)
        self.trial_run_button = ttk.Button(trial_frame, text="Search Trial Signals", command=self.on_run_trial_signals)
        self.trial_run_button.pack(side="left", **pad)
        self.trial_open_button = ttk.Button(
            trial_frame, text="Open Trial Signals Report", command=self.open_trial_signals_report, state="disabled"
        )
        self.trial_open_button.pack(side="left", **pad)

        self.new_search_button = ttk.Button(self, text="New Search", command=self.on_new_search)
        self.new_search_button.pack(anchor="w", **pad)

        log_frame = ttk.LabelFrame(self, text="Progress")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def _initial_banner_text(self) -> str:
        text = upcoming_meetings_banner_text()
        if text:
            return text
        if CONFERENCE_DATES_CACHE_PATH.exists():
            return "No major meetings in the next 45 days (based on the last refresh)."
        return "Click \"Refresh Dates\" to check for upcoming meetings (uses a small amount of API budget)."

    def _log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "text":
                    self._log(payload)
                elif kind == "conference_done":
                    self.conference_report_path = payload
                    self.conf_open_button.configure(state="normal")
                    self._set_run_buttons_state("normal")
                elif kind == "trial_signals_done":
                    self.trial_signals_report_path = payload
                    self.trial_open_button.configure(state="normal")
                    self._set_run_buttons_state("normal")
                elif kind == "error":
                    self._set_run_buttons_state("normal")
                elif kind == "banner":
                    self.calendar_var.set(payload)
                    self.refresh_dates_button.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _set_run_buttons_state(self, state: str) -> None:
        # Both searches share one log window and can't usefully run at the
        # same time (Tkinter widgets are only safe to touch from the main
        # thread, and there's only one background-thread slot in use at
        # once) — disable both while either is running, re-enable both when
        # it finishes, regardless of which one was clicked.
        self.conf_run_button.configure(state=state)
        self.trial_run_button.configure(state=state)

    def _current_form(self) -> dict:
        form = {key: var.get() for key, var in self.field_vars.items()}
        form["anthropic_api_key"] = self.anthropic_key_var.get()
        form["hunter_api_key"] = self.hunter_key_var.get()
        return form

    def _save_form(self, form: dict) -> None:
        self.config_data.update({k: v.strip() for k, v in form.items()})
        save_config(self.config_data)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def on_run_conferences(self) -> None:
        form = self._current_form()
        if not form["anthropic_api_key"].strip():
            messagebox.showerror("Missing key", "The conference search needs your Anthropic API key.")
            return

        self._save_form(form)
        try:
            args = build_conference_args(form)
        except ValueError:
            messagebox.showerror("Invalid input", "Year(s) and Hunter min confidence must be numbers.")
            return

        os.environ["ANTHROPIC_API_KEY"] = form["anthropic_api_key"].strip()

        self._set_run_buttons_state("disabled")
        self.conf_open_button.configure(state="disabled")
        self._clear_log()

        threading.Thread(target=self._run_conferences_in_background, args=(args,), daemon=True).start()

    def _run_conferences_in_background(self, args) -> None:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = QueueWriter(self.log_queue)
        sys.stdout = writer
        sys.stderr = writer
        try:
            report_path = run_conference_pipeline(args)
            self.log_queue.put(("text", f"\n\nDone! Conference report saved to: {report_path.resolve()}\n"))
            self.log_queue.put(("conference_done", report_path))
        except PermissionError as exc:
            self._report_permission_error(exc, args)
        except Exception as exc:  # surface any failure in the window instead of a silent crash
            self.log_queue.put(("text", f"\n\nError: {exc}\n"))
            self.log_queue.put(("error", str(exc)))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def on_run_trial_signals(self) -> None:
        # Deliberately no Anthropic-key check here — this search is free and
        # deterministic (ClinicalTrials.gov/SEC EDGAR/press-release RSS), no
        # LLM call involved, so it doesn't need one.
        form = self._current_form()
        self._save_form(form)
        try:
            args = build_trial_signals_args(form)
        except ValueError:
            messagebox.showerror("Invalid input", "Hunter min confidence must be a number.")
            return

        self._set_run_buttons_state("disabled")
        self.trial_open_button.configure(state="disabled")
        self._clear_log()

        threading.Thread(target=self._run_trial_signals_in_background, args=(args,), daemon=True).start()

    def _run_trial_signals_in_background(self, args) -> None:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = QueueWriter(self.log_queue)
        sys.stdout = writer
        sys.stderr = writer
        try:
            report_path = run_trial_signals_pipeline(args)
            self.log_queue.put(("text", f"\n\nDone! Trial signals report saved to: {report_path.resolve()}\n"))
            self.log_queue.put(("trial_signals_done", report_path))
        except PermissionError as exc:
            self._report_permission_error(exc, args)
        except Exception as exc:  # surface any failure in the window instead of a silent crash
            self.log_queue.put(("text", f"\n\nError: {exc}\n"))
            self.log_queue.put(("error", str(exc)))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def _report_permission_error(self, exc: PermissionError, args) -> None:
        # The most common real-world cause: the previous report is still
        # open in Word/Notepad/Excel, which locks the file on Windows.
        msg = (
            f"Could not save the report — '{exc.filename or args.output}' is open in "
            "another program (e.g. Word, Notepad, Excel) or is set to read-only. "
            "Close it (or right-click it, Properties, and untick Read-only), then search again."
        )
        self.log_queue.put(("text", f"\n\nError: {msg}\n"))
        self.log_queue.put(("error", msg))

    def on_refresh_dates(self) -> None:
        """Look up actual confirmed conference dates via a small, separate
        API call (not part of either search above) and cache them locally.
        Only runs when clicked — never automatically — since it's a real,
        billed API call each time."""
        anthropic_key = self.anthropic_key_var.get().strip()
        if not anthropic_key:
            messagebox.showerror("Missing key", "Please enter your Anthropic API key first.")
            return
        os.environ["ANTHROPIC_API_KEY"] = anthropic_key

        self.refresh_dates_button.configure(state="disabled")
        self.calendar_var.set("Looking up confirmed conference dates...")
        threading.Thread(target=self._refresh_dates_in_background, daemon=True).start()

    def _refresh_dates_in_background(self) -> None:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = QueueWriter(self.log_queue)
        sys.stdout = writer
        sys.stderr = writer
        try:
            conference_dates.refresh(CONFERENCE_DATES_CACHE_PATH)
            banner = upcoming_meetings_banner_text() or "No major meetings in the next 45 days (just refreshed)."
            self.log_queue.put(("banner", banner))
        except Exception as exc:
            self.log_queue.put(("text", f"\n\n[Conference date refresh failed: {exc}]\n"))
            self.log_queue.put(("banner", f"Refresh failed: {exc}"))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def on_new_search(self) -> None:
        """Reset every search-parameter field to its default and clear the
        progress log/report state, so the window looks like a fresh launch
        without touching the saved API keys (those stay filled in)."""
        for key, _label, default, _width in self.FIELDS:
            self.field_vars[key].set(default)

        self._clear_log()

        self.conference_report_path = None
        self.trial_signals_report_path = None
        self.conf_open_button.configure(state="disabled")
        self.trial_open_button.configure(state="disabled")

    def open_conference_report(self) -> None:
        if self.conference_report_path and self.conference_report_path.exists():
            os.startfile(self.conference_report_path)  # Windows-only — matches this app's target platform

    def open_trial_signals_report(self) -> None:
        if self.trial_signals_report_path and self.trial_signals_report_path.exists():
            os.startfile(self.trial_signals_report_path)  # Windows-only — matches this app's target platform


if __name__ == "__main__":
    App().mainloop()
