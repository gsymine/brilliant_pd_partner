"""Scoring & ranking. Dimension scores are 0-100; overall = weighted average.

For now dimension scores come from seed data; later the agent / costing logic fills them.
"""
from __future__ import annotations

DIMENSIONS = [
    ("cost", "Cost & margin"),
    ("moq", "MOQ fit"),
    ("lead", "Lead time"),
    ("trend", "Trend fit"),
    ("spec", "Spec compliance"),
    ("vendor", "Vendor reliability"),
]
DIM_KEYS = [k for k, _ in DIMENSIONS]

PRESETS = {
    "balanced": {"cost": 25, "moq": 15, "lead": 15, "trend": 20, "spec": 15, "vendor": 10},
    "margin": {"cost": 40, "moq": 20, "lead": 10, "trend": 10, "spec": 10, "vendor": 10},
    "speed": {"cost": 15, "moq": 10, "lead": 35, "trend": 20, "spec": 10, "vendor": 10},
}

# Illustrative landed-cost assumptions (NOT verified tariff rates).
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "CNY": 0.14, "INR": 0.012}
DUTY = {"CN": 0.31, "VN": 0.06, "IN": 0.06, "PT": 0.06, "TW": 0.06}  # incl. extra CN tariff
FREIGHT_USD = 0.60

ACCEPT_AT = 75   # overall >= this and no change request -> accept
DECLINE_BELOW = 60
SPEC_FLOOR = 50  # spec compliance below this -> decline


def landed_usd(fields: dict) -> float | None:
    try:
        fob = float(fields["fob_price"]) * FX_TO_USD[fields.get("currency") or "USD"]
    except (KeyError, TypeError, ValueError):
        return None
    return round(fob * (1 + DUTY.get(fields.get("origin"), 0.06)) + FREIGHT_USD, 2)


def overall(scores: dict | None, weights: dict) -> int | None:
    if not scores or any(scores.get(k) is None for k in DIM_KEYS):
        return None
    total = sum(weights.get(k, 0) for k in DIM_KEYS) or 1
    return round(sum(scores[k] * weights.get(k, 0) for k in DIM_KEYS) / total)


def suggest(score: int | None, scores: dict | None, change_request: str | None) -> str:
    if score is None:
        return "pending"
    if (scores or {}).get("spec", 100) < SPEC_FLOOR or score < DECLINE_BELOW:
        return "reject"
    if change_request or score < ACCEPT_AT:
        return "modify"
    return "accept"
