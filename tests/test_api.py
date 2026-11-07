"""API flow tests on a throwaway database: reply -> check -> follow-up -> approve -> decide."""
import os
import tempfile
import unittest

os.environ["PD_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


class ApiFlowTest(unittest.TestCase):
    def setUp(self):
        self.ctx = TestClient(app)
        self.c = self.ctx.__enter__()
        self.c.post("/api/demo/reset")

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def status(self, vid):
        return self.c.get(f"/api/responses/{vid}").json()["status"]

    def test_seed_state(self):
        r = self.c.get("/api/round").json()
        self.assertEqual(r["invited"], 10)
        self.assertEqual(r["counts"]["complete"], 6)
        self.assertFalse(r["ready"])  # 60% < 70%

    def test_confirming_check_reaches_threshold(self):
        pid = self.c.get("/api/responses/V3").json()["products"][0]["id"]
        self.assertEqual(self.c.post(f"/api/products/{pid}/confirm").status_code, 200)
        self.assertTrue(self.c.get("/api/round").json()["ready"])

    def test_cannot_confirm_with_missing_fields(self):
        pid = self.c.get("/api/responses/V7").json()["products"][0]["id"]
        self.assertEqual(self.c.post(f"/api/products/{pid}/confirm").status_code, 400)

    def test_followup_flow(self):
        self.assertEqual(self.status("V7"), "needs_followup")
        mid = next(m["id"] for m in self.c.get("/api/approvals").json() if m["vendor_id"] == "V7")
        self.c.post(f"/api/messages/{mid}/approve")
        self.assertEqual(self.status("V7"), "followup_sent")
        self.assertTrue(self.c.post("/api/demo/simulate-reply/V7").json()["ok"])
        self.assertEqual(self.status("V7"), "needs_check")
        pid = self.c.get("/api/responses/V7").json()["products"][0]["id"]
        self.c.post(f"/api/products/{pid}/confirm")
        self.assertEqual(self.status("V7"), "complete")
        ranked = {p["id"]: p for p in self.c.get("/api/ranking").json()}
        self.assertIsNotNone(ranked[pid]["overall"])

    def test_edit_fields_and_numeric_validation(self):
        pid = self.c.get("/api/responses/V7").json()["products"][0]["id"]
        r = self.c.patch(f"/api/products/{pid}", json={"fields": {"lead_time_days": "42", "certifications": "FDA"}})
        self.assertEqual(r.json()["fields"]["lead_time_days"], 42)
        self.assertEqual(self.c.patch(f"/api/products/{pid}", json={"fields": {"moq": "lots"}}).status_code, 400)

    def test_weights_change_ranking_and_decisions_draft_per_vendor(self):
        top_cost = self.c.get("/api/ranking?cost=50&moq=0&lead=0&trend=0&spec=0&vendor=0").json()[0]
        top_moq = self.c.get("/api/ranking?cost=0&moq=50&lead=0&trend=0&spec=0&vendor=0").json()[0]
        self.assertNotEqual(top_cost["id"], top_moq["id"])
        n = self.c.post("/api/decisions/draft").json()["drafted"]
        drafts = [m for m in self.c.get("/api/approvals").json() if m["kind"] == "decision"]
        self.assertEqual(len(drafts), n)
        self.assertEqual(len({m["vendor_id"] for m in drafts}), n)


if __name__ == "__main__":
    unittest.main()
