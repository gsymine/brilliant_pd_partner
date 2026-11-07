"""Custom tool declarations for the three ZooWork agents.

Each tool is executed by this backend (ZooWork "application-executed custom tools"): the agent
requests it, the run pauses, the backend runs the handler in app/tools.py and resolves the call.
Keep these in sync with the "Tools" section of each agent's skill.md.
"""
from __future__ import annotations

from typing import Any

DEFAULT_REQUIRED_FIELDS = [
    "design",            # design description / photos received
    "materials",         # material composition
    "unit_price",
    "currency",
    "incoterm",          # e.g. FOB
    "moq",               # total units (per-colour MOQ x colours)
    "production_days",
    "sampling_days",
    "sampling_cost",
    "payment_terms",
    "port_of_loading",
    "certifications",
]

_FACT = {
    "type": "object",
    "properties": {
        "product_ref": {"type": "string", "description": "Stable short name for the vendor's product, e.g. 'Mare vase 27cm'. Reuse it for every fact about the same product."},
        "brief_item": {"type": "string", "description": "Brief item (SKU) this product answers, if known."},
        "field": {"type": "string", "description": "Checklist field name (see skill.md) or another useful field name."},
        "value": {"description": "The value as stated by the vendor (string, number, boolean or list)."},
        "source_email_id": {"type": "string", "description": "id of the email the value came from."},
        "quote": {"type": "string", "description": "Short verbatim quote supporting the value."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["product_ref", "field", "value"],
}

COMMUNICATION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "send_email",
        "description": "Send an email to THIS session's vendor only (the recipient is fixed by the backend). "
                       "Use for the initial request, follow-ups for missing or unclear fields, and reminders. "
                       "Never commit to orders, prices, MOQs or payments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "maxLength": 200},
                "body": {"type": "string", "maxLength": 8000},
                "purpose": {"type": "string", "enum": ["rfq", "follow_up", "reminder", "clarification", "acknowledgement"]},
            },
            "required": ["subject", "body", "purpose"],
        },
    },
    {
        "name": "read_thread",
        "description": "Return every email in this vendor thread (both directions), with ids and attachment ids.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_vendor_facts",
        "description": "Save every new fact learned from the vendor into the persistent Vendor Memory. "
                       "Call it after EVERY inbound email that contains information. Values are versioned; "
                       "saving a new value for the same product+field keeps the old one.",
        "input_schema": {
            "type": "object",
            "properties": {"facts": {"type": "array", "items": _FACT, "minItems": 1, "maxItems": 100}},
            "required": ["facts"],
        },
    },
    {
        "name": "get_vendor_memory",
        "description": "Return the latest saved facts per product and, per product, which checklist fields are still missing.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_thread_status",
        "description": "Close this vendor conversation. 'complete' = every checklist field known for every proposed product; "
                       "'declined' = vendor cannot or will not quote; 'stalled' = vendor stopped answering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["complete", "declined", "stalled"]},
                "reason": {"type": "string", "maxLength": 1000},
            },
            "required": ["status", "reason"],
        },
    },
]

_OFFER = {
    "type": "object",
    "properties": {
        "thread_id": {"type": "string"},
        "product_ref": {"type": "string", "description": "The product_ref used in Vendor Memory."},
        "product_name": {"type": "string"},
        "brief_item": {"type": "string", "description": "SKU of the brief item this product answers."},
        "design": {"type": ["string", "null"]},
        "materials": {"type": ["string", "null"]},
        "country": {"type": ["string", "null"], "description": "ISO-2 country of production, e.g. CN, VN, PT, IN."},
        "currency": {"type": ["string", "null"], "description": "ISO currency code, e.g. USD, CNY, EUR, INR."},
        "unit_price": {"type": ["number", "null"]},
        "incoterm": {"type": ["string", "null"]},
        "moq": {"type": ["integer", "null"], "description": "TOTAL units. Per-colour MOQ x number of colours in the brief."},
        "moq_note": {"type": ["string", "null"]},
        "production_days": {"type": ["integer", "null"]},
        "sampling_days": {"type": ["integer", "null"]},
        "sampling_cost": {"type": ["string", "null"]},
        "payment_terms": {"type": ["string", "null"]},
        "port_of_loading": {"type": ["string", "null"]},
        "certifications": {"type": ["array", "null"], "items": {"type": "string"}},
        "extra_costs_usd": {"type": "number", "description": "Known one-off costs in USD (moulds, lab tests)."},
        "path": {"type": "string", "enum": ["accept", "modify", "co_develop"]},
        "meets_spec": {"type": "boolean"},
        "spec_issues": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "email_id": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["field", "email_id"],
            },
        },
    },
    "required": ["thread_id", "product_name", "brief_item", "path", "meets_spec", "spec_issues"],
}

EXTRACTION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_vendor_threads",
        "description": "List every vendor thread in this sourcing run with its status and close reason.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_thread",
        "description": "Return every email in one vendor thread, with email ids and attachment ids.",
        "input_schema": {"type": "object", "properties": {"thread_id": {"type": "string"}}, "required": ["thread_id"]},
    },
    {
        "name": "get_vendor_memory",
        "description": "Return the facts the Communication Agent saved for one vendor thread (latest version per field).",
        "input_schema": {"type": "object", "properties": {"thread_id": {"type": "string"}}, "required": ["thread_id"]},
    },
    {
        "name": "read_attachment",
        "description": "Return the content of one email attachment: text for text/CSV files, the image for PNG/JPEG/GIF/WebP. "
                       "Other types return metadata only.",
        "input_schema": {"type": "object", "properties": {"attachment_id": {"type": "string"}}, "required": ["attachment_id"]},
    },
    {
        "name": "save_offers",
        "description": "Save ALL structured offers for this run in one call (replaces earlier saves). One offer per proposed "
                       "product; a vendor may have several. Returns validation errors to fix, or the saved offer ids.",
        "input_schema": {
            "type": "object",
            "properties": {"offers": {"type": "array", "items": _OFFER, "maxItems": 200}},
            "required": ["offers"],
        },
    },
]

ANALYSIS_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_run_context",
        "description": "Return the sourcing brief, brief items, required checklist fields and whether costing data "
                       "(demand forecast, cost assumptions) is available.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_offers",
        "description": "Return every structured offer in this run.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_costing",
        "description": "Deterministic math: landed cost, MOQ excess-inventory cost, late-delivery lost margin, cost per sold "
                       "unit, a 0-100 score, per-item ranking and overall ranking for every offer. Use these numbers; "
                       "never compute your own.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_analysis_report",
        "description": "Save the final analysis for the human decision maker. Scores and ranks are taken from the latest "
                       "run_costing result by the backend; you supply the judgement and explanations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "executive_summary": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
                "offer_assessments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "offer_id": {"type": "string"},
                            "recommendation": {"type": "string", "enum": ["recommend", "consider", "not_recommended"]},
                            "rationale": {"type": "string", "description": "Why, in dollars and facts."},
                            "risks": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["offer_id", "recommendation", "rationale"],
                    },
                },
                "recommended_offer_ids": {"type": "array", "items": {"type": "string"}},
                "risks_and_open_questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["executive_summary", "offer_assessments", "recommended_offer_ids"],
        },
    },
]

TOOLS_BY_ROLE = {
    "communication": COMMUNICATION_TOOLS,
    "extraction": EXTRACTION_TOOLS,
    "analysis": ANALYSIS_TOOLS,
}
