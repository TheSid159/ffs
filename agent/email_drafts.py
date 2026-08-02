"""Creates real, unsent draft emails in an external mailbox (via IMAP) for
each lead's drafted outreach email, so they sit ready-to-send in an outbox
that's connected to HubSpot (or wherever) for logging/tracking — rather
than sending anything automatically. Entirely optional: only runs if
outbox credentials are provided; the tool's core "never sends anything
automatically" design is unchanged, since creating a draft is not sending.

Uses stdlib `imaplib`/`email` (no new dependency), consistent with the
rest of this tool's free/deterministic sources. IMAP APPEND to the Drafts
folder works across virtually every mail provider (Gmail, Microsoft 365/
Outlook, and generic IMAP hosts) with just a username + app password —
no OAuth setup required, unlike using each provider's own API directly.
"""

import imaplib
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional


class OutboxError(Exception):
    """Raised when connecting to or writing to the outbox mailbox fails,
    with the IMAP server's own explanation attached where available."""


def _build_message(from_addr: str, to_addr: Optional[str], subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    if to_addr:
        msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    return msg.as_bytes()


def create_draft(
    imap_host: str,
    imap_port: int,
    imap_user: str,
    imap_password: str,
    drafts_folder: str,
    to_addr: Optional[str],
    subject: str,
    body: str,
) -> None:
    """Append one message to `drafts_folder` in the mailbox at imap_user@
    imap_host — this is what makes it show up as a draft in that mailbox's
    email client, without ever sending it. Raises OutboxError on any
    failure (connection, auth, folder not found, etc.) rather than
    swallowing it, since a failed draft push should be visible to the user,
    not silently lost the way a below-threshold Hunter match is."""
    message = _build_message(imap_user, to_addr, subject, body)

    try:
        conn = imaplib.IMAP4_SSL(imap_host, imap_port)
    except OSError as exc:
        raise OutboxError(f"Could not connect to {imap_host}:{imap_port} — {exc}") from exc

    try:
        try:
            conn.login(imap_user, imap_password)
        except imaplib.IMAP4.error as exc:
            raise OutboxError(f"IMAP login failed for {imap_user} — {exc}") from exc

        # The \Draft flag marks it as a draft for clients that check flags;
        # appending to the Drafts folder itself is what actually makes it
        # show up there for most clients regardless of flag support.
        status, response = conn.append(
            drafts_folder, r"(\Draft)", imaplib.Time2Internaldate(time.time()), message
        )
        if status != "OK":
            raise OutboxError(f"IMAP APPEND to '{drafts_folder}' failed — {response}")
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def push_drafts_for_report(enriched_leads: list, args, draft_email_fn) -> tuple:
    """Create one draft per lead in `enriched_leads` (a list of (lead,
    contact) tuples, same shape used throughout this tool). `draft_email_fn`
    is bd_agent.draft_email, passed in rather than imported directly to
    avoid a circular import (bd_agent.py imports this module, not the other
    way round). Returns (success_count, [(lead, error_message), ...]) — one
    lead's draft failing doesn't stop the rest from being attempted.

    The "To:" field is only ever set from a Hunter-confirmed contact email
    (contact.email) — never a guessed or unconfirmed address, same
    anti-fabrication discipline as everywhere else in this tool. Leads
    without a confirmed contact still get a draft (subject/body ready to
    go), just with no recipient filled in yet — the CRO reviews and adds
    one before sending.
    """
    successes = 0
    failures = []
    for lead, contact in enriched_leads:
        subject, body = draft_email_fn(lead, contact, args)
        to_addr = contact.email if contact and contact.email else None
        try:
            create_draft(
                imap_host=args.outbox_imap_host,
                imap_port=args.outbox_imap_port,
                imap_user=args.outbox_email,
                imap_password=args.outbox_app_password,
                drafts_folder=args.outbox_drafts_folder,
                to_addr=to_addr,
                subject=subject,
                body=body,
            )
            successes += 1
        except OutboxError as exc:
            failures.append((lead, str(exc)))
    return successes, failures
