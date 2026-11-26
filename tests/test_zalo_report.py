"""Zalo sheets in the daily report, the summary, the page Excel and the office screens (spec R7, criteria 10-11)."""
from datetime import date
import gc
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from hasc.db import create_event, create_row, create_sheet, create_user, get_connection, get_sheet_data, upsert_cell
from hasc.excel import build_page_workbook, page_filename
from hasc.excel.daily_report import UNKNOWN_MACHINE
from hasc.excel.page import COLUMNS, FIRST_DATA_ROW
from hasc.web.app import create_app
from hasc.web.security import hash_password
from hasc.zalo.summary import summary_text
from tests import test_daily_report as daily
from tests.test_zalo_hygiene import forbid_sends

ZALO = ""


class ZaloReport(unittest.TestCase):
    def setUp(self):
        daily.DailyReport.setUp(self)
        forbid_sends(self)

    sheet = daily.DailyReport.sheet
    roll = daily.DailyReport.roll
    build = daily.DailyReport.build
    block = daily.DailyReport.block

    def zalo_sheet(self, shift=1, page_no=1, layout="form_a", status="reviewed"):
        return self.sheet(ZALO, shift, None, status=status, layout=layout, page_no=page_no)

    def lines(self, ws, shift=1):
        return {(row[0], row[1], row[3]): row for row in self.block(ws, shift)[2:]}

    def seed_zalo(self):
        sheet, photo = self.zalo_sheet()
        self.roll(sheet, photo, 100, "Alpha", "GT", 40, 500, 1500, machine="T1")
        self.roll(sheet, photo, 50, "Alpha", "GT", 40, 500, 1500, machine="T1")
        self.roll(sheet, photo, 70, "Beta", "PE", 30, 600, 1200, machine="T6")
        self.roll(sheet, photo, 5, "Gamma", "PE", 30, 600, 1200)
        return sheet, photo

    def test_zalo_rows_go_under_their_machine_and_unknown_machine_with_totals_of_every_row(self):
        self.seed_zalo()
        ws = self.build()
        found = {(row[0], row[1]): row for row in self.block(ws, 1)}
        self.assertEqual(found[("T1", "Alpha")][4:7:2], [150, 2])
        self.assertEqual(found[("T6", "Beta")][4:7:2], [70, 1])
        self.assertEqual(found[(UNKNOWN_MACHINE, "Gamma")][4:7:2], [5, 1])
        totals = {ws.cell(r, 1).value: ws.cell(r, 5).value for r in range(1, ws.max_row + 1)}
        self.assertEqual(totals["TỔNG KHỐI CUỘN MÀNG (KG)"], 225)

    def test_a_shift_without_unknown_rows_has_no_unknown_line(self):
        self.seed_zalo()
        ws = self.build()
        self.assertNotIn(UNKNOWN_MACHINE, [row[0] for row in self.block(ws, 2)])

    def test_a_website_only_day_has_no_unknown_line(self):
        t1, photo = self.sheet("T1", 1, self.an)
        self.roll(t1, photo, 10, "Alpha", "GT", 40, 500, 1500)
        self.assertNotIn(UNKNOWN_MACHINE, [row[0] for row in self.block(self.build(), 1)])

    def test_two_zalo_sheets_of_one_shift_do_not_share_carry_down(self):
        first, photo = self.zalo_sheet(page_no=1)
        self.roll(first, photo, 10, "Alpha", "GT", 40, 500, 1500, machine="T1")
        second, photo2 = self.zalo_sheet(page_no=3)
        self.roll(second, photo2, 20, None, None, None, None, machine="T1")
        ws = self.build()
        rows = [row for row in self.block(ws, 1)[2:] if row[0] == "T1"]
        self.assertEqual(sorted((row[1] or "", row[4]) for row in rows), [("", 20), ("Alpha", 10)])

    def test_zalo_rows_of_one_sheet_carry_product_details_down(self):
        sheet, photo = self.zalo_sheet()
        self.roll(sheet, photo, 10, "Alpha", "GT", 40, 500, 1500, machine="T1")
        self.roll(sheet, photo, 20, None, None, None, None, machine="T1")
        rows = [row for row in self.block(self.build(), 1)[2:] if row[0] == "T1"]
        self.assertEqual([(row[1], row[4], row[6]) for row in rows], [("Alpha", 30, 2)])

    def test_a_machine_not_on_the_list_is_unknown_and_named_in_the_note(self):
        sheet, photo = self.zalo_sheet()
        self.roll(sheet, photo, 10, "Alpha", "GT", 40, 500, 1500, machine="T4")
        row = next(r for r in self.block(self.build(), 1)[2:] if r[0] == UNKNOWN_MACHINE)
        self.assertIn("Máy ghi không rõ: T4", row[8])

    def test_summary_lists_zalo_machines_and_unknown_with_the_same_totals(self):
        self.seed_zalo()
        text = summary_text(self.conn, daily.DAY)
        self.assertIn("- T1: 150 kg, 2 cuộn", text)
        self.assertIn("- T6: 70 kg, 1 cuộn", text)
        self.assertIn(f"- {UNKNOWN_MACHINE}: 5 kg, 1 cuộn", text)
        self.assertIn("Tổng: 225 kg màng, 4 cuộn", text)

    def test_a_machine_counts_as_received_when_a_zalo_row_of_that_shift_has_it(self):
        self.seed_zalo()
        missing = next(l for l in summary_text(self.conn, daily.DAY).splitlines() if l.startswith("Chưa nhận tờ"))
        self.assertNotIn("T1 Ca 1", missing)
        self.assertNotIn("T6 Ca 1", missing)
        self.assertIn("T1 Ca 2", missing)
        self.assertIn("T3 Ca 1", missing)

    def test_a_zalo_back_page_does_not_count_as_a_received_front(self):
        sheet, photo = self.zalo_sheet(layout="back", page_no=2)
        self.roll(sheet, photo, 30, "Alpha", "TX", 30, 400, machine="T3")
        missing = next(l for l in summary_text(self.conn, daily.DAY).splitlines() if l.startswith("Chưa nhận tờ"))
        self.assertIn("T3 Ca 1", missing)

    def inbox(self, status, staging=0, n=1):
        for i in range(n):
            self.conn.execute("""
                INSERT INTO zalo_inbox (chat_id, chat_type, message_id, staging, command, received_at, status)
                VALUES ('c', 'PRIVATE', ?, ?, 0, '2026-12-02T01:00:00+00:00', ?)
            """, (f"{status}{staging}{i}", staging, status))
        self.conn.commit()

    def test_summary_shows_waiting_and_unprocessed_counts_only_when_above_zero(self):
        self.seed_zalo()
        self.assertNotIn("ảnh Zalo", summary_text(self.conn, daily.DAY))
        self.inbox("waiting", n=3)
        self.inbox("delivered", n=2)
        self.inbox("waiting", staging=1, n=5)
        self.inbox("filed", n=4)
        lines = summary_text(self.conn, daily.DAY).splitlines()
        self.assertEqual(lines[-2:], ["3 ảnh Zalo đang chờ xếp", "2 ảnh Zalo chưa xử lý"])

    def test_the_counts_show_on_a_day_with_no_data(self):
        self.assertEqual(summary_text(self.conn, daily.DAY), "[HASC] Không có số liệu ngày 01/12/2026.")
        self.inbox("delivered", n=1)
        self.assertEqual(summary_text(self.conn, daily.DAY).splitlines(),
                         ["[HASC] Không có số liệu ngày 01/12/2026.", "1 ảnh Zalo chưa xử lý"])


