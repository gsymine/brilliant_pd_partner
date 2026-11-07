"""Vendor simulator: fictional vendors that answer the Communication Agent's emails (demo).

How it plugs in
    EmailService.send() notifies listeners after every outbound email. For recipients whose
    address ends in ".sim" (see simulator/vendors.json) the simulator waits a few seconds, writes
    the vendor's reply, and feeds it to Orchestrator.handle_inbound(), exactly like an email
    fetched by IMAP. Real vendor addresses are untouched, so simulated and real vendors can share
    one run.

Deterministic behaviour, LLM wording
    Code decides WHAT each vendor says in each reply (which facts are withheld, ignore the first
    email, decline, prompt-injection text). A ZooWork "vendor_sim" agent only turns those facts
    into a natural email in the persona's voice. Without ZooWork (SIM_MODE=scripted, or on any
    LLM error) a template writes the email instead, so the demo flow never depends on the LLM.

Stateless: the reply number for a thread is derived from the emails stored in the database.
"""
from __future__ import annotations

import asyncio
import json
import logging
from email.utils import make_msgid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import AGENTS_DIR, BACKEND_DIR, Settings
from .db import Database
from .email_service import OPEN_THREAD_STATUSES, ParsedEmail, is_simulated_address

log = logging.getLogger(__name__)

VENDORS_PATH = BACKEND_DIR / "simulator" / "vendors.json"
MAX_REPLIES_PER_THREAD = 6

LABELS = {
    "design": "Design", "materials": "Material", "capacity": "Capacity", "lid_type": "Lid",
    "unit_price": "Price", "currency": "Currency", "incoterm": "Terms", "moq": "MOQ",
    "production_days": "Production lead time", "sampling_days": "Sample time", "sampling_cost": "Sample cost",
    "payment_terms": "Payment", "port_of_loading": "Port of loading", "certifications": "Certifications",
}
TERSE = {"production_days": "leadtime", "sampling_days": "sample", "sampling_cost": "sample cost",
         "payment_terms": "payment", "port_of_loading": "port", "certifications": "cert", "lid_type": "lid"}


