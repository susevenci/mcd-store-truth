"""单元测试：全部离线，不需要 Token。"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from store_engine import (Coupon, Store, render_matrix, summarize,  # noqa: E402
                          verify_coupon, verify_matrix)

TODAY = "2026-10-25"


def store(code="3450101", name="测试餐厅"):
    return Store(store_code=code, name=name)


class NormalizeTest(unittest.TestCase):
    def test_coupon_channel_detected(self):
        self.assertEqual(Coupon.from_raw({"channel": "到店自取"}).channel, "pickup")
        self.assertEqual(Coupon.from_raw({"channel": "外送"}).channel, "delivery")
        self.assertEqual(Coupon.from_raw({"channel": "通用"}).channel, "any")
        self.assertEqual(Coupon.from_raw({}).channel, "unknown")

    def test_store_codes_may_be_csv(self):
        c = Coupon.from_raw({"storeCodes": "3450101,3450102"})
        self.assertEqual(c.store_codes, ["3450101", "3450102"])

    def test_missing_vs_empty_store_list(self):
        self.assertIsNone(Coupon.from_raw({"name": "x"}).store_codes)
        self.assertEqual(Coupon.from_raw({"storeCodes": []}).store_codes, [])


class VerifyTest(unittest.TestCase):
    def test_general_coupon_usable(self):
        c = Coupon(coupon_id="1", name="全渠道券", channel="any",
                   start_time="2026-01-01", end_time="2026-12-31", store_codes=[])
        r = verify_coupon(c, store(), scenario="pickup", today=TODAY)
        self.assertEqual(r.level, "TRUE")

    def test_expired_is_false(self):
        c = Coupon(coupon_id="2", name="过期券", channel="any",
                   start_time="2025-01-01", end_time="2025-08-31", store_codes=[])
        r = verify_coupon(c, store(), today=TODAY)
        self.assertEqual(r.level, "FALSE")
        self.assertTrue(any("过期" in b for b in r.blockers))

    def test_channel_mismatch_is_false(self):
        c = Coupon(coupon_id="3", name="外送券", channel="delivery",
                   start_time="2026-01-01", end_time="2026-12-31", store_codes=[])
        r = verify_coupon(c, store(), scenario="pickup", today=TODAY)
        self.assertEqual(r.level, "FALSE")
        self.assertTrue(any("渠道不匹配" in b for b in r.blockers))

    def test_store_not_in_whitelist(self):
        c = Coupon(coupon_id="4", name="限门店券", channel="pickup",
                   start_time="2026-01-01", end_time="2026-12-31",
                   store_codes=["3450999"])
        r = verify_coupon(c, store("3450101"), scenario="pickup", today=TODAY)
        self.assertEqual(r.level, "FALSE")
        self.assertTrue(any("不在适用清单" in b for b in r.blockers))

    def test_missing_fields_yield_unknown_not_true(self):
        c = Coupon(coupon_id="5", name="残缺券", channel="unknown")
        r = verify_coupon(c, store(), scenario="pickup", today=TODAY)
        self.assertEqual(r.level, "UNKNOWN")
        self.assertIn("适用门店清单", " ".join(r.missing))

    def test_no_store_yields_unknown(self):
        c = Coupon(coupon_id="6", name="券", channel="any",
                   start_time="2026-01-01", end_time="2026-12-31", store_codes=[])
        r = verify_coupon(c, None, scenario="pickup", today=TODAY)
        self.assertEqual(r.level, "UNKNOWN")

    def test_hard_evidence_beats_unknown(self):
        """有硬证据否决时，即使其他字段缺失也必须是 FALSE。"""
        c = Coupon(coupon_id="7", name="既过期又残缺", channel="unknown",
                   end_time="2020-01-01")
        r = verify_coupon(c, store(), today=TODAY)
        self.assertEqual(r.level, "FALSE")


class MatrixTest(unittest.TestCase):
    def setUp(self):
        self.stores = [store("3450101", "A店"), store("3450102", "B店")]
        self.coupons = [
            Coupon(coupon_id="1", name="通用券", channel="any",
                   start_time="2026-01-01", end_time="2026-12-31", store_codes=[]),
            Coupon(coupon_id="2", name="限 A 店", channel="pickup",
                   start_time="2026-01-01", end_time="2026-12-31", store_codes=["3450101"]),
        ]

    def test_matrix_size(self):
        results = verify_matrix(self.coupons, self.stores, today=TODAY)
        self.assertEqual(len(results), 4)

    def test_matrix_has_all_three_states(self):
        results = verify_matrix(self.coupons, self.stores, today=TODAY)
        levels = {r.level for r in results}
        self.assertIn("TRUE", levels)
        self.assertIn("FALSE", levels)

    def test_render_and_summary(self):
        results = verify_matrix(self.coupons, self.stores, today=TODAY)
        text = render_matrix(self.coupons, self.stores, results)
        self.assertIn("图例", text)
        s = summarize(results)
        self.assertEqual(s["total"], 4)
        self.assertEqual(sum(s["counts"].values()), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
