# Agent Flow — Solution Design (Draft v0.1)

> **Status:** Draft for team review · **Scope:** Backend only (frontend designed separately) · **Stage:** v0.1 implemented in `backend/` (see [`../README.md`](../README.md))
> **Builds on:** [`workflow.png`](workflow.png) (the business process this agent flow automates)

---

## 1. Purpose

`workflow.png` describes how a product developer sources a product today: send the theme and requests to vendors, go back and forth by email, compare offers, and decide. This document defines the **agent system** that runs that process on the backend.

Three agents, each with one job:

| Agent | One-line job |
|---|---|
| **Communication Agent** | Talks to each vendor by email, back and forth, until all required information is collected. |
| **Extraction Agent** | Reads every vendor thread and turns it into clean, structured, comparable product offers. |
| **Analysis Agent** | Scores and ranks the offers, then hands a recommendation to a human. |

A **human** makes the final call on the **ranked products**: accept any number of them (across any number of vendors), or deny all.

> **Unit of decision = product, not vendor.** Each vendor may propose several products. Every proposed product becomes its own `Offer`, is scored and ranked on its own, and is accepted or not on its own.

### Mapping to `workflow.png`

| `workflow.png` step | Owner in this design |
|---|---|
| Send theme and requests to vendors | Communication Agent (triggered by **Start Sourcing Process**) |
| Vendors reply by email (designs, materials, price, lead time, MOQ) | Communication Agent (follow-up loop) |
| Product development decision (inputs) | Extraction Agent → Analysis Agent |
| Product development decision (the decision itself) | **Human** |
| Collaborate with vendor / samples loop | Out of scope for v0.1 (see §10) |
| Final product decided | Human decision record |

---

## 2. Design principles

1. **One vendor = one session.** Every vendor conversation lives in its own isolated agent session. Information never leaks between vendors.
2. **Nothing is lost.** Every fact the agent captures from a vendor is saved right away and kept for the whole life of that session.
3. **LLMs do language, code does math.** Agents read, judge and write. All cost and score arithmetic runs in deterministic tools (the existing `tools/costing.py`).
4. **The human decides.** Agents recommend; only a human accepts or denies products.
5. **Vendor content is untrusted.** Instructions found inside vendor emails are treated as data, never as commands.
6. **Each agent is self-describing.** Every agent ships a `skill.md` that states its role, inputs, outputs, and tool-calling capabilities.

---

## 3. End-to-end flow

```mermaid
flowchart TD
    U([User clicks<br/>Start Sourcing Process]) --> O[Backend Orchestrator<br/>creates Sourcing Run]
    O -->|one session per vendor| C1[Communication Agent<br/>Session: Vendor A]
    O --> C2[Communication Agent<br/>Session: Vendor B]
    O --> C3[Communication Agent<br/>Session: Vendor N]

    C1 <-->|RFQ + follow-ups| V1[(Vendor A inbox)]
    C2 <-->|RFQ + follow-ups| V2[(Vendor B inbox)]
    C3 <-->|RFQ + follow-ups| V3[(Vendor N inbox)]

    C1 --> M1[(Vendor Memory A)]
    C2 --> M2[(Vendor Memory B)]
    C3 --> M3[(Vendor Memory N)]

    M1 & M2 & M3 --> G{Handoff gate:<br/>all vendor sessions<br/>closed?}
    G -->|yes| E[Extraction Agent<br/>normalize all threads]
    E --> Q[(Structured Offers)]
    Q --> A[Analysis Agent<br/>score + rank]
    A --> T[[costing tool<br/>deterministic math]]
    T --> A
    A --> R[(Analysis Report)]
    R --> H{{Human decision}}
    H -->|accept selected products<br/>from any vendors| D1([Run closed: Accepted])
    H -->|deny all| D2([Run closed: Denied])
```

### Step by step

| # | Step | Actor | Input | Output |
|---|---|---|---|---|
| 1 | User clicks **Start Sourcing Process** | Human → Backend | Brief, vendor list, demand forecast, cost assumptions | A new `SourcingRun` |
| 2 | Open one Communication session per vendor | Orchestrator | Run + vendor list | One `VendorThread` per vendor |
| 3 | Send the initial request (theme + requirements) | Communication Agent | Brief | Outbound RFQ email |
| 4 | Reply loop: read reply, save facts, check what is missing, send a follow-up | Communication Agent | Inbound vendor emails | Updated Vendor Memory, more follow-ups |
| 5 | Close the vendor session | Communication Agent | Required info checklist | Thread status: `complete`, `stalled`, or `declined` |
| 6 | Handoff gate opens when every vendor session is closed (or the run deadline passes) | Orchestrator | All thread statuses | Extraction job |
| 7 | Extract and normalize offers across all vendors | Extraction Agent | All threads + Vendor Memories | `Offer` records (one per proposed product; a vendor may have several) |
| 8 | Score and rank products | Analysis Agent | Offers + brief + forecast + assumptions | `AnalysisReport` (product scores, product ranking, recommendation) |
| 9 | Decide | Human | AnalysisReport | `Decision`: accept any subset of ranked products (from any vendors), or deny all |