def load_vendors(path: Path = VENDORS_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["vendors"]


# ---------------------------------------------------------------- reply planning (deterministic)
def hidden_after(behavior: dict[str, Any], reply_index: int) -> set[str]:
    """Fields still withheld in reply number `reply_index` (0 = first reply)."""
    rounds = behavior.get("withhold") or []
    return set().union(*rounds[reply_index:]) if reply_index < len(rounds) else set()


def plan_reply(vendor: dict[str, Any], n_out: int, n_in: int) -> dict[str, Any] | None:
    """Decide what the vendor does after our n_out-th email, having replied n_in times so far.

    Returns None (no reply) or {"kind": first|more|decline, "facts": {product: {field: value}},
    "new_fields": [...], "late": bool, "injection": str|None}.
    """
    b = vendor.get("behavior") or {}
    if n_in >= MAX_REPLIES_PER_THREAD:
        return None
    if n_out <= int(b.get("ignore_first", 0)):
        return None  # slow vendor: misses the first email(s) and waits for a reminder
    late = n_in == 0 and int(b.get("ignore_first", 0)) > 0
    if b.get("type") == "decline":
        return {"kind": "decline", "reason": b.get("reason", "we cannot quote this time"), "late": late} if n_in == 0 else None

    hidden_now = hidden_after(b, n_in)
    if n_in == 0:
        facts = {p["product_ref"]: {k: v for k, v in p["facts"].items() if k not in hidden_now}
                 for p in vendor["products"]}
        return {"kind": "first", "facts": facts, "new_fields": [], "late": late, "injection": b.get("injection")}

    revealed = hidden_after(b, n_in - 1) - hidden_now
    if not revealed:
        return None  # nothing new to say (e.g. our email was a thank-you)
    facts = {p["product_ref"]: {k: v for k, v in p["facts"].items() if k in revealed} for p in vendor["products"]}
    return {"kind": "more", "facts": facts, "new_fields": sorted(revealed), "late": False, "injection": None}


# ---------------------------------------------------------------- template writer (no LLM)
def _fmt(field: str, value: Any, facts: dict[str, Any]) -> str:
    if field == "unit_price":
        return f"{facts.get('currency', '')} {value}".strip() + (f" {facts['incoterm']}" if facts.get("incoterm") else "")
    return str(value)


def write_scripted(vendor: dict[str, Any], plan: dict[str, Any]) -> str:
    first = vendor.get("contact", vendor["name"]).split()[0]
    terse = vendor.get("style") == "terse"
    if plan["kind"] == "decline":
        return (f"Dear team,\n\nThank you for your request. Unfortunately {plan['reason']}, so we cannot quote "
                f"this time. We hope to work with you next season.\n\nBest regards,\n{vendor['contact']}\n{vendor['name']}")
    lines: list[str] = []
    for product, facts in plan["facts"].items():
        shown = {k: v for k, v in facts.items() if k not in ("currency", "incoterm") or "unit_price" not in facts}
        if terse:
            lines.append(f"{product}:" if plan["kind"] == "first" else f"{product.lower()}")
            lines += [f"{TERSE.get(k, k.replace('_', ' '))} {_fmt(k, v, facts)}" for k, v in shown.items()]
        else:
            lines.append(f"{product}")
            lines += [f"  - {LABELS.get(k, k)}: {_fmt(k, v, facts)}" for k, v in shown.items()]
        lines.append("")
    body = "\n".join(lines).strip()
    if terse:
        intro = "hi,\npls find our offer:" if plan["kind"] == "first" else "hi, pls see below"
        text = f"{intro}\n{body}\n\nthanks\n{first}"
    else:
        sorry = "Apologies for the late reply, your first email was caught by our filter.\n\n" if plan["late"] else ""
        intro = ("Thank you for your enquiry. Please find our offer below:" if plan["kind"] == "first"
                 else "Thank you for your follow-up. Here are the missing details:")
        text = f"Dear team,\n\n{sorry}{intro}\n\n{body}\n\nBest regards,\n{vendor['contact']}\n{vendor['name']}"
    if plan.get("injection"):
        text += f"\n\n{plan['injection']}"
    return text


def llm_instructions(vendor: dict[str, Any], plan: dict[str, Any], buyer_email: str) -> str:
    if plan["kind"] == "decline":
        what = f"DECLINE. Reason: {plan['reason']}."
    else:
        what = ("FACTS FOR THIS REPLY (state all of them, nothing else):\n"
                + json.dumps(plan["facts"], indent=2, ensure_ascii=False))
        if plan["kind"] == "more":
            what += "\nThese answer the buyer's follow-up; you previously left them out."
    late = "\nThe vendor missed the earlier email; apologise briefly for the delay." if plan["late"] else ""
    return (f"BUYER'S LATEST EMAIL:\n---\n{buyer_email[:4000]}\n---\n\n{what}{late}\n\n"
            "Write the vendor's reply now (email body only).")


# ---------------------------------------------------------------- the simulator
class VendorSimulator:
    def __init__(self, settings: Settings, db: Database, vendors: list[dict[str, Any]] | None = None):
        self.s = settings
        self.db = db
        self.vendors = {v["email"].lower(): v for v in (vendors if vendors is not None else load_vendors())}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.deliver: Callable[[ParsedEmail], Awaitable[Any]] | None = None
        self.client: Any = None          # ZooWork client for LLM wording; None = templates only
        self.agent_id: str | None = None
        self._sessions: dict[str, dict[str, Any]] = {}   # thread_id -> {session_id, cursor}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---- wiring ------------------------------------------------------------------------------
    def attach(self, loop: asyncio.AbstractEventLoop, deliver: Callable[[ParsedEmail], Awaitable[Any]],
               client: Any = None) -> None:
        self.loop, self.deliver, self.client = loop, deliver, client

    def vendor_for(self, address: str) -> dict[str, Any] | None:
        return self.vendors.get((address or "").lower()) if is_simulated_address(address) else None

    def on_outbound(self, thread: dict[str, Any], row: dict[str, Any]) -> None:
        """EmailService listener. May be called from a worker thread."""
        vendor = self.vendor_for(thread["vendor_email"])
        if not vendor or self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._spawn, thread["id"], row)

    def _spawn(self, thread_id: str, row: dict[str, Any]) -> None:
        task = asyncio.ensure_future(self._respond(thread_id, row))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- one reply ---------------------------------------------------------------------------
    async def _respond(self, thread_id: str, outbound: dict[str, Any]) -> None:
        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        async with lock:  # replies on one thread stay in order
            try:
                thread = self.db.get_thread(thread_id)
                vendor = self.vendor_for(thread["vendor_email"]) if thread else None
                if not vendor:
                    return
                emails = self.db.emails_for_thread(thread_id)
                n_out = sum(e["direction"] == "out" for e in emails)
                n_in = sum(e["direction"] == "in" for e in emails)
                plan = plan_reply(vendor, n_out, n_in)
                if plan is None:
                    return
                await asyncio.sleep(max(0.0, float(vendor.get("reply_delay_s", 8)) * self.s.sim_speed))
                thread = self.db.get_thread(thread_id)
                run = self.db.get_run(thread["run_id"]) if thread else None
                if not thread or thread["status"] not in OPEN_THREAD_STATUSES or not run or run["status"] != "outreach":
                    return
                body = await self._write(thread, vendor, plan, outbound)
                subject = outbound["subject"] if outbound["subject"].lower().startswith("re:") else f"RE: {outbound['subject']}"
                parsed = ParsedEmail(
                    from_addr=vendor["email"].lower(), to_addr=self.s.from_address, subject=subject,
                    message_id=make_msgid(domain=vendor["email"].split("@")[-1]),
                    in_reply_to=outbound.get("message_id"),
                    references=[m for m in [*(outbound.get("references_") or "").split(), outbound.get("message_id")] if m],
                    body=body)
                log.info("Simulated reply from %s (%s)", vendor["name"], plan["kind"])
                if self.deliver:
                    await self.deliver(parsed)
            except Exception:
                log.exception("Vendor simulator failed for thread %s", thread_id)

    async def _write(self, thread: dict[str, Any], vendor: dict[str, Any], plan: dict[str, Any],
                     outbound: dict[str, Any]) -> str:
        text = None
        if self.client is not None and self.agent_id:
            try:
                text = await asyncio.wait_for(self._write_llm(thread["id"], vendor, plan, outbound["body"] or ""), 120)
            except Exception:
                log.exception("LLM vendor reply failed; using template for %s", vendor["name"])
        if not text or not text.strip():
            return write_scripted(vendor, plan)
        if plan.get("injection"):  # added by code so the demo always contains it
            text = f"{text.rstrip()}\n\n{plan['injection']}"
        return text.strip()

    # ---- ZooWork wording -----------------------------------------------------------------------
    async def ensure_agent(self) -> str | None:
        """Create / update / start the vendor_sim agent. Returns None if ZooWork is unavailable."""
        if self.client is None:
            return None
        from .zoowork_runtime import load_skill  # local import: keeps the scripted mode light

        labels = {"app": self.s.agent_label_app, "env": self.s.agent_label_env, "role": "vendor_sim"}
        models = [r for r in await self.client.list_models()
                  if r.get("selectable") is not False and "model" in (r.get("default_for") or [])]
        model = self.s.zoowork_model or (models[0]["model"] if models else None)
        resource = {"name": "pd-partner-vendor-sim", "model": {"primary": model},
                    "persona": {"docs": [{"name": "AGENTS.md", "content": load_skill("vendor_sim")}]},
                    "labels": labels, "sandbox": {"scope": "agent"}}
        page = await self.client.list_agents(labels=labels)
        if page.data:
            agent_id = page.data[0]["agent_id"]
            await self.client.update_agent(agent_id, {"model": resource["model"], "persona": resource["persona"]})
        else:
            created = await self.client.create_agent(
                resource, idempotency_key=f"{self.s.agent_label_app}-{self.s.agent_label_env}-vendor_sim-v1")
            agent_id = created["agent_id"]
        await self.client.start_agent(agent_id)
        await self.client.wait_until_running(agent_id, timeout=90)
        self.agent_id = agent_id
        return agent_id

    async def _write_llm(self, thread_id: str, vendor: dict[str, Any], plan: dict[str, Any], buyer_email: str) -> str:
        from zoowork import assistant_text, is_run_finished, run_outcome

        message = llm_instructions(vendor, plan, buyer_email)
        sess = self._sessions.get(thread_id)
        if sess is None:
            card = {k: vendor.get(k) for k in ("name", "contact", "country", "style", "personality")}
            created = await self.client.create_session(self.agent_id, {"initial_events": [{
                "type": "user.message",
                "content": f"PERSONA CARD (you are this vendor):\n{json.dumps(card, ensure_ascii=False)}\n\n{message}"}]})
            sess = self._sessions[thread_id] = {"session_id": created["session_id"], "cursor": None}
        else:
            receipts = await self.client.post_events(self.agent_id, sess["session_id"],
                                                     [{"type": "user.message", "content": message}])
            if not receipts or receipts[0].get("accepted") is not True:
                raise RuntimeError(f"vendor_sim message not accepted: {receipts}")
        parts: list[str] = []
        async for event in self.client.stream_events(self.agent_id, sess["session_id"], cursor=sess["cursor"]):
            parts.append(assistant_text(event))
            if event.cursor:
                sess["cursor"] = event.cursor
            if is_run_finished(event):
                if run_outcome(event) != "succeeded":
                    raise RuntimeError(f"vendor_sim turn {run_outcome(event)}")
                break
        return "".join(parts)