class ZaloPageExcel(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(gc.collect)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path, photos_dir=Path(self.temp_dir.name) / "photos")
        conn = get_connection(self.db_path)
        self.addCleanup(conn.close)
        for role in ("employee", "boss", "operator"):
            create_user(conn, role, hash_password("pass"), role=role, active=1)
        self.zalo = create_sheet(conn, form="BMMT01-2022/HASC", machine="", production_date="2026-11-06", shift=1,
                                 page_no=1, status="needs_review", version=1)
        self.web = create_sheet(conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06", shift=1,
                                page_no=1, status="needs_review", version=1)
        self.rows = []
        for position, (machine, flag, note) in enumerate(
                (("T1", "doubt", "theo kế hoạch: KHSX.pdf"), (None, "doubt", None), ("T6", "none", None))):
            row_id = create_row(conn, self.zalo, str(position + 1), position)
            self.rows.append(row_id)
            upsert_cell(conn, row_id, "roll_code", str(position + 1))
            upsert_cell(conn, row_id, "gross_kg", "25.4")
            upsert_cell(conn, row_id, "machine", machine, flag=flag, note=note)
        self.conn = conn
        forbid_sends(self)
        self.client = TestClient(self.app)
        self.assertEqual(self.client.post("/api/login", json={"username": "employee", "password": "pass"}).status_code, 200)

    def test_file_name_and_header_say_zalo_and_per_row(self):
        self.assertEqual(page_filename("", date(2026, 11, 6), 1, 1),
                         "HASC_Zalo_2026-11-06_Ca1_Trang1.xlsx")
        res = self.client.get(f"/office/sheets/{self.zalo}/excel")
        self.assertIn("HASC_Zalo_2026-11-06_Ca1_Trang1.xlsx", res.headers["content-disposition"])
        ws = load_workbook(io.BytesIO(res.content))["Báo cáo"]
        self.assertIn("Máy: theo dòng", ws["A2"].value)
        column = [field for _, _, field in COLUMNS].index("machine") + 1
        self.assertEqual([ws.cell(FIRST_DATA_ROW + i, column).value for i in range(3)], ["T1", None, "T6"])
        web = self.client.get(f"/office/sheets/{self.web}/excel")
        self.assertIn("HASC_T1_2026-11-06", web.headers["content-disposition"])
        self.assertIn("Máy: T1", load_workbook(io.BytesIO(web.content))["Báo cáo"]["A2"].value)

    def test_a_corrected_file_keeps_each_rows_machine_and_a_changed_one_is_a_person_edit(self):
        wb = load_workbook(io.BytesIO(self.client.get(f"/api/sheets/{self.zalo}/excel").content))
        column = [field for _, _, field in COLUMNS].index("machine") + 1
        buf = io.BytesIO()
        wb.save(buf)
        xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        res = self.client.post(f"/api/sheets/{self.zalo}/excel", files={"file": ("sua.xlsx", buf.getvalue(), xlsx)})
        self.assertEqual(res.json()["changed"], 0, res.text)
        self.assertEqual([r["cells"]["machine"]["value"] for r in get_sheet_data(self.conn, self.zalo)],
                         ["T1", None, "T6"])
        wb["Báo cáo"].cell(FIRST_DATA_ROW + 1, column).value = "T3"
        buf = io.BytesIO()
        wb.save(buf)
        res = self.client.post(f"/api/sheets/{self.zalo}/excel", files={"file": ("sua.xlsx", buf.getvalue(), xlsx)})
        self.assertEqual(res.json()["changed"], 1, res.text)
        cell = self.conn.execute("SELECT value, source FROM cells WHERE row_id = ? AND field = 'machine'",
                                 (self.rows[1],)).fetchone()
        self.assertEqual((cell["value"], cell["source"]), ("T3", "person"))

    def test_office_list_shows_zalo_per_row_and_the_machine_filter(self):
        html = self.client.get("/office?status=all").text
        self.assertIn("Zalo · theo dòng", html)
        self.assertIn('<option value="Zalo"', html)
        only_zalo = self.client.get("/office/sheets/partial?machine=Zalo&status=all").text
        self.assertIn("Zalo · theo dòng", only_zalo)
        self.assertNotIn(f"/office/sheets/{self.web}\"", only_zalo)
        only_t1 = self.client.get("/office/sheets/partial?machine=T1&status=all").text
        self.assertIn(f"/office/sheets/{self.web}\"", only_t1)
        self.assertNotIn("Zalo · theo dòng", only_t1)

    def test_gui_lai_shows_on_the_list_and_the_review_screen_also_after_review(self):
        self.assertNotIn("gửi lại", self.client.get("/office?status=all").text)
        create_event(self.conn, None, "zalo_resend", sheet_id=self.zalo, detail="Zalo ảnh 9, trang 1: gửi lại, tự xếp")
        self.conn.execute("UPDATE sheets SET status = 'reviewed' WHERE id = ?", (self.zalo,))
        self.conn.commit()
        self.assertIn("gửi lại", self.client.get("/office?status=all").text)
        detail = self.client.get(f"/office/sheets/{self.zalo}").text
        self.assertIn("gửi lại", detail)
        self.assertIn("Zalo · theo dòng", detail)
        self.assertNotIn("gửi lại", self.client.get(f"/office/sheets/{self.web}").text)

    def test_the_review_screen_shows_the_plan_note_on_the_machine_cell(self):
        detail = self.client.get(f"/office/sheets/{self.zalo}").text
        self.assertIn("theo kế hoạch: KHSX.pdf", detail)


if __name__ == "__main__":
    unittest.main()
