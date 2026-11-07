"""Offline tests: no ZooWork calls, no real email (EMAIL_MODE=dry_run).

    cd backend && python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ["EMAIL_MODE"] = "dry_run"
os.environ["IMAP_ENABLED"] = "false"

from zoowork import SessionEvent  # noqa: E402

from app import costing  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import Database, new_id, now_iso  # noqa: E402
from app.email_service import EmailService, ParsedEmail, match_thread, parse_message, thread_tag  # noqa: E402
from app.orchestrator import Orchestrator  # noqa: E402
from app.tool_specs import DEFAULT_REQUIRED_FIELDS  # noqa: E402
from app.tools import ToolContext, dispatch  # noqa: E402
from app.zoowork_runtime import ROLES, ZooworkRuntime, agent_resource, load_skill  # noqa: E402

SAMPLE = json.loads((BACKEND / "examples" / "sample_run.json").read_text(encoding="utf-8"))
QUOTES = json.loads((BACKEND.parent / "data" / "quotes.example.json").read_text(encoding="utf-8"))


def offers_from_quotes() -> list[dict]:
    out = []
    for i, q in enumerate(QUOTES):
        out.append({
            "id": f"off_{i}", "thread_id": "thr_x", "vendor_name": q["vendor"], "product_name": q["item"],
            "brief_item": q["sku"], "country": q["country"], "currency": q["currency"], "unit_price": q["unit_price"],
            "moq": q["moq"], "production_days": q["production_days"], "sampling_days": q["sampling_days"],
            "path": q["path"], "extra_costs_usd": q["extra_costs_usd"], "meets_spec": q.get("meets_spec", True),
            "spec_issues": q["spec_issues"],
        })
    return out


class Env:
    """A fresh database + services in a temp dir."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = load_settings()
        self.settings = replace(base, data_dir=Path(self.tmp.name), db_path=Path(self.tmp.name) / "t.sqlite3",
                                email_mode="dry_run", from_address="sourcing@brand.example",
                                reminder_after_hours=1, max_reminders=1)
        self.db = Database(self.settings.db_path)
        self.email = EmailService(self.settings, self.db)
        self.orch = Orchestrator(self.db, self.settings, self.email)

    def make_run(self, n_vendors: int = 2) -> tuple[str, list[str]]:
        run_id = new_id("run")
        self.db.insert("runs", {
            "id": run_id, "status": "outreach", "title": SAMPLE["title"], "brief": SAMPLE["brief"],
            "brief_items": SAMPLE["brief_items"], "required_fields": DEFAULT_REQUIRED_FIELDS,
            "costing": SAMPLE["costing"], "deadline_at": "2999-01-01T00:00:00+00:00", "created_by": "t",
            "error": None, "created_at": now_iso(), "updated_at": now_iso(),
        })
        tids = []
        for i in range(n_vendors):
            tid = new_id("thr")
            self.db.insert("vendor_threads", {
                "id": tid, "run_id": run_id, "vendor_name": f"Vendor {i}", "vendor_email": f"v{i}@vendor.example",
                "country": "VN", "status": "new", "close_reason": None, "outbound_count": 0, "reminders_sent": 0,
                "last_outbound_at": None, "last_inbound_at": None, "created_at": now_iso(), "updated_at": now_iso(),
            })
            tids.append(tid)
        return run_id, tids

    def ctx(self, role: str, run_id: str, thread_id: str | None = None) -> ToolContext:
        return self.orch.tool_context(role, run_id, thread_id)


def val(content):  # first json block of a tool result
    return next(b["value"] for b in content if b["type"] == "json")


def full_facts(product: str, email_id: str) -> list[dict]:
    values = {"design": "matte vase", "materials": "stoneware", "unit_price": 7.1, "currency": "USD", "incoterm": "FOB",
              "moq": 1000, "production_days": 45, "sampling_days": 14, "sampling_cost": "USD 60",
              "payment_terms": "30/70", "port_of_loading": "Haiphong", "certifications": "not available"}
    return [{"product_ref": product, "brief_item": "A", "field": k, "value": v, "source_email_id": email_id}
            for k, v in values.items()]