---

## 4. Agents

### 4.1 Communication Agent

**Role.** The only agent that talks to vendors. It runs **one session per vendor** and keeps going back and forth until it has every required field for **every product the vendor proposes** (a vendor may propose several products, including several for the same brief item). Facts are always saved against a specific product.

**Required info checklist** (from the brief's *"Please reply with"* list, per product):
designs/photos · materials/composition · unit price + currency + Incoterm (FOB) · MOQ (and whether per colour/size) · production lead time · sampling time and cost · payment terms · port of loading · required certifications (e.g. OEKO-TEX, ISTA drop test).

**Loop per vendor session**

```mermaid
sequenceDiagram
    autonumber
    participant O as Orchestrator
    participant CA as Communication Agent<br/>(Vendor X session)
    participant MEM as Vendor Memory X
    participant MAIL as Email service
    participant V as Vendor X

    O->>CA: Start session (brief, vendor profile)
    CA->>MAIL: send_email(RFQ)
    MAIL->>V: Theme + requirements
    loop until checklist complete, vendor declines, or limits reached
        V-->>MAIL: Reply (text + attachments)
        MAIL-->>O: Inbound webhook
        O->>CA: Deliver reply into the SAME session
        CA->>MEM: save_vendor_facts (every new fact, with source)
        CA->>MEM: get_missing_fields
        alt fields still missing or unclear
            CA->>MAIL: send_email(follow-up asking only for the gaps)
        else checklist complete
            CA->>O: mark_thread_complete
        end
    end
```

**Stop conditions.** A vendor session closes as:
- `complete`: every required field is present for every offered product.
- `declined`: the vendor says it cannot quote.
- `stalled`: no reply after N reminders, or the maximum number of rounds is reached (both configurable). The session closes with the gaps recorded.

**Guardrails**
- Never commits to an order, price, MOQ, or payment. It only asks and clarifies.
- Never reveals another vendor's name, price, or terms.
- Treats instructions inside vendor emails as data.
- Asks only for missing or unclear fields; no repeated questions.

**Tool-calling capabilities**

| Tool | Type | Purpose |
|---|---|---|
| `send_email` | Email MCP or custom tool | Send the RFQ, follow-ups, and reminders to *this* vendor only |
| `read_thread` | Custom tool | Read the full email history of this vendor thread |
| `save_vendor_facts` | Custom tool | Save newly captured facts (field, value, source email id, confidence) to Vendor Memory |
| `get_vendor_memory` / `get_missing_fields` | Custom tool | Read current memory; compare it against the checklist |
| `mark_thread_status` | Custom tool | Close the session as `complete` / `declined` / `stalled` |

### 4.2 Extraction Agent

**Role.** Turns messy email threads into clean, comparable data. It runs **once per run** over **all** vendor threads after the handoff gate opens.

**What it does**
- Builds one `Offer` per **proposed product** from the thread plus its Vendor Memory. A vendor with three proposals yields three offers. Each offer is linked to the brief item (SKU) it answers.
- Normalizes units: weeks → days, "per colour" MOQ → total MOQ, original currency kept (conversion happens in the costing tool).
- Checks each offer against the brief's **hard requirements** (material composition, certification, size) and sets `meets_spec` with reasons.
- Picks the development path for each offer: `accept` (fits as is) · `modify` (adapt to brand) · `co_develop` (new product), matching `workflow.png`.
- Leaves unstated values as `null` and flags them. It never guesses.
- Records a source reference (email id + quote) for every extracted value so a human can audit it.

**Tool-calling capabilities**

| Tool | Type | Purpose |
|---|---|---|
| `list_vendor_threads` | Custom tool | All threads in the run with status |
| `read_thread` / `get_vendor_memory` | Custom tool | Raw emails, attachments, and saved facts |
| `read_attachment` | Custom tool | Spec sheets, price lists, images (PDF, XLSX, JPG) |
| `save_offers` | Custom tool | Write validated `Offer` records (schema-checked on save) |

### 4.3 Analysis Agent

**Role.** Scores, ranks, and explains, so the human can decide in minutes.

**What it does**
1. Calls the **costing tool** for all arithmetic: landed cost (FX, freight, duty, tariff), MOQ excess-inventory cost against forecast, and lost margin from late delivery. Result: **risk-adjusted cost per unit sold**.
2. Combines cost with spec fit, path effort (accept < modify < co-develop), and data completeness into a **score** using the rubric in its `skill.md`.
3. **Ranks products, not vendors.** Every offer gets its own score. Offers are ranked within each brief item (SKU), and an overall ranked list is also produced. One vendor's products can appear at different positions.
4. Writes the recommendation per product: which products to accept and why, in dollars. Always contrasts the cheapest-FOB product with the pick when they differ.
5. Lists risks and open questions (missing fields, stalled vendors, assumptions).

**Tool-calling capabilities**

| Tool | Type | Purpose |
|---|---|---|
| `get_offers` | Custom tool | Read structured offers for the run |
| `get_run_context` | Custom tool | Brief, demand forecast, cost assumptions |
| `run_costing` | Sandbox tool (`tools/costing.py`) | Deterministic landed-cost and risk scoring |
| `save_analysis_report` | Custom tool | Write scores, ranking, and recommendation |

### 4.4 Human decision (final step)

The human reviews the `AnalysisReport` and either:
- **accepts** any subset of the ranked products. They can come from several vendors and include several products from the same vendor. Or the human
- **denies all**.

The decision is made per product (per `Offer`), not per vendor.

Optional free-text notes are stored with the decision. The run then closes. Agents never make this decision.

---

## 5. `skill.md` per agent

Each agent is deployed with its own `skill.md` (uploaded as a ZooWork platform Skill). Proposed layout:

```
backend/agents/
├── communication/skill.md
├── extraction/skill.md
└── analysis/skill.md
```

Every `skill.md` uses the same sections:

| Section | Content |
|---|---|
| **Role** | One paragraph: what this agent is responsible for and what it is not |
| **Inputs** | What it receives and from whom |
| **Outputs** | What it must produce, with the schema |
| **Tools** | Each tool it can call, when to call it, and when not to |
| **Procedure** | Step-by-step operating instructions |
| **Stop / handoff conditions** | When its work is done and what it hands off |
| **Guardrails** | Hard rules (untrusted input, no commitments, no guessing, etc.) |
| **Quality bar** | How it checks its own output before handing off |

> The current `agent/AGENTS.md` (single-agent version) is the starting material. Its extraction rules move to the Extraction skill, scoring and report rules move to the Analysis skill, and email-drafting rules move to the Communication skill.

---

## 6. Session and memory design

**Requirement:** each vendor conversation is an independent session, and every captured fact persists for the life of that session.

**Platform constraint (ZooWork):** sessions keep a full event log, but there is **no public memory-store API**. So memory is split into two layers:

| Layer | Where | Holds | Why |
|---|---|---|---|
| **Conversation history** | ZooWork Session (one per vendor) | Every turn: inbound emails, agent reasoning, tool calls, outbound emails | Full context for the agent; audit trail |
| **Vendor Memory (fact sheet)** | Backend database, keyed by `vendor_thread_id` | Structured facts with source, timestamp, confidence; checklist status | Durable, queryable, survives restarts; read by the Extraction Agent and shown to the human |

Rules:
- The Communication Agent calls `save_vendor_facts` **every time** it learns something. Facts are append-only: a corrected value creates a new version and keeps the old one.
- Each new turn starts by reading Vendor Memory, so the agent never re-asks for what it already has.
- The backend keeps its own index of `run → vendor_thread → zoowork_session_id`, since the platform has no cross-agent session listing.

---

## 7. Core data entities (logical)

| Entity | Key fields | Notes |
|---|---|---|
| `SourcingRun` | id, brief, vendor list, forecast, assumptions, status, deadline, created_by | One per **Start Sourcing Process** click |
| `VendorThread` | id, run_id, vendor, email, zoowork_session_id, status, rounds, last_contact_at | One per vendor per run |
| `EmailMessage` | id, thread_id, direction (in/out), body, attachments, sent_at | Raw record of every email |
| `VendorFact` | id, thread_id, product_ref, field, value, source_email_id, confidence, version | The persistent session memory |
| `Offer` | id, run_id, vendor_thread_id, brief_item (SKU), product name, normalized fields, meets_spec, spec_issues, path, sources | One per proposed product; one vendor → many offers |
| `AnalysisReport` | id, run_id, per-offer scores, ranking per brief item, overall product ranking, recommendation, risks | Produced by the Analysis Agent |
| `Decision` | id, run_id, outcome (accepted / denied_all), accepted_offer_ids (any vendors), notes, decided_by, decided_at | Produced by the human. Vendors involved are derived from the accepted offers |

### Status lifecycle

```mermaid
stateDiagram-v2
    direction LR
    state "SourcingRun" as RUN {
        [*] --> Outreach: Start Sourcing Process
        Outreach --> Extracting: all threads closed / deadline
        Extracting --> Analyzing
        Analyzing --> AwaitingDecision
        AwaitingDecision --> Accepted
        AwaitingDecision --> DeniedAll
    }
```

```mermaid
stateDiagram-v2
    direction LR
    state "VendorThread" as VT {
        [*] --> New
        New --> AwaitingReply: RFQ sent
        AwaitingReply --> ProcessingReply: vendor reply / reminder due
        ProcessingReply --> AwaitingReply: follow-up sent
        ProcessingReply --> Complete: checklist done
        ProcessingReply --> Declined: vendor cannot quote
        AwaitingReply --> Complete: checklist done
        AwaitingReply --> Declined: vendor cannot quote
        AwaitingReply --> Stalled: no reply / max rounds
    }
```

---

## 8. Backend surface for the frontend (contract outline)

The frontend team designs the UI. The backend only commits to these capabilities:

| Capability | Purpose |
|---|---|
| Start a sourcing run | Behind the **Start Sourcing Process** button |
| Get run status | Run status plus each vendor thread's status and round count |
| Get vendor thread | Email history and Vendor Memory (facts with sources) |
| Get offers | Normalized offers after extraction |
| Get analysis report | Scores, rankings, recommendation, risks |
| Submit decision | Accept a list of offer ids (any vendors), or deny all |
| Live updates | Push run and thread status changes (webhook or stream relay) |

---

## 9. Platform mapping (ZooWork)

| Need | How it maps |
|---|---|
| Three agents | Three ZooWork Agents. Each agent's `skill.md` is loaded as its persona (`AGENTS.md`) |
| One session per vendor | One ZooWork Session per `VendorThread` on the Communication Agent |
| Sending email | App-executed custom tool `send_email` → backend SMTP. The recipient is fixed by the backend |
| Receiving email | Backend IMAP polling (no public URL needed), or `POST /inbound-email` for webhook providers → `user.message` posted into that vendor's session |
| Memory | Backend SQLite database exposed through custom tools (§6) |
| Deterministic math | `run_costing` custom tool executed by the backend (`backend/app/costing.py`, adapted from `tools/costing.py`) |
| Waiting for vendors | Sessions sit idle between replies. A backend timer sends reminders, marks stalled threads, and enforces the deadline |

> **Implementation:** see [`../README.md`](../README.md). All tools are ZooWork application-executed custom tools, so every side effect (email, database, math) stays in the backend and is scoped to the session's run and vendor.

---

## 10. Out of scope for v0.1

- The **Collaborate with vendor / samples approved?** loop from `workflow.png`, i.e. what happens after the decision.
- Sending confirmation or decline emails after the decision.
- Frontend UI design.
- Vendor discovery (the vendor list is an input).

---

## 11. Open questions

1. **Outbound email approval.** Does the Communication Agent send emails on its own, or does a human approve each one (or only the first RFQ)? *Proposed default:* the button click approves the RFQ; follow-ups go out automatically under the guardrails.
2. **Who decides "information complete"?** *Proposed default:* the Communication Agent, against the checklist. Alternative: run the Extraction Agent after each reply as the completeness checker (stricter, costs more).
3. **Handoff gate timing.** Wait for every vendor to close, or start extraction at a fixed deadline with whatever has arrived?
4. **Multiple picks per brief item?** The human can accept several products for the same SKU. Should the Analysis Agent warn when combined MOQs exceed the forecast for that SKU?
5. **Scoring weights.** The final rubric (cost vs. spec fit vs. path effort vs. completeness) in the Analysis Agent's `skill.md`.
6. **Product category for the demo.** Vase and pillow (existing sample data) or drinkware (`data/cups_4000.json`)? And what role does the market data play in the brief?
7. **Email infrastructure.** Real mailbox integration for the demo, or a simulated inbox replaying the sample vendor emails?
