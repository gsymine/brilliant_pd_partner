"""The sourcing-run state machine.

Run:     outreach -> extracting -> analyzing -> awaiting_decision -> accepted | denied_all
         (any agent-stage failure -> needs_attention; retry with POST /runs/{id}/retry)
Thread:  new -> awaiting_reply <-> processing_reply -> complete | declined | stalled
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .db import Database, new_id, now_iso
from .email_service import OPEN_THREAD_STATUSES, EmailService, match_thread, thread_tag
from .tool_specs import DEFAULT_REQUIRED_FIELDS
from .tools import TERMINAL_THREAD_STATUSES, ToolContext, untrusted
from .zoowork_runtime import ZooworkRuntime

log = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = ("outreach", "extracting", "analyzing")


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


class Orchestrator:
    def __init__(self, db: Database, settings: Settings, email: EmailService):
        self.db = db
        self.s = settings
        self.email = email
        self.runtime: ZooworkRuntime | None = None
        self._tasks: set[asyncio.Task] = set()

    def tool_context(self, role: str, run_id: str, thread_id: str | None) -> ToolContext:
        return ToolContext(role=role, run_id=run_id, thread_id=thread_id, db=self.db, email=self.email, settings=self.s)

    def _spawn(self, coro: Any, label: str) -> None:
        async def guarded() -> None:
            try:
                await coro
            except Exception as exc:
                log.exception("Background task %s failed", label)
                run_id = label.split(":")[1] if ":" in label else None
                if run_id and self.db.get_run(run_id):
                    self.db.log(run_id, "error", f"{label}: {type(exc).__name__}: {exc}")
        task = asyncio.create_task(guarded(), name=label)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def rt(self) -> ZooworkRuntime:
        if self.runtime is None:
            raise RuntimeError("ZooWork runtime not initialised")
        return self.runtime

    # ---- 1. Start Sourcing Process -------------------------------------------------------
    def create_run(self, req: dict[str, Any]) -> dict[str, Any]:
        run_id = new_id("run")
        hours = req.get("deadline_hours") or self.s.default_run_deadline_hours
        run = {
            "id": run_id, "status": "outreach", "title": req["title"], "brief": req["brief"],
            "brief_items": req["brief_items"], "required_fields": req.get("required_fields") or DEFAULT_REQUIRED_FIELDS,
            "costing": req.get("costing"),
            "deadline_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds"),
            "created_by": req.get("created_by"), "error": None, "created_at": now_iso(), "updated_at": now_iso(),
        }
        self.db.insert("runs", run)
        for v in req["vendors"]:
            self.db.insert("vendor_threads", {
                "id": new_id("thr"), "run_id": run_id, "vendor_name": v["name"], "vendor_email": v["email"].lower(),
                "country": v.get("country"), "status": "new", "close_reason": None, "outbound_count": 0,
                "reminders_sent": 0, "last_outbound_at": None, "last_inbound_at": None,
                "created_at": now_iso(), "updated_at": now_iso(),
            })
        self.db.log(run_id, "run_started", f"Sourcing run started with {len(req['vendors'])} vendors")
        self._spawn(self._open_vendor_sessions(run_id), f"outreach:{run_id}")
        return self.db.get_run(run_id)  # type: ignore[return-value]

    async def _open_vendor_sessions(self, run_id: str) -> None:
        run = self.db.get_run(run_id)
        assert run
        threads = self.db.threads_for_run(run_id)
        await asyncio.gather(*(self._open_one(run, t) for t in threads))
        await self.check_gate(run_id)

    async def _open_one(self, run: dict[str, Any], thread: dict[str, Any]) -> None:
        msg = (
            "NEW SOURCING CONVERSATION. You own this vendor conversation until every checklist field is known.\n\n"
            f"Vendor: {thread['vendor_name']} <{thread['vendor_email']}>"
            f"{' (country: ' + thread['country'] + ')' if thread['country'] else ''}\n"
            f"Thread id: {thread['id']} (subject tag {thread_tag(thread['id'])} is added automatically)\n\n"
            f"## Sourcing brief: {run['title']}\n{run['brief']}\n\n"
            f"## Brief items\n{json.dumps(run['brief_items'], indent=2, ensure_ascii=False)}\n\n"
            f"## Required checklist fields (per proposed product)\n{', '.join(run['required_fields'])}\n\n"
            "Step now: send the initial request (purpose 'rfq') with send_email, then end your turn. "
            "Vendor replies will arrive as new messages in this session."
        )
        await self.rt.start_session("communication", thread["id"], run["id"], msg, thread_id=thread["id"])
        await self._after_comm_turn(thread["id"])

    # ---- 2. Vendor reply loop --------------------------------------------------------------
    async def handle_inbound(self, parsed: Any) -> dict[str, Any] | None:
        thread = match_thread(self.db, parsed)
        if not thread:
            log.info("Inbound email from %s did not match any vendor thread; ignored", parsed.from_addr)
            return None
        row = self.email.store_inbound(thread, parsed)
        if row is None:
            return None  # duplicate
        run = self.db.get_run(thread["run_id"])
        self.db.update("vendor_threads", "id", thread["id"], {"last_inbound_at": row["created_at"]})
        self.db.log(thread["run_id"], "email_in", f"Reply from {thread['vendor_name']}: {row['subject']}", thread["id"])
        if not run or run["status"] != "outreach" or thread["status"] not in OPEN_THREAD_STATUSES:
            # Stored for the record (Extraction still reads it), but the conversation is closed.
            return row
        self.db.update("vendor_threads", "id", thread["id"], {"status": "processing_reply"})
        atts = ", ".join(f"{a['filename']} (attachment_id {a['id']})" for a in row["attachments"]) or "none"
        msg = (
            f"NEW EMAIL FROM VENDOR (email_id {row['id']}). Attachments: {atts}.\n"
            f"Subject: {row['subject']}\n\n{untrusted(row['body'] or '')}\n\n"
            "Steps: 1) save_vendor_facts for every new fact (source_email_id = this email_id). "
            "2) get_vendor_memory. 3) If fields are missing or unclear, send ONE follow_up email asking only for "
            "them; if everything is known, mark_thread_status complete; if the vendor declined, mark declined."
        )
        self._spawn(self._comm_turn(thread["id"], msg, f"inbound-{row['id']}"), f"inbound:{thread['run_id']}")
        return row

    async def _comm_turn(self, thread_id: str, message: str, key: str) -> None:
        await self.rt.send("communication", thread_id, message, key)
        await self._after_comm_turn(thread_id)

    async def _after_comm_turn(self, thread_id: str) -> None:
        t = self.db.get_thread(thread_id)
        if t and t["status"] == "processing_reply":  # agent ended the turn without emailing or closing
            self.db.update("vendor_threads", "id", thread_id, {"status": "awaiting_reply"})
        if t:
            await self.check_gate(t["run_id"])

    # ---- 3. Timers: reminders, stalls, deadline ------------------------------------------
    async def tick(self) -> None:
        now = datetime.now(timezone.utc)
        for run in self.db.runs_with_status("outreach"):
            past_deadline = now >= _parse(run["deadline_at"])  # type: ignore[operator]
            for t in self.db.threads_for_run(run["id"]):
                if t["status"] != "awaiting_reply":
                    continue
                last_out = _parse(t["last_outbound_at"])
                waited = (now - last_out).total_seconds() / 3600 if last_out else 0
                if past_deadline or t["outbound_count"] >= self.s.max_outbound_per_vendor or (
                        waited >= self.s.reminder_after_hours and t["reminders_sent"] >= self.s.max_reminders):
                    reason = "run deadline reached" if past_deadline else "no reply after reminders / round limit"
                    self.db.update("vendor_threads", "id", t["id"], {"status": "stalled", "close_reason": reason})
                    self.db.log(run["id"], "thread_closed", f"{t['vendor_name']}: stalled ({reason})", t["id"])
                elif waited >= self.s.reminder_after_hours:
                    # Mark busy first so the next tick does not queue a second reminder.
                    self.db.update("vendor_threads", "id", t["id"], {
                        "status": "processing_reply", "reminders_sent": t["reminders_sent"] + 1,
                        "last_outbound_at": now.isoformat(timespec="seconds")})
                    msg = (f"No reply from the vendor for {waited:.0f} hours. Send a short, polite reminder "
                           "(purpose 'reminder') restating only the missing fields, then end your turn.")
                    self._spawn(self._comm_turn(t["id"], msg, f"reminder-{t['id']}-{t['reminders_sent'] + 1}"),
                                f"reminder:{run['id']}")
            await self.check_gate(run["id"])

    # ---- 4. Handoff gate -> Extraction -> Analysis ---------------------------------------
    async def check_gate(self, run_id: str, force: bool = False) -> None:
        run = self.db.get_run(run_id)
        if not run or run["status"] != "outreach":
            return
        threads = self.db.threads_for_run(run_id)
        if force:
            for t in threads:
                if t["status"] not in TERMINAL_THREAD_STATUSES:
                    self.db.update("vendor_threads", "id", t["id"], {"status": "stalled", "close_reason": "closed by user"})
            threads = self.db.threads_for_run(run_id)
        if not all(t["status"] in TERMINAL_THREAD_STATUSES for t in threads):
            return
        self.db.update("runs", "id", run_id, {"status": "extracting"})
        self.db.log(run_id, "gate_open", "All vendor conversations closed; extraction started")
        self._spawn(self._extract_then_analyze(run_id), f"extract:{run_id}")

    async def _extract_then_analyze(self, run_id: str) -> None:
        run = self.db.get_run(run_id)
        assert run
        try:
            await self.rt.start_session("extraction", run_id, run_id, (
                f"Extract structured offers for sourcing run {run_id} ('{run['title']}').\n\n"
                f"## Brief\n{run['brief']}\n\n## Brief items\n{json.dumps(run['brief_items'], ensure_ascii=False)}\n\n"
                f"## Required fields\n{', '.join(run['required_fields'])}\n\n"
                "Follow your skill: list_vendor_threads, read every thread and its Vendor Memory, then save ALL offers "
                "in one save_offers call."))
            if not self.db.offers_for_run(run_id):
                raise RuntimeError("Extraction finished without saving offers")
            self.db.update("runs", "id", run_id, {"status": "analyzing"})
            await self.rt.start_session("analysis", run_id, run_id, (
                f"Analyse the offers of sourcing run {run_id} ('{run['title']}') for the human decision maker. "
                "Follow your skill: get_run_context, get_offers, run_costing, then save_analysis_report."))
            if not self.db.get_report(run_id):
                raise RuntimeError("Analysis finished without saving a report")
            self.db.update("runs", "id", run_id, {"status": "awaiting_decision", "error": None})
            self.db.log(run_id, "ready_for_decision", "Analysis report ready for human decision")
        except Exception as exc:
            self.db.update("runs", "id", run_id, {"status": "needs_attention", "error": f"{type(exc).__name__}: {exc}"})
            self.db.log(run_id, "error", f"Agent stage failed: {exc}")
            raise

    async def retry(self, run_id: str) -> None:
        """Re-run extraction + analysis (fresh sessions) after a failure."""
        run = self.db.get_run(run_id)
        if not run or run["status"] not in ("needs_attention", "awaiting_decision"):
            raise ValueError("Only runs in needs_attention or awaiting_decision can be retried")
        self.db.execute("DELETE FROM agent_sessions WHERE run_id = ? AND role IN ('extraction', 'analysis')", [run_id])
        self.db.update("runs", "id", run_id, {"status": "extracting", "error": None})
        self._spawn(self._extract_then_analyze(run_id), f"extract:{run_id}")

    # ---- 5. Human decision -----------------------------------------------------------------
    def decide(self, run_id: str, outcome: str, offer_ids: list[str], notes: str | None, decided_by: str | None) -> dict[str, Any]:
        run = self.db.get_run(run_id)
        if not run:
            raise LookupError("Run not found")
        if run["status"] != "awaiting_decision":
            raise ValueError(f"Run is {run['status']}; a decision is only possible in awaiting_decision")
        valid = {o["id"] for o in self.db.offers_for_run(run_id)}
        if outcome == "accepted":
            unknown = [i for i in offer_ids if i not in valid]
            if not offer_ids or unknown:
                raise ValueError(f"accepted needs at least one offer id from this run; unknown: {unknown}")
        elif outcome == "denied_all":
            offer_ids = []
        else:
            raise ValueError("outcome must be 'accepted' or 'denied_all'")
        offer_ids = list(dict.fromkeys(offer_ids))
        self.db.insert("decisions", {"run_id": run_id, "outcome": outcome, "accepted_offer_ids": offer_ids,
                                     "notes": notes, "decided_by": decided_by, "decided_at": now_iso()})
        self.db.update("runs", "id", run_id, {"status": outcome})
        self.db.log(run_id, "decision", f"{outcome}: {len(offer_ids)} products accepted")
        return self.db.get_decision(run_id)  # type: ignore[return-value]