# ------------------------------------------------------------------------------------------
class CostingTest(unittest.TestCase):
    def test_matches_original_tool_on_answer_key(self):
        res = costing.score_offers(offers_from_quotes(), SAMPLE["costing"], [])
        by = {r["vendor"]: r for r in res["offers"]}
        self.assertLess(by["Jingde Ceramics"]["unit_usd"], by["Hanoi Clayworks"]["unit_usd"])
        self.assertGreater(by["Jingde Ceramics"]["cost_per_sold_unit"], by["Hanoi Clayworks"]["cost_per_sold_unit"])
        self.assertEqual(by["Jingde Ceramics"]["excess_units"], 1200)
        self.assertAlmostEqual(by["Jingde Ceramics"]["landed_usd"], round(6.20 * 1.31 + 0.95, 2))

    def test_failed_spec_ranks_last_in_item(self):
        res = costing.score_offers(offers_from_quotes(), SAMPLE["costing"], [])
        ranking_b = res["ranking_by_item"]["B"]
        by_id = {r["offer_id"]: r for r in res["offers"]}
        self.assertEqual(by_id[ranking_b[-1]]["vendor"], "Ningbo Hometex")
        self.assertEqual(by_id[ranking_b[-1]]["score_breakdown"]["spec"], 0)

    def test_missing_costing_context_degrades_gracefully(self):
        res = costing.score_offers(offers_from_quotes(), None, [])
        for r in res["offers"]:
            self.assertIsNone(r["cost_per_sold_unit"])
            self.assertTrue(any("forecast" in f for f in r["flags"]))
        self.assertEqual(len(res["overall_ranking"]), len(QUOTES))


class EmailTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.run_id, self.tids = self.env.make_run()

    def test_parse_and_match_by_reply_header_tag_and_sender(self):
        thread = self.env.db.get_thread(self.tids[0])
        out = self.env.email.send(thread, "Spring 2027 request", "hello")
        self.assertIn(thread_tag(thread["id"]), out["subject"])
        raw = (f"From: Vendor <v0@vendor.example>\r\nTo: sourcing@brand.example\r\nSubject: Re: hi\r\n"
               f"Message-ID: <r1@vendor.example>\r\nIn-Reply-To: {out['message_id']}\r\n\r\nPrice is $7.10 FOB\r\n").encode()
        parsed = parse_message(raw)
        self.assertEqual(parsed.body, "Price is $7.10 FOB")
        self.assertEqual(match_thread(self.env.db, parsed)["id"], thread["id"])
        by_tag = ParsedEmail("x@other.example", "", f"Re: {thread_tag(self.tids[1])}", None, None, [], "b")
        self.assertEqual(match_thread(self.env.db, by_tag)["id"], self.tids[1])
        self.env.db.update("vendor_threads", "id", self.tids[1], {"status": "awaiting_reply"})
        by_sender = ParsedEmail("v1@vendor.example", "", "no tag", None, None, [], "b")
        self.assertEqual(match_thread(self.env.db, by_sender)["id"], self.tids[1])
        self.assertIsNone(match_thread(self.env.db, ParsedEmail("who@nowhere.example", "", "x", None, None, [], "b")))

    def test_inbound_dedup(self):
        thread = self.env.db.get_thread(self.tids[0])
        p = ParsedEmail("v0@vendor.example", "", "s", "<dup@x>", None, [], "body")
        self.assertIsNotNone(self.env.email.store_inbound(thread, p))
        self.assertIsNone(self.env.email.store_inbound(thread, p))


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.run_id, self.tids = self.env.make_run()
        self.cctx = self.env.ctx("communication", self.run_id, self.tids[0])

    def test_communication_flow_and_scoping(self):
        content, err = dispatch(self.cctx, "send_email", {"subject": "RFQ", "body": "Please quote", "purpose": "rfq"})
        self.assertFalse(err)
        t = self.env.db.get_thread(self.tids[0])
        self.assertEqual((t["status"], t["outbound_count"]), ("awaiting_reply", 1))
        self.assertEqual(self.env.db.emails_for_thread(self.tids[0])[0]["to_addr"], "v0@vendor.example")

        inbound = self.env.email.store_inbound(t, ParsedEmail("v0@vendor.example", "", "Re", "<m1@v>", None, [], "x"))
        # A source email from another thread is rejected.
        other = self.env.email.store_inbound(self.env.db.get_thread(self.tids[1]),
                                             ParsedEmail("v1@vendor.example", "", "Re", "<m2@v>", None, [], "y"))
        _, err = dispatch(self.cctx, "save_vendor_facts", {"facts": [
            {"product_ref": "Vase", "field": "unit_price", "value": 7, "source_email_id": other["id"]}]})
        self.assertTrue(err)

        facts = full_facts("Mare vase", inbound["id"])
        _, err = dispatch(self.cctx, "save_vendor_facts", {"facts": facts[:3]})
        self.assertFalse(err)
        content, err = dispatch(self.cctx, "mark_thread_status", {"status": "complete", "reason": "done"})
        self.assertTrue(err)
        self.assertIn("missing", content[0]["text"])

        dispatch(self.cctx, "save_vendor_facts", {"facts": facts[3:]})
        dispatch(self.cctx, "save_vendor_facts", {"facts": [
            {"product_ref": "Mare vase", "field": "unit_price", "value": 6.9, "source_email_id": inbound["id"]}]})
        mem = val(dispatch(self.cctx, "get_vendor_memory", {})[0])
        self.assertTrue(mem["all_complete"])
        self.assertEqual(mem["products"][0]["fields"]["unit_price"]["value"], 6.9)
        self.assertEqual(mem["products"][0]["fields"]["unit_price"]["version"], 2)
        _, err = dispatch(self.cctx, "mark_thread_status", {"status": "complete", "reason": "done"})
        self.assertFalse(err)
        _, err = dispatch(self.cctx, "send_email", {"subject": "x", "body": "y", "purpose": "follow_up"})
        self.assertTrue(err)  # closed thread cannot email

    def test_unknown_tool_for_role(self):
        _, err = dispatch(self.cctx, "save_offers", {"offers": []})
        self.assertTrue(err)

    def test_extraction_validation_and_analysis_report(self):
        ectx = self.env.ctx("extraction", self.run_id)
        bad = {"thread_id": self.tids[0], "product_name": "Vase", "brief_item": "Z", "path": "accept",
               "meets_spec": True, "spec_issues": []}
        content, err = dispatch(ectx, "save_offers", {"offers": [bad]})
        self.assertTrue(err)
        self.assertIn("brief_item", content[0]["text"])
        good = [
            {**bad, "brief_item": "A", "country": "VN", "currency": "USD", "unit_price": 7.1, "moq": 1000,
             "production_days": 45, "sampling_days": 14},
            {**bad, "thread_id": self.tids[1], "product_name": "Vase 2", "brief_item": "A", "country": "CN",
             "currency": "USD", "unit_price": 6.2, "moq": 3000, "production_days": 55, "sampling_days": 10},
            {**bad, "thread_id": self.tids[1], "product_name": "Pillow", "brief_item": "B", "country": "CN",
             "currency": "CNY", "unit_price": 22, "moq": 2000, "production_days": 35, "meets_spec": False,
             "spec_issues": ["45% linen"]},
        ]
        saved = val(dispatch(ectx, "save_offers", {"offers": good})[0])
        self.assertEqual(saved["saved"], 3)

        actx = self.env.ctx("analysis", self.run_id)
        scored = val(dispatch(actx, "run_costing", {})[0])
        self.assertEqual(len(scored["ranking_by_item"]["A"]), 2)
        ids = saved["offer_ids"]
        content, err = dispatch(actx, "save_analysis_report", {
            "executive_summary": ["x"], "recommended_offer_ids": [ids[0]],
            "offer_assessments": [{"offer_id": ids[0], "recommendation": "recommend", "rationale": "r"}]})
        self.assertTrue(err)  # every offer needs an assessment
        dispatch(actx, "save_analysis_report", {
            "executive_summary": ["x"], "recommended_offer_ids": [ids[0], ids[1]],
            "offer_assessments": [{"offer_id": i, "recommendation": "recommend", "rationale": "r"} for i in ids]})
        report = self.env.db.get_report(self.run_id)
        self.assertEqual(len(report["offers"]), 3)
        self.assertIn("score", report["offers"][0])


