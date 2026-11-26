"""Gate dispatches and Zalo inbox statuses on the AI page (zalo-intake spec R0, criteria 13 and 15)."""
from datetime import date
import unittest

from hasc.web.ai_stats import ai_overview, daily, month_cost_usd
from hasc.web.zalo_inbox import usage, zalo_overview
from datetime import datetime, timezone
from tests import test_zalo_gate as gate
from tests.test_zalo_gate import OTHER, front, vn

DAY = date(2026, 10, 19)  # the Vietnam day of the batches below (23:00)
CALL_COST, CALL_TOKENS = 0.0004, 1260  # what the fake transport reports for one dispatch


class GateOnTheAiPage(gate.GateTest):
    def test_every_dispatch_counts_in_the_daily_and_monthly_cost_and_tokens(self):
        for minute in range(3):
            self.deliver(vn(2026, 10, 19, 15, minute))
        self.deliver(vn(2026, 10, 19, 15, 5), staging=1)
        failed = self.deliver(vn(2026, 10, 19, 15, 6))
        self.batch(front(), front(shift=3), OTHER, front(), gate.TransportError("down"))
        self.assertEqual(self.count("gate_calls"), 5)
        rate = 26000
        overview = ai_overview(self.conn, DAY)
        self.assertEqual(overview["tokens"], 4 * CALL_TOKENS + 0 * 1, "the failed dispatch has no usage to count")
        self.assertEqual(overview["cost_vnd"], round(4 * CALL_COST * rate))
        self.assertAlmostEqual(month_cost_usd(self.conn, DAY), 4 * CALL_COST)
        today = daily(self.conn, DAY, DAY)[DAY.isoformat()]
        self.assertAlmostEqual(today["cost_usd"], 4 * CALL_COST)
        self.assertEqual(today["tokens"], 4 * CALL_TOKENS)
        row = next(r for r in overview["recent"] if r["day"] == DAY)
        self.assertEqual((row["tokens"], row["cost_vnd"]), (4 * CALL_TOKENS, round(4 * CALL_COST * rate)))
        self.assertEqual(self.inbox(failed)["status"], "delivered")

    def test_a_day_of_another_month_or_another_day_is_kept_apart(self):
        self.deliver(vn(2026, 10, 19, 15))
        self.batch(front())
        self.assertEqual(ai_overview(self.conn, date(2026, 11, 3))["tokens"], 0)
        self.assertEqual(daily(self.conn, date(2026, 10, 20), date(2026, 10, 25)), {})

    def test_download_counters_count_every_saved_photo_whatever_the_gate_did(self):
        rows = [self.deliver(vn(2026, 10, 19, 15, i)) for i in range(5)]
        self.deliver(vn(2026, 10, 19, 16), staging=1)
        self.batch(front(), front(shift=3), OTHER, front(code="9", nets=("1", "2", "3")), front(), front(shift=3))
        self.assertEqual(sorted(self.inbox(i)["status"] for i in rows),
                         ["filed", "filed", "filed", "not_form", "waiting"])
        counts = usage(self.conn, DAY)
        self.assertEqual(counts["month"]["pilot"]["delivered"], 5)
        self.assertEqual(counts["month"]["staging"]["delivered"], 1)
        today = next(d for d in counts["days"] if d["day"] == DAY)
        self.assertEqual((today["pilot"]["delivered"], today["staging"]["delivered"]), (5, 1))

    def test_the_inbox_card_names_the_new_statuses_and_the_staging_would_be_outcome(self):
        self.deliver(vn(2026, 10, 19, 15))
        self.deliver(vn(2026, 10, 19, 15, 1))
        self.deliver(vn(2026, 10, 19, 15, 2), staging=1)
        self.batch(front(), front(shift=3), front())
        overview = zalo_overview(self.conn, datetime(2026, 10, 19, 20, tzinfo=timezone.utc))
        by_status = {r["status"]: r for r in overview["recent"]}
        self.assertEqual(set(by_status), {"filed", "waiting", "staging_checked"})
        self.assertEqual(by_status["staging_checked"]["outcome_label"], "sẽ thành biểu mẫu mới")
        self.assertEqual(by_status["waiting"]["reason"], "ca trên giấy không hợp lệ")
        self.assertEqual(overview["staging_test"]["saved"], 1)


if __name__ == "__main__":
    unittest.main()
