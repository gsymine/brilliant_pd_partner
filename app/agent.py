"""Agent hooks. Today these are simple templates and heuristics so the UI works end to end.

Each function is a seam for the ZooWork agent later:
- draft_rfq / draft_followup / draft_decision -> agent writes the email
- simulate_vendor_reply                       -> real inbox + agent extraction
- score_product                               -> agent + costing / trend analysis
"""
from __future__ import annotations

import json

from . import db, fields, scoring, seed

FIELD_LABEL = {f["key"]: f["label"] for f in fields.as_dicts()}
TARGET_FOB = {"Tumbler": 4.5, "Water bottle": 4.5, "Kids bottle": 3.5, "Glass cup": 2.5, "Mug": 3.5}
FORECAST_UNITS = 1500  # placeholder per-SKU season forecast


def first_name(vendor: dict) -> str:
    return (vendor.get("contact_name") or "there").split()[0]


def draft_rfq(round_: dict, vendor: dict) -> tuple[str, str]:
    subject = f"RFQ — {round_['name']} (reply by {round_['deadline']})"
    body = (f"Hi {first_name(vendor)},\n\nWe're developing our {round_['name']} drinkware collection.\n"
            f"Theme: {round_['theme']}\n\nRequirements:\n{round_['requirements']}\n\n"
            "For each product you propose, please include:\n"
            + "\n".join(f" • {f['label']}" for f in fields.as_dicts() if f["required"])
            + f"\n\nPlease reply by {round_['deadline']}. Thank you!")
    return subject, body


def draft_followup(vendor: dict, missing: dict[str, list[str]]) -> tuple[str, str]:
    n = sum(len(v) for v in missing.values())
    lines = []
    for product, keys in missing.items():
        lines.append(f"{product}:")
        lines += [f"  • {FIELD_LABEL[k]}" for k in keys]
    body = (f"Hi {first_name(vendor)},\n\nThank you for your offer. To include it in our review, could you send:\n\n"
            + "\n".join(lines) + "\n\nThank you!")
    return f"RE: RFQ — {n} detail{'s' if n > 1 else ''} needed", body


def draft_decision(vendor: dict, products: list[dict]) -> tuple[str, str]:
    parts = []
    for p in products:
        name, d = p["fields"].get("product_name"), p["final_decision"]
        if d == "accept":
            parts.append(f"✔ {name}: we'd like to move to sampling. Please send 3 pre-production samples "
                         f"({p['fields'].get('colors')}).")
        elif d == "modify":
            parts.append(f"✎ {name}: we're interested if we can adjust:\n    {p['change_request'] or 'price and lead time'}")
        else:
            parts.append(f"✖ {name}: we won't move forward this season.")
    body = (f"Hi {first_name(vendor)},\n\nThank you for your proposals. Our decisions:\n\n" + "\n\n".join(parts)
            + "\n\nPlease let us know by Nov 20 on any requested changes.\n\nBest regards,\nProduct Development")
    return "Spring 2027 drinkware — our decisions", body


def score_product(p: dict, vendor_rating: float = 4.0) -> dict:
    """Rough placeholder scoring until the agent/costing analysis is wired in."""
    f = p["fields"]
    landed = scoring.landed_usd(f) or 0
    target = TARGET_FOB.get(f.get("category"), 4.0) * 1.3
    cost = max(0, min(100, round(100 - (landed - target * 0.6) / target * 100)))
    moq = max(0, min(100, round(100 - max(0, (f.get("moq") or 0) - FORECAST_UNITS) / FORECAST_UNITS * 100)))
    lead = max(0, min(100, round(100 - max(0, (f.get("lead_time_days") or 90) - 30) * 1.5)))
    text = " ".join(str(v) for v in f.values()).lower()
    hits = sum(any(w in text for w in t["name"].lower().split()[:2]) for t in seed.TRENDS if t["direction"] == "up")
    trend = min(100, 45 + hits * 12)
    certs = (f.get("certifications") or "").upper()
    spec = 40 + 25 * ("FDA" in certs) + 25 * ("LFGB" in certs) + 10 * ("PROP 65" in certs)
    return dict(cost=cost, moq=moq, lead=lead, trend=trend, spec=spec, vendor=round(vendor_rating * 20))


