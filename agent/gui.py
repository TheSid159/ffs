#!/usr/bin/env python3
"""
Desktop GUI for bd_agent.py — no terminal, no environment variables.

Enter your API keys once; they're saved locally in gui_config.json (next to
this file, gitignored) and pre-filled every time after. Fill in the search
fields, click Run, and the report opens automatically when it's done.

Usage: python gui.py   (or double-click run_gui.bat)
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from gui_logic import QueueWriter, build_args, load_config, run_pipeline, save_config


class App(tk.Tk):
    FIELDS = [
        # (config_key, label, default, width)
        ("conference", "Conference(s) (space-separated):", "ASCO GU", 40),
        ("year", "Year(s) (space-separated):", "2025 2026", 40),
        ("indication", "Indication:", "bladder cancer", 40),
        ("phase", "Phase:", "Phase II", 40),
        ("sender_name", "Your name:", "Dr. Darren Brennan", 40),
        ("sender_title", "Your title:", "Medical Director", 40),
        ("sender_company", "Company:", "Elevate Imaging", 40),
        ("hunter_min_confidence", "Hunter min confidence (0-100):", "90", 10),
        ("output", "Output file:", "leads_report.md", 40),
    ]

    def __init__(self):
        super().__init__()
        self.title("Elevate Imaging — BD Lead Finder")
        self.geometry("760x680")
        self.log_queue: "queue.Queue" = queue.Queue()
        self.config_data = load_config()
        self.report_path = None
        self.field_vars = {}
        self._build_ui()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        keys_frame = ttk.LabelFrame(self, text="API Keys (saved locally, entered once)")
        keys_frame.pack(fill="x", **pad)

        ttk.Label(keys_frame, text="Anthropic API Key:").grid(row=0, column=0, sticky="w", **pad)
        self.anthropic_key_var = tk.StringVar(value=self.config_data.get("anthropic_api_key", ""))
        ttk.Entry(keys_frame, textvariable=self.anthropic_key_var, show="*", width=60).grid(row=0, column=1, **pad)

        ttk.Label(keys_frame, text="Hunter.io API Key (optional):").grid(row=1, column=0, sticky="w", **pad)
        self.hunter_key_var = tk.StringVar(value=self.config_data.get("hunter_api_key", ""))
        ttk.Entry(keys_frame, textvariable=self.hunter_key_var, show="*", width=60).grid(row=1, column=1, **pad)

        params_frame = ttk.LabelFrame(self, text="Search parameters")
        params_frame.pack(fill="x", **pad)

        for row, (key, label, default, width) in enumerate(self.FIELDS):
            ttk.Label(params_frame, text=label).grid(row=row, column=0, sticky="w", **pad)
            var = tk.StringVar(value=str(self.config_data.get(key, default)))
            ttk.Entry(params_frame, textvariable=var, width=width).grid(row=row, column=1, sticky="w", **pad)
            self.field_vars[key] = var

        run_frame = ttk.Frame(self)
        run_frame.pack(fill="x", **pad)
        self.run_button = ttk.Button(run_frame, text="Run", command=self.on_run)
        self.run_button.pack(side="left", **pad)
        self.open_report_button = ttk.Button(run_frame, text="Open report", command=self.open_report, state="disabled")
        self.open_report_button.pack(side="left", **pad)

        log_frame = ttk.LabelFrame(self, text="Progress")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

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
                elif kind == "done":
                    self.report_path = payload
                    self.open_report_button.configure(state="normal")
                    self.run_button.configure(state="normal")
                elif kind == "error":
                    self.run_button.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _current_form(self) -> dict:
        form = {key: var.get() for key, var in self.field_vars.items()}
        form["anthropic_api_key"] = self.anthropic_key_var.get()
        form["hunter_api_key"] = self.hunter_key_var.get()
        return form

    def on_run(self) -> None:
        form = self._current_form()
        if not form["anthropic_api_key"].strip():
            messagebox.showerror("Missing key", "Please enter your Anthropic API key.")
            return

        self.config_data.update({k: v.strip() for k, v in form.items()})
        save_config(self.config_data)

        try:
            args = build_args(form)
        except ValueError:
            messagebox.showerror("Invalid input", "Year(s) and Hunter min confidence must be numbers.")
            return

        os.environ["ANTHROPIC_API_KEY"] = form["anthropic_api_key"].strip()

        self.run_button.configure(state="disabled")
        self.open_report_button.configure(state="disabled")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        threading.Thread(target=self._run_in_background, args=(args,), daemon=True).start()

    def _run_in_background(self, args) -> None:
        old_stdout, old_stderr = sys.stdout, sys.stderr
        writer = QueueWriter(self.log_queue)
        sys.stdout = writer
        sys.stderr = writer
        try:
            report_path = run_pipeline(args)
            self.log_queue.put(("text", f"\n\nDone! Report saved to: {report_path.resolve()}\n"))
            self.log_queue.put(("done", report_path))
        except Exception as exc:  # surface any failure in the window instead of a silent crash
            self.log_queue.put(("text", f"\n\nError: {exc}\n"))
            self.log_queue.put(("error", str(exc)))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def open_report(self) -> None:
        if self.report_path and self.report_path.exists():
            os.startfile(self.report_path)  # Windows-only — matches this app's target platform


if __name__ == "__main__":
    App().mainloop()
