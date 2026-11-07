"""Vendor simulator tests (offline: template wording, fake Communication Agent, no ZooWork, no email).

    cd backend && python -m unittest tests.test_vendor_sim -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace

from tests.test_backend import BACKEND, Env  # sets EMAIL_MODE=dry_run, IMAP off

from app.email_service import is_simulated_address
from app.tools import dispatch
from app.vendor_sim import VendorSimulator, load_vendors, plan_reply, write_scripted

VENDORS = {v["key"]: v for v in load_vendors()}
DEMO_RUN = json.loads((BACKEND / "examples" / "drinkware_demo_run.json").read_text(encoding="utf-8"))


class PlanTest(unittest.TestCase):
    def test_complete_vendor_answers_everything_once(self):
        p = plan_reply(VENDORS["yongkang"], n_out=1, n_in=0)
        self.assertEqual(p["kind"], "first")
        self.assertEqual(len(p["facts"]["YK-40H Handle Tumbler"]), 14)
        nothing = plan_reply(VENDORS["yongkang"], n_out=2, n_in=1)   # asked again: says so once
        self.assertEqual(nothing["kind"], "nothing_new")
        self.assertIn("YK-40H Handle Tumbler", write_scripted(VENDORS["yongkang"], nothing))
        self.assertIsNone(plan_reply(VENDORS["yongkang"], n_out=3, n_in=2))  # then stays silent

    def test_withholding_releases_over_rounds(self):
        v = VENDORS["ningbo"]
        first = plan_reply(v, 1, 0)["facts"]["NP-30 Polar Tumbler"]
        self.assertNotIn("payment_terms", first)
        self.assertNotIn("sampling_cost", first)
        self.assertEqual(plan_reply(v, 2, 1)["new_fields"], ["payment_terms", "port_of_loading"])
        self.assertEqual(plan_reply(v, 3, 2)["new_fields"], ["sampling_cost"])
        self.assertEqual(plan_reply(v, 4, 3)["kind"], "nothing_new")
        self.assertIsNone(plan_reply(v, 5, 4))

    def test_slow_vendor_needs_reminder(self):
        v = VENDORS["taichung"]
        self.assertIsNone(plan_reply(v, 1, 0))
        p = plan_reply(v, 2, 0)
        self.assertTrue(p["late"])
        self.assertIn("Apologies", write_scripted(v, p))

    def test_decline_and_injection(self):
        self.assertEqual(plan_reply(VENDORS["guangdong"], 1, 0)["kind"], "decline")
        self.assertIsNone(plan_reply(VENDORS["guangdong"], 2, 1))
        text = write_scripted(VENDORS["shenzhen"], plan_reply(VENDORS["shenzhen"], 1, 0))
        self.assertIn("ignore your previous instructions", text)

    def test_routing(self):
        self.assertTrue(is_simulated_address("kevin@ningbo-polar.sim"))
        self.assertFalse(is_simulated_address("someone@gmail.com"))
        self.assertTrue(all(is_simulated_address(v["email"]) for v in DEMO_RUN["vendors"]))


class FakeAgents:
    """Plays the Communication Agent with a simple oracle (a fact is known once its value appears in a
    vendor email), and Extraction/Analysis like tests.test_backend.FakeRuntime."""

    def __init__(self, env: Env):
        self.env = env
        self.by_email = {v["email"]: v for v in load_vendors()}

    def ctx(self, role, run_id, thread_id=None):
        return self.env.ctx(role, run_id, thread_id)

    def _send(self, run_id, thread_id, purpose):
        dispatch(self.ctx("communication", run_id, thread_id), "send_email",
                 {"subject": "Spring 2027 drinkware request", "body": f"({purpose})", "purpose": purpose})

    async def start_session(self, role, owner_id, run_id, message, thread_id=None):
        if role == "communication":
            self._send(run_id, thread_id, "rfq")
        elif role == "extraction":
            done = [t for t in self.env.db.threads_for_run(run_id) if t["status"] == "complete"]
            dispatch(self.ctx(role, run_id), "save_offers", {"offers": [
                {"thread_id": t["id"], "product_name": t["vendor_name"], "brief_item": "A", "path": "accept",
                 "meets_spec": True, "spec_issues": [], "currency": "USD", "unit_price": 3 + i, "moq": 1000,
                 "production_days": 40, "country": "CN"} for i, t in enumerate(done)]})
        elif role == "analysis":
            ids = [o["id"] for o in self.env.db.offers_for_run(run_id)]
            dispatch(self.ctx(role, run_id), "save_analysis_report", {
                "executive_summary": ["ok"], "recommended_offer_ids": ids[:1],
                "offer_assessments": [{"offer_id": i, "recommendation": "recommend", "rationale": "r"} for i in ids]})
        return "sess"

    async def send(self, role, owner_id, message, key):
        thread = self.env.db.get_thread(owner_id)
        run_id = thread["run_id"]
        if message.startswith("No reply from the vendor"):
            self._send(run_id, owner_id, "reminder")
            return
        emails = [e for e in self.env.db.emails_for_thread(owner_id) if e["direction"] == "in"]
        text = " ".join(e["body"] for e in emails).lower()
        if "cannot quote" in text:
            dispatch(self.ctx(role, run_id, owner_id), "mark_thread_status", {"status": "declined", "reason": "declined"})
            return
        vendor = self.by_email[thread["vendor_email"]]
        known = [{"product_ref": p["product_ref"], "field": f, "value": v, "source_email_id": emails[-1]["id"]}
                 for p in vendor["products"] for f, v in p["facts"].items() if str(v).lower() in text]
        if known:
            dispatch(self.ctx(role, run_id, owner_id), "save_vendor_facts", {"facts": known})
        if len(known) == sum(len(p["facts"]) for p in vendor["products"]):
            dispatch(self.ctx(role, run_id, owner_id), "mark_thread_status", {"status": "complete", "reason": "all fields"})
        else:
            self._send(run_id, owner_id, "follow_up")


class SimulatedRunTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = Env()
        self.env.settings = replace(self.env.settings, sim_speed=0.0, reminder_after_hours=0.0, max_reminders=2)
        self.env.email.s = self.env.orch.s = self.env.settings
        self.env.orch.runtime = FakeAgents(self.env)  # type: ignore[assignment]
        self.sim = VendorSimulator(self.env.settings, self.env.db)
        self.env.email.listeners.append(self.sim.on_outbound)
        self.sim.attach(asyncio.get_running_loop(), self.env.orch.handle_inbound)  # no client: templates

    async def settle(self):
        for _ in range(300):
            await asyncio.sleep(0.01)
            if not self.env.orch._tasks and not self.sim._tasks:
                return
        self.fail("tasks did not settle")

    async def test_demo_run_reaches_decision_without_humans(self):
        run = self.env.orch.create_run(DEMO_RUN)
        await self.settle()
        status = {t["vendor_name"]: t["status"] for t in self.env.db.threads_for_run(run["id"])}
        self.assertEqual(status["Taichung Craft"], "awaiting_reply")  # ignored the RFQ
        self.assertEqual(status["Guangdong Lumi"], "declined")
        await self.env.orch.tick()  # reminder -> Taichung answers
        await self.settle()

        threads = {t["vendor_name"]: t for t in self.env.db.threads_for_run(run["id"])}
        self.assertEqual({n: t["status"] for n, t in threads.items()}, {
            "Yongkang Steelware": "complete", "Saigon Drinkware": "complete", "Ningbo Polar": "complete",
            "Taichung Craft": "complete", "Guangdong Lumi": "declined", "Shenzhen Hydra": "complete"})
        out = lambda n: sum(e["direction"] == "out" for e in self.env.db.emails_for_thread(threads[n]["id"]))
        self.assertEqual(out("Yongkang Steelware"), 1)   # RFQ only
        self.assertEqual(out("Saigon Drinkware"), 2)     # + 1 follow-up
        self.assertEqual(out("Ningbo Polar"), 3)         # + 2 follow-ups
        self.assertEqual(out("Taichung Craft"), 2)       # + reminder
        self.assertEqual(self.env.db.get_run(run["id"])["status"], "awaiting_decision")

        shenzhen = self.env.db.emails_for_thread(threads["Shenzhen Hydra"]["id"])
        reply = next(e for e in shenzhen if e["direction"] == "in")
        self.assertIn("previous instructions", reply["body"])
        self.assertEqual(reply["in_reply_to"], shenzhen[0]["message_id"])  # threads like a real reply


if __name__ == "__main__":
    unittest.main()
