"""Fictional sample data for a drinkware sourcing round. Used until the agent is wired in."""
from __future__ import annotations

import json

from . import db

ROUND = dict(
    id=1, name="Spring 2027 · Insulated & Glass",
    theme="'Fresh Air': soft pastels, matte powder coat, outdoor & on-the-go",
    requirements=(
        "• Capacity 16–40 oz; 18/8 (304) stainless for insulated items\n"
        "• Food-contact compliance: FDA + LFGB; BPA-free lids; Prop 65 compliant\n"
        "• Dishwasher-safe preferred\n"
        "• Target FOB: tumblers ≤ $4.50, glass ≤ $2.50, mugs ≤ $3.50\n"
        "• Launch 2027-04-01 → goods in DC by 2027-03-15"
    ),
    categories="Tumbler, Water bottle, Glass cup, Mug",
    deadline="2026-11-10", launch_date="2027-04-01", threshold=0.7, status="collecting",
)

VENDORS = [
    # id, name, country, contact, email, categories, rating, history
    ("V1", "Yongkang Steelware", "CN", "Lily Zhou", "lily@yongkang-steel.example", "Tumbler, Water bottle", 4.6, "6 POs, 98% on time"),
    ("V2", "Zhejiang Everbright", "CN", "Grace Li", "sales@everbright.example", "Water bottle, Kids bottle", 4.1, "3 POs, 90% on time"),
    ("V3", "Saigon Drinkware", "VN", "Minh Tran", "minh@saigondw.example", "Tumbler", 4.4, "2 POs, 100% on time"),
    ("V4", "Bharat Glassworks", "IN", "Anil Mehta", "anil@bharatglass.example", "Glass cup", 3.9, "1 PO, 1 late"),
    ("V5", "Lisbon Cerâmica", "PT", "Inês Carvalho", "ines@lisbonceramica.example", "Mug", 4.7, "4 POs, 100% on time"),
    ("V6", "Shenzhen Hydra", "CN", "Amy Chen", "amy@szhydra.example", "Kids bottle", 3.6, "New vendor"),
    ("V7", "Ningbo Polar", "CN", "Kevin Wang", "kevin@ningbopolar.example", "Tumbler", 3.8, "2 POs, 1 quality claim"),
    ("V8", "Taichung Craft", "TW", "Jay Lin", "jay@tccraft.example", "Glass cup", 4.5, "3 POs, 100% on time"),
    ("V9", "Hanoi Thermo", "VN", "Lan Pham", "lan@hanoithermo.example", "Tumbler, Water bottle", 4.2, "1 PO"),
    ("V10", "Guangdong Lumi", "CN", "Leo Huang", "leo@gdlumi.example", "Glass cup, Mug", 3.7, "New vendor"),
]


def P(vendor, name, cat, desc, cap, mat, ins, lid, size, wt, colors, fob, cur, moq, lead, origin, cert,
      scores, change=None, low=(), dw=True, terms="30/70 T/T", sample="7–10 days, free"):
    return dict(vendor=vendor, scores=scores, change=change, low=list(low), fields=dict(
        product_name=name, category=cat, description=desc, capacity_oz=cap, material=mat, insulation=ins,
        lid_type=lid, size_mm=size, weight_g=wt, colors=colors, fob_price=fob, currency=cur, moq=moq,
        lead_time_days=lead, sample=sample, origin=origin, certifications=cert, dishwasher_safe=dw,
        payment_terms=terms))


def S(cost, moq, lead, trend, spec, vendor):
    return dict(cost=cost, moq=moq, lead=lead, trend=trend, spec=spec, vendor=vendor)