class FakeRuntime:
    """Stands in for ZooWork: records calls, and plays Extraction/Analysis by calling the real tools."""

    def __init__(self, env: Env):
        self.env = env
        self.calls: list[tuple] = []

    async def start_session(self, role, owner_id, run_id, message, thread_id=None):
        self.calls.append(("start", role, owner_id))
        if role == "communication":
            dispatch(self.env.ctx(role, run_id, thread_id), "send_email",
                     {"subject": "RFQ", "body": "Please quote", "purpose": "rfq"})
        elif role == "extraction":
            threads = self.env.db.threads_for_run(run_id)
            dispatch(self.env.ctx(role, run_id), "save_offers", {"offers": [
                {"thread_id": t["id"], "product_name": f"P{i}", "brief_item": "A", "path": "accept",
                 "meets_spec": True, "spec_issues": [], "currency": "USD", "unit_price": 5 + i, "moq": 1000,
                 "production_days": 40} for i, t in enumerate(threads)]})
        elif role == "analysis":
            ids = [o["id"] for o in self.env.db.offers_for_run(run_id)]
            dispatch(self.env.ctx(role, run_id), "save_analysis_report", {
                "executive_summary": ["ok"], "recommended_offer_ids": ids,
                "offer_assessments": [{"offer_id": i, "recommendation": "recommend", "rationale": "r"} for i in ids]})
        return "sess"

    async def send(self, role, owner_id, message, key):
        self.calls.append(("send", role, owner_id, key))
        run_id = self.env.db.get_thread(owner_id)["run_id"]
        dispatch(self.env.ctx(role, run_id, owner_id), "mark_thread_status", {"status": "declined", "reason": "test"})


class OrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = Env()
        self.rt = FakeRuntime(self.env)
        self.env.orch.runtime = self.rt  # type: ignore[assignment]

    async def _drain(self):
        for _ in range(50):
            if not self.env.orch._tasks:
                return
            await asyncio.sleep(0.01)
        self.fail("background tasks did not finish")

    async def test_full_flow_to_decision(self):
        req = {**SAMPLE, "vendors": [{"name": "A Co", "email": "A@x.example", "country": "VN"},
                                     {"name": "B Co", "email": "b@x.example", "country": "CN"}]}
        run = self.env.orch.create_run(req)
        await self._drain()
        threads = self.env.db.threads_for_run(run["id"])
        self.assertTrue(all(t["status"] == "awaiting_reply" for t in threads))
        self.assertEqual(threads[0]["vendor_email"], "a@x.example")

        for i, t in enumerate(threads):  # each vendor replies; fake agent declines -> gate opens after the last
            await self.env.orch.handle_inbound(ParsedEmail(t["vendor_email"], "", "Re", f"<in{i}@x>", None, [], "hi"))
            await self._drain()
        self.assertEqual(self.env.db.get_run(run["id"])["status"], "awaiting_decision")
        self.assertEqual([c[1] for c in self.rt.calls if c[0] == "start"][-2:], ["extraction", "analysis"])

        offers = self.env.db.offers_for_run(run["id"])
        with self.assertRaises(ValueError):
            self.env.orch.decide(run["id"], "accepted", ["off_nope"], None, None)
        d = self.env.orch.decide(run["id"], "accepted", [offers[0]["id"], offers[1]["id"]], "both", "pd")
        self.assertEqual(len(d["accepted_offer_ids"]), 2)  # products from two vendors
        self.assertEqual(self.env.db.get_run(run["id"])["status"], "accepted")
        with self.assertRaises(ValueError):
            self.env.orch.decide(run["id"], "denied_all", [], None, None)

    async def test_reminder_then_stall(self):
        run_id, tids = self.env.make_run(1)
        self.env.db.update("vendor_threads", "id", tids[0], {"status": "awaiting_reply", "outbound_count": 1,
                                                              "last_outbound_at": "2000-01-01T00:00:00+00:00"})
        self.rt.send = self._noop_send  # agent sends nothing on reminder
        await self.env.orch.tick()
        await self._drain()
        t = self.env.db.get_thread(tids[0])
        self.assertEqual((t["status"], t["reminders_sent"]), ("awaiting_reply", 1))
        self.env.db.update("vendor_threads", "id", tids[0], {"last_outbound_at": "2000-01-01T00:00:00+00:00"})
        await self.env.orch.tick()
        await self._drain()
        self.assertEqual(self.env.db.get_thread(tids[0])["status"], "stalled")
        self.assertEqual(self.env.db.get_run(run_id)["status"], "awaiting_decision")

    async def _noop_send(self, *a):
        return None


class FakeClient:
    def __init__(self, events):
        self.events = events
        self.resolved = []

    async def stream_events(self, agent_id, session_id, cursor=None):
        for e in self.events:
            yield e

    async def resolve_custom_tool_call(self, agent_id, call_id, *, content, is_error=None, resolved_by=None):
        self.resolved.append((call_id, content, is_error))
        return {}


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_drive_executes_custom_tool_and_stops_on_run_finished(self):
        env = Env()
        run_id, tids = env.make_run(1)
        events = [
            SessionEvent(seq=1, event_type="agent.custom_tool_use", payload={
                "phase": "requested", "callId": "call_1", "name": "get_vendor_memory", "input": {}}, cursor="c1"),
            SessionEvent(seq=2, event_type="run.finished", payload={"status": "succeeded"}, cursor="c2"),
        ]
        client = FakeClient(events)
        rt = ZooworkRuntime(client, env.db, env.settings, env.orch.tool_context)
        env.db.save_session("communication", tids[0], run_id, "agt_1", "ses_1")
        outcome = await rt._drive("communication", run_id, tids[0], "agt_1", "ses_1")
        self.assertEqual(outcome, "succeeded")
        self.assertEqual(client.resolved[0][0], "call_1")
        self.assertFalse(client.resolved[0][2])
        self.assertEqual(env.db.get_session("communication", tids[0])["cursor"], "c2")
        await rt._drive("communication", run_id, tids[0], "agt_1", "ses_1")  # replay: no second execution
        self.assertEqual(len(client.resolved), 2)
        self.assertEqual(env.db.one("SELECT COUNT(*) AS n FROM tool_calls")["n"], 1)

    def test_skills_and_tool_specs_load(self):
        settings = load_settings()
        for role in ROLES:
            body = load_skill(role)
            self.assertFalse(body.startswith("---"))
            res = agent_resource(role, settings, "model-x")
            names = {t["name"] for t in res["custom_tools"]}
            for name in names:  # every declared tool is documented in the role's skill.md
                self.assertIn(f"`{name}", body, f"{role}: {name} missing from skill.md")


if __name__ == "__main__":
    unittest.main()
