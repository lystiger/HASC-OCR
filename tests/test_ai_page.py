"""The AI page (P149): reads, time, accuracy from corrections, daily use, VND cost, budget alert.

Made-up sheets, reads and costs only.
"""
from datetime import date, datetime, timedelta, timezone
import gc
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from hasc.excel.page import COLUMNS, FIRST_DATA_ROW

from hasc.db import create_row, create_sheet, create_user, get_connection, upsert_cell
from hasc.reader.reader import CloudReader, ReaderError, TransportError
from hasc.web.ai_stats import VN, accuracy, ai_overview, budget_status, heatmap, month_window, vnd
from hasc.web.app import create_app
from hasc.web.security import hash_password
from hasc.worker.config import CloudPhotoReader

NAMES = {"customers": {}, "products": {}}


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


class TestAiStats(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        self.conn = get_connection(self.db_path)
        self.ids = {n: create_user(self.conn, n, hash_password("pass"), role=r, active=1)
                    for n, r in (("emp_user", "employee"), ("boss_user", "boss"), ("admin_user", "admin"))}
        self.today = date(2026, 11, 10)

    def tearDown(self):
        self.conn.close()
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _photo_job(self, sheet_id, status, cost, started, seconds=30):
        cur = self.conn.execute(
            "INSERT INTO photos (sheet_id, path, sha256, uploaded_at) VALUES (?, 'x.jpg', ?, ?)",
            (sheet_id, f"h{started.timestamp()}{status}", iso(started)))
        finished = started + timedelta(seconds=seconds)
        self.conn.execute("""
            INSERT INTO read_jobs (photo_id, status, queued_at, started_at, finished_at, cost_usd, prompt_tokens, completion_tokens)
            VALUES (?, ?, ?, ?, ?, ?, 4000, 3000)
        """, (cur.lastrowid, status, iso(started - timedelta(seconds=10)), iso(started), iso(finished), cost))
        self.conn.commit()

    def _reviewed_sheet(self):
        sheet = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                             shift=1, page_no=1, status="reviewed", version=2)
        row = create_row(self.conn, sheet, "1", 0)
        # 6 AI values kept: 2 flagged but right (false alarms), 4 plain.
        for i, flag in enumerate(("doubt", "rule", "none", "none", "none", "none")):
            upsert_cell(self.conn, row, f"thickness_{i + 1}", "30", flag=flag)
        upsert_cell(self.conn, row, "customer", "Khach A", inherited=1)  # copied down: not counted
        # 4 AI values a person corrected: 3 had been flagged, 1 was missed.
        for field, detail in (("gross_kg", "ai|doubt"), ("core_kg", "ai|rule"), ("cover_kg", "ai|unreadable"), ("net_kg", "ai|none")):
            upsert_cell(self.conn, row, field, "9", source="person")
            self.conn.execute("""INSERT INTO events (at, user_id, action, sheet_id, row_id, field, old_value, new_value, detail)
                                 VALUES (?, ?, 'value_change', ?, ?, ?, '1', '9', ?)""",
                              ("2026-11-07T01:00:00+00:00", self.ids["emp_user"], sheet, row, field, detail))
        # A second correction of the same value is not a second AI error.
        self.conn.execute("""INSERT INTO events (at, user_id, action, sheet_id, row_id, field, old_value, new_value, detail)
                             VALUES ('2026-11-07T02:00:00+00:00', ?, 'value_change', ?, ?, 'gross_kg', '9', '8', 'person|none')""",
                          (self.ids["emp_user"], sheet, row))
        self.conn.commit()
        return sheet

    def test_accuracy_precision_false_alarms_and_misses(self):
        self._reviewed_sheet()
        q = accuracy(self.conn, "2026-11-01", "2026-12-01")
        self.assertEqual((q["sheets"], q["values"], q["corrected"], q["flagged"]), (1, 10, 4, 5))
        self.assertAlmostEqual(q["accuracy"], 0.6)
        self.assertAlmostEqual(q["precision"], 3 / 5)
        self.assertEqual((q["false_alarms"], q["missed"]), (2, 1))
        self.assertAlmostEqual(q["missed_share"], 0.1)
        self.assertIsNone(accuracy(self.conn, "2026-10-01", "2026-11-01")["accuracy"])  # nothing reviewed then

    def test_editing_a_copied_value_stays_out_of_ai_accuracy(self):
        sheet = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                             shift=1, page_no=1, status="in_review", version=1)
        for position in range(2):
            row = create_row(self.conn, sheet, str(position + 1), position)
            upsert_cell(self.conn, row, "customer", "Khach A", inherited=position)
        client = self._client("emp_user")
        book = load_workbook(io.BytesIO(client.get(f"/api/sheets/{sheet}/excel").content))
        column = next(i for i, (_, _, field) in enumerate(COLUMNS, 1) if field == "customer")
        book["Báo cáo"].cell(row=FIRST_DATA_ROW + 1, column=column).value = "Khach B"
        data = io.BytesIO()
        book.save(data)
        response = client.post(f"/api/sheets/{sheet}/excel", files={"file": ("fixed.xlsx", data.getvalue())})
        self.assertEqual(response.status_code, 200, response.text)
        quality = accuracy(self.conn, "2026-11-01", "2026-12-01")
        self.assertEqual((quality["values"], quality["corrected"], quality["missed"]), (1, 0, 0))
        self.assertEqual(quality["accuracy"], 1.0)

    def test_corrections_on_rows_later_marked_invalid_do_not_count(self):
        sheet = self._reviewed_sheet()
        self.conn.execute("UPDATE rows SET invalid = 1 WHERE sheet_id = ?", (sheet,))
        self.conn.commit()
        q = accuracy(self.conn, "2026-11-01", "2026-12-01")
        self.assertEqual((q["values"], q["corrected"]), (0, 0))

    def test_read_time_starts_at_the_first_attempt(self):
        from hasc.worker.jobs import claim_job
        sheet = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                             shift=1, page_no=1, status="reading", version=1)
        photo = self.conn.execute("INSERT INTO photos (sheet_id, path, sha256, uploaded_at) VALUES (?, 'x.jpg', 'h', 'x')", (sheet,)).lastrowid
        self.conn.execute("INSERT INTO read_jobs (photo_id, status, queued_at) VALUES (?, 'queued', '2026-11-10T01:00:00+00:00')", (photo,))
        self.conn.commit()
        first = claim_job(self.conn)["started_at"]
        self.conn.execute("UPDATE read_jobs SET status = 'queued'")
        self.conn.commit()
        self.assertEqual(claim_job(self.conn)["started_at"], first)

    def test_sheets_not_yet_reviewed_do_not_count(self):
        sheet = self._reviewed_sheet()
        self.conn.execute("UPDATE sheets SET status = 'needs_review' WHERE id = ?", (sheet,))
        self.assertEqual(accuracy(self.conn, "2026-11-01", "2026-12-01")["values"], 0)

    def test_month_cost_uses_vietnam_days_and_the_rate(self):
        sheet = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                             shift=1, page_no=1, status="needs_review", version=1)
        # 31 Oct 23:30 in Vietnam is still October, though 1 Nov 00:30 is November.
        self._photo_job(sheet, "done", 0.02, datetime(2026, 10, 31, 23, 0, tzinfo=VN))
        self._photo_job(sheet, "done", 0.016, datetime(2026, 11, 1, 0, 30, tzinfo=VN))
        self._photo_job(sheet, "failed", 0.004, datetime(2026, 11, 9, 8, 0, tzinfo=VN))
        status = budget_status(self.conn, self.today)
        self.assertEqual(status["spent_vnd"], round(0.02 * 26000))  # 520 ₫ at the default rate
        overview = ai_overview(self.conn, self.today)
        self.assertEqual((overview["reads"], overview["failed"]), (1, 1))
        self.assertEqual(overview["cost_per_read_vnd"], 520)
        self.assertAlmostEqual(overview["read_s"], 30, places=3)
        self.assertAlmostEqual(overview["total_s"], 40, places=3)
        self.assertEqual(month_window(self.today)[0], "2026-10-31T17:00:00+00:00")

    def test_budget_levels_warn_at_80_and_100_percent(self):
        sheet = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                             shift=1, page_no=1, status="needs_review", version=1)
        self._photo_job(sheet, "done", 1.0, datetime(2026, 11, 5, 9, 0, tzinfo=VN))  # 26.000 ₫
        for budget, level in ((0, None), (40000, None), (32000, "near"), (26000, "over")):
            with self.subTest(budget=budget):
                self.conn.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('ai_budget_vnd', ?, 'x')", (str(budget),))
                self.assertEqual(budget_status(self.conn, self.today)["level"], level)

    def test_heatmap_is_26_monday_first_weeks_ending_this_week(self):
        weeks = heatmap({"2026-11-09": {"reads": 7, "failed": 1, "cost_usd": 0.1}}, self.today, 26000)
        self.assertEqual(len(weeks), 26)
        self.assertTrue(all(len(w) == 7 for w in weeks))
        self.assertEqual(weeks[0][0]["day"].weekday(), 0)
        monday = weeks[-1][0]
        self.assertEqual((monday["day"], monday["reads"], monday["level"], monday["cost_vnd"]), (date(2026, 11, 9), 8, 3, 2600))
        self.assertTrue(weeks[-1][6]["future"])

    def test_vnd_format(self):
        self.assertEqual(vnd(12345678), "12.345.678 ₫")
        self.assertEqual(vnd(0), "0 ₫")
        self.assertEqual(vnd(None), "—")

    def _client(self, name):
        client = TestClient(self.app)
        self.assertEqual(client.post("/api/login", json={"username": name, "password": "pass"}).status_code, 200)
        return client

    def test_page_is_for_bosses_and_admins_and_refreshes_itself(self):
        for name in ("boss_user", "admin_user"):
            with self.subTest(name=name):
                client = self._client(name)
                page = client.get("/ai")
                self.assertEqual(page.status_code, 200)
                self.assertIn('hx-get="/ai/partial" hx-trigger="every 10s"', page.text)
                self.assertIn("Lượt đọc mỗi ngày", page.text)
                self.assertIn('href="/ai"', client.get("/office").text)
                self.assertEqual(client.get("/ai/partial").status_code, 200)
                self.assertEqual(client.get("/api/ai/stats").status_code, 200)
        emp = self._client("emp_user")
        self.assertEqual(emp.get("/ai").status_code, 403)
        self.assertNotIn('href="/ai"', emp.get("/office").text)

    def test_accuracy_denominator_renders_as_number(self):
        self._reviewed_sheet()
        client = self._client("boss_user")
        for path in ("/ai", "/ai/partial"):
            page = client.get(path)
            self.assertIn("4 / 10 giá trị bị sửa", page.text)
            self.assertNotIn("built-in method", page.text)

    def test_only_admins_set_the_budget_and_changes_are_logged(self):
        boss = self._client("boss_user")
        self.assertEqual(boss.post("/ai/settings", data={"budget_vnd": 1, "rate": 26000}).status_code, 403)
        admin = self._client("admin_user")
        res = admin.post("/ai/settings", data={"budget_vnd": 500000, "rate": 25500}, follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertEqual(admin.post("/ai/settings", data={"budget_vnd": -1, "rate": 25500}).status_code, 400)
        rows = self.conn.execute("SELECT field, new_value FROM events WHERE action = 'setting_change' ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("ai_budget_vnd", "500000"), ("usd_vnd_rate", "25500")])

    def test_banner_shows_on_every_office_page_for_bosses_and_admins_only(self):
        sheet = create_sheet(self.conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                             shift=1, page_no=1, status="needs_review", version=1)
        self._photo_job(sheet, "done", 1.0, datetime.now(VN) - timedelta(minutes=5))
        self.conn.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('ai_budget_vnd', '20000', 'x')")
        self.conn.commit()
        self.assertIn("đã vượt ngân sách", self._client("boss_user").get("/office").text)
        self.assertIn("đã vượt ngân sách", self._client("admin_user").get("/account").text)
        self.assertNotIn("ngân sách", self._client("emp_user").get("/office").text)


class TestReaderCountsWhatItSpends(unittest.TestCase):
    def test_retries_and_fallback_all_count(self):
        good = {"choices": [{"message": {"content": "layout=form_a\nroll_code=1 gross_kg=20,5"}}],
                "usage": {"cost": 0.01, "prompt_tokens": 4000, "completion_tokens": 2000}}
        bad = {"choices": [{"message": {"content": "nonsense"}}], "usage": {"cost": 0.002, "prompt_tokens": 100, "completion_tokens": 10}}
        replies = iter([bad, bad, bad, good])
        reader = CloudReader(lambda body: next(replies), NAMES, "main", fallback="backup", sleep=lambda s: None)
        usage = reader.read(b"jpg")["usage"]
        self.assertAlmostEqual(usage["cost"], 0.016)
        self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"], usage["tries"]), (4300, 2030, 4))

    def test_worker_reader_counts_successful_and_failed_rule_rereads(self):
        first = {"choices": [{"message": {"content": "layout=form_a\nroll_code=1 gross_kg=20 core_kg=1 net_kg=10"}}],
                 "usage": {"cost": 0.01, "prompt_tokens": 100, "completion_tokens": 50}}
        second = {"choices": [{"message": {"content": "layout=form_a\nroll_code=1 gross_kg=20 core_kg=1 net_kg=19"}}],
                  "usage": {"cost": 0.02, "prompt_tokens": 200, "completion_tokens": 60}}
        bad = {"choices": [{"message": {"content": "nonsense"}}],
               "usage": {"cost": 0.002, "prompt_tokens": 10, "completion_tokens": 5}}
        for replies, cost, tokens, output, tries in (([first, second], 0.03, 300, 110, 2),
                                                   ([first, bad, bad, bad], 0.016, 130, 65, 4)):
            with self.subTest(cost=cost), tempfile.TemporaryDirectory() as folder:
                photo = Path(folder) / "photo.jpg"
                photo.write_bytes(b"synthetic")
                sequence = iter(replies)
                reader = CloudReader(lambda body: next(sequence), NAMES, "main", sleep=lambda s: None)
                usage = CloudPhotoReader(reader).read(photo)["usage"]
                self.assertAlmostEqual(usage["cost"], cost)
                self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"], usage["tries"]),
                                 (tokens, output, tries))

    def test_a_failed_read_still_reports_its_cost(self):
        def transport(body):
            raise TransportError("down")
        bad = {"choices": [{"message": {"content": "nonsense"}}], "usage": {"cost": 0.002}}
        replies = iter([bad, TransportError("down"), TransportError("down")])

        def flaky(body):
            item = next(replies)
            if isinstance(item, Exception):
                raise item
            return item
        with self.assertRaises(ReaderError) as caught:
            CloudReader(flaky, NAMES, "main", sleep=lambda s: None).read(b"jpg")
        self.assertAlmostEqual(caught.exception.usage["cost"], 0.002)


if __name__ == "__main__":
    unittest.main()
