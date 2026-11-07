import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import costing  # noqa: E402


class CostingTest(unittest.TestCase):
    def setUp(self):
        self.a, self.fc = costing.load_inputs(ROOT / "data")
        self.quotes = json.loads((ROOT / "data" / "quotes.example.json").read_text())

    def by_vendor(self):
        return {s.vendor: s for s in costing.score_all(self.quotes, self.a, self.fc)}

    def test_china_tariff_and_moq_make_cheapest_fob_lose(self):
        s = self.by_vendor()
        self.assertLess(s["Jingde Ceramics"].unit_usd, s["Hanoi Clayworks"].unit_usd)
        self.assertGreater(s["Jingde Ceramics"].cost_per_sold_unit, s["Hanoi Clayworks"].cost_per_sold_unit)
        self.assertEqual(s["Jingde Ceramics"].excess_units, 1200)

    def test_landed_cost_math(self):
        j = self.by_vendor()["Jingde Ceramics"]
        # 6.20 FOB + 31% duty/tariff + 0.95 freight
        self.assertAlmostEqual(j.landed_usd, round(6.20 * 1.31 + 0.95, 2))

    def test_missing_moq_is_flagged(self):
        p = self.by_vendor()["Porto Atelier"]
        self.assertTrue(any("MOQ not stated" in f for f in p.flags))

    def test_off_spec_vendor_ranks_last(self):
        ranked = [s.vendor for s in costing.score_all(self.quotes, self.a, self.fc) if s.sku == "B"]
        self.assertEqual(ranked, ["Panipat Linens", "Ningbo Hometex"])

    def test_late_arrival_costs_margin(self):
        p = self.by_vendor()["Panipat Linens"]
        self.assertGreater(p.days_late, 0)
        self.assertGreater(p.lost_margin_usd, 0)


if __name__ == "__main__":
    unittest.main()
