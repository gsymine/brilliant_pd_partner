"""Deterministic landed-cost, inventory-risk and scoring math.

Adapted from the repo's tools/costing.py for the multi-agent backend:
- works per *offer* (one proposed product), not per vendor;
- tolerates missing data (returns flags and null costs instead of crashing);
- adds a 0-100 score and rankings so the Analysis Agent never does arithmetic itself.

Costing context (per run, optional) mirrors data/assumptions.json + demand_forecast.csv:
    {"forecast": {"<sku>": {"season_units": 1800, "season_weeks": 20, "retail_price": 48}},
     "assumptions": {"po_date": ..., "in_store_date": ..., "fx_to_usd": {...}, ...}}
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

PATHS = ("accept", "modify", "co_develop")

DEFAULT_WEIGHTS = {"cost": 60.0, "spec": 25.0, "completeness": 15.0}


def _get(d: dict | None, *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _present(v: Any) -> bool:
    return v not in (None, "", [], {})


def cost_offer(offer: dict, costing: dict | None) -> dict:
    """Return cost metrics for one offer. Missing inputs produce flags, never exceptions."""
    a = (costing or {}).get("assumptions") or {}
    sku = offer.get("brief_item")
    fc = _get(costing, "forecast", str(sku)) if sku is not None else None
    country = offer.get("country")
    path = offer.get("path") or "accept"
    if path not in PATHS:
        path = "accept"
    flags: list[str] = []
    out: dict[str, Any] = {
        "unit_usd": None, "duty_usd": None, "freight_usd": None, "landed_usd": None, "imu": None,
        "order_qty": None, "excess_units": None, "excess_cost_usd": None, "arrival_date": None,
        "days_late": None, "lost_margin_usd": None, "one_off_usd": None, "total_cost_usd": None,
        "cost_per_sold_unit": None,
    }

    # FOB -> USD
    price = _num(offer.get("unit_price"))
    currency = offer.get("currency")
    fx = _num(_get(a, "fx_to_usd", str(currency))) if currency else None
    if currency == "USD" and fx is None:
        fx = 1.0
    if price is None:
        flags.append("Unit price missing")
    elif fx is None:
        flags.append(f"No FX rate for currency {currency!r}")
    else:
        out["unit_usd"] = round(price * fx, 2)

    if not fc:
        flags.append(f"No demand forecast for brief item {sku!r}: cost per sold unit not computed")
    if out["unit_usd"] is None or not fc:
        out["flags"] = flags
        return out

    unit_usd = price * fx  # type: ignore[operator]
    demand = int(fc["season_units"])
    weeks = int(fc.get("season_weeks") or 20)
    retail = float(fc["retail_price"])

    duty = _num(_get(a, "duty_rate", str(sku), str(country)))
    tariff = _num(_get(a, "extra_tariff_rate", str(country))) or 0.0
    if duty is None:
        flags.append(f"No duty rate for {sku}/{country}: assumed 0%")
        duty = 0.0
    freight = _num(_get(a, "freight_per_unit_usd", str(sku), str(country)))
    if freight is None:
        flags.append(f"No freight rate for {sku}/{country}: assumed $0")
        freight = 0.0
    duty_usd = unit_usd * (duty + tariff)
    landed = unit_usd + duty_usd + freight
    imu = (retail - landed) / retail if retail else None
    target_imu = _num(a.get("target_imu"))
    if imu is not None and target_imu is not None and imu < target_imu:
        flags.append(f"IMU {imu:.0%} below target {target_imu:.0%}")

    moq = offer.get("moq")
    if moq is None:
        flags.append("MOQ not stated: assumed = forecast")
        moq = demand
    order_qty = max(demand, int(moq))
    excess = order_qty - demand
    salvage = _num(a.get("excess_salvage_rate")) or 0.0
    excess_cost = excess * landed * (1 - salvage)
    if excess:
        flags.append(f"MOQ forces {excess} units over forecast")

    days_late = 0
    lost_margin = 0.0
    arrival_iso = None
    prod_days = _num(offer.get("production_days"))
    transit = _num(_get(a, "transit_days", str(country)))
    if a.get("po_date") and a.get("in_store_date") and prod_days is not None:
        if transit is None:
            flags.append(f"No transit time for {country}: assumed 0 days")
            transit = 0
        total_days = (
            int(_num(offer.get("sampling_days")) or 0)
            + int(_num(_get(a, "extra_dev_days", path)) or 0)
            + int(prod_days)
            + int(transit)
            + int(_num(a.get("receiving_buffer_days")) or 0)
        )
        arrival = date.fromisoformat(a["po_date"]) + timedelta(days=total_days)
        arrival_iso = arrival.isoformat()
        days_late = max(0, (arrival - date.fromisoformat(a["in_store_date"])).days)
        lost_units = min(demand, demand / weeks * days_late / 7)
        lost_margin = lost_units * (retail - landed)
        if days_late:
            flags.append(f"Arrives {days_late} days after in-store date")
    else:
        lost_units = 0.0
        flags.append("Timeline not scored (missing production time or PO/in-store dates)")

    one_off = (_num(_get(a, "extra_dev_cost_usd", path)) or 0.0) + (_num(offer.get("extra_costs_usd")) or 0.0)
    sold = demand - lost_units
    total = order_qty * landed + one_off - excess * landed * salvage + lost_margin
    out.update({
        "unit_usd": round(unit_usd, 2), "duty_usd": round(duty_usd, 2), "freight_usd": round(freight, 2),
        "landed_usd": round(landed, 2), "imu": round(imu, 3) if imu is not None else None,
        "order_qty": order_qty, "excess_units": excess, "excess_cost_usd": round(excess_cost, 2),
        "arrival_date": arrival_iso, "days_late": days_late, "lost_margin_usd": round(lost_margin, 2),
        "one_off_usd": round(one_off, 2), "total_cost_usd": round(total, 2),
        "cost_per_sold_unit": round(total / sold, 2) if sold > 0 else None,
    })
    out["flags"] = flags
    return out


def completeness(offer: dict, required_fields: list[str]) -> float:
    if not required_fields:
        return 1.0
    have = sum(1 for f in required_fields if _present(offer.get(f)))
    return round(have / len(required_fields), 3)


def score_offers(offers: list[dict], costing: dict | None, required_fields: list[str]) -> dict:
    """Cost, score and rank every offer. Returns {"offers": [...], "ranking_by_item": {...}, "overall_ranking": [...]}."""
    weights = {**DEFAULT_WEIGHTS, **((costing or {}).get("assumptions", {}) or {}).get("score_weights", {})}
    rows = []
    for o in offers:
        metrics = cost_offer(o, costing)
        comp = completeness(o, required_fields)
        missing = [f for f in required_fields if not _present(o.get(f))]
        rows.append({
            "offer_id": o["id"], "vendor": o.get("vendor_name"), "product_name": o.get("product_name"),
            "brief_item": o.get("brief_item"), "meets_spec": bool(o.get("meets_spec", False)),
            "path": o.get("path"), "completeness": comp, "missing_fields": missing,
            **metrics,
        })

    # Cost component: relative to the cheapest offer for the same brief item. The basis is the
    # best metric every offer has (cost per sold unit > landed > FOB in USD); if none is shared,
    # the best metric any offer has, and offers without it get no cost points.
    keys = ("cost_per_sold_unit", "landed_usd", "unit_usd")
    by_item: dict[str, list[dict]] = {}
    for r in rows:
        by_item.setdefault(str(r["brief_item"]), []).append(r)
    for item_rows in by_item.values():
        basis_key = next((k for k in keys if all(r.get(k) is not None for r in item_rows)), None) or \
            next((k for k in keys if any(r.get(k) is not None for r in item_rows)), None)
        values = [float(r[basis_key]) for r in item_rows if basis_key and r.get(basis_key) is not None]
        best = min(values) if values else None
        for r in item_rows:
            v = r.get(basis_key) if basis_key else None
            cost_pts = weights["cost"] * (best / float(v)) if best and v else 0.0
            spec_pts = weights["spec"] if r["meets_spec"] else 0.0
            comp_pts = weights["completeness"] * r["completeness"]
            r["score_basis"] = basis_key
            r["score_breakdown"] = {"cost": round(cost_pts, 1), "spec": round(spec_pts, 1),
                                    "completeness": round(comp_pts, 1)}
            r["score"] = round(cost_pts + spec_pts + comp_pts, 1)
        # Offers that fail a hard requirement rank below every compliant offer.
        item_rows.sort(key=lambda r: (not r["meets_spec"], -r["score"]))
        for i, r in enumerate(item_rows, 1):
            r["rank_in_item"] = i

    overall = sorted(rows, key=lambda r: (not r["meets_spec"], -r["score"]))
    for i, r in enumerate(overall, 1):
        r["overall_rank"] = i
    return {
        "weights": weights,
        "offers": overall,
        "ranking_by_item": {k: [r["offer_id"] for r in v] for k, v in sorted(by_item.items())},
        "overall_ranking": [r["offer_id"] for r in overall],
    }
