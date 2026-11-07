#!/usr/bin/env python3
"""Landed-cost and inventory-risk scoring for vendor quotes.

Deterministic finance math so the agent never does arithmetic "in its head".
Standard library only, so it runs unchanged inside the ZooWork sandbox.

Usage:
    python tools/costing.py QUOTES_JSON [--data-dir data] [--out-md report.md] [--out-json scored.json]

QUOTES_JSON is a list of quote objects (see data/quotes.example.json):
    vendor, sku, item, country (CN/VN/PT/IN), currency (USD/CNY/INR/EUR),
    unit_price, moq (int or null if the vendor did not state it),
    production_days, sampling_days, path ("accept" | "modify" | "co_develop"),
    extra_costs_usd (one-off: moulds, lab tests, ...), spec_issues (list of strings),
    meets_spec (bool: false if a hard requirement in the brief is failed), notes
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

PATHS = ("accept", "modify", "co_develop")


@dataclass
class Scored:
    vendor: str
    sku: str
    item: str
    path: str
    unit_usd: float
    duty_usd: float
    freight_usd: float
    landed_usd: float
    imu: float
    order_qty: int
    excess_units: int
    excess_cost_usd: float
    arrival_date: str
    days_late: int
    lost_margin_usd: float
    one_off_usd: float
    total_cost_usd: float
    cost_per_sold_unit: float
    meets_spec: bool = True
    flags: list[str] = field(default_factory=list)


def load_inputs(data_dir: Path) -> tuple[dict, dict]:
    assumptions = json.loads((data_dir / "assumptions.json").read_text())
    with (data_dir / "demand_forecast.csv").open() as f:
        forecast = {row["sku"]: row for row in csv.DictReader(f)}
    return assumptions, forecast


def score_quote(q: dict, a: dict, forecast: dict) -> Scored:
    sku = q["sku"]
    fc = forecast[sku]
    demand = int(fc["season_units"])
    weeks = int(fc["season_weeks"])
    retail = float(fc["retail_price"])
    country = q["country"]
    path = q.get("path", "accept")
    if path not in PATHS:
        raise ValueError(f"{q['vendor']}: path must be one of {PATHS}, got {path!r}")
    flags: list[str] = list(q.get("spec_issues") or [])

    # Landed cost per unit (duty assessed on FOB value).
    unit_usd = float(q["unit_price"]) * a["fx_to_usd"][q["currency"]]
    duty_rate = a["duty_rate"][sku][country] + a["extra_tariff_rate"].get(country, 0.0)
    duty_usd = unit_usd * duty_rate
    freight_usd = a["freight_per_unit_usd"][sku][country]
    landed = unit_usd + duty_usd + freight_usd
    imu = (retail - landed) / retail
    if imu < a["target_imu"]:
        flags.append(f"IMU {imu:.0%} below target {a['target_imu']:.0%}")

    # MOQ vs forecast -> excess inventory risk.
    moq = q.get("moq")
    if moq is None:
        flags.append("MOQ not stated: confirm with vendor (assumed = forecast)")
        moq = demand
    order_qty = max(demand, int(moq))
    excess = order_qty - demand
    excess_cost = excess * landed * (1 - a["excess_salvage_rate"])
    if excess:
        flags.append(f"MOQ forces {excess} units over forecast")

    # Timeline vs in-store date -> lost sales risk.
    po = date.fromisoformat(a["po_date"])
    total_days = (
        int(q.get("sampling_days") or 0)
        + a["extra_dev_days"][path]
        + int(q["production_days"])
        + a["transit_days"][country]
        + a["receiving_buffer_days"]
    )
    arrival = po + timedelta(days=total_days)
    days_late = max(0, (arrival - date.fromisoformat(a["in_store_date"])).days)
    weekly_rate = demand / weeks
    lost_units = min(demand, weekly_rate * days_late / 7)
    lost_margin = lost_units * (retail - landed)
    if days_late:
        flags.append(f"Arrives {days_late} days after in-store date")

    one_off = a["extra_dev_cost_usd"][path] + float(q.get("extra_costs_usd") or 0)

    # Total cost to serve forecast demand, risk-adjusted.
    sold = demand - lost_units
    total = order_qty * landed + one_off - excess * landed * a["excess_salvage_rate"] + lost_margin
    return Scored(
        vendor=q["vendor"], sku=sku, item=q.get("item", ""), path=path,
        unit_usd=round(unit_usd, 2), duty_usd=round(duty_usd, 2),
        freight_usd=round(freight_usd, 2), landed_usd=round(landed, 2), imu=round(imu, 3),
        order_qty=order_qty, excess_units=excess, excess_cost_usd=round(excess_cost, 2),
        arrival_date=arrival.isoformat(), days_late=days_late,
        lost_margin_usd=round(lost_margin, 2), one_off_usd=round(one_off, 2),
        total_cost_usd=round(total, 2),
        cost_per_sold_unit=round(total / sold, 2) if sold else float("inf"),
        meets_spec=bool(q.get("meets_spec", True)),
        flags=flags,
    )


def score_all(quotes: list[dict], a: dict, forecast: dict) -> list[Scored]:
    scored = [score_quote(q, a, forecast) for q in quotes]
    # Vendors that fail a hard requirement rank below every compliant vendor.
    return sorted(scored, key=lambda s: (s.sku, not s.meets_spec, s.cost_per_sold_unit))


def to_markdown(scored: list[Scored]) -> str:
    lines = []
    for sku in sorted({s.sku for s in scored}):
        rows = [s for s in scored if s.sku == sku]
        lines += [
            f"### SKU {sku} — {rows[0].item}",
            "",
            "| Rank | Vendor | Meets spec | Path | FOB $ | Landed $ | IMU | Order qty | Excess $ | Days late | Lost margin $ | One-off $ | Cost / sold unit |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for i, s in enumerate(rows, 1):
            lines.append(
                f"| {i} | {s.vendor} | {'yes' if s.meets_spec else '**NO**'} | {s.path} | {s.unit_usd:.2f} | {s.landed_usd:.2f} | {s.imu:.0%} "
                f"| {s.order_qty:,} | {s.excess_cost_usd:,.0f} | {s.days_late} | {s.lost_margin_usd:,.0f} "
                f"| {s.one_off_usd:,.0f} | **{s.cost_per_sold_unit:.2f}** |"
            )
        lines.append("")
        for s in rows:
            if s.flags:
                lines.append(f"- **{s.vendor}:** " + "; ".join(s.flags))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("quotes")
    p.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"))
    p.add_argument("--out-md")
    p.add_argument("--out-json")
    args = p.parse_args()

    a, forecast = load_inputs(Path(args.data_dir))
    scored = score_all(json.loads(Path(args.quotes).read_text()), a, forecast)
    md = to_markdown(scored)
    print(md)
    if args.out_md:
        Path(args.out_md).write_text(md)
    if args.out_json:
        Path(args.out_json).write_text(json.dumps([asdict(s) for s in scored], indent=2))


if __name__ == "__main__":
    main()
