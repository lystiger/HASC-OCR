"""Daily summary to the office group (P151): text, 9:00 timing, and at most one message per day."""
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest import mock

from hasc.db import mark_not_running
from hasc.zalo.__main__ import main
from hasc.zalo.receiver import ZaloError, ZaloTimeout
from hasc.zalo.summary import due_date, send_due_summary, summary_text
from tests.test_daily_report import DAY, DailyReport

VN = timezone(timedelta(hours=7))
TODAY = DAY + timedelta(days=1)  # the morning after production date DAY
CHAT = "zgr-office-00001"
TOKEN = "123456:secret-token"


def at(hour, minute=0, day=TODAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=VN)


class Summary(unittest.TestCase):
    setUp = DailyReport.setUp
    sheet = DailyReport.sheet
    roll = DailyReport.roll
    seed = DailyReport.seed

    def calls(self, *results):
        """A fake Bot API call that answers sendMessage with each result in turn (an exception is raised)."""
        sent = []
        results = list(results)

        def call(token, method, payload=None):
            sent.append((method, payload))
            result = results.pop(0) if results else {"message_id": "m"}
            if isinstance(result, Exception):
                raise result
            return result
        return call, sent

    def row(self):
        return dict(self.conn.execute("SELECT * FROM zalo_summary").fetchone())

    def test_text_matches_the_daily_report(self):
        self.seed()
        self.assertEqual(summary_text(self.conn, DAY), "\n".join([
            "[HASC] Số liệu ngày 01/12/2026 (Ca 1 + Ca 2)",
            "Ca 1:",
            "- T1: 375,8 kg, 6 cuộn",
            "- T3: túi 50 kg, 1 cuộn, chưa xác nhận",
            "Ca 2:",
            "- T6: 125 kg, 2 cuộn",
            "Tổng: 500,8 kg màng, 9 cuộn; túi 50 kg",
            "Chưa nhận tờ: T6 Ca 1, T1 Ca 2, T3 Ca 2",
            "Chưa xác nhận: 1 tờ",
        ]))

    def test_shifts_marked_not_running_are_not_missing(self):
        self.seed()
        for machine, shift in (("T1", 2), ("T3", 2), ("T6", 1)):
            mark_not_running(self.conn, machine, DAY.isoformat(), shift, "nghỉ")
        self.assertNotIn("Chưa nhận tờ", summary_text(self.conn, DAY))

    def test_unread_weight_is_flagged(self):
        t1, photo = self.sheet("T1", 1, self.an)
        self.roll(t1, photo, "8?6", "Alpha", "GT", 40, 500, 1500)
        self.assertIn("- T1: 0 kg, 1 cuộn, có số chưa đọc được", summary_text(self.conn, DAY))

    def test_empty_day_says_so(self):
        self.assertEqual(summary_text(self.conn, DAY), "[HASC] Không có số liệu ngày 01/12/2026.")

    def test_due_from_nine_for_yesterday(self):
        self.assertIsNone(due_date(at(8, 59)))
        self.assertEqual(due_date(at(9, 0)), DAY)
        self.assertEqual(due_date(at(23, 59)), DAY)
        self.assertEqual(due_date(at(1, 30)), None)
        # 9:00 Vietnam is 2:00 UTC: the clock's time zone does not move the day
        self.assertEqual(due_date(at(9, 0).astimezone(timezone.utc)), DAY)

    def test_sends_once_at_nine(self):
        self.seed()
        call, sent = self.calls()
        self.assertIsNone(send_due_summary(self.conn, TOKEN, CHAT, at(8, 59), call))
        self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9, 0), call), "sent")
        self.assertIsNone(send_due_summary(self.conn, TOKEN, CHAT, at(9, 1), call))
        self.assertIsNone(send_due_summary(self.conn, TOKEN, CHAT, at(15, 0), call))
        self.assertEqual(len(sent), 1)
        method, payload = sent[0]
        self.assertEqual((method, payload["chat_id"]), ("sendMessage", CHAT))
        self.assertEqual(payload["text"], summary_text(self.conn, DAY))
        self.assertEqual((self.row()["status"], self.row()["attempts"]), ("sent", 1))

    def test_next_morning_sends_the_next_day(self):
        call, sent = self.calls()
        send_due_summary(self.conn, TOKEN, CHAT, at(9), call)
        send_due_summary(self.conn, TOKEN, CHAT, at(9, day=TODAY + timedelta(days=1)), call)
        self.assertEqual([p["text"][-11:-1] for _, p in sent], ["01/12/2026", "02/12/2026"])

    def test_failure_retries_after_ten_minutes(self):
        call, sent = self.calls(ZaloError("sendMessage: HTTP 500"))
        self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9), call), "waiting")
        self.assertEqual(self.row()["last_error"], "sendMessage: HTTP 500")
        self.assertIsNone(send_due_summary(self.conn, TOKEN, CHAT, at(9, 9), call))
        self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9, 10), call), "sent")
        self.assertEqual((len(sent), self.row()["attempts"], self.row()["last_error"]), (2, 2, None))

    def test_gives_up_after_three_attempts(self):
        call, sent = self.calls(*[ZaloError("sendMessage: không kết nối được tới Zalo")] * 3)
        statuses = [send_due_summary(self.conn, TOKEN, CHAT, at(9, 10 * n), call) for n in range(5)]
        self.assertEqual(statuses, ["waiting", "waiting", "failed", None, None])
        self.assertEqual(len(sent), 3)

    def test_timeout_is_never_retried(self):
        call, sent = self.calls(ZaloTimeout("sendMessage: hết thời gian chờ kết nối"))
        self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9), call), "failed")
        self.assertIsNone(send_due_summary(self.conn, TOKEN, CHAT, at(10), call))
        self.assertEqual(len(sent), 1)
        self.assertIn("xem nhóm Zalo", self.row()["last_error"])

    def test_a_send_cut_off_midway_is_not_repeated(self):
        call, sent = self.calls()
        self.conn.execute("""INSERT INTO zalo_summary (production_date, chat_id, status, attempts, last_try_at)
                             VALUES (?, ?, 'sending', 1, ?)""", (DAY.isoformat(), CHAT, at(9).isoformat()))
        self.conn.commit()
        self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9, 30), call), "failed")
        self.assertEqual(sent, [])

    def test_text_error_sends_nothing_and_retries(self):
        call, sent = self.calls()
        with mock.patch("hasc.zalo.summary.summary_text", side_effect=RuntimeError("db")):
            self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9), call), "waiting")
        self.assertEqual((sent, self.row()["last_error"]), ([], "lỗi nội bộ: RuntimeError"))
        self.assertEqual(send_due_summary(self.conn, TOKEN, CHAT, at(9, 10), call), "sent")

    def test_receiver_sends_the_summary_after_its_poll(self):
        methods = []

        def fake_call(token, method, payload=None):
            methods.append(method)
            return {}

        db = str(self.root / "hasc.db")
        with mock.patch.dict("os.environ", {"HASC_DB_PATH": db}), mock.patch("builtins.print") as printed:
            code = main(["--once", "--photos-dir", str(self.root / "photos")], settings=(TOKEN, {}, CHAT),
                        call=fake_call, clock=lambda: at(9, 1))
        self.assertEqual(code, 0)
        self.assertEqual(methods, ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])
        log = " ".join(str(c.args) for c in printed.call_args_list)
        self.assertNotIn(CHAT, log)  # the group id is masked in the log
        self.assertNotIn(TOKEN, log)

    def test_receiver_without_a_summary_group_never_sends(self):
        methods = []

        def fake_call(token, method, payload=None):
            methods.append(method)
            return {}

        with mock.patch.dict("os.environ", {"HASC_DB_PATH": str(self.root / "hasc.db")}):
            main(["--once", "--photos-dir", str(self.root / "photos")], settings=(TOKEN, {}, None),
                 call=fake_call, clock=lambda: at(9, 1))
        self.assertNotIn("sendMessage", methods)


if __name__ == "__main__":
    unittest.main()
