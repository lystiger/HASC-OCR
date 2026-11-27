"""The waiting-photo page and one-step filing (zalo-intake spec R8, criterion 12). Made-up photos and users."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote

from fastapi.testclient import TestClient

from hasc import intake
from hasc.db import create_user
from hasc.web.app import create_app
from hasc.web.security import hash_password
from tests import test_zalo_gate as gate
from tests.test_zalo_gate import OTHER, back, front, vn


class FilingPage(gate.GateTest):
    def setUp(self):
        super().setUp()
        self.app = create_app(db_path=self.db_path, photos_dir=self.photos)
        self.clients = {}
        for role in ("admin", "employee", "boss", "operator"):
            create_user(self.conn, role, hash_password("pass"), role=role, active=1)
            client = TestClient(self.app)
            self.assertEqual(client.post("/api/login", json={"username": role, "password": "pass"}).status_code, 200)
            self.clients[role] = client

    def wait(self, count, start_minute=0):
        """`count` photos that the gate holds as waiting, sent on 19 Oct at 15:xx."""
        ids = [self.deliver(vn(2026, 10, 19, 15, start_minute + i)) for i in range(count)]
        self.batch(*[front(shift=3)] * count)
        return ids

    def post(self, role, path, **form):
        return self.clients[role].post(path, data=form, follow_redirects=False)

    def test_admin_and_employee_see_every_waiting_row_with_reason_sender_time_and_photo(self):
        ids = self.wait(25)
        keep = self.deliver(vn(2026, 10, 19, 16))
        self.batch(OTHER)
        staging = self.deliver(vn(2026, 10, 19, 16, 1), staging=1)
        self.batch(front(shift=3))
        for role in ("admin", "employee"):
            page = self.clients[role].get("/zalo/cho-xep")
            self.assertEqual(page.status_code, 200)
            shown = [int(n) for n in re.findall(r'id="anh-(\d+)"', page.text)]
            self.assertEqual(shown, [*ids, keep], "oldest first, not just the latest 20, staging left out")
            self.assertEqual(page.text.count(intake.WAIT_BAD_SHIFT), 25)
            self.assertIn(intake.NOT_FORM, page.text)
            self.assertIn("…" + gate.A[-5:], page.text)
            self.assertNotIn(gate.A, page.text)
            photo = self.clients[role].get(f"/zalo/cho-xep/{ids[0]}/photo")
            self.assertEqual(photo.status_code, 200)
            self.assertEqual(photo.content, Path(self.inbox(ids[0])["path"]).read_bytes())
        self.assertEqual(self.clients["admin"].get(f"/zalo/cho-xep/{staging}/photo").status_code, 404)

    def test_the_list_is_paginated(self):
        ids = self.wait(52)
        first = self.clients["admin"].get("/zalo/cho-xep").text
        second = self.clients["admin"].get("/zalo/cho-xep?page=2").text
        self.assertEqual(len(re.findall(r'id="anh-\d+"', first)), 50)
        self.assertEqual([int(n) for n in re.findall(r'id="anh-(\d+)"', second)], ids[50:])

    def test_a_person_files_a_front_and_a_back_with_the_results_of_the_gate(self):
        first = self.deliver(vn(2026, 10, 19, 15))
        resend = self.deliver(vn(2026, 10, 19, 15, 5))
        self.batch(front(), front(shift=3))
        self.assertEqual([self.inbox(i)["status"] for i in (first, resend)], ["filed", "waiting"])
        self.deliver(vn(2026, 10, 19, 15, 10))
        side = self.deliver(vn(2026, 10, 19, 15, 12))
        lonely = self.deliver(vn(2026, 10, 19, 15, 13), sender=gate.B)
        self.batch(front(code="7", nets=("5", "6", "7")), back(), back())
        self.assertEqual(self.inbox(side)["status"], "filed")
        self.assertEqual(self.inbox(lonely)["status"], "waiting")
        res = self.post("employee", f"/zalo/cho-xep/{resend}/mat-truoc", production_date="2026-10-19", shift="1")
        self.assertEqual(res.status_code, 303)
        self.assertIn("gửi lại", unquote(res.headers["location"]))
        self.assertEqual(self.inbox(resend)["sheet_id"], self.inbox(first)["sheet_id"])
        res = self.post("admin", f"/zalo/cho-xep/{lonely}/mat-sau", production_date="2026-10-19", shift="1", paper_no="1")
        self.assertIn("mặt sau", unquote(res.headers["location"]))
        sheet = self.sheet(lonely)
        self.assertEqual((sheet["production_date"], sheet["shift"], sheet["page_no"], sheet["layout"]),
                         ("2026-10-19", 1, 2, "back"))
        users = {r["action"]: r["user_id"] for r in self.conn.execute("SELECT action, user_id FROM events")}
        self.assertIsNotNone(users["zalo_resend"])
        self.assertIsNotNone(users["zalo_back"])
        self.assertEqual(self.count("read_jobs"), 4, "two fronts and two backs; a resend adds no job")

    def test_a_new_front_gets_the_next_odd_page_and_a_read_job(self):
        first = self.deliver(vn(2026, 10, 19, 15))
        held = self.deliver(vn(2026, 10, 19, 15, 5))
        self.batch(front(), front(shift=3, code="9", nets=("1", "2", "3")))
        self.post("employee", f"/zalo/cho-xep/{held}/mat-truoc", production_date="2026-10-19", shift="1")
        self.assertEqual(self.sheet(held)["page_no"], 3)
        self.assertEqual(self.sheet(first)["page_no"], 1)
        self.assertEqual(self.count("read_jobs"), 2)

    def test_a_refused_filing_shows_its_reason_and_changes_nothing(self):
        paper = self.deliver(vn(2026, 10, 19, 15))
        one = self.deliver(vn(2026, 10, 19, 15, 1))
        two = self.deliver(vn(2026, 10, 19, 15, 2))
        self.batch(front(), back(), back())
        self.assertEqual(self.inbox(two)["status"], "waiting")
        res = self.post("admin", f"/zalo/cho-xep/{two}/mat-sau", production_date="2026-10-19", shift="1", paper_no="1")
        self.assertIn(unquote(intake.PAGE_TAKEN), unquote(res.headers["location"]))
        self.assertEqual(self.inbox(two)["status"], "waiting")
        bad = self.post("admin", f"/zalo/cho-xep/{two}/mat-truoc", production_date="khong", shift="1")
        self.assertIn("ngày không hợp lệ", unquote(bad.headers["location"]))
        self.assertEqual(self.inbox(two)["status"], "waiting")

    def test_boss_and_operator_get_403_on_the_page_the_photo_and_every_post(self):
        row = self.wait(1)[0]
        before = self.snapshot()
        for role in ("boss", "operator"):
            client = self.clients[role]
            self.assertEqual(client.get("/zalo/cho-xep").status_code, 403)
            self.assertEqual(client.get(f"/zalo/cho-xep/{row}/photo").status_code, 403)
            self.assertEqual(self.post(role, f"/zalo/cho-xep/{row}/mat-truoc", production_date="2026-10-19",
                                       shift="1").status_code, 403)
            self.assertEqual(self.post(role, f"/zalo/cho-xep/{row}/mat-sau", production_date="2026-10-19", shift="1",
                                       paper_no="1").status_code, 403)
        self.assertEqual(self.snapshot(), before)

    def test_filing_a_staging_row_is_not_possible(self):
        row = self.deliver(vn(2026, 10, 19, 15), staging=1)
        self.batch(front(shift=3))
        res = self.post("admin", f"/zalo/cho-xep/{row}/mat-truoc", production_date="2026-10-19", shift="1")
        self.assertEqual(res.status_code, 404)

    def test_a_row_whose_photo_file_is_gone_shows_no_form_and_cannot_be_filed(self):
        row = self.wait(1)[0]
        self.assertEqual(self.clients["admin"].get("/zalo/cho-xep").text.count("/mat-truoc"), 1)
        Path(self.inbox(row)["path"]).unlink()
        page = self.clients["admin"].get("/zalo/cho-xep").text
        self.assertNotIn("/mat-truoc", page)
        self.assertIn("Không còn tệp ảnh", page)
        res = self.post("admin", f"/zalo/cho-xep/{row}/mat-truoc", production_date="2026-10-19", shift="1")
        self.assertIn(unquote(intake.WAIT_NO_FILE), unquote(res.headers["location"]))
        self.assertEqual(self.inbox(row)["status"], "waiting")

    def test_the_office_list_links_to_the_page_with_the_waiting_count_for_filing_roles_only(self):
        self.assertNotIn("/zalo/cho-xep", self.clients["employee"].get("/office").text)
        self.wait(3)
        text = self.clients["employee"].get("/office").text
        self.assertRegex(text, r'href="/zalo/cho-xep"><strong class="num">3</strong> ảnh Zalo đang chờ xếp')
        self.assertIn("/zalo/cho-xep", self.clients["admin"].get("/office").text)
        self.assertNotIn("/zalo/cho-xep", self.clients["boss"].get("/office").text)

    def test_no_zalo_message_is_sent_by_filing(self):
        row = self.wait(1)[0]
        self.post("admin", f"/zalo/cho-xep/{row}/mat-truoc", production_date="2026-10-19", shift="1")


if __name__ == "__main__":
    unittest.main()
