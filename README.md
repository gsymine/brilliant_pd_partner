# Brilliant PD Partner

An AI sourcing & product development agent for home decor brands, built on
[ZooWork Managed Agents](https://zoowork.ai/docs/) for the Fashion & Finance Hackathon (Nov 2026, NYC).

**The pitch:** buyers compare vendor quotes on FOB price. That's the wrong number.
Brilliant PD Partner reads messy vendor emails, turns them into structured quotes, and ranks vendors
on **risk-adjusted cost per unit sold**: landed cost (FX, freight, duty, tariffs) **plus** the
excess-inventory cost of MOQs above forecast **plus** the lost margin from missing the in-store date.
Then it recommends accept / modify / co-develop and drafts the follow-up and counter-offer emails.

> *"Jingde is $0.90 cheaper FOB, but its 3,000-unit MOQ against an 1,800-unit forecast and the China
> tariff make it $4.60 more expensive per unit sold than Hanoi Clayworks."*

## Workflow

```
Brief ──► Vendors reply by email ──► [Agent] extract quotes ──► [costing.py] landed cost + MOQ + timeline risk
                                                                          │
           Draft emails (follow-ups, counter-offers, declines) ◄── Recommend accept / modify / co-develop
```

The agent does the language work (reading emails, judging spec fit, writing emails).
All math runs in `tools/costing.py`, so the numbers are deterministic and testable.

## Repo layout

| Path | What it is |
|---|---|
| `agent/AGENTS.md` | The agent's standing instructions (uploaded as its ZooWork persona) |
| `data/brief.md` | Sourcing brief: "Coastal Calm" Spring 2027 — ceramic vase (SKU A) + linen pillow cover (SKU B) |
| `data/vendor_emails/` | 5 sample vendor replies (CN, VN, PT ceramics; IN, CN textiles), with deliberate traps |
| `data/demand_forecast.csv` | Season demand + retail price per SKU |
| `data/assumptions.json` | FX, freight, duty/tariff, timeline, salvage assumptions (**illustrative**, not verified HTS rates) |
| `data/quotes.example.json` | Hand-extracted "answer key" for the sample emails |
| `tools/costing.py` | Landed cost & inventory-risk scoring (stdlib only; runs locally and in the agent sandbox) |
| `run_agent.py` | Creates/reuses the hosted agent, uploads inputs, runs the analysis, downloads `out/` |
| `tests/` | Unit tests for the costing logic |

### Traps planted in the sample emails
- **Jingde (CN vase):** lowest FOB, but MOQ 3,000 vs forecast 1,800 and +25% tariff.
- **Hanoi (VN vase):** no ISTA drop test yet (adds a one-off lab cost).
- **Porto (PT vase):** price in EUR, **no MOQ stated** → follow-up email needed.
- **Panipat (IN pillow):** price in INR, MOQ is *per colour* (500 × 3), arrives 13 days late.
- **Ningbo (CN pillow):** cheapest, but only 45% linen (spec ≥55%) and no OEKO-TEX → fails spec.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate   # or reuse ~/envs/zoowork
pip install -r requirements.txt
cp .env.example .env    # then paste your ZOOWORK_API_KEY
```

## Run

```bash
# Costing only, offline, on the answer key
python tools/costing.py data/quotes.example.json

# Tests
python -m unittest discover -s tests -v

# Full agent run on ZooWork (billable): writes out/report.md, out/quotes.json, out/scored.json
python run_agent.py

# Remove the hosted agent when done
python run_agent.py --teardown
```

## Ideas / next steps
- Sample-round tracker: log each sample round's comments and open issues; cap the revise loop.
- Negotiation simulator: what MOQ / price would make the runner-up win?
- Open-to-buy check across the whole collection, not per SKU.
- Real inbox ingestion (Gmail) instead of sample files.
