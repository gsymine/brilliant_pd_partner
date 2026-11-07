# Brilliant PD Partner — Backend

Multi-agent sourcing backend on **ZooWork Managed Agents**. Design: [`solution design/agent_flow.md`](solution%20design/agent_flow.md).

```
Start Sourcing Process (POST /runs)
   └─► Communication Agent ── one ZooWork session per vendor ──► real email (SMTP out / IMAP in)
          │  every fact saved to Vendor Memory (SQLite), follow-ups until the checklist is complete
          ▼  gate: every vendor thread complete / declined / stalled
       Extraction Agent ── one offer per proposed product, with sources
          ▼
       Analysis Agent ── deterministic costing + 0-100 score + rankings + recommendation
          ▼
       Human (POST /runs/{id}/decision) ── accept any products from any vendors, or deny all
```

## Layout

| Path | What it is |
|---|---|
| `agents/<role>/skill.md` | Each agent's role, inputs, outputs, tools, procedure and guardrails. Loaded as the ZooWork persona (`AGENTS.md`). |
| `app/tool_specs.py` | Custom tool schemas declared on each ZooWork agent |
| `app/tools.py` | Backend handlers for those tools (scoped to one run / one vendor thread) |
| `app/zoowork_runtime.py` | Agent provisioning (create / update / start) and the session driver that executes custom tools |
| `app/orchestrator.py` | Run and thread state machine, reminders, deadline, handoff gate, decision |
| `app/email_service.py` | SMTP send, IMAP poll, reply-to-thread matching |
| `app/costing.py` | Landed cost, MOQ risk, timeline risk, score and ranking (no LLM math) |
| `app/db.py` | SQLite schema: runs, threads, emails, vendor facts (versioned), offers, reports, decisions |
| `app/main.py` | FastAPI app and background loops |
| `examples/drinkware_demo_run.json` | Drinkware demo run used by `POST /demo/start` (simulated vendors) |
| `app/vendor_sim.py`, `simulator/vendors.json` | Vendor simulator for the demo (see below) |
| `examples/sample_run.json` | Example `POST /runs` body (Coastal Calm brief, placeholder vendor emails) |
| `tests/` | Offline tests (fake ZooWork client, dry-run email) |

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # fill in ZOOWORK_API_KEY and the mailbox (SMTP + IMAP, app password)
uvicorn app.main:app --port 8000
```

On startup the backend creates (or finds by labels, or updates) the three ZooWork agents and
starts them. Changing a `skill.md` or a tool schema updates the agent on the next start.

Use `EMAIL_MODE=dry_run` to record outbound emails without sending them. Inbound replies can then
be injected with `POST /inbound-email` (requires `INBOUND_TOKEN`).

## API (for the frontend team)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/runs` | **Start Sourcing Process**: brief, brief items, vendors, optional costing data |
| `GET` | `/runs` · `/runs/{id}` | Run status with every vendor thread's status |
| `GET` | `/runs/{id}/events` | Timeline (emails in/out, facts saved, threads closed, errors) for live updates by polling |
| `GET` | `/runs/{id}/threads/{thread_id}` | Email history and Vendor Memory (latest + full version history) |
| `GET` | `/runs/{id}/offers` | Structured offers after extraction |
| `GET` | `/runs/{id}/report` | Scores, per-item and overall rankings, recommendation, risks |
| `POST` | `/runs/{id}/decision` | `{"outcome": "accepted", "accepted_offer_ids": [...]}` or `{"outcome": "denied_all"}` |
| `POST` | `/runs/{id}/close-outreach` | Stop waiting for vendors; open threads become `stalled` and extraction starts |
| `POST` | `/runs/{id}/retry` | Re-run extraction and analysis after a failure |
| `POST` | `/inbound-email` | Push an inbound email (mail-provider webhook or testing) |

Run status: `outreach → extracting → analyzing → awaiting_decision → accepted | denied_all` (or `needs_attention`).
Thread status: `new → awaiting_reply ⇄ processing_reply → complete | declined | stalled`.

## Demo: vendor simulator

Six fictional drinkware vendors (`simulator/vendors.json`, addresses ending in `.sim`) answer the
Communication Agent automatically, so a whole sourcing run plays out with no human input:

| Vendor | Behaviour |
|---|---|
| Yongkang Steelware | Everything in the first reply (but a 3,000-unit MOQ) |
| Saigon Drinkware | Forgets lead time and certifications → 1 follow-up |
| Ningbo Polar | Terse, RMB and metric units → 2 follow-ups |
| Taichung Craft | Ignores the RFQ → answers after the reminder |
| Guangdong Lumi | Politely declines |
| Shenzhen Hydra | Hides a prompt-injection attempt in its reply; cheapest, but only BPA-free |

Code decides **what** each vendor reveals in each reply (deterministic, same demo every time).
The `vendor_sim` ZooWork agent (`agents/vendor_sim/skill.md`) only writes the wording in the
vendor's voice; with `SIM_MODE=scripted` or on any LLM error, templates write it instead.

```bash
# in .env: EMAIL_MODE=dry_run, IMAP_ENABLED=false, plus the compressed demo timing lines
uvicorn app.main:app --port 8000
curl -X POST localhost:8000/demo/start                     # simulated vendors only
curl -X POST localhost:8000/demo/start -H 'Content-Type: application/json' \
     -d '{"extra_vendors":[{"name":"Live Vendor","email":"teammate@gmail.com","country":"VN"}]}'  # + one real inbox
```

Mixing works because routing is per address: `.sim` vendors go to the simulator, every other
address uses SMTP/IMAP as usual (needs `EMAIL_MODE=smtp` and IMAP settings for the real one).
`GET /sim/vendors` lists the simulated vendors.

## Tests

```bash
cd backend && python -m unittest discover -s tests -v
```

These cover costing parity with `tools/costing.py`, email threading, tool scoping and validation,
the full run lifecycle with a fake ZooWork runtime, reminders and stalls, and custom-tool
execution and replay in the session driver. They do **not** call ZooWork or a real mailbox.