PRODUCTS = [
    P("V1", "YK-40H Handle Tumbler", "Tumbler", "40oz tumbler with handle, matte powder coat", 40, "18/8 stainless", "Double-wall vacuum",
      "Flip straw", "275 × 100", 560, "Sage, Lilac, Sand", 3.85, "USD", 3000, 45, "CN", "FDA, LFGB",
      S(72, 48, 85, 95, 95, 92), change="Reduce MOQ to 2,000 (3 colours) or FOB to $3.45 to offset the China tariff"),
    P("V1", "YK-20 Slim Tumbler", "Tumbler", "20oz slim tumbler, slide lid", 20, "18/8 stainless", "Double-wall vacuum",
      "Slide", "190 × 85", 340, "Matte pastel ×4", 2.60, "USD", 2000, 40, "CN", "FDA, LFGB", S(85, 70, 88, 62, 95, 92)),
    P("V2", "EB-32 Sport Bottle", "Water bottle", "32oz bottle with chug lid and carry loop", 32, "18/8 stainless", "Double-wall vacuum",
      "Chug + loop", "260 × 90", 480, "5 colours (gloss)", 3.40, "USD", 2000, 50, "CN", "FDA",
      S(70, 68, 75, 58, 72, 80), change="Provide LFGB test report; switch to matte powder coat"),
    P("V2", "EB-24 Kids Bottle", "Kids bottle", "24oz kids bottle with straw", 24, "18/8 stainless", "Double-wall vacuum",
      "Straw", "220 × 75", 330, "4 colours", 2.90, "USD", 2000, 50, "CN", "FDA", S(64, 62, 75, 50, 70, 80)),
    P("V3", "SD-40 Trek Tumbler", "Tumbler", "40oz ceramic-lined tumbler, flip straw", 40, "18/8 stainless, ceramic-lined",
      "Double-wall vacuum", "Flip straw", "270 × 100", 520, "Sea glass, Oat (matte)", 4.30, "USD", 1000, 40, "VN",
      "FDA, LFGB, Prop 65", S(78, 92, 88, 97, 98, 90), low=["capacity_oz"], sample="10 days, $30"),
    P("V4", "BG-16 Glass Cup + Bamboo Lid", "Glass cup", "16oz borosilicate cup with bamboo lid and glass straw", 16,
      "Borosilicate glass, bamboo", "None", "Bamboo + glass straw", "150 × 85", 210, "Clear, Frosted", 1.90, "USD", 1500, 60,
      "IN", "FDA", S(88, 74, 52, 90, 70, 66), change="Shorten lead time to ≤45 days; add LFGB; drop-test packaging", dw=False),
    P("V5", "LC-12 Stoneware Mug", "Mug", "12oz stoneware mug, reactive glaze", 12, "Stoneware", "None", "None", "95 × 85", 360,
      "Oat, Sky", 2.95, "EUR", 500, 30, "PT", "FDA, LFGB, Prop 65", S(60, 98, 95, 45, 96, 96), terms="50/50"),
    P("V5", "LC-14 Matte Travel Mug", "Mug", "14oz stoneware travel mug, silicone lid", 14, "Stoneware, matte glaze", "None", "Silicone",
      "135 × 85", 390, "Pastel ×3", 3.60, "EUR", 500, 35, "PT", "FDA, LFGB", S(52, 98, 92, 70, 90, 96),
      change="Target price €3.20; confirm silicone lid is food-grade", terms="50/50"),
    P("V6", "HY-24 Kids Tritan Bottle", "Kids bottle", "24oz Tritan kids bottle, push-button straw", 24, "Tritan, silicone straw", "None",
      "Push-button straw", "230 × 75", 180, "Print ×6", 1.75, "USD", 5000, 35, "CN", "BPA-free only", S(92, 20, 90, 55, 40, 55)),
    P("V7", "NP-30 Polar Tumbler", "Tumbler", "30oz tumbler, slide lid, pantone colour match", 30, "18/8 stainless", "Double-wall vacuum",
      "Slide", "265 × 98", 410, "Pantone match", 2.95, "USD", 2000, None, "CN", None, None),
    P("V8", "TC-16 Ribbed Glass + Straw", "Glass cup", "16oz ribbed glass with bamboo lid and straw", 16, "Soda-lime glass", "None",
      "Bamboo", "140 × 80", 230, "Clear, Amber, Sage", 2.20, "USD", 1000, 40, "TW", "FDA, LFGB", S(82, 90, 88, 88, 92, 90)),
    P("V8", "TC-20 Can Glass", "Glass cup", "20oz can-shaped glass", 20, "Soda-lime glass", "None", "None", "150 × 80", 240,
      "Clear", 1.80, "USD", 1000, 40, "TW", "FDA", S(88, 90, 88, 60, 80, 90)),
]

REPLY_V7 = """hi,
pls find our offer:
NP-30 tumbler 30oz 304 steel, slide lid,
900ml, 26.5*9.8cm, 410g
price USD 2.95 FOB Ningbo, moq 2000pcs mix color
color can match pantone. dishwasher ok.
payment 30/70 TT
sample 7 days free

thanks
Kevin"""

REPLY_V3 = """Dear team,
SD-40 Trek tumbler, 1.2L / 40oz? ceramic-lined 18/8
stainless, double wall vacuum, flip straw lid.
Size 27 x 10 cm, 520 g. Sea glass & Oat matte.
FOB HCMC USD 4.30, MOQ 1,000 pcs, 40 days.
FDA, LFGB, Prop 65 reports attached.
Sample 10 days, $30. Payment 30/70.
Best, Minh"""

STATUS = {"V1": "complete", "V2": "complete", "V3": "needs_check", "V4": "complete", "V5": "complete",
          "V6": "complete", "V7": "needs_followup", "V8": "complete", "V9": "waiting", "V10": "waiting"}


