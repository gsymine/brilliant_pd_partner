"""Brilliant PD Partner — FastAPI backend.

Run:  .venv/bin/uvicorn app.main:app --reload   then open http://127.0.0.1:8000
Data is fictional sample data (app/seed.py) until the ZooWork agent is wired in (app/agent.py).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, db, fields, scoring, seed

ROUND_ID = 1  # single active round for now
STATIC = Path(__file__).resolve().parent / "static"



@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed.seed()  # creates tables and loads sample data on first run
    yield


app = FastAPI(title="Brilliant PD Partner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------- helpers
def vendor_status(c, vid: str) -> str:
    """waiting | needs_followup | followup_sent | needs_check | complete"""
    rv = c.execute("SELECT replied_at FROM round_vendors WHERE round_id=? AND vendor_id=?", (ROUND_ID, vid)).fetchone()
    prods = db.rows(c.execute("SELECT * FROM products WHERE round_id=? AND vendor_id=?", (ROUND_ID, vid)))
    if not rv or not rv["replied_at"] or not prods:
        return "waiting"
    if any(fields.missing_required(p["fields"]) for p in prods):
        last_in = c.execute("SELECT MAX(created_at) FROM messages WHERE round_id=? AND vendor_id=? AND direction='in'",
                            (ROUND_ID, vid)).fetchone()[0]
        sent = c.execute("SELECT 1 FROM messages WHERE round_id=? AND vendor_id=? AND kind='followup' AND status='sent' "
                         "AND sent_at>=?", (ROUND_ID, vid, last_in)).fetchone()
        return "followup_sent" if sent else "needs_followup"
    if any(p["status"] != "saved" for p in prods):
        return "needs_check"
    return "complete"


def vendors_map(c) -> dict:
    return {v["id"]: v for v in db.rows(c.execute("SELECT * FROM vendors"))}


def product_view(p: dict, vendors: dict) -> dict:
    p["vendor_name"] = vendors[p["vendor_id"]]["name"]
    p["missing"] = fields.missing_required(p["fields"])
    p["landed_usd"] = scoring.landed_usd(p["fields"])
    return p


def get_product(c, pid: int) -> dict:
    p = db.row(c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone())
    if not p:
        raise HTTPException(404, "product not found")
    return p


# ---------------------------------------------------------------- meta & round
@app.get("/api/meta")
def meta():
    return {"fields": fields.as_dicts(), "dimensions": [{"key": k, "label": l} for k, l in scoring.DIMENSIONS],
            "presets": scoring.PRESETS, "trends": seed.TRENDS}


@app.get("/api/round")
def get_round():
    with db.connect() as c:
        r = db.row(c.execute("SELECT * FROM rounds WHERE id=?", (ROUND_ID,)).fetchone())
        vids = [x[0] for x in c.execute("SELECT vendor_id FROM round_vendors WHERE round_id=?", (ROUND_ID,))]
        statuses = [vendor_status(c, v) for v in vids]
        counts = {s: statuses.count(s) for s in ("waiting", "needs_followup", "followup_sent", "needs_check", "complete")}
        n_products = c.execute("SELECT COUNT(*) FROM products WHERE round_id=?", (ROUND_ID,)).fetchone()[0]
        n_pending = c.execute("SELECT COUNT(*) FROM messages WHERE round_id=? AND status='pending_approval'",
                              (ROUND_ID,)).fetchone()[0]
    invited = len(vids) or 1
    r.update(invited=len(vids), counts=counts, products=n_products, pending_approvals=n_pending,
             complete_ratio=counts["complete"] / invited,
             ready=counts["complete"] / invited >= r["threshold"])
    return r


class RoundUpdate(BaseModel):
    theme: str | None = None
    requirements: str | None = None
    categories: str | None = None
    deadline: str | None = None
    threshold: float | None = None


@app.put("/api/round")
def update_round(body: RoundUpdate):
    data = body.model_dump(exclude_none=True)
    if data:
        with db.connect() as c:
            c.execute(f"UPDATE rounds SET {', '.join(f'{k}=?' for k in data)} WHERE id=?", (*data.values(), ROUND_ID))
    return get_round()


@app.get("/api/activity")
def activity():
    with db.connect() as c:
        return db.rows(c.execute("SELECT * FROM activity WHERE round_id=? ORDER BY ts DESC, id DESC LIMIT 30", (ROUND_ID,)))


# ---------------------------------------------------------------- vendors & outreach
@app.get("/api/vendors")
def list_vendors():
    with db.connect() as c:
        out = []
        for v in db.rows(c.execute("SELECT * FROM vendors ORDER BY CAST(SUBSTR(id,2) AS INTEGER)")):
            invited = c.execute("SELECT 1 FROM round_vendors WHERE round_id=? AND vendor_id=?", (ROUND_ID, v["id"])).fetchone()
            v["status"] = vendor_status(c, v["id"]) if invited else None
            v["product_count"] = c.execute("SELECT COUNT(*) FROM products WHERE round_id=? AND vendor_id=?",
                                           (ROUND_ID, v["id"])).fetchone()[0]
            out.append(v)
        return out


class Outreach(BaseModel):
    vendor_ids: list[str]


@app.post("/api/outreach")
def outreach(body: Outreach):
    """Draft a personalized RFQ for each selected vendor. Drafts wait for approval."""
    with db.connect() as c:
        r = db.row(c.execute("SELECT * FROM rounds WHERE id=?", (ROUND_ID,)).fetchone())
        vendors = vendors_map(c)
        for vid in body.vendor_ids:
            subject, text = agent.draft_rfq(r, vendors[vid])
            c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", (ROUND_ID, vid, "out", "rfq", subject, text, "pending_approval", db.now()))
        db.log(c, ROUND_ID, f"Drafted RFQ for {len(body.vendor_ids)} vendors, waiting for approval.")
    return {"drafted": len(body.vendor_ids)}


# ---------------------------------------------------------------- responses & extraction
@app.get("/api/responses/{vid}")
def vendor_response(vid: str):
    with db.connect() as c:
        vendors = vendors_map(c)
        if vid not in vendors:
            raise HTTPException(404, "vendor not found")
        msgs = db.rows(c.execute("SELECT * FROM messages WHERE round_id=? AND vendor_id=? ORDER BY created_at, id",
                                 (ROUND_ID, vid)))
        prods = [product_view(p, vendors) for p in
                 db.rows(c.execute("SELECT * FROM products WHERE round_id=? AND vendor_id=? ORDER BY id", (ROUND_ID, vid)))]
        return {"vendor": vendors[vid], "status": vendor_status(c, vid), "messages": msgs, "products": prods}


class FieldsUpdate(BaseModel):
    fields: dict


@app.patch("/api/products/{pid}")
def update_product(pid: int, body: FieldsUpdate):
    with db.connect() as c:
        p = get_product(c, pid)
        for k, v in body.fields.items():
            if k not in fields.FIELD_KEYS:
                raise HTTPException(400, f"unknown field {k}")
            if k in fields.NUMERIC and v not in (None, ""):
                try:
                    v = float(v) if "." in str(v) else int(v)
                except ValueError:
                    raise HTTPException(400, f"{k} must be a number")
            p["fields"][k] = None if v == "" else v
        low = [k for k in (p["low_confidence"] or []) if k not in body.fields]  # edited = human-checked
        c.execute("UPDATE products SET fields=?, low_confidence=? WHERE id=?", (json.dumps(p["fields"]), json.dumps(low), pid))
        return product_view(get_product(c, pid), vendors_map(c))


@app.post("/api/products/{pid}/confirm")
def confirm_product(pid: int):
    with db.connect() as c:
        p = get_product(c, pid)
        missing = fields.missing_required(p["fields"])
        if missing:
            raise HTTPException(400, f"missing required fields: {', '.join(missing)}")
        c.execute("UPDATE products SET status='saved', low_confidence='[]' WHERE id=?", (pid,))
        vendor = vendors_map(c)[p["vendor_id"]]
        if not p["scores"]:
            scores = agent.score_product(p, vendor["rating"] or 4.0)
            c.execute("UPDATE products SET scores=? WHERE id=?", (json.dumps(scores), pid))
        vname = vendor["name"]
        db.log(c, ROUND_ID, f"Saved {p['fields'].get('product_name')} ({vname}) to the database.", "good")
        return {"ok": True, "vendor_status": vendor_status(c, p["vendor_id"])}


@app.post("/api/responses/{vid}/followup")
def draft_followup(vid: str):
    with db.connect() as c:
        vendor = vendors_map(c)[vid]
        prods = db.rows(c.execute("SELECT * FROM products WHERE round_id=? AND vendor_id=?", (ROUND_ID, vid)))
        missing = {p["fields"].get("product_name") or f"#{p['id']}": fields.missing_required(p["fields"]) for p in prods}
        missing = {k: v for k, v in missing.items() if v}
        if not missing:
            raise HTTPException(400, "nothing missing")
        c.execute("DELETE FROM messages WHERE round_id=? AND vendor_id=? AND kind='followup' AND status='pending_approval'",
                  (ROUND_ID, vid))
        subject, text = agent.draft_followup(vendor, missing)
        c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (ROUND_ID, vid, "out", "followup", subject, text, "pending_approval", db.now()))
        db.log(c, ROUND_ID, f"Follow-up drafted for {vendor['name']}.", "warn")
    return {"ok": True}


# ---------------------------------------------------------------- database
@app.get("/api/products")
def list_products():
    with db.connect() as c:
        vendors = vendors_map(c)
        return [product_view(p, vendors) for p in
                db.rows(c.execute("SELECT * FROM products WHERE round_id=? ORDER BY CAST(SUBSTR(vendor_id,2) AS INTEGER), id",
                                  (ROUND_ID,)))]


# ---------------------------------------------------------------- analysis & decisions
@app.get("/api/ranking")
def ranking(cost: float = 25, moq: float = 15, lead: float = 15, trend: float = 20, spec: float = 15, vendor: float = 10):
    weights = dict(cost=cost, moq=moq, lead=lead, trend=trend, spec=spec, vendor=vendor)
    rows = list_products()
    for p in rows:
        p["overall"] = scoring.overall(p["scores"], weights)
        p["suggested"] = scoring.suggest(p["overall"], p["scores"], p["change_request"])
        p["final_decision"] = p["decision"] or p["suggested"]
    rows.sort(key=lambda p: -1 if p["overall"] is None else p["overall"], reverse=True)
    return rows


class Decision(BaseModel):
    decision: str | None = None  # accept | modify | reject | None (= follow suggestion)
    change_request: str | None = None


@app.put("/api/products/{pid}/decision")
def set_decision(pid: int, body: Decision):
    if body.decision not in (None, "accept", "modify", "reject"):
        raise HTTPException(400, "bad decision")
    with db.connect() as c:
        get_product(c, pid)
        c.execute("UPDATE products SET decision=?, change_request=COALESCE(?, change_request) WHERE id=?",
                  (body.decision, body.change_request, pid))
    return {"ok": True}


@app.post("/api/decisions/draft")
def draft_decisions(cost: float = 25, moq: float = 15, lead: float = 15, trend: float = 20, spec: float = 15, vendor: float = 10):
    """One email per vendor summarizing decisions on all its products. Drafts wait for approval."""
    rows = [p for p in ranking(cost, moq, lead, trend, spec, vendor) if p["final_decision"] != "pending"]
    by_vendor: dict[str, list] = {}
    for p in rows:
        by_vendor.setdefault(p["vendor_id"], []).append(p)
    with db.connect() as c:
        vendors = vendors_map(c)
        c.execute("DELETE FROM messages WHERE round_id=? AND kind='decision' AND status='pending_approval'", (ROUND_ID,))
        for vid, prods in by_vendor.items():
            subject, text = agent.draft_decision(vendors[vid], prods)
            c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", (ROUND_ID, vid, "out", "decision", subject, text, "pending_approval", db.now()))
        db.log(c, ROUND_ID, f"Drafted decision emails for {len(by_vendor)} vendors, waiting for approval.")
    return {"drafted": len(by_vendor)}


# ---------------------------------------------------------------- approvals (outbox)
@app.get("/api/approvals")
def approvals():
    with db.connect() as c:
        vendors = vendors_map(c)
        msgs = db.rows(c.execute("SELECT * FROM messages WHERE round_id=? AND status='pending_approval' ORDER BY created_at, id",
                                 (ROUND_ID,)))
        for m in msgs:
            m["vendor_name"] = vendors[m["vendor_id"]]["name"]
            m["to"] = vendors[m["vendor_id"]]["email"]
        return msgs


class MessageEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


@app.patch("/api/messages/{mid}")
def edit_message(mid: int, body: MessageEdit):
    with db.connect() as c:
        c.execute("UPDATE messages SET subject=COALESCE(?,subject), body=COALESCE(?,body) WHERE id=? AND status='pending_approval'",
                  (body.subject, body.body, mid))
    return {"ok": True}


@app.post("/api/messages/{mid}/approve")
def approve_message(mid: int):
    """Marks the email as sent. Real sending (Gmail/SMTP) plugs in here later."""
    with db.connect() as c:
        m = db.row(c.execute("SELECT * FROM messages WHERE id=? AND status='pending_approval'", (mid,)).fetchone())
        if not m:
            raise HTTPException(404, "no pending message")
        c.execute("UPDATE messages SET status='sent', sent_at=? WHERE id=?", (db.now(), mid))
        if m["kind"] == "rfq":
            c.execute("INSERT OR IGNORE INTO round_vendors VALUES (?,?,?,NULL)", (ROUND_ID, m["vendor_id"], db.now()))
        vname = vendors_map(c)[m["vendor_id"]]["name"]
        db.log(c, ROUND_ID, f"Sent {m['kind']} email to {vname}.", "good")
    return {"ok": True}


@app.post("/api/messages/{mid}/discard")
def discard_message(mid: int):
    with db.connect() as c:
        c.execute("DELETE FROM messages WHERE id=? AND status='pending_approval'", (mid,))
    return {"ok": True}


# ---------------------------------------------------------------- demo helpers
@app.post("/api/demo/simulate-reply/{vid}")
def simulate_reply(vid: str):
    """Pretend a vendor replied, so the flow can be demoed without real email."""
    with db.connect() as c:
        vendors = vendors_map(c)
        if vid not in vendors:
            raise HTTPException(404, "vendor not found")
        result = agent.simulate_vendor_reply(c, ROUND_ID, vendors[vid])
    return result


@app.post("/api/demo/reset")
def reset():
    seed.seed(force=True)
    return {"ok": True}
