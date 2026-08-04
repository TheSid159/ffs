#!/usr/bin/env python3
"""
Desktop GUI for bd_agent.py — no terminal, no environment variables.

Enter your API keys once; they're saved locally in gui_config.json (next to
this file, gitignored) and pre-filled every time after. Fill in the search
fields, then pick one of four independent searches:

- "Search Conferences" — Claude-driven web research across the two
  conference-anchored signal types (trial results, conference highlights)
  at named conferences (costs real API usage, needs an Anthropic API key).
- "Search Trial Signals" — free, deterministic checks against
  ClinicalTrials.gov, SEC EDGAR, and press-release RSS feeds (no LLM, no
  Anthropic key needed, safe to run as often as you like).
- "Search Phase Transitions" — Claude-driven deep web search (LinkedIn,
  biotech news, blogs, hospital/university press) focused only on Phase
  1-to-Phase 2 transition signals (costs real API usage, needs an
  Anthropic API key).
- "Search Signal Sweep" — Claude-driven web research across the other nine
  BD signal types (funding, leadership changes, new registrations,
  regulatory designations/milestones, trial expansions, protocol
  amendments, hiring signals, vendor-switch signals) — not tied to any
  conference, meant to run on its own regular cadence (e.g. weekly; costs
  real API usage, needs an Anthropic API key).

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
import hubspot_sync
from bd_agent import validate_outbox_args
from gui_logic import (
    CONFERENCE_DATES_CACHE_PATH,
    QueueWriter,
    build_conference_args,
    build_phase_transition_args,
    build_signal_sweep_args,
    build_trial_signals_args,
    load_config,
    run_conference_pipeline,
    run_phase_transition_pipeline,
    run_signal_sweep_pipeline,
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
        ("phase_transition_days", "Search window in days — Phase Transitions search only:", "60", 10),
        ("signal_sweep_days", "Search window in days — Signal Sweep search only:", "30", 10),
        ("output", "Output file (blank = auto-name from search below):", "", 40),
    ]

    def __init__(self):
        super().__init__()
        self.title("Elevate Imaging — BD Lead Finder")
        self.geometry("820x820")
        self.minsize(700, 500)
        self.log_queue: "queue.Queue" = queue.Queue()
        self.config_data = load_config()
        self.conference_report_path = None
        self.trial_signals_report_path = None
        self.phase_transition_report_path = None
        self.signal_sweep_report_path = None
        self.field_vars = {}
        self.settings_window = None
        # Outbox/HubSpot vars are created up front (not lazily inside the
        # Settings dialog) because _current_form() needs to read them
        # every run regardless of whether that dialog has ever been opened
        # this session.
        self.outbox_email_var = tk.StringVar(value=self.config_data.get("outbox_email", ""))
        self.outbox_app_password_var = tk.StringVar(value=self.config_data.get("outbox_app_password", ""))
        self.outbox_imap_host_var = tk.StringVar(value=self.config_data.get("outbox_imap_host", ""))
        self.outbox_drafts_folder_var = tk.StringVar(value=self.config_data.get("outbox_drafts_folder", "Drafts"))
        self.hubspot_api_key_var = tk.StringVar(value=self.config_data.get("hubspot_api_key", ""))
        self.hubspot_outreach_property_var = tk.StringVar(
            value=self.config_data.get("hubspot_outreach_property", hubspot_sync.DEFAULT_OUTREACH_PROPERTY)
        )
        self.hubspot_no_call_property_var = tk.StringVar(
            value=self.config_data.get("hubspot_no_call_property", hubspot_sync.DEFAULT_NO_COLD_CALL_PROPERTY)
        )
        self._build_ui()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # Everything except the Progress log lives in a scrollable area with
        # a capped height, so no matter how many fields/sections get added
        # above it, the Progress log below always keeps its own guaranteed
        # visible space instead of being squeezed off the bottom of a
        # fixed-size window (the original layout's problem — see CLAUDE.md).
        top_wrapper = ttk.Frame(self)
        top_wrapper.pack(side="top", fill="x")

        top_canvas = tk.Canvas(top_wrapper, height=420, highlightthickness=0)
        top_scrollbar = ttk.Scrollbar(top_wrapper, orient="vertical", command=top_canvas.yview)
        top_canvas.configure(yscrollcommand=top_scrollbar.set)
        top_canvas.pack(side="left", fill="both", expand=True)
        top_scrollbar.pack(side="right", fill="y")

        scroll_frame = ttk.Frame(top_canvas)
        canvas_window = top_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        def _on_scroll_frame_configure(_event):
            top_canvas.configure(scrollregion=top_canvas.bbox("all"))

        def _on_canvas_configure(event):
            top_canvas.itemconfig(canvas_window, width=event.width)

        scroll_frame.bind("<Configure>", _on_scroll_frame_configure)
        top_canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            top_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        top_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        calendar_frame = ttk.LabelFrame(scroll_frame, text="Conference Calendar")
        calendar_frame.pack(fill="x", **pad)
        self.calendar_var = tk.StringVar(value=self._initial_banner_text())
        ttk.Label(calendar_frame, textvariable=self.calendar_var, wraplength=580, justify="left").pack(
            side="left", fill="x", expand=True, **pad
        )
        self.refresh_dates_button = ttk.Button(
            calendar_frame, text="Refresh Dates", command=self.on_refresh_dates
        )
        self.refresh_dates_button.pack(side="right", **pad)

        keys_frame = ttk.LabelFrame(scroll_frame, text="API Keys (saved locally, entered once)")
        keys_frame.pack(fill="x", **pad)

        ttk.Label(keys_frame, text="Anthropic API Key (Conferences + Phase Transitions + Signal Sweep searches):").grid(
            row=0, column=0, sticky="w", **pad
        )
        self.anthropic_key_var = tk.StringVar(value=self.config_data.get("anthropic_api_key", ""))
        ttk.Entry(keys_frame, textvariable=self.anthropic_key_var, show="*", width=60).grid(row=0, column=1, **pad)

        ttk.Label(keys_frame, text="Hunter.io API Key (optional, all searches):").grid(row=1, column=0, sticky="w", **pad)
        self.hunter_key_var = tk.StringVar(value=self.config_data.get("hunter_api_key", ""))
        ttk.Entry(keys_frame, textvariable=self.hunter_key_var, show="*", width=60).grid(row=1, column=1, **pad)

        ttk.Button(keys_frame, text="Outbox / HubSpot Settings...", command=self.open_settings_dialog).grid(
            row=2, column=0, columnspan=2, sticky="w", **pad
        )

        params_frame = ttk.LabelFrame(scroll_frame, text="Search parameters")
        params_frame.pack(fill="x", **pad)

        for row, (key, label, default, width) in enumerate(self.FIELDS):
            ttk.Label(params_frame, text=label).grid(row=row, column=0, sticky="w", **pad)
            var = tk.StringVar(value=str(self.config_data.get(key, default)))
            ttk.Entry(params_frame, textvariable=var, width=width).grid(row=row, column=1, sticky="w", **pad)
            self.field_vars[key] = var

        conf_frame = ttk.LabelFrame(scroll_frame, text="Conference search (Claude web research — costs API usage)")
        conf_frame.pack(fill="x", **pad)
        self.conf_run_button = ttk.Button(conf_frame, text="Search Conferences", command=self.on_run_conferences)
        self.conf_run_button.pack(side="left", **pad)
        self.conf_open_button = ttk.Button(
            conf_frame, text="Open Conference Report", command=self.open_conference_report, state="disabled"
        )
        self.conf_open_button.pack(side="left", **pad)

        trial_frame = ttk.LabelFrame(scroll_frame, text="Trial signals search (ClinicalTrials.gov + SEC EDGAR + press releases — free, no LLM)")
        trial_frame.pack(fill="x", **pad)
        self.trial_run_button = ttk.Button(trial_frame, text="Search Trial Signals", command=self.on_run_trial_signals)
        self.trial_run_button.pack(side="left", **pad)
        self.trial_open_button = ttk.Button(
            trial_frame, text="Open Trial Signals Report", command=self.open_trial_signals_report, state="disabled"
        )
        self.trial_open_button.pack(side="left", **pad)

        phase_frame = ttk.LabelFrame(
            scroll_frame, text="Phase transitions search (deep web search — LinkedIn, biotech news, blogs — costs API usage)"
        )
        phase_frame.pack(fill="x", **pad)
        self.phase_run_button = ttk.Button(
            phase_frame, text="Search Phase Transitions", command=self.on_run_phase_transitions
        )
        self.phase_run_button.pack(side="left", **pad)
        self.phase_open_button = ttk.Button(
            phase_frame, text="Open Phase Transition Report", command=self.open_phase_transition_report, state="disabled"
        )
        self.phase_open_button.pack(side="left", **pad)

        sweep_frame = ttk.LabelFrame(
            scroll_frame,
            text="Signal sweep search (funding, leadership, regulatory, and other non-conference "
            "signals — costs API usage, run this one regularly)",
        )
        sweep_frame.pack(fill="x", **pad)
        self.sweep_run_button = ttk.Button(sweep_frame, text="Search Signal Sweep", command=self.on_run_signal_sweep)
        self.sweep_run_button.pack(side="left", **pad)
        self.sweep_open_button = ttk.Button(
            sweep_frame, text="Open Signal Sweep Report", command=self.open_signal_sweep_report, state="disabled"
        )
        self.sweep_open_button.pack(side="left", **pad)

        self.new_search_button = ttk.Button(scroll_frame, text="New Search", command=self.on_new_search)
        self.new_search_button.pack(anchor="w", **pad)

        # Deliberately packed with side="bottom" after the (side="top")
        # top_wrapper above, and fill="both"/expand=True — this is what
        # guarantees the log gets all remaining vertical space instead of
        # being squeezed to nothing by however much content is above it.
        log_frame = ttk.LabelFrame(self, text="Progress")
        log_frame.pack(side="bottom", fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=14, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def open_settings_dialog(self) -> None:
        """Outbox and HubSpot are both optional, occasional-setup fields —
        moved out of the main dashboard into their own window so the
        common case (just running a search) isn't cluttered with fields
        most runs never touch. Re-focuses the existing window instead of
        opening a second one if it's already open."""
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        pad = {"padx": 6, "pady": 4}
        win = tk.Toplevel(self)
        win.title("Outbox / HubSpot Settings")
        win.geometry("640x420")
        self.settings_window = win

        outbox_frame = ttk.LabelFrame(
            win, text="Outbox (optional — creates real, unsent draft emails in this mailbox instead of just the report)"
        )
        outbox_frame.pack(fill="x", **pad)

        ttk.Label(outbox_frame, text="Outbox email address:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(outbox_frame, textvariable=self.outbox_email_var, width=50).grid(row=0, column=1, **pad)

        ttk.Label(outbox_frame, text="Outbox app password:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(outbox_frame, textvariable=self.outbox_app_password_var, show="*", width=50).grid(row=1, column=1, **pad)

        ttk.Label(outbox_frame, text="Outbox IMAP host (e.g. imap.gmail.com):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(outbox_frame, textvariable=self.outbox_imap_host_var, width=50).grid(row=2, column=1, **pad)

        ttk.Label(outbox_frame, text='Outbox Drafts folder name (default "Drafts", Gmail needs "[Gmail]/Drafts"):').grid(
            row=3, column=0, sticky="w", **pad
        )
        ttk.Entry(outbox_frame, textvariable=self.outbox_drafts_folder_var, width=50).grid(row=3, column=1, **pad)

        hubspot_frame = ttk.LabelFrame(
            win,
            text="HubSpot (optional — syncs each new lead as a Contact + Company, and skips companies "
            "already marked Declined)",
        )
        hubspot_frame.pack(fill="x", **pad)

        ttk.Label(hubspot_frame, text="HubSpot Private App access token:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(hubspot_frame, textvariable=self.hubspot_api_key_var, show="*", width=50).grid(row=0, column=1, **pad)

        ttk.Label(hubspot_frame, text='"Outreach Status" property internal name (Contact + Company):').grid(
            row=1, column=0, sticky="w", **pad
        )
        ttk.Entry(hubspot_frame, textvariable=self.hubspot_outreach_property_var, width=50).grid(row=1, column=1, **pad)

        ttk.Label(hubspot_frame, text='"Channel Methods Do Not Call" property internal name (Company only):').grid(
            row=2, column=0, sticky="w", **pad
        )
        ttk.Entry(hubspot_frame, textvariable=self.hubspot_no_call_property_var, width=50).grid(row=2, column=1, **pad)

        def _save_and_close():
            self._save_form(self._current_form())
            win.destroy()

        ttk.Button(win, text="Save & Close", command=_save_and_close).pack(anchor="e", **pad)

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
                elif kind == "phase_transition_done":
                    self.phase_transition_report_path = payload
                    self.phase_open_button.configure(state="normal")
                    self._set_run_buttons_state("normal")
                elif kind == "signal_sweep_done":
                    self.signal_sweep_report_path = payload
                    self.sweep_open_button.configure(state="normal")
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
        # All four searches share one log window and can't usefully run at
        # the same time (Tkinter widgets are only safe to touch from the
        # main thread, and there's only one background-thread slot in use
        # at once) — disable all four while any one is running, re-enable
        # all four when it finishes, regardless of which one was clicked.
        self.conf_run_button.configure(state=state)
        self.trial_run_button.configure(state=state)
        self.phase_run_button.configure(state=state)
        self.sweep_run_button.configure(state=state)

    def _current_form(self) -> dict:
        form = {key: var.get() for key, var in self.field_vars.items()}
        form["anthropic_api_key"] = self.anthropic_key_var.get()
        form["hunter_api_key"] = self.hunter_key_var.get()
        form["outbox_email"] = self.outbox_email_var.get()
        form["outbox_app_password"] = self.outbox_app_password_var.get()
        form["outbox_imap_host"] = self.outbox_imap_host_var.get()
        form["outbox_drafts_folder"] = self.outbox_drafts_folder_var.get()
        form["hubspot_api_key"] = self.hubspot_api_key_var.get()
        form["hubspot_outreach_property"] = self.hubspot_outreach_property_var.get()
        form["hubspot_no_call_property"] = self.hubspot_no_call_property_var.get()
        return form

    def _save_form(self, form: dict) -> None:
        self.config_data.update({k: v.strip() for k, v in form.items()})
        save_config(self.config_data)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _outbox_ok(self, args) -> bool:
        """Shared by all four on_run_* handlers below — checks the outbox
        fields are either fully filled in or fully blank before starting a
        background search, so an incomplete outbox config fails fast with a
        clear dialog instead of partway through pushing drafts for a whole
        report."""
        error = validate_outbox_args(args)
        if error:
            messagebox.showerror("Outbox not fully configured", error)
            return False
        return True

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
        if not self._outbox_ok(args):
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
        if not self._outbox_ok(args):
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

    def on_run_phase_transitions(self) -> None:
        form = self._current_form()
        if not form["anthropic_api_key"].strip():
            messagebox.showerror("Missing key", "The phase transitions search needs your Anthropic API key.")
            return

        self._save_form(form)
        try:
            args = build_phase_transition_args(form)
        except ValueError:
            messagebox.showerror("Invalid input", "Search window (days) and Hunter min confidence must be numbers.")
            return
        if not self._outbox_ok(args):
            return

        os.environ["ANTHROPIC_API_KEY"] = form["anthropic_api_key"].strip()

        self._set_run_buttons_state("disabled")
        self.phase_open_button.configure(state="disabled")
        self._clear_log()

        threading.Thread(target=self._run_phase_transitions_in_background, args=(args,), daemon=True).start()

    def _run_phase_transitions_in_background(self, args) -> None:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = QueueWriter(self.log_queue)
        sys.stdout = writer
        sys.stderr = writer
        try:
            report_path = run_phase_transition_pipeline(args)
            self.log_queue.put(("text", f"\n\nDone! Phase transition report saved to: {report_path.resolve()}\n"))
            self.log_queue.put(("phase_transition_done", report_path))
        except PermissionError as exc:
            self._report_permission_error(exc, args)
        except Exception as exc:  # surface any failure in the window instead of a silent crash
            self.log_queue.put(("text", f"\n\nError: {exc}\n"))
            self.log_queue.put(("error", str(exc)))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def on_run_signal_sweep(self) -> None:
        form = self._current_form()
        if not form["anthropic_api_key"].strip():
            messagebox.showerror("Missing key", "The signal sweep search needs your Anthropic API key.")
            return

        self._save_form(form)
        try:
            args = build_signal_sweep_args(form)
        except ValueError:
            messagebox.showerror("Invalid input", "Search window (days) and Hunter min confidence must be numbers.")
            return
        if not self._outbox_ok(args):
            return

        os.environ["ANTHROPIC_API_KEY"] = form["anthropic_api_key"].strip()

        self._set_run_buttons_state("disabled")
        self.sweep_open_button.configure(state="disabled")
        self._clear_log()

        threading.Thread(target=self._run_signal_sweep_in_background, args=(args,), daemon=True).start()

    def _run_signal_sweep_in_background(self, args) -> None:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = QueueWriter(self.log_queue)
        sys.stdout = writer
        sys.stderr = writer
        try:
            report_path = run_signal_sweep_pipeline(args)
            self.log_queue.put(("text", f"\n\nDone! Signal sweep report saved to: {report_path.resolve()}\n"))
            self.log_queue.put(("signal_sweep_done", report_path))
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
        self.phase_transition_report_path = None
        self.signal_sweep_report_path = None
        self.conf_open_button.configure(state="disabled")
        self.trial_open_button.configure(state="disabled")
        self.phase_open_button.configure(state="disabled")
        self.sweep_open_button.configure(state="disabled")

    def open_conference_report(self) -> None:
        if self.conference_report_path and self.conference_report_path.exists():
            os.startfile(self.conference_report_path)  # Windows-only — matches this app's target platform

    def open_trial_signals_report(self) -> None:
        if self.trial_signals_report_path and self.trial_signals_report_path.exists():
            os.startfile(self.trial_signals_report_path)  # Windows-only — matches this app's target platform

    def open_phase_transition_report(self) -> None:
        if self.phase_transition_report_path and self.phase_transition_report_path.exists():
            os.startfile(self.phase_transition_report_path)  # Windows-only — matches this app's target platform

    def open_signal_sweep_report(self) -> None:
        if self.signal_sweep_report_path and self.signal_sweep_report_path.exists():
            os.startfile(self.signal_sweep_report_path)  # Windows-only — matches this app's target platform


if __name__ == "__main__":
    App().mainloop()
