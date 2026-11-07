"""Real email transport: SMTP for sending, IMAP polling for receiving.

Works with any standard mailbox (Gmail / Outlook / company mail with an app password).
IMAP polling needs no public URL, so the backend can run on a laptop during the demo.

Threading: every outbound email carries a Message-ID and a subject tag "[PD-xxxxxxxx]".
Inbound replies are matched to a vendor thread by In-Reply-To / References first, then by the
subject tag, then by sender address among threads that are still open.
"""
from __future__ import annotations

import email
import email.policy
import html
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database, new_id, now_iso

log = logging.getLogger(__name__)

# new: session created, RFQ not sent yet · awaiting_reply: last email was ours ·
# processing_reply: a vendor reply arrived and the agent is working on it.
OPEN_THREAD_STATUSES = ("new", "awaiting_reply", "processing_reply")
TAG_RE = re.compile(r"\[PD-([0-9a-f]{8})\]", re.I)
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def is_simulated_address(address: str | None) -> bool:
    """Demo vendors use addresses ending in .sim; their mail is answered by app/vendor_sim.py, never sent."""
    return (address or "").lower().rsplit("@", 1)[-1].endswith(".sim")


def thread_tag(thread_id: str) -> str:
    return f"[PD-{thread_id.split('_')[-1][:8]}]"


@dataclass
class ParsedEmail:
    from_addr: str
    to_addr: str
    subject: str
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    body: str
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)  # (filename, content_type, data)


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def parse_message(raw: bytes) -> ParsedEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body_plain, body_html = None, None
    attachments: list[tuple[str, str, bytes]] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        ctype = part.get_content_type()
        if disposition == "attachment" or (disposition == "inline" and part.get_filename()):
            data = part.get_payload(decode=True) or b""
            if len(data) <= MAX_ATTACHMENT_BYTES:
                attachments.append((part.get_filename() or "attachment", ctype, data))
            continue
        if ctype == "text/plain" and body_plain is None:
            body_plain = part.get_content()
        elif ctype == "text/html" and body_html is None:
            body_html = part.get_content()
    body = body_plain if body_plain is not None else _html_to_text(body_html or "")
    refs = (msg.get("References") or "").split()
    return ParsedEmail(
        from_addr=parseaddr(msg.get("From", ""))[1].lower(),
        to_addr=parseaddr(msg.get("To", ""))[1].lower(),
        subject=str(msg.get("Subject", "")),
        message_id=(msg.get("Message-ID") or "").strip() or None,
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        references=refs,
        body=body.strip(),
        attachments=attachments,
    )


def match_thread(db: Database, parsed: ParsedEmail) -> dict[str, Any] | None:
    # 1. Reply headers point at one of our outbound Message-IDs.
    for ref in [parsed.in_reply_to, *reversed(parsed.references)]:
        if ref:
            hit = db.email_by_message_id(ref)
            if hit:
                return db.get_thread(hit["thread_id"])
    # 2. Subject tag survives most mail clients.
    m = TAG_RE.search(parsed.subject or "")
    if m:
        for t in db.all("SELECT * FROM vendor_threads WHERE id LIKE ?", [f"%{m.group(1).lower()}%"]):
            return t
    # 3. Sender address of an open thread (most recent first).
    marks = ", ".join("?" for _ in OPEN_THREAD_STATUSES)
    rows = db.all(
        f"SELECT * FROM vendor_threads WHERE lower(vendor_email) = ? AND status IN ({marks}) "
        "ORDER BY created_at DESC",
        [parsed.from_addr, *OPEN_THREAD_STATUSES],
    )
    return rows[0] if rows else None


