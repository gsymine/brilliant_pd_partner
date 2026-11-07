"""Backend handlers for the agents' custom tools.

Every handler receives a ToolContext that pins it to one run (and, for the Communication Agent,
one vendor thread). An agent can never reach another vendor's thread or another run: the scope
comes from the session the call arrived on, not from the tool input.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Callable

from . import costing
from .config import Settings
from .db import Database, now_iso
from .email_service import OPEN_THREAD_STATUSES, EmailService, read_attachment

log = logging.getLogger(__name__)

TERMINAL_THREAD_STATUSES = ("complete", "declined", "stalled")
NOT_AVAILABLE = {"not available", "n/a", "not applicable", "none"}

Content = list[dict[str, Any]]


class ToolError(Exception):
    """Raised by a handler to return an error result the agent can read and fix."""


@dataclass
class ToolContext:
    role: str
    run_id: str
    thread_id: str | None
    db: Database
    email: EmailService
    settings: Settings


def _json(value: Any) -> Content:
    return [{"type": "json", "value": value}]


def untrusted(text: str) -> str:
    return f"<vendor_content untrusted=\"true\">\n{text}\n</vendor_content>"


def _email_view(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "email_id": e["id"], "direction": e["direction"], "at": e["created_at"], "from": e["from_addr"],
        "to": e["to_addr"], "subject": e["subject"],
        "body": untrusted(e["body"] or "") if e["direction"] == "in" else e["body"],
        "attachments": [{"attachment_id": a["id"], "filename": a["filename"], "content_type": a["content_type"],
                         "size": a["size"]} for a in e["attachments"]],
    }


def _run(ctx: ToolContext) -> dict[str, Any]:
    run = ctx.db.get_run(ctx.run_id)
    if not run:
        raise ToolError("Run not found")
    return run


def _thread(ctx: ToolContext, thread_id: str | None = None) -> dict[str, Any]:
    tid = thread_id or ctx.thread_id
    thread = ctx.db.get_thread(tid) if tid else None
    if not thread or thread["run_id"] != ctx.run_id:
        raise ToolError(f"Unknown thread_id {tid!r} for this run")
    return thread


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def vendor_memory(db: Database, thread_id: str, required_fields: list[str]) -> dict[str, Any]:
    products: dict[str, dict[str, Any]] = {}
    for f in db.facts_for_thread(thread_id):
        p = products.setdefault(f["product_ref"], {"product_ref": f["product_ref"], "fields": {}})
        if f["field"] == "brief_item":
            p["brief_item"] = f["value"]
        p["fields"][f["field"]] = {
            "value": f["value"], "source_email_id": f["source_email_id"], "quote": f["quote"],
            "version": f["version"], "saved_at": f["created_at"],
        }
    for p in products.values():
        p["missing_fields"] = [k for k in required_fields if not _present((p["fields"].get(k) or {}).get("value"))]
    return {
        "products": list(products.values()),
        "all_complete": bool(products) and all(not p["missing_fields"] for p in products.values()),
        "note": None if products else "No facts saved yet. Product list unknown: ask the vendor what they propose.",
        "how_to_mark_unavailable": "If the vendor confirms a field does not apply or cannot be provided, "
                                   "save it with value 'not available' and the supporting quote.",
    }


# ---------------------------------------------------------------- Communication Agent tools
def send_email(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    thread = _thread(ctx)
    if thread["status"] not in OPEN_THREAD_STATUSES:
        raise ToolError(f"Thread is {thread['status']}; no more emails can be sent.")
    if thread["outbound_count"] >= ctx.settings.max_outbound_per_vendor:
        raise ToolError("Outbound email limit for this vendor reached. Close the thread with mark_thread_status.")
    subject, body = (inp.get("subject") or "").strip(), (inp.get("body") or "").strip()
    if not subject or not body:
        raise ToolError("subject and body are required")
    row = ctx.email.send(thread, subject, body)
    changes: dict[str, Any] = {
        "outbound_count": thread["outbound_count"] + 1, "last_outbound_at": row["created_at"],
        "status": "awaiting_reply",
    }
    ctx.db.update("vendor_threads", "id", thread["id"], changes)
    ctx.db.log(ctx.run_id, "email_out", f"{inp.get('purpose', 'email')} to {thread['vendor_name']}: {row['subject']}", thread["id"])
    return _json({"sent": True, "email_id": row["id"], "subject": row["subject"], "mode": ctx.settings.email_mode})


def read_thread(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    thread = _thread(ctx, inp.get("thread_id") if ctx.role != "communication" else None)
    return _json({
        "thread_id": thread["id"], "vendor": thread["vendor_name"], "status": thread["status"],
        "emails": [_email_view(e) for e in ctx.db.emails_for_thread(thread["id"])],
    })


def save_vendor_facts(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    thread = _thread(ctx)
    facts = inp.get("facts") or []
    if not facts:
        raise ToolError("facts must be a non-empty list")
    own_emails = {e["id"] for e in ctx.db.emails_for_thread(thread["id"])}
    saved = []
    for f in facts:
        product_ref = str(f.get("product_ref") or "").strip()
        field = str(f.get("field") or "").strip()
        if not product_ref or not field:
            raise ToolError(f"Each fact needs product_ref and field: {f}")
        src = f.get("source_email_id")
        if src and src not in own_emails:
            raise ToolError(f"source_email_id {src} is not an email in this thread")
        version = ctx.db.add_fact(thread["id"], product_ref, field, f.get("value"), src, f.get("quote"), f.get("confidence"))
        if f.get("brief_item"):
            ctx.db.add_fact(thread["id"], product_ref, "brief_item", f["brief_item"], src, None, f.get("confidence"))
        saved.append({"product_ref": product_ref, "field": field, "version": version})
    ctx.db.log(ctx.run_id, "facts_saved", f"{len(saved)} facts saved for {thread['vendor_name']}", thread["id"])
    run = _run(ctx)
    memory = vendor_memory(ctx.db, thread["id"], run["required_fields"])
    return _json({"saved": saved, "missing_by_product": {p["product_ref"]: p["missing_fields"] for p in memory["products"]}})


def get_vendor_memory(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    thread = _thread(ctx, inp.get("thread_id") if ctx.role != "communication" else None)
    run = _run(ctx)
    return _json({"thread_id": thread["id"], "vendor": thread["vendor_name"],
                  **vendor_memory(ctx.db, thread["id"], run["required_fields"])})


def mark_thread_status(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    thread = _thread(ctx)
    status = inp.get("status")
    if status not in TERMINAL_THREAD_STATUSES:
        raise ToolError(f"status must be one of {TERMINAL_THREAD_STATUSES}")
    if status == "complete":
        memory = vendor_memory(ctx.db, thread["id"], _run(ctx)["required_fields"])
        if not memory["all_complete"]:
            gaps = {p["product_ref"]: p["missing_fields"] for p in memory["products"]} or "no products saved"
            raise ToolError(f"Cannot mark complete, fields still missing: {gaps}. Follow up with the vendor, or save "
                            "confirmed-unavailable fields as 'not available'.")
    ctx.db.update("vendor_threads", "id", thread["id"], {"status": status, "close_reason": inp.get("reason")})
    ctx.db.log(ctx.run_id, "thread_closed", f"{thread['vendor_name']}: {status} ({inp.get('reason', '')})", thread["id"])
    return _json({"thread_id": thread["id"], "status": status})


# ---------------------------------------------------------------- Extraction Agent tools
def list_vendor_threads(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    return _json([
        {"thread_id": t["id"], "vendor": t["vendor_name"], "vendor_email": t["vendor_email"],
         "country": t["country"], "status": t["status"], "close_reason": t["close_reason"]}
        for t in ctx.db.threads_for_run(ctx.run_id)
    ])


def read_attachment_tool(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    att_id = inp.get("attachment_id") or ""
    found = read_attachment(ctx.db, att_id)
    if not found:
        raise ToolError(f"Unknown attachment_id {att_id!r}")
    meta, data = found
    email_row = ctx.db.get_email(att_id.split(":")[0])
    if not email_row or email_row["run_id"] != ctx.run_id:
        raise ToolError("Attachment does not belong to this run")
    ctype = meta["content_type"]
    info = {k: meta[k] for k in ("id", "filename", "content_type", "size")}
    if ctype in {"image/png", "image/jpeg", "image/gif", "image/webp"} and len(data) <= 4 * 1024 * 1024:
        return [{"type": "json", "value": info},
                {"type": "image", "source": {"type": "base64", "media_type": ctype, "data": base64.b64encode(data).decode()}}]
    if ctype.startswith("text/") or meta["filename"].lower().endswith((".csv", ".txt", ".md", ".json")):
        return [{"type": "json", "value": info},
                {"type": "text", "text": untrusted(data.decode("utf-8", errors="replace")[:50_000])}]
    return _json({**info, "content": None, "note": "Binary format not readable by this tool; rely on the email text."})


def _validate_offer(o: dict[str, Any], run: dict[str, Any], threads: dict[str, dict[str, Any]]) -> list[str]:
    errs = []
    name = o.get("product_name") or "?"
    if o.get("thread_id") not in threads:
        errs.append(f"{name}: unknown thread_id {o.get('thread_id')!r}")
    skus = {str(i["sku"]) for i in run["brief_items"]}
    if skus and str(o.get("brief_item")) not in skus:
        errs.append(f"{name}: brief_item must be one of {sorted(skus)}")
    if o.get("path") not in ("accept", "modify", "co_develop"):
        errs.append(f"{name}: path must be accept | modify | co_develop")
    if not isinstance(o.get("meets_spec"), bool):
        errs.append(f"{name}: meets_spec must be true or false")
    for k in ("unit_price", "extra_costs_usd"):
        if o.get(k) is not None and not isinstance(o.get(k), (int, float)):
            errs.append(f"{name}: {k} must be a number or null")
    for k in ("moq", "production_days", "sampling_days"):
        if o.get(k) is not None and not isinstance(o.get(k), int):
            errs.append(f"{name}: {k} must be an integer or null")
    return errs


def save_offers(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    run = _run(ctx)
    threads = {t["id"]: t for t in ctx.db.threads_for_run(ctx.run_id)}
    offers = inp.get("offers") or []
    errors = [e for o in offers for e in _validate_offer(o, run, threads)]
    if errors:
        raise ToolError("Nothing saved. Fix these and call save_offers again with ALL offers: " + "; ".join(errors))
    normalized = []
    for o in offers:
        t = threads[o["thread_id"]]
        normalized.append({
            **o, "vendor_name": t["vendor_name"], "country": o.get("country") or t["country"],
            "extra_costs_usd": o.get("extra_costs_usd") or 0, "spec_issues": o.get("spec_issues") or [],
        })
    ids = ctx.db.replace_offers(ctx.run_id, normalized)
    ctx.db.log(ctx.run_id, "offers_saved", f"{len(ids)} offers saved")
    return _json({"saved": len(ids), "offer_ids": ids})


# ---------------------------------------------------------------- Analysis Agent tools
def get_run_context(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    run = _run(ctx)
    c = run.get("costing") or {}
    return _json({
        "run_id": run["id"], "title": run["title"], "brief": run["brief"], "brief_items": run["brief_items"],
        "required_fields": run["required_fields"],
        "costing_available": {"forecast_items": sorted((c.get("forecast") or {}).keys()),
                              "assumptions": bool(c.get("assumptions"))},
        "assumptions_note": (c.get("assumptions") or {}).get("_note"),
    })


def get_offers(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    return _json(ctx.db.offers_for_run(ctx.run_id))


def score_run(db: Database, run_id: str) -> dict[str, Any]:
    run = db.get_run(run_id)
    assert run is not None
    return costing.score_offers(db.offers_for_run(run_id), run.get("costing"), run["required_fields"])


def run_costing(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    result = score_run(ctx.db, ctx.run_id)
    if not result["offers"]:
        raise ToolError("No offers in this run.")
    return _json(result)


def save_analysis_report(ctx: ToolContext, inp: dict[str, Any]) -> Content:
    scored = score_run(ctx.db, ctx.run_id)
    by_id = {r["offer_id"]: r for r in scored["offers"]}
    assessments = {a["offer_id"]: a for a in inp.get("offer_assessments") or []}
    unknown = [i for i in [*assessments, *(inp.get("recommended_offer_ids") or [])] if i not in by_id]
    missing = [i for i in by_id if i not in assessments]
    if unknown or missing:
        raise ToolError(f"Nothing saved. Unknown offer ids: {unknown}. Offers without an assessment: {missing}.")
    offers = [{**row, "assessment": assessments[row["offer_id"]]} for row in scored["offers"]]
    report = {
        "generated_at": now_iso(),
        "executive_summary": inp.get("executive_summary") or [],
        "recommended_offer_ids": inp.get("recommended_offer_ids") or [],
        "risks_and_open_questions": inp.get("risks_and_open_questions") or [],
        "weights": scored["weights"],
        "ranking_by_item": scored["ranking_by_item"],
        "overall_ranking": scored["overall_ranking"],
        "offers": offers,
    }
    ctx.db.save_report(ctx.run_id, report)
    ctx.db.log(ctx.run_id, "report_saved", f"Analysis report saved ({len(offers)} offers ranked)")
    return _json({"saved": True, "offers_ranked": len(offers)})


HANDLERS: dict[str, dict[str, Callable[[ToolContext, dict[str, Any]], Content]]] = {
    "communication": {
        "send_email": send_email, "read_thread": read_thread, "save_vendor_facts": save_vendor_facts,
        "get_vendor_memory": get_vendor_memory, "mark_thread_status": mark_thread_status,
    },
    "extraction": {
        "list_vendor_threads": list_vendor_threads, "read_thread": read_thread,
        "get_vendor_memory": get_vendor_memory, "read_attachment": read_attachment_tool, "save_offers": save_offers,
    },
    "analysis": {
        "get_run_context": get_run_context, "get_offers": get_offers, "run_costing": run_costing,
        "save_analysis_report": save_analysis_report,
    },
}


def dispatch(ctx: ToolContext, name: str, tool_input: dict[str, Any] | None) -> tuple[Content, bool]:
    """Run one tool. Returns (content, is_error); never raises for agent-fixable problems."""
    handler = HANDLERS.get(ctx.role, {}).get(name)
    if handler is None:
        return [{"type": "text", "text": f"Tool {name!r} is not available to the {ctx.role} agent."}], True
    try:
        return handler(ctx, tool_input or {}), False
    except ToolError as exc:
        return [{"type": "text", "text": str(exc)}], True
    except Exception as exc:  # unexpected backend failure: report, keep the run alive
        log.exception("Tool %s failed", name)
        return [{"type": "text", "text": f"Backend error in {name}: {type(exc).__name__}. Try again later."}], True