SIMULATED = {
    "V7": dict(reply="Hi,\n\nlead time 42 days after sample approve.\nFDA and LFGB report attached.\n\nKevin",
               update={"lead_time_days": 42, "certifications": "FDA, LFGB"}),
    "V9": dict(reply=("Dear team,\nHT-30 Trail Tumbler 30oz (890ml), 18/8 stainless double wall, leak-proof flip straw lid,\n"
                      "255 x 95 mm, 430g, matte Mint / Peach / Stone.\nFOB Hai Phong USD 3.70, MOQ 1,200 pcs, 38 days.\n"
                      "FDA, LFGB, Prop 65. Sample 7 days free. Payment 30/70.\nLan"),
               new=dict(product_name="HT-30 Trail Tumbler", category="Tumbler",
                        description="30oz tumbler, leak-proof flip straw lid, matte pastel", capacity_oz=30,
                        material="18/8 stainless", insulation="Double-wall vacuum", lid_type="Leak-proof flip straw",
                        size_mm="255 × 95", weight_g=430, colors="Mint, Peach, Stone (matte)", fob_price=3.70,
                        currency="USD", moq=1200, lead_time_days=38, sample="7 days, free", origin="VN",
                        certifications="FDA, LFGB, Prop 65", dishwasher_safe=True, payment_terms="30/70 T/T"),
               low=[]),
    "V10": dict(reply=("hello,\nGL-16 frosted glass cup 16oz 480ml w/ bamboo lid + glass straw, 145*82mm\n"
                       "price RMB 13.5 FOB Shenzhen, moq 3000, 45 days. FDA ok.\nLeo"),
                new=dict(product_name="GL-16 Frosted Glass Cup", category="Glass cup",
                         description="16oz frosted glass cup with bamboo lid and glass straw", capacity_oz=16,
                         material="Borosilicate glass, bamboo", insulation="None", lid_type="Bamboo + glass straw",
                         size_mm="145 × 82", weight_g=None, colors=None, fob_price=13.5, currency="CNY", moq=3000,
                         lead_time_days=45, sample=None, origin="CN", certifications="FDA", dishwasher_safe=None,
                         payment_terms=None),
                low=["material"]),
}


def simulate_vendor_reply(c, round_id: int, vendor: dict) -> dict:
    vid = vendor["id"]
    sim = SIMULATED.get(vid)
    if not sim:
        return {"ok": False, "message": f"No simulated reply for {vendor['name']}"}
    already = c.execute("SELECT COUNT(*) FROM messages WHERE round_id=? AND vendor_id=? AND direction='in'",
                        (round_id, vid)).fetchone()[0]
    if (vid == "V7" and already > 1) or (vid != "V7" and already):
        return {"ok": False, "message": f"{vendor['name']} has already replied"}
    ts = db.now()
    c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
              (round_id, vid, "in", "reply", "RE: RFQ — Spring 2027 drinkware", sim["reply"], "received", ts))
    c.execute("UPDATE round_vendors SET replied_at=? WHERE round_id=? AND vendor_id=?", (ts, round_id, vid))
    c.execute("DELETE FROM messages WHERE round_id=? AND vendor_id=? AND status='pending_approval' AND kind IN ('reminder','followup')",
              (round_id, vid))
    if "update" in sim:
        p = db.row(c.execute("SELECT * FROM products WHERE round_id=? AND vendor_id=?", (round_id, vid)).fetchone())
        p["fields"].update(sim["update"])
        c.execute("UPDATE products SET fields=?, status='extracted' WHERE id=?", (json.dumps(p["fields"]), p["id"]))
    else:
        c.execute("INSERT INTO products (round_id,vendor_id,fields,low_confidence,status) VALUES (?,?,?,?,?)",
                  (round_id, vid, json.dumps(sim["new"]), json.dumps(sim["low"]), "extracted"))
        p = {"fields": sim["new"]}
    missing = fields.missing_required(p["fields"])
    db.log(c, round_id, f"{vendor['name']} replied → extracted. " +
           (f"Missing {len(missing)} required field(s)." if missing else "Ready for your check."),
           "warn" if missing else "good")
    return {"ok": True, "missing": missing}