class EmailService:
    def __init__(self, settings: Settings, db: Database):
        self.s = settings
        self.db = db
        self.attachments_dir = settings.data_dir / "attachments"
        # Called as listener(thread, row) after every outbound email is recorded (e.g. vendor simulator).
        self.listeners: list[Any] = []

    # ---- outbound ------------------------------------------------------------------------
    def send(self, thread: dict[str, Any], subject: str, body: str) -> dict[str, Any]:
        """Send one email to the thread's vendor (the only allowed recipient) and record it."""
        tag = thread_tag(thread["id"])
        if tag.lower() not in subject.lower():
            subject = f"{subject.strip()} {tag}"
        domain = (self.s.from_address.split("@")[-1] or "localhost") if self.s.from_address else "localhost"
        message_id = make_msgid(domain=domain)

        history = self.db.emails_for_thread(thread["id"])
        last_in = next((e for e in reversed(history) if e["direction"] == "in" and e["message_id"]), None)
        refs = [e["message_id"] for e in history if e["message_id"]][-10:]

        msg = EmailMessage()
        msg["From"] = formataddr((self.s.from_name, self.s.from_address))
        msg["To"] = formataddr((thread["vendor_name"], thread["vendor_email"]))
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        if last_in:
            msg["In-Reply-To"] = last_in["message_id"]
        if refs:
            msg["References"] = " ".join(refs)
        msg.set_content(body)

        if is_simulated_address(thread["vendor_email"]):
            log.info("Simulated vendor %s: handed to the vendor simulator: %s", thread["vendor_email"], subject)
        elif self.s.email_mode == "smtp":
            self._smtp_send(msg)
        else:
            log.info("EMAIL_MODE=%s: not sending to %s: %s", self.s.email_mode, thread["vendor_email"], subject)

        row = {
            "id": new_id("eml"), "run_id": thread["run_id"], "thread_id": thread["id"], "direction": "out",
            "message_id": message_id, "in_reply_to": last_in["message_id"] if last_in else None,
            "references_": " ".join(refs) or None, "from_addr": self.s.from_address,
            "to_addr": thread["vendor_email"], "subject": subject, "body": body, "attachments": [],
            "created_at": now_iso(),
        }
        self.db.insert("emails", row)
        for listener in self.listeners:
            try:
                listener(thread, row)
            except Exception:  # a listener must never break sending
                log.exception("Outbound email listener failed")
        return row

    def _smtp_send(self, msg: EmailMessage) -> None:
        if not self.s.smtp_host:
            raise RuntimeError("SMTP_HOST is not configured (or set EMAIL_MODE=dry_run)")
        ctx = ssl.create_default_context()
        if self.s.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=30)
        with server:
            if self.s.smtp_security == "starttls":
                server.starttls(context=ctx)
            if self.s.smtp_user:
                server.login(self.s.smtp_user, self.s.smtp_password)
            server.send_message(msg)

    # ---- inbound -------------------------------------------------------------------------
    def open_vendor_addresses(self) -> list[str]:
        marks = ", ".join("?" for _ in OPEN_THREAD_STATUSES)
        rows = self.db.all(
            f"SELECT DISTINCT lower(vendor_email) AS e FROM vendor_threads WHERE status IN ({marks})",
            OPEN_THREAD_STATUSES,
        )
        return [r["e"] for r in rows]

    def fetch_unseen(self) -> list[ParsedEmail]:
        """Fetch unseen messages FROM VENDORS OF OPEN THREADS only, and mark just those seen.

        The mailbox may be a personal inbox: every other email is never fetched or touched.
        Blocking: call via a thread.
        """
        if not (self.s.imap_enabled and self.s.imap_host):
            return []
        senders = [a for a in self.open_vendor_addresses() if not is_simulated_address(a)]
        if not senders:
            return []
        out: list[ParsedEmail] = []
        with imaplib.IMAP4_SSL(self.s.imap_host, self.s.imap_port) as imap:
            imap.login(self.s.imap_user, self.s.imap_password)
            imap.select(self.s.imap_folder)
            nums: list[bytes] = []
            for sender in senders:
                status, data = imap.search(None, "UNSEEN", "FROM", f'"{sender}"')
                if status == "OK":
                    nums.extend(n for n in data[0].split() if n not in nums)
            for num in nums:
                status, parts = imap.fetch(num, "(RFC822)")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                try:
                    out.append(parse_message(parts[0][1]))
                except Exception:  # one malformed email must not stop the poller
                    log.exception("Could not parse inbound email %s", num)
                imap.store(num, "+FLAGS", "\\Seen")
        return out

    def store_inbound(self, thread: dict[str, Any], parsed: ParsedEmail) -> dict[str, Any] | None:
        """Persist an inbound email (deduplicated by Message-ID). Returns None for duplicates."""
        if parsed.message_id and self.db.email_by_message_id(parsed.message_id):
            return None
        email_id = new_id("eml")
        saved = []
        for i, (filename, ctype, data) in enumerate(parsed.attachments):
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:120] or f"attachment_{i}"
            path = self.attachments_dir / email_id / safe
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            saved.append({"id": f"{email_id}:{i}", "filename": filename, "content_type": ctype,
                          "size": len(data), "path": str(path)})
        row = {
            "id": email_id, "run_id": thread["run_id"], "thread_id": thread["id"], "direction": "in",
            "message_id": parsed.message_id, "in_reply_to": parsed.in_reply_to,
            "references_": " ".join(parsed.references) or None, "from_addr": parsed.from_addr,
            "to_addr": parsed.to_addr, "subject": parsed.subject, "body": parsed.body,
            "attachments": saved, "created_at": now_iso(),
        }
        self.db.insert("emails", row)
        return row


def read_attachment(db: Database, attachment_id: str) -> tuple[dict[str, Any], bytes] | None:
    email_id = attachment_id.split(":")[0]
    row = db.get_email(email_id)
    if not row:
        return None
    for att in row["attachments"]:
        if att["id"] == attachment_id:
            return att, Path(att["path"]).read_bytes()
    return None