def rfq_body(contact: str) -> str:
    return (f"Hi {contact.split()[0]},\n\nFor Spring 2027 we're developing a \"Fresh Air\" drinkware collection: "
            f"{ROUND['theme']}.\n\nRequirements:\n{ROUND['requirements']}\n\n"
            "For each product you propose, please reply with: product name & photos, category & capacity, material & finish, "
            "size (H × D) & weight, lid type, colours, FOB unit price & currency, MOQ (total and per colour), production lead time, "
            "sample time & cost, country of origin, certifications (FDA, LFGB, Prop 65), and payment terms.\n\n"
            f"Please reply by {ROUND['deadline']}. Thank you!")


def seed(force: bool = False) -> None:
    db.init()
    with db.connect() as c:
        if c.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] and not force:
            return
        for t in ("vendors", "rounds", "round_vendors", "messages", "products", "activity"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT INTO rounds VALUES (:id,:name,:theme,:requirements,:categories,:deadline,:launch_date,:threshold,:status)", ROUND)
        c.executemany("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?)", VENDORS)
        for vid, name, _, contact, email, *_ in VENDORS:
            st = STATUS[vid]
            c.execute("INSERT INTO round_vendors VALUES (1,?,?,?)",
                      (vid, "2026-10-20T09:00:00", None if st == "waiting" else "2026-11-05T10:00:00"))
            c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at,sent_at) "
                      "VALUES (1,?,?,?,?,?,?,?,?)",
                      (vid, "out", "rfq", "RFQ — Spring 2027 drinkware (reply by Nov 10)", rfq_body(contact), "sent",
                       "2026-10-20T09:00:00", "2026-10-20T09:00:00"))
            if st != "waiting":
                body = {"V7": REPLY_V7, "V3": REPLY_V3}.get(vid, f"Dear team,\n\nPlease find our offer attached "
                       f"(catalogue.pdf, test-reports.zip).\n\nBest regards,\n{contact}")
                c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) "
                          "VALUES (1,?,?,?,?,?,?,?)",
                          (vid, "in", "reply", "RE: RFQ — Spring 2027 drinkware", body, "received", "2026-11-05T10:00:00"))
        for p in PRODUCTS:
            status = "saved" if STATUS[p["vendor"]] == "complete" else "extracted"
            c.execute("INSERT INTO products (round_id,vendor_id,fields,low_confidence,status,scores,change_request) "
                      "VALUES (1,?,?,?,?,?,?)",
                      (p["vendor"], json.dumps(p["fields"]), json.dumps(p["low"]), status,
                       json.dumps(p["scores"]) if p["scores"] else None, p["change"]))
        # Drafts waiting for approval.
        c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) VALUES (1,?,?,?,?,?,?,?)",
                  ("V7", "out", "followup", "RE: RFQ — Spring 2027 drinkware — 2 details needed",
                   "Hi Kevin,\n\nThanks for the NP-30 offer. To include it in our review, could you send:\n"
                   " 1. Production lead time (days after sample approval)\n"
                   " 2. Certifications: FDA and LFGB test reports (Prop 65 if available)\n\n"
                   "We're finalizing the shortlist on Nov 10. Thank you!", "pending_approval", db.now()))
        for vid, contact in (("V9", "Lan"), ("V10", "Leo")):
            c.execute("INSERT INTO messages (round_id,vendor_id,direction,kind,subject,body,status,created_at) VALUES (1,?,?,?,?,?,?,?)",
                      (vid, "out", "reminder", "Reminder: RFQ — Spring 2027 drinkware (due Nov 10)",
                       f"Hi {contact},\n\nJust a friendly reminder about our Spring 2027 drinkware request from Oct 20. "
                       "We'd love to include your products — could you send your offer by Nov 10?\n\nThank you!",
                       "pending_approval", db.now()))
        for ts, level, text in [
            ("2026-11-04T16:20:00", "info", "Trend scan refreshed: 6 signals for drinkware (sample data)."),
            ("2026-11-05T10:05:00", "good", "Extracted 2 products from Taichung Craft. All required fields present."),
            ("2026-11-05T10:06:00", "warn", "Ningbo Polar reply is missing 2 required fields → follow-up drafted."),
            ("2026-11-05T10:07:00", "info", "Saigon Drinkware: capacity is ambiguous (\"1.2L / 40oz?\"), please check."),
        ]:
            c.execute("INSERT INTO activity (round_id,ts,level,text) VALUES (1,?,?,?)", (ts, level, text))


TRENDS = [
    {"name": "Handle tumblers 30–40 oz", "direction": "up", "label": "high"},
    {"name": "Matte pastel powder coat", "direction": "up", "label": "rising"},
    {"name": "Glass cups + bamboo lid & straw", "direction": "up", "label": "rising"},
    {"name": "Leak-proof flip-straw lids", "direction": "up", "label": "rising"},
    {"name": "Ceramic-lined stainless", "direction": "up", "label": "emerging"},
    {"name": "Classic glossy ceramic mugs", "direction": "down", "label": "cooling"},
]
