"""Review screen (P154): the page's table round-trips, and Lưu / Xác nhận follow the Excel path's rules.
Made-up sheets and values only. The payload is built from the rendered page, as review.js builds it."""
import gc
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from hasc.db import create_row, create_sheet, create_user, get_connection, get_sheet_by_id, upsert_cell
from hasc.excel.correction import normalise
from hasc.web.app import create_app
from hasc.web.corrections import apply_values
from hasc.web.errors import AppError
from hasc.web.review import FIELD_LABELS
from hasc.web.security import hash_password


class PageTable(HTMLParser):
    """Collects what review.js sends: each body row's key and its inputs' values by field."""

    def __init__(self):
        super().__init__()
        self.keys, self.rows, self.inputs = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-row-key" in attrs:
            self.keys.append(attrs["data-row-key"])
            self.rows.append({})
        if tag == "input" and "cell" in attrs.get("class", "").split():
            self.rows[-1][attrs["data-field"]] = attrs.get("value", "")
            self.inputs.append(attrs)


class TestReviewScreen(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        conn = get_connection(self.db_path)
        for name, role in (("emp_user", "employee"), ("boss_user", "boss"), ("op_user", "operator"),
                           ("admin_user", "admin")):
            create_user(conn, name, hash_password("pass"), role=role, active=1)
        self.sheet_id = create_sheet(conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-11-06",
                                     shift=1, page_no=1, status="needs_review", version=1)
        # Roll 1 writes the product details; rolls 2-3 copy them down, as the worker stores them.
        self.rows = []
        for position, (roll, net, flag) in enumerate((("1", "86.4", "doubt"), ("2", "86.6", "none"), ("3", "?", "unreadable"))):
            row_id = create_row(conn, self.sheet_id, roll, position)
            self.rows.append(row_id)
            upsert_cell(conn, row_id, "roll_code", roll)
            upsert_cell(conn, row_id, "net_kg", net, flag=flag)
            upsert_cell(conn, row_id, "customer", "Khach A", inherited=0 if position == 0 else 1)
            upsert_cell(conn, row_id, "thickness_mic", "30", inherited=0 if position == 0 else 1)
        conn.close()
        self.client = self.login("emp_user")

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def login(self, name):
        client = TestClient(self.app)
        self.assertEqual(client.post("/api/login", json={"username": name, "password": "pass"}).status_code, 200)
        return client

    def page(self, client=None):
        res = (client or self.client).get(f"/office/sheets/{self.sheet_id}")
        self.assertEqual(res.status_code, 200)
        table = PageTable()
        table.feed(res.text)
        return res.text, table

    def send(self, table, confirm=False, version=1, client=None, **edits):
        rows = [dict(r) for r in table.rows]
        for (position, field), value in edits.get("cells", {}).items():
            rows[position][field] = value
        return (client or self.client).post(f"/api/sheets/{self.sheet_id}/review", json={
            "version": version, "row_keys": table.keys, "rows": rows, "confirm": confirm})

    def db(self, sql, *args):
        conn = get_connection(self.db_path)
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def cell(self, position, field):
        return self.db("SELECT value, source, flag, inherited FROM cells WHERE row_id = ? AND field = ?",
                       self.rows[position], field)[0]

    def sheet(self):
        return self.db("SELECT status, version, picked_up_by FROM sheets WHERE id = ?", self.sheet_id)[0]

    def test_page_shows_the_table_flags_and_copied_down_values(self):
        html, table = self.page()
        self.assertEqual(table.keys, ["1", "2", "3"])
        self.assertEqual(len(table.rows[0]), 22)  # every column of the page file
        self.assertEqual([r["customer"] for r in table.rows], ["Khach A"] * 3)  # rows 2-3 copied down
        self.assertEqual(table.rows[0]["net_kg"], "86,4")  # numbers shown the Vietnamese way
        inherited = [i for i in table.inputs if "is-inherited" in i["class"]]
        self.assertEqual({i["data-field"] for i in inherited}, {"customer", "thickness_mic"})
        self.assertEqual({i["data-flag"] for i in table.inputs if "data-flag" in i}, {"doubt", "unreadable"})
        self.assertIn('aria-label="Còn 2 trên 2 ô cần kiểm tra">2/2</span>', html)
        for text in ("Các cuộn", "Thông tin", "Lịch sử", "AI chưa chắc", "Không đọc được", ">Lưu<", ">Xác nhận<",
                     "Tải tệp Excel"):
            self.assertIn(text, html)

    def test_unchanged_table_saves_nothing(self):
        _, table = self.page()
        res = self.send(table)  # the untouched unreadable "?" may stay while the page is still in review
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["changed"], 0)  # "86,4", "?" and copied values all round-trip
        self.assertEqual(self.cell(0, "net_kg")["source"], "ai")
        self.assertEqual(self.cell(2, "net_kg")["value"], "?")

    def test_confirm_needs_a_number_in_place_of_the_unreadable_mark(self):
        _, table = self.page()
        res = self.send(table, confirm=True)
        self.assertEqual(res.status_code, 422)
        self.assertIn("dòng 3 · Khối lượng Kg · Sau trừ bì phải là số", res.json()["error"]["message"])
        self.assertEqual(self.sheet()["status"], "needs_review")
        self.assertEqual(self.send(table, confirm=True, cells={(2, "net_kg"): "85"}).status_code, 200)

    def test_save_keeps_the_page_in_review_and_records_each_change(self):
        _, table = self.page()
        res = self.send(table, cells={(0, "net_kg"): "86,5", (2, "net_kg"): "85"})
        self.assertEqual(res.json(), {"version": 2, "status": "in_review", "changed": 2})
        self.assertEqual(self.cell(0, "net_kg"), {"value": "86.5", "source": "person", "flag": "none", "inherited": 0})
        sheet = self.sheet()
        self.assertEqual(sheet["status"], "in_review")
        self.assertIsNotNone(sheet["picked_up_by"])  # Lưu picks the page up
        changes = self.db("SELECT old_value, new_value, detail FROM events WHERE action = 'value_change' ORDER BY id")
        self.assertEqual([(c["old_value"], c["new_value"]) for c in changes], [("86.4", "86.5"), ("?", "85")])
        self.assertEqual(changes[0]["detail"], "ai|doubt")  # counted for AI accuracy like an Excel correction
        self.assertEqual(self.db("SELECT COUNT(*) AS n FROM events WHERE action = 'web_save'")[0]["n"], 1)
        self.assertEqual(self.db("SELECT SUM(reviewed) AS n FROM rows")[0]["n"], 0)
        html, _ = self.page()
        self.assertIn("Lưu trên web", html)  # the history tab
        self.assertIn("Dòng 1 · Khối lượng Kg · Sau trừ bì", html)

    def test_confirm_saves_and_marks_reviewed(self):
        _, table = self.page()
        res = self.send(table, confirm=True, cells={(2, "net_kg"): "85"})
        self.assertEqual(res.json(), {"version": 2, "status": "reviewed", "changed": 1})
        self.assertEqual(self.db("SELECT SUM(reviewed) AS n FROM rows")[0]["n"], 3)
        self.assertEqual(self.db("SELECT COUNT(*) AS n FROM events WHERE action = 'web_confirm'")[0]["n"], 1)
        html, _ = self.page()
        self.assertNotIn(">Xác nhận<", html)  # a reviewed page offers Lưu only
        self.assertIn(">Lưu<", html)
        again = self.send(PageTable(), confirm=True, version=2)
        self.assertEqual(again.status_code, 409)

    def test_saving_a_reviewed_page_keeps_it_reviewed(self):
        _, table = self.page()
        self.send(table, confirm=True, cells={(2, "net_kg"): "85"})
        _, table = self.page()
        res = self.send(table, version=2, cells={(1, "net_kg"): "86,7"})
        self.assertEqual(res.json()["status"], "reviewed")

    def test_a_save_read_before_a_confirm_cannot_undo_it(self):
        conn = get_connection(self.db_path)
        try:
            conn.execute("UPDATE cells SET value = '85' WHERE row_id = ? AND field = 'net_kg'", (self.rows[2],))
            conn.commit()
            before = dict(get_sheet_by_id(conn, self.sheet_id))  # a save read the page here...
        finally:
            conn.close()
        _, table = self.page()
        res = self.send(table, confirm=True)  # ...then a colleague confirmed it unchanged: still version 1
        self.assertEqual(res.json(), {"version": 1, "status": "reviewed", "changed": 0})
        table.rows[0]["net_kg"] = "90"
        values = [{f: normalise(f, r.get(f), "x") for f in FIELD_LABELS} for r in table.rows]
        conn = get_connection(self.db_path)
        try:
            with self.assertRaises(AppError) as raised:
                apply_values(conn, before, {"id": 1}, 1, table.keys, values, finish=False, action="web_save")
        finally:
            conn.close()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.sheet()["status"], "reviewed")

    def test_a_finish_restamps_the_status_time_a_save_keeps_it(self):
        def stamp():
            return self.db("SELECT status_since FROM sheets WHERE id = ?", self.sheet_id)[0]["status_since"]
        conn = get_connection(self.db_path)
        conn.execute("UPDATE sheets SET status = 'reviewed', status_since = '2000-01-01T00:00:00' WHERE id = ?",
                     (self.sheet_id,))
        conn.commit()
        conn.close()
        _, table = self.page()
        table.rows[2]["net_kg"] = "85"
        self.send(table)
        self.assertEqual(stamp(), "2000-01-01T00:00:00")  # Lưu on a reviewed page: status unchanged
        _, table = self.page()
        self.send(table, version=2, cells={(1, "net_kg"): "86,7"}, confirm=False)
        conn = get_connection(self.db_path)
        values = [{f: normalise(f, r.get(f), "x") for f in FIELD_LABELS} for r in self.page()[1].rows]
        apply_values(conn, get_sheet_by_id(conn, self.sheet_id), {"id": 1}, 3, table.keys, values,
                     finish=True, action="reupload")  # an Excel re-upload always restamps, as before
        conn.close()
        self.assertNotEqual(stamp(), "2000-01-01T00:00:00")

    def test_first_row_change_updates_the_copied_values_below(self):
        _, table = self.page()
        res = self.send(table, cells={(0, "customer"): "Khach B", (2, "net_kg"): "85"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([self.cell(p, "customer")["value"] for p in range(3)], ["Khach B"] * 3)
        self.assertEqual(self.db("SELECT COUNT(*) AS n FROM events WHERE action = 'value_carry'")[0]["n"], 2)

    def test_a_number_column_refuses_text_and_names_the_cell(self):
        _, table = self.page()
        res = self.send(table, cells={(2, "net_kg"): "8?5"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("dòng 3 · Khối lượng Kg · Sau trừ bì phải là số", res.json()["error"]["message"])
        self.assertEqual(self.sheet()["version"], 1)

    def test_out_of_date_page_is_refused_and_changes_nothing(self):
        _, table = self.page()
        self.send(table, cells={(2, "net_kg"): "85"})  # a colleague saves first: version 2
        res = self.send(table, cells={(0, "net_kg"): "90"})  # this page still shows version 1
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.cell(0, "net_kg")["value"], "86.4")

    def test_rows_cannot_be_added_removed_or_given_new_columns(self):
        _, table = self.page()
        table.rows[2]["net_kg"] = "85"
        res = self.client.post(f"/api/sheets/{self.sheet_id}/review", json={
            "version": 1, "row_keys": table.keys[:2], "rows": table.rows[:2]})
        self.assertEqual(res.status_code, 409)
        rows = [dict(r) for r in table.rows]
        rows[0]["secret"] = "x"
        res = self.client.post(f"/api/sheets/{self.sheet_id}/review", json={
            "version": 1, "row_keys": table.keys, "rows": rows})
        self.assertEqual(res.status_code, 422)

    def test_only_reviewers_may_save(self):
        _, table = self.page()
        table.rows[2]["net_kg"] = "85"
        for name, status in (("op_user", 403), ("admin_user", 200), ("boss_user", 200)):
            with self.subTest(name=name):
                res = self.send(table, client=self.login(name), version=self.sheet()["version"])
                self.assertEqual(res.status_code, status, res.text)

    def test_a_page_not_open_for_review_gets_a_read_only_table(self):
        conn = get_connection(self.db_path)
        conn.execute("UPDATE sheets SET status = 'failed' WHERE id = ?", (self.sheet_id,))
        conn.commit()
        conn.close()
        _, table = self.page()
        self.assertTrue(table.inputs)
        self.assertTrue(all("readonly" in i for i in table.inputs))


if __name__ == "__main__":
    unittest.main()
