"""FastAPI entry point.

    uvicorn app.main:app --port 8000          (run from the backend/ folder)

Starts the three ZooWork agents, an IMAP poller for vendor replies and a timer for reminders and
deadlines, and exposes the endpoints the frontend team will call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from zoowork import create_zoowork_client

from .config import BACKEND_DIR, load_settings
from .db import Database
from .email_service import EmailService, ParsedEmail
from .orchestrator import Orchestrator
from .tools import vendor_memory
from .vendor_sim import VendorSimulator
from .zoowork_runtime import ZooworkRuntime

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("pd_backend")


# ---------------------------------------------------------------- request models
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class BriefItem(BaseModel):
    sku: str = Field(..., description="Stable id for the brief item, e.g. 'A'")
    name: str
    requirements: str | None = Field(None, description="Hard requirements and preferences for this item")


class Vendor(BaseModel):
    name: str
    email: str = Field(..., pattern=EMAIL_PATTERN)
    country: str | None = Field(None, description="ISO-2 production country, e.g. CN")


class StartRunRequest(BaseModel):
    title: str
    brief: str = Field(..., description="Theme and requests sent to vendors (markdown)")
    brief_items: list[BriefItem] = Field(..., min_length=1)
    vendors: list[Vendor] = Field(..., min_length=1)
    required_fields: list[str] | None = Field(None, description="Checklist; default = brief's 'please reply with' list")
    costing: dict[str, Any] | None = Field(None, description="{forecast: {sku: {...}}, assumptions: {...}}")
    deadline_hours: float | None = None
    created_by: str | None = None


class DecisionRequest(BaseModel):
    outcome: Literal["accepted", "denied_all"]
    accepted_offer_ids: list[str] = []
    notes: str | None = None
    decided_by: str | None = None


class InboundEmail(BaseModel):
    """For mail providers that push inbound email by webhook, and for manual testing."""
    from_addr: str = Field(..., pattern=EMAIL_PATTERN)
    subject: str
    body: str
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = []


# ---------------------------------------------------------------- app lifecycle
settings = load_settings()
db = Database(settings.db_path)
email_service = EmailService(settings, db)
orchestrator = Orchestrator(db, settings, email_service)
simulator = VendorSimulator(settings, db) if settings.sim_mode != "off" else None
if simulator:
    email_service.listeners.append(simulator.on_outbound)
DEMO_RUN_PATH = BACKEND_DIR / "examples" / "drinkware_demo_run.json"


async def _poll_inbox() -> None:
    while True:
        try:
            for parsed in await asyncio.to_thread(email_service.fetch_unseen):
                await orchestrator.handle_inbound(parsed)
        except Exception:
            log.exception("IMAP poll failed")
        await asyncio.sleep(settings.poll_seconds)


async def _ticker() -> None:
    while True:
        await asyncio.sleep(settings.tick_seconds)
        try:
            await orchestrator.tick()
        except Exception:
            log.exception("Tick failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with create_zoowork_client() as client:
        runtime = ZooworkRuntime(client, db, settings, orchestrator.tool_context)
        orchestrator.runtime = runtime
        agents = await runtime.ensure_agents()
        log.info("ZooWork agents ready: %s", agents)
        await runtime.recover_pending_tool_calls()
        if simulator:
            simulator.attach(asyncio.get_running_loop(), orchestrator.handle_inbound,
                             client if settings.sim_mode == "llm" else None)
            if settings.sim_mode == "llm":
                try:
                    log.info("Vendor simulator agent ready: %s", await simulator.ensure_agent())
                except Exception:
                    log.exception("Vendor simulator agent unavailable; simulated vendors will use templates")
        background = [asyncio.create_task(_poll_inbox()), asyncio.create_task(_ticker())]
        try:
            yield
        finally:
            for task in background:
                task.cancel()


app = FastAPI(title="Brilliant PD Partner backend", version="0.1.0", lifespan=lifespan)


def _run_or_404(run_id: str) -> dict[str, Any]:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


def _thread_summary(t: dict[str, Any]) -> dict[str, Any]:
    return {k: t[k] for k in ("id", "vendor_name", "vendor_email", "country", "status", "close_reason",
                              "outbound_count", "reminders_sent", "last_outbound_at", "last_inbound_at")}


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "agents": orchestrator.runtime.agent_ids if orchestrator.runtime else {},
            "email_mode": settings.email_mode, "imap": bool(settings.imap_enabled and settings.imap_host),
            "vendor_simulator": {"mode": settings.sim_mode, "agent": simulator.agent_id if simulator else None}}


# ---------------------------------------------------------------- demo: vendor simulator
@app.get("/sim/vendors")
def sim_vendors() -> list[dict[str, Any]]:
    """The fictional vendors the simulator plays (emails ending in .sim)."""
    if not simulator:
        return []
    return [{k: v.get(k) for k in ("key", "name", "email", "contact", "country", "style", "demo_role")}
            for v in simulator.vendors.values()]


class DemoStartRequest(BaseModel):
    extra_vendors: list[Vendor] = Field([], description="Real vendors to add (e.g. a teammate's phone) next to the simulated ones")
    title: str | None = None


@app.post("/demo/start", status_code=201)
async def demo_start(req: DemoStartRequest | None = None) -> dict[str, Any]:
    """Start the drinkware demo run with the simulated vendors (plus optional real ones)."""
    body = json.loads(DEMO_RUN_PATH.read_text(encoding="utf-8"))
    if req:
        body["vendors"] += [v.model_dump() for v in req.extra_vendors]
        if req.title:
            body["title"] = req.title
    return await start_sourcing_process(StartRunRequest(**body))


@app.post("/runs", status_code=201)
async def start_sourcing_process(req: StartRunRequest) -> dict[str, Any]:  # async: spawns background tasks
    """The 'Start Sourcing Process' button."""
    skus = [i.sku for i in req.brief_items]
    if len(set(skus)) != len(skus):
        raise HTTPException(422, "brief_items must have unique sku values")
    return orchestrator.create_run(req.model_dump())


@app.get("/runs")
def list_runs() -> list[dict[str, Any]]:
    return [{k: r[k] for k in ("id", "title", "status", "created_at", "deadline_at")} for r in db.list_runs()]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = _run_or_404(run_id)
    return {**run, "threads": [_thread_summary(t) for t in db.threads_for_run(run_id)],
            "decision": db.get_decision(run_id)}


@app.get("/runs/{run_id}/events")
def get_run_events(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    return db.run_events(run_id, limit)


@app.get("/runs/{run_id}/threads/{thread_id}")
def get_thread(run_id: str, thread_id: str) -> dict[str, Any]:
    run = _run_or_404(run_id)
    t = db.get_thread(thread_id)
    if not t or t["run_id"] != run_id:
        raise HTTPException(404, "Thread not found")
    emails = [{k: v for k, v in e.items() if k != "attachments"} |
              {"attachments": [{k: a[k] for k in ("id", "filename", "content_type", "size")} for a in e["attachments"]]}
              for e in db.emails_for_thread(thread_id)]
    return {**_thread_summary(t), "emails": emails,
            "memory": vendor_memory(db, thread_id, run["required_fields"]),
            "fact_history": db.facts_for_thread(thread_id, latest_only=False)}


@app.get("/runs/{run_id}/offers")
def get_offers(run_id: str) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    return db.offers_for_run(run_id)


@app.get("/runs/{run_id}/report")
def get_report(run_id: str) -> dict[str, Any]:
    _run_or_404(run_id)
    report = db.get_report(run_id)
    if not report:
        raise HTTPException(404, "Report not ready")
    return report


@app.post("/runs/{run_id}/close-outreach")
async def close_outreach(run_id: str) -> dict[str, Any]:
    """Stop waiting: mark open vendor threads stalled and move on to extraction."""
    run = _run_or_404(run_id)
    if run["status"] != "outreach":
        raise HTTPException(409, f"Run is {run['status']}")
    await orchestrator.check_gate(run_id, force=True)
    return {"status": db.get_run(run_id)["status"]}  # type: ignore[index]


@app.post("/runs/{run_id}/retry")
async def retry(run_id: str) -> dict[str, Any]:
    _run_or_404(run_id)
    try:
        await orchestrator.retry(run_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"status": "extracting"}


@app.post("/runs/{run_id}/decision")
def decide(run_id: str, req: DecisionRequest) -> dict[str, Any]:
    try:
        return orchestrator.decide(run_id, req.outcome, req.accepted_offer_ids, req.notes, req.decided_by)
    except LookupError:
        raise HTTPException(404, "Run not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/inbound-email", status_code=202)
async def inbound_email(req: InboundEmail, x_inbound_token: str | None = Header(None)) -> dict[str, Any]:
    expected = os.environ.get("INBOUND_TOKEN")
    if not expected or x_inbound_token != expected:
        raise HTTPException(401, "Invalid or missing X-Inbound-Token (set INBOUND_TOKEN to enable this endpoint)")
    parsed = ParsedEmail(from_addr=req.from_addr.lower(), to_addr=settings.from_address, subject=req.subject,
                         message_id=req.message_id, in_reply_to=req.in_reply_to, references=req.references,
                         body=req.body)
    row = await orchestrator.handle_inbound(parsed)
    return {"matched": row is not None, "email_id": row["id"] if row else None}
