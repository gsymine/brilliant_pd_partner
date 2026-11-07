"""Replay vendors: 8 vendors (6 real emails, 2 rewritten declines) answer our RFQ (offline).

    cd backend && python -m unittest tests.test_replay_vendors -v
"""
from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_backend import BACKEND, Env  # sets EMAIL_MODE=dry_run, IMAP off

from app.costing import cost_offer
from app.media import offer_images
from app.email_service import is_simulated_address
from app.tools import dispatch
from app.vendor_sim import (MAX_REPLAY_FOLLOWUP_REPLIES, VendorSimulator, llm_instructions, load_replay_vendors,
                            plan_reply, read_eml, write_scripted)

RUN = json.loads((BACKEND / "examples" / "brightwell_pet_run.json").read_text(encoding="utf-8"))
VENDORS = {v["key"]: v for v in load_replay_vendors()}


class DatasetTest(unittest.TestCase):
    def test_eight_vendors_two_decline(self):
        self.assertEqual(len(VENDORS), 8)
        declines = [k for k, v in VENDORS.items() if "cannot quote" in read_eml(Path(v["replay_eml"]))["body"]
                    or "not be able to quote" in read_eml(Path(v["replay_eml"]))["body"]]
        self.assertEqual(sorted(declines), ["casavidrio", "pearlpine"])
        self.assertIn("modified_emails", VENDORS["casavidrio"]["replay_eml"])  # originals in data/ untouched
        pawprint = VENDORS["pawprint"]
        self.assertEqual((pawprint["email"], pawprint["contact"]), ("lily.chen@pawprintceramics.example", "Lily Chen"))
        self.assertTrue(all(is_simulated_address(v["email"]) for v in VENDORS.values()))  # never really emailed
        # the run config lists exactly the same vendors
        self.assertEqual({v["email"] for v in RUN["vendors"]}, {v["email"] for v in VENDORS.values()})

    def test_eml_body_and_photos(self):
        eml = read_eml(Path(VENDORS["pawprint"]["replay_eml"]))
        self.assertIn("TK3K53CTC", eml["body"])
        self.assertEqual([a[0] for a in eml["attachments"]],
                         ["TK3K53CTC_photo.jpg", "TKFNX3CTC_photo.jpg", "TK37Q3CTC_photo.jpg"])
        self.assertTrue(all(a[1] == "image/jpeg" and len(a[2]) > 10_000 for a in eml["attachments"]))

    def test_brief_comes_from_the_real_rfq(self):
        self.assertIn("PET LOVERS", RUN["brief"])
        self.assertIn("Compliance documents (FDA, LFGB, Prop 65, lead and cadmium)", RUN["brief"])
        self.assertEqual(len(RUN["required_fields"]), 20)

    def test_rmb_quote_is_costed_in_usd(self):
        m = cost_offer({"unit_price": 36.60, "currency": "RMB", "brief_item": "travel_cup"}, RUN["costing"])
        self.assertEqual(m["unit_usd"], round(36.60 * 0.14, 2))


class PlanTest(unittest.TestCase):
    def test_original_email_first_then_grounded_followups(self):
        v = VENDORS["whisker"]
        self.assertEqual(plan_reply(v, n_out=1, n_in=0)["kind"], "replay")
        for n_in in range(1, MAX_REPLAY_FOLLOWUP_REPLIES + 1):
            self.assertEqual(plan_reply(v, n_out=n_in + 1, n_in=n_in)["kind"], "grounded")
        self.assertIsNone(plan_reply(v, n_out=9, n_in=MAX_REPLAY_FOLLOWUP_REPLIES + 1))

    def test_grounded_prompt_and_fallback_never_invent(self):
        v = VENDORS["seasonal"]
        plan = {"kind": "grounded", "late": False, "injection": None,
                "original": read_eml(Path(v["replay_eml"]))["body"]}
        prompt = llm_instructions(v, plan, "Which price is right for 3SSHDM9998: 6.90 or 6.09?")
        self.assertIn("ONLY thing you know", prompt)
        self.assertIn("6.09", prompt)  # the original email is in the prompt
        self.assertIn("never resolve a contradiction", prompt)
        text = write_scripted(v, plan)
        self.assertIn("cannot provide more information", text)
        self.assertIn("Priya Nair", text)


