"""Re-uploading a corrected page file (spec criteria 33-36). Made-up sheets and values only."""
import gc
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from hasc.db import create_row, create_sheet, create_user, get_connection, get_sheet_by_id, upsert_cell
from hasc.excel.page import COLUMNS, FIRST_DATA_ROW
from hasc.web.app import create_app
from hasc.web.security import hash_password

COLUMN = {field: index for index, (_, _, field) in enumerate(COLUMNS, start=1)}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestCorrectionUpload(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        conn = get_connection(self.db_path)
        create_user(conn, "emp_user", hash_password("pass"), role="employee", active=1)
        create_user(conn, "boss_user", hash_password("pass"), role="boss", active=1)
        self.sheet_id = create_sheet(conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                                     shift=1, page_no=1, status="in_review", version=1)
        # Roll 1 writes the product details; roll 2 copies them down (stored inherited, as the worker does).
        self.rows = []
        for position, (roll, gross, flag) in enumerate((("1", "25.4", "doubt"), ("2", "26.1", "none"), ("3", "24.9", "rule"))):
            row_id = create_row(conn, self.sheet_id, roll, position)
            self.rows.append(row_id)
            upsert_cell(conn, row_id, "roll_code", roll)
            upsert_cell(conn, row_id, "gross_kg", gross, flag=flag)
            upsert_cell(conn, row_id, "customer", "Khach A", inherited=0 if position == 0 else 1)
            upsert_cell(conn, row_id, "thickness_mic", "30", inherited=0 if position == 0 else 1)
        conn.close()
        self.client = TestClient(self.app)
        self.assertEqual(self.client.post("/api/login", json={"username": "emp_user", "password": "pass"}).status_code, 200)

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _download(self):
        res = self.client.get(f"/api/sheets/{self.sheet_id}/excel")
        self.assertEqual(res.status_code, 200)
        return load_workbook(io.BytesIO(res.content))

    def _upload(self, wb, path=None):
        buf = io.BytesIO()
        wb.save(buf)
        return self.client.post(path or f"/api/sheets/{self.sheet_id}/excel",
                                files={"file": ("sua.xlsx", buf.getvalue(), XLSX)}, follow_redirects=False)

    def _cell(self, wb, position, field):
        return wb["Báo cáo"].cell(row=FIRST_DATA_ROW + position, column=COLUMN[field])

    def _db(self, sql, *args):
        conn = get_connection(self.db_path)
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def _value(self, position, field):
        rows = self._db("SELECT value, source, flag, inherited FROM cells WHERE row_id = ? AND field = ?", self.rows[position], field)
        return rows[0] if rows else None

    def test_criterion_33_one_change_changes_exactly_that_value_and_records_it(self):
        wb = self._download()
        self._cell(wb, 0, "gross_kg").value = 25.6
        res = self._upload(wb)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), {"version": 2, "status": "reviewed", "changed": 1})
        self.assertEqual(self._value(0, "gross_kg"), {"value": "25.6", "source": "person", "flag": "none", "inherited": 0})
        self.assertEqual(self._value(1, "gross_kg")["value"], "26.1")
        self.assertEqual(self._value(2, "gross_kg")["source"], "ai")
        events = self._db("SELECT user_id, row_id, field, old_value, new_value, detail, at FROM events WHERE action = 'value_change'")
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual((e["row_id"], e["field"], e["old_value"], e["new_value"], e["detail"]),
                         (self.rows[0], "gross_kg", "25.4", "25.6", "ai|doubt"))
        self.assertTrue(e["at"])
        self.assertEqual(self._db("SELECT reviewed FROM rows WHERE sheet_id = ?", self.sheet_id), [{"reviewed": 1}] * 3)

    def test_unchanged_file_changes_nothing_but_marks_reviewed(self):
        res = self._upload(self._download())
        self.assertEqual(res.json(), {"version": 1, "status": "reviewed", "changed": 0})
        self.assertEqual(self._db("SELECT COUNT(*) AS n FROM events WHERE action = 'value_change'"), [{"n": 0}])

    def test_criterion_34_first_row_fix_updates_copied_down_values(self):
        wb = self._download()
        self._cell(wb, 0, "customer").value = "Khach B"
        self._cell(wb, 2, "thickness_mic").value = 35  # a person overrides one copied-down value
        res = self._upload(wb)
        self.assertEqual(res.json()["changed"], 2)
        self.assertEqual([self._value(i, "customer")["value"] for i in range(3)], ["Khach B"] * 3)
        self.assertEqual(self._value(1, "customer")["inherited"], 1)
        self.assertEqual(self._value(2, "thickness_mic"), {"value": "35", "source": "person", "flag": "none", "inherited": 0})
        self.assertEqual(self._value(1, "thickness_mic")["value"], "30")
        fields = [e["field"] for e in self._db("SELECT field FROM events WHERE action = 'value_change' ORDER BY id")]
        self.assertEqual(fields, ["customer", "thickness_mic"])  # copied-down updates are not separate corrections

    def test_criterion_35_older_version_is_refused_and_changes_nothing(self):
        old = self._download()
        newer = self._download()
        self._cell(newer, 0, "gross_kg").value = 25.5
        self.assertEqual(self._upload(newer).status_code, 200)
        self._cell(old, 1, "gross_kg").value = 99
        res = self._upload(old)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "stale_version")
        self.assertEqual(self._value(1, "gross_kg")["value"], "26.1")

    def test_criterion_35_new_rows_from_a_photo_make_the_file_stale(self):
        wb = self._download()
        conn = get_connection(self.db_path)
        create_row(conn, self.sheet_id, "4", 3)
        conn.close()
        self.assertEqual(self._upload(wb).status_code, 409)

    def test_criterion_36_structure_and_formulas_are_refused(self):
        cases = {
            "heading": lambda wb: setattr(wb["Báo cáo"].cell(row=6, column=COLUMN["gross_kg"]), "value", "Tong"),
            "meta": lambda wb: wb.remove(wb["_meta"]),
            "added row": lambda wb: setattr(self._cell(wb, 3, "gross_kg"), "value", 20),
            "formula": lambda wb: setattr(self._cell(wb, 0, "net_kg"), "value", "=J7-K7"),
            "text in number": lambda wb: setattr(self._cell(wb, 0, "gross_kg"), "value", "hai muoi"),
        }
        for name, damage in cases.items():
            with self.subTest(name=name):
                wb = self._download()
                damage(wb)
                res = self._upload(wb)
                self.assertEqual(res.status_code, 422, res.text)
                self.assertEqual(res.json()["error"]["code"], "bad_layout")
        self.assertEqual(self._db("SELECT COUNT(*) AS n FROM events WHERE action = 'value_change'"), [{"n": 0}])
        self.assertEqual(get_sheet_by_id(get_connection(self.db_path), self.sheet_id)["version"], 1)

    def test_not_an_excel_file_and_another_sheets_file(self):
        res = self.client.post(f"/api/sheets/{self.sheet_id}/excel", files={"file": ("x.xlsx", b"not a zip", XLSX)})
        self.assertEqual(res.status_code, 422)
        conn = get_connection(self.db_path)
        other = create_sheet(conn, form="BMMT01-2022/HASC", machine="T3", production_date="2026-11-06",
                             shift=1, page_no=1, status="in_review", version=1)
        conn.close()
        res = self._upload(self._download(), path=f"/api/sheets/{other}/excel")
        self.assertEqual(res.status_code, 422)

    def test_office_page_form_uploads_and_shows_the_result(self):
        page = self.client.get(f"/office/sheets/{self.sheet_id}").text
        self.assertIn(f'action="/office/sheets/{self.sheet_id}/excel"', page)
        wb = self._download()
        self._cell(wb, 2, "gross_kg").value = 25
        res = self._upload(wb, path=f"/office/sheets/{self.sheet_id}/excel")
        self.assertEqual(res.status_code, 303)
        self.assertIn("corrected=1", res.headers["location"])
        self.assertIn("1 giá trị thay đổi", self.client.get(res.headers["location"]).text)
        stale = self._upload(wb, path=f"/office/sheets/{self.sheet_id}/excel")
        self.assertIn("upload_error=", stale.headers["location"])
        self.assertIn("Vui lòng tải lại tệp mới nhất", self.client.get(stale.headers["location"]).text)

    def test_operators_cannot_upload_corrections(self):
        conn = get_connection(self.db_path)
        create_user(conn, "op_user", hash_password("pass"), role="operator", active=1)
        conn.close()
        op = TestClient(self.app)
        op.post("/api/login", json={"username": "op_user", "password": "pass"})
        res = op.post(f"/api/sheets/{self.sheet_id}/excel", files={"file": ("x.xlsx", b"x", XLSX)})
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
