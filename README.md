# Brilliant PD Partner

An AI assistant for **drinkware product development managers / assistants**, built on
[ZooWork Managed Agents](https://zoowork.ai/docs/) for the Fashion & Finance Hackathon (Nov 2026, NYC).

It automates the sourcing round: send the brief to vendors → read their replies → extract the
predefined fields → chase missing info → store everything by vendor × product → once ≥70% of
vendors are complete, score and rank all products → decide accept / request changes / decline →
draft the emails. **Every outgoing email waits for human approval.**

## Run the web app

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

The app starts with fictional sample data (10 vendors, 12 products) in `var/pd_partner.db`.
"reset demo" in the sidebar restores it. Run tests with `.venv/bin/python -m pytest -q tests`.

### Demo walkthrough
1. **Overview**: 6/10 vendors complete, threshold not reached yet; 3 emails waiting for approval.
2. **Vendor responses → Saigon Drinkware**: the agent was unsure about capacity (blue). Check it and save → **threshold reached**.
3. **Ningbo Polar**: 2 required fields missing (red) → approve the follow-up → "▶ Simulate vendor reply" → check & save.
4. **Guangdong Lumi**: simulate reply → a field is missing → "Draft follow-up email".
5. **Analysis**: drag the weight sliders or pick a preset; the ranking re-orders. Click a product to see why it scores what it does.
6. **Decisions**: adjust any decision, then "Draft decision emails" → one email per vendor → approve.

## Code map

| Path | What it is |
|---|---|
| `app/fields.py` | **The predefined data fields** (single source of truth; required ones trigger follow-ups) |
| `app/main.py` | FastAPI JSON API + serves the page |
| `app/db.py` | SQLite tables: vendors, rounds, messages, products (by vendor × product), activity |
| `app/scoring.py` | Scoring dimensions, weight presets, landed cost, accept/modify/decline rules |
| `app/agent.py` | **Agent hooks**: email drafting, reply extraction, product scoring. Templates and fake data today; ZooWork agent later |
| `app/seed.py` | Fictional sample round, vendors, products, trends |
| `app/static/` | Frontend (`index.html`, `app.js`, `style.css`), plain JS, no build step |
| `design/mockup.html` | The original static mockup |

## API used by the frontend

| Area | Endpoints |
|---|---|
| Global | `GET /api/meta`, `GET /api/round` |
| Overview | `GET /api/approvals`, `GET /api/activity` |
| Outreach | `PUT /api/round`, `GET /api/vendors`, `POST /api/outreach` |
| Responses | `GET /api/responses/{vendor_id}`, `PATCH /api/products/{id}`, `POST /api/products/{id}/confirm`, `POST /api/responses/{vendor_id}/followup` |
| Database | `GET /api/products` |
| Analysis | `GET /api/ranking?cost=&moq=&lead=&trend=&spec=&vendor=` |
| Decisions | `PUT /api/products/{id}/decision`, `POST /api/decisions/draft` |
| Approvals | `PATCH /api/messages/{id}`, `POST /api/messages/{id}/approve`, `POST /api/messages/{id}/discard` |
| Demo only | `POST /api/demo/simulate-reply/{vendor_id}`, `POST /api/demo/reset` |

## Next steps
- Wire `app/agent.py` to the ZooWork agent (extraction from real emails, drafting, scoring).
- Real email in/out (Gmail) behind the approval step.
- Live trend signals instead of sample data.
- Possibly move the frontend to React; the JSON API stays the same.

## Earlier prototype (home decor, CLI)

`agent/AGENTS.md`, `data/`, `tools/costing.py` and `run_agent.py` are the first prototype: a one-shot
ZooWork agent run with landed-cost / MOQ-risk scoring for home decor
(`python tools/costing.py data/quotes.example.json`). The costing logic can be reused for the
drinkware "Cost & margin" and "MOQ fit" scores.