class FakeComm:
    """Plays the Communication Agent: RFQ, then one follow-up per vendor reply, then complete."""

    def __init__(self, env: Env):
        self.env = env

    async def start_session(self, role, owner_id, run_id, message, thread_id=None, timeout=None):
        dispatch(self.env.ctx("communication", run_id, thread_id), "send_email",
                 {"subject": "Brightwell sourcing request", "body": "RFQ", "purpose": "rfq"})

    async def send(self, role, owner_id, message, key, timeout=None):
        run_id = self.env.db.get_thread(owner_id)["run_id"]
        ctx = self.env.ctx("communication", run_id, owner_id)
        replies = [e for e in self.env.db.emails_for_thread(owner_id) if e["direction"] == "in"]
        if len(replies) == 1:  # after the second reply: do nothing (no extraction in this test)
            dispatch(ctx, "send_email", {"subject": "Follow-up", "body": "Price validity?", "purpose": "follow_up"})


class ReplayRunTest(unittest.IsolatedAsyncioTestCase):
    async def test_vendors_reply_with_their_real_email_and_photos(self):
        env = Env()
        env.settings = replace(env.settings, sim_speed=0.0, demo_dataset="brightwell")
        env.email.s = env.orch.s = env.settings
        env.orch.runtime = FakeComm(env)  # type: ignore[assignment]
        sim = VendorSimulator(env.settings, env.db)
        env.email.listeners.append(sim.on_outbound)
        sim.attach(asyncio.get_running_loop(), env.orch.handle_inbound)  # no client: templates for follow-ups

        req = {**RUN, "vendors": [v for v in RUN["vendors"] if v["email"].startswith(("lily.chen", "kevin@barnyard"))]}
        run = env.orch.create_run(req)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if not env.orch._tasks and not sim._tasks:
                break
        threads = {t["vendor_email"]: t for t in env.db.threads_for_run(run["id"])}
        lily = env.db.emails_for_thread(threads["lily.chen@pawprintceramics.example"]["id"])
        first = next(e for e in lily if e["direction"] == "in")
        self.assertIn("Golden Doodle dog", first["body"])                 # verbatim real email
        self.assertEqual(len(first["attachments"]), 3)                    # with its photos
        self.assertEqual(first["in_reply_to"], lily[0]["message_id"])    # threaded to our RFQ
        second = [e for e in lily if e["direction"] == "in"][1]
        self.assertIn("cannot provide more information", second["body"])  # grounded follow-up (template)
        kevin = env.db.emails_for_thread(threads["kevin@barnyardbrights.example"]["id"])
        self.assertIn("RMB 36.60", next(e for e in kevin if e["direction"] == "in")["body"])


if __name__ == "__main__":
    unittest.main()


class PhotoTest(unittest.TestCase):
    def test_photos_match_offers_by_item_code(self):
        env = Env()
        run_id, (tid, tid2) = env.make_run(2)
        from app.email_service import ParsedEmail
        eml = read_eml(Path(VENDORS["pawprint"]["replay_eml"]))
        env.email.store_inbound(env.db.get_thread(tid), ParsedEmail(
            VENDORS["pawprint"]["email"], "", "Re", "<p1@x>", None, [], eml["body"], eml["attachments"]))
        base = {"path": "accept", "meets_spec": True, "spec_issues": []}
        ids = env.db.replace_offers(run_id, [
            {**base, "thread_id": tid, "product_name": "TK3K53CTC Golden Doodle dog travel cup", "brief_item": "A"},
            {**base, "thread_id": tid, "product_name": "TKFNX3CTC German Shepherd", "brief_item": "A"},
            {**base, "thread_id": tid2, "product_name": "No photo cup", "brief_item": "A"},
        ])
        imgs = offer_images(env.db, run_id)
        self.assertEqual(len(imgs[ids[0]]), 1)
        self.assertTrue(imgs[ids[0]][0].startswith(f"/runs/{run_id}/attachments/"))
        self.assertNotEqual(imgs[ids[0]], imgs[ids[1]])
        self.assertNotIn(ids[2], imgs)
