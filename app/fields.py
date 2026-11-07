"""The data fields extracted for every vendor x product. Single source of truth.

Required fields trigger a follow-up email to the vendor when missing.
"""

FIELDS = [
    # key, label, type, required, notes
    ("product_name", "Product name", "text", True, "Vendor's item name / code"),
    ("category", "Category", "enum", True, "Tumbler · Water bottle · Glass cup · Mug · Kids bottle"),
    ("description", "Product description", "text", True, "Short description incl. design features"),
    ("capacity_oz", "Capacity (oz)", "number", True, "Converted from ml if needed (1 oz = 29.57 ml)"),
    ("material", "Material", "text", True, "e.g. 18/8 stainless, borosilicate glass, stoneware, Tritan"),
    ("insulation", "Insulation", "enum", False, "Double-wall vacuum · Single wall · None"),
    ("lid_type", "Lid type", "text", True, "Straw, flip, slide, screw..."),
    ("size_mm", "Size (H × D, mm)", "text", True, "Millimetres"),
    ("weight_g", "Weight (g)", "number", False, ""),
    ("colors", "Colors / finish", "text", True, ""),
    ("fob_price", "FOB unit price", "number", True, "In the vendor's currency"),
    ("currency", "Currency", "enum", True, "USD · EUR · CNY · INR"),
    ("moq", "MOQ (total)", "number", True, "Per-colour MOQs multiplied out"),
    ("lead_time_days", "Production lead time (days)", "number", True, "After sample approval"),
    ("sample", "Sample time & cost", "text", False, ""),
    ("origin", "Country of origin", "text", True, "ISO code, e.g. CN, VN"),
    ("certifications", "Certifications", "text", True, "FDA, LFGB, Prop 65, BPA-free"),
    ("dishwasher_safe", "Dishwasher safe", "bool", False, ""),
    ("payment_terms", "Payment terms", "text", False, ""),
]

FIELD_KEYS = [f[0] for f in FIELDS]
REQUIRED = [f[0] for f in FIELDS if f[3]]
NUMERIC = {f[0] for f in FIELDS if f[2] == "number"}


def as_dicts() -> list[dict]:
    return [dict(key=k, label=l, type=t, required=r, notes=n) for k, l, t, r, n in FIELDS]


def missing_required(values: dict) -> list[str]:
    return [k for k in REQUIRED if values.get(k) in (None, "")]
