from datetime import date, datetime, timedelta, timezone
import gc
import re
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from hasc.db import (
    create_photo,
    create_row,
    create_sheet,
    create_user,
    get_connection,
    get_sheet_by_id,
    list_events,
    upsert_cell,
)
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestOfficeScreens(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos_dir = Path(self.temp_dir.name) / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)

        conn = get_connection(self.db_path)
        # Create users for all roles
        self.users = {
            "operator": create_user(conn, "op_lead", hash_password("pass"), role="operator", active=1),
            "employee": create_user(conn, "emp_check", hash_password("pass"), role="employee", active=1),
            "employee2": create_user(conn, "emp_second", hash_password("pass"), role="employee", active=1),
            "boss": create_user(conn, "boss_user", hash_password("pass"), role="boss", active=1),
            "admin": create_user(conn, "admin_user", hash_password("pass"), role="admin", active=1),
        }

        self.clients = {}
        for role_name, username in [
            ("operator", "op_lead"),
            ("employee", "emp_check"),
            ("employee2", "emp_second"),
            ("boss", "boss_user"),
            ("admin", "admin_user"),
        ]:
            client = TestClient(self.app)
            res = client.post("/api/login", json={"username": username, "password": "pass"})
            self.assertEqual(res.status_code, 200)
            self.clients[role_name] = client

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _get_conn(self):
        return get_connection(self.db_path)

    def test_office_sheet_list_default_and_columns(self):
        """Office list opens on Needs review, In review and Failed sheets, overdue first.
        Shows machine, date, shift, page, status, uploading shift leader and time waited.
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc)
        # Sheet 1: needs_review, 1 hour ago
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=(now - timedelta(hours=1)).isoformat(),
        )
        # Photo for Sheet 1 by op_lead
        create_photo(
            conn,
            sheet_id=s1,
            path="photo1.jpg",
            sha256="hash1",
            uploaded_by=self.users["operator"],
            uploaded_at=(now - timedelta(hours=1)).isoformat(),
        )

        # Sheet 2: in_review
        s2 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T3",
            production_date="2026-10-19",
            shift=2,
            page_no=1,
            status="in_review",
            version=1,
            status_since=(now - timedelta(hours=2)).isoformat(),
        )

        # Sheet 3: failed
        s3 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T6",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="failed",
            version=1,
            status_since=(now - timedelta(minutes=30)).isoformat(),
        )

        # Sheet 4: reviewed (should not be in default view)
        s4 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-18",
            shift=1,
            page_no=1,
            status="reviewed",
            version=1,
            status_since=(now - timedelta(hours=10)).isoformat(),
        )

        # Sheet 5: reading (should not be in default view)
        s5 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=2,
            status="reading",
            version=1,
            status_since=(now - timedelta(minutes=5)).isoformat(),
        )

        emp_client = self.clients["employee"]

        # Plain page load
        res = emp_client.get("/office")
        self.assertEqual(res.status_code, 200)
        html = res.text

        # Base layout present
        self.assertIn("<!doctype html>", html)
        self.assertIn("HASC VN", html)

        # Column headers in Vietnamese
        self.assertIn("Máy", html)
        self.assertIn("Ngày", html)
        self.assertIn("Ca", html)
        self.assertIn("Trang", html)
        self.assertIn("Trạng thái", html)
        self.assertIn("Trưởng ca tải lên", html)
        self.assertIn("Thời gian chờ", html)

        # Expected status labels in Vietnamese
        self.assertIn("Cần kiểm tra", html)
        self.assertIn("Đang kiểm tra", html)
        self.assertIn("Lỗi", html)

        # Shift leader displayed
        self.assertIn("op_lead", html)

        # Machines displayed
        self.assertIn("T1", html)
        self.assertIn("T3", html)
        self.assertIn("T6", html)

        # Time waited displayed
        self.assertIn("1 giờ", html)
        self.assertIn("2 giờ", html)
        self.assertIn("30 phút", html)

        # Reviewed sheet should not be visible in default view
        self.assertNotIn("2026-10-18", html)

    def test_overdue_first_sorting(self):
        """A sheet is overdue 24 hours after entering Needs review or In review.
        Overdue sheets appear first in list.
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc)

        # Sheet Normal: needs_review, 2 hours ago (not overdue)
        s_normal = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=(now - timedelta(hours=2)).isoformat(),
        )

        # Sheet Overdue: needs_review, 25 hours ago (overdue)
        s_overdue = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T3",
            production_date="2026-10-18",
            shift=2,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=(now - timedelta(hours=25)).isoformat(),
        )

        # Sheet Failed: failed, 30 hours ago (failed is not overdue per spec definition)
        s_failed = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T6",
            production_date="2026-10-17",
            shift=1,
            page_no=1,
            status="failed",
            version=1,
            status_since=(now - timedelta(hours=30)).isoformat(),
        )

        emp_client = self.clients["employee"]
        res = emp_client.get("/api/sheets")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Check overdue boolean
        overdue_sheet = next(s for s in data if s["id"] == s_overdue)
        normal_sheet = next(s for s in data if s["id"] == s_normal)
        failed_sheet = next(s for s in data if s["id"] == s_failed)

        self.assertTrue(overdue_sheet["overdue"])
        self.assertFalse(normal_sheet["overdue"])
        self.assertFalse(failed_sheet["overdue"])

        # Check sorting: overdue sheet is first
        self.assertEqual(data[0]["id"], s_overdue)

        # Check HTML display
        html_res = emp_client.get("/office")
        self.assertEqual(html_res.status_code, 200)
        self.assertIn("Quá hạn", html_res.text)

    def test_htmx_partial_and_plain_page(self):
        """Sheet list is refreshed live with htmx and still works as a plain page."""
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        emp_client = self.clients["employee"]

        # 1. Plain page contains htmx attributes
        res_full = emp_client.get("/office")
        self.assertEqual(res_full.status_code, 200)
        self.assertIn('hx-get="/office/sheets/partial', res_full.text)
        self.assertIn('hx-trigger="every 5s"', res_full.text)
        self.assertIn('hx-swap="innerHTML"', res_full.text)
        self.assertIn("<!doctype html>", res_full.text)

        # 2. HTMX partial returns only table fragment
        res_partial = emp_client.get("/office/sheets/partial")
        self.assertEqual(res_partial.status_code, 200)
        self.assertNotIn("<!doctype html>", res_partial.text)
        self.assertIn("<table", res_partial.text)
        self.assertIn("T1", res_partial.text)

    def test_detail_page_and_photo_view(self):
        """Detail page with sheet photos, pick-up action, Excel download and 'Đúng, xác nhận'."""
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        # Create real photo file
        test_photo_path = self.photos_dir / "test_sheet.jpg"
        test_photo_path.write_bytes(b"dummy_jpeg_bytes")

        photo_id = create_photo(
            conn,
            sheet_id=s1,
            path=str(test_photo_path),
            sha256="testsha",
            uploaded_by=self.users["operator"],
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )

        emp_client = self.clients["employee"]

        # Detail page
        res = emp_client.get(f"/office/sheets/{s1}")
        self.assertEqual(res.status_code, 200)
        html = res.text

        # Elements on detail page
        self.assertIn(f"/api/photos/{photo_id}", html)
        self.assertIn("Nhận kiểm tra", html)
        self.assertIn("Tải tệp Excel", html)
        self.assertIn("Đúng, xác nhận", html)

        # Photo view endpoint
        res_photo = emp_client.get(f"/api/photos/{photo_id}")
        self.assertEqual(res_photo.status_code, 200)
        self.assertEqual(res_photo.content, b"dummy_jpeg_bytes")

    def test_pickup_and_takeover(self):
        """Any office member can pick up a sheet. Others can take over, which is recorded."""
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        emp1 = self.clients["employee"]
        emp2 = self.clients["employee2"]

        # Emp1 picks up
        res = emp1.post(f"/api/sheets/{s1}/pickup", json={"takeover": False})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "in_review", "picked_up_by": self.users["employee"]})

        # Verify DB
        sheet = get_sheet_by_id(conn, s1)
        self.assertEqual(sheet["status"], "in_review")
        self.assertEqual(sheet["picked_up_by"], self.users["employee"])

        # Emp2 tries to pickup without takeover -> 409 already_picked_up
        res_conflict = emp2.post(f"/api/sheets/{s1}/pickup", json={"takeover": False})
        self.assertEqual(res_conflict.status_code, 409)
        self.assertEqual(res_conflict.json()["error"]["code"], "already_picked_up")

        # Emp2 takes over -> success
        res_takeover = emp2.post(f"/api/sheets/{s1}/pickup", json={"takeover": True})
        self.assertEqual(res_takeover.status_code, 200)
        self.assertEqual(res_takeover.json(), {"status": "in_review", "picked_up_by": self.users["employee2"]})

        sheet_after = get_sheet_by_id(conn, s1)
        self.assertEqual(sheet_after["picked_up_by"], self.users["employee2"])

        # Check events
        events = list_events(conn, sheet_id=s1)
        actions = [e["action"] for e in events]
        self.assertIn("pickup", actions)
        self.assertIn("takeover", actions)

    def test_excel_download_using_hasc_excel(self):
        """Excel download using hasc/excel produces valid workbook with _meta tab."""
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=2,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        # Add a test row
        row_id = create_row(conn, sheet_id=s1, row_key="101", position=1)
        upsert_cell(conn, row_id=row_id, field="gross_kg", value="15.8")
        upsert_cell(conn, row_id=row_id, field="core_kg", value="2.2")
        upsert_cell(conn, row_id=row_id, field="net_kg", value="13.6")

        emp = self.clients["employee"]
        res = emp.get(f"/api/sheets/{s1}/excel")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("HASC_T1_2026-10-19_Ca1_Trang1.xlsx", res.headers["content-disposition"])

        # Load workbook
        wb = load_workbook(io.BytesIO(res.content))
        self.assertIn("Báo cáo", wb.sheetnames)
        self.assertIn("_meta", wb.sheetnames)

        meta_ws = wb["_meta"]
        # Hidden sheet_id and version
        meta_dict = {row[0]: row[1] for row in meta_ws.iter_rows(values_only=True) if len(row) >= 2}
        self.assertEqual(meta_dict.get("sheet_id"), s1)
        self.assertEqual(meta_dict.get("version"), 2)

    def test_confirm_sets_reviewed_and_stale_refused(self):
        """'Đúng, xác nhận' button confirms downloaded version.
        Refused with 409 stale_version if data changed since download.
        Confirmation sets sheet to Reviewed.
        """
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="in_review",
            version=1,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        emp = self.clients["employee"]

        # 1. Stale confirmation attempt (version 0 instead of 1)
        res_stale = emp.post(f"/api/sheets/{s1}/confirm", json={"version": 0})
        self.assertEqual(res_stale.status_code, 409)
        err = res_stale.json()["error"]
        self.assertEqual(err["code"], "stale_version")
        self.assertTrue(len(err["message"]) > 0)

        # Status still in_review
        sheet_check = get_sheet_by_id(conn, s1)
        self.assertEqual(sheet_check["status"], "in_review")

        # 2. Matching confirmation (version 1)
        res_ok = emp.post(f"/api/sheets/{s1}/confirm", json={"version": 1})
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.json(), {"version": 1, "status": "reviewed"})

        # Status updated to reviewed
        sheet_reviewed = get_sheet_by_id(conn, s1)
        self.assertEqual(sheet_reviewed["status"], "reviewed")

        # Confirm event created
        events = list_events(conn, sheet_id=s1)
        confirm_events = [e for e in events if e["action"] == "confirm"]
        self.assertEqual(len(confirm_events), 1)
        self.assertEqual(confirm_events[0]["user_id"], self.users["employee"])

    def test_html_form_confirm_and_stale_refusal(self):
        """HTML POST /office/sheets/{id}/confirm handles form submit:
        - Stale returns 409 stale_version spec error shape.
        - Matching version redirects to detail page with ?confirmed=1.
        """
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="in_review",
            version=3,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        emp = self.clients["employee"]

        # Stale version via form POST: refused, and the clerk is sent back to the sheet with the reason
        res_stale = emp.post(f"/office/sheets/{s1}/confirm", data={"version": "2"}, follow_redirects=False)
        self.assertEqual(res_stale.status_code, 303)
        self.assertIn(f"/office/sheets/{s1}?upload_error=", res_stale.headers["location"])
        self.assertIn("Dữ liệu đã thay đổi", emp.get(res_stale.headers["location"]).text)

        # Matching version via form POST
        res_ok = emp.post(f"/office/sheets/{s1}/confirm", data={"version": "3"}, follow_redirects=False)
        self.assertEqual(res_ok.status_code, 303)
        self.assertIn(f"/office/sheets/{s1}?confirmed=1", res_ok.headers["location"])

        sheet = get_sheet_by_id(conn, s1)
        self.assertEqual(sheet["status"], "reviewed")

    def _sheet(self, status, machine="T1"):
        return create_sheet(self._get_conn(), form="BMMT01-2022/HASC", machine=machine, production_date="2026-10-19",
                            shift=1, page_no=1, status=status, version=1,
                            status_since=datetime.now(timezone.utc).isoformat())

    def test_live_refresh_url_from_the_page_works_with_empty_filters(self):
        self._sheet("needs_review")
        self._sheet("reviewed", machine="T3")
        emp = self.clients["employee"]
        url = re.search(r'hx-get="([^"]+)"', emp.get("/office").text).group(1).replace("&amp;", "&")
        res = emp.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("T1", res.text)
        self.assertNotIn("T3", res.text)  # a reviewed sheet is not in the default list

    def test_confirm_is_refused_unless_the_sheet_is_waiting_for_review(self):
        for status in ("received", "reading", "failed", "reviewed"):
            s1 = self._sheet(status, machine={"received": "T1", "reading": "T3", "failed": "T6", "reviewed": "T2"}[status])
            res = self.clients["employee"].post(f"/api/sheets/{s1}/confirm", json={"version": 1})
            self.assertEqual(res.status_code, 409, status)
            self.assertEqual(res.json()["error"]["code"], "not_reviewable")
            self.assertEqual(get_sheet_by_id(self._get_conn(), s1)["status"], status)

    def test_bad_shift_filter_is_a_validation_error(self):
        for shift in ("abc", "1.5", "²"):
            res = self.clients["employee"].get(f"/office/sheets/partial?shift={shift}")
            self.assertEqual(res.status_code, 422, shift)

    def test_stale_confirm_is_stale_even_when_already_reviewed(self):
        s1 = self._sheet("reviewed")
        res = self.clients["employee"].post(f"/api/sheets/{s1}/confirm", json={"version": 0})
        self.assertEqual(res.json()["error"]["code"], "stale_version")

    def test_missing_sheet_is_not_found(self):
        emp = self.clients["employee"]
        for res in (emp.get("/api/sheets/999"), emp.post("/api/sheets/999/pickup", json={}),
                    emp.post("/api/sheets/999/confirm", json={"version": 1}), emp.get("/api/sheets/999/excel")):
            self.assertEqual(res.status_code, 404)
            self.assertEqual(res.json()["error"]["code"], "not_found")

    def test_role_enforcement(self):
        """Roles are enforced:
        - Operator cannot access office routes (403).
        - Admin can pick up, download and confirm.
        - Admin viewing sheet logs admin_open_sheet event.
        """
        conn = self._get_conn()
        s1 = create_sheet(
            conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
            status="needs_review",
            version=1,
            status_since=datetime.now(timezone.utc).isoformat(),
        )

        op = self.clients["operator"]
        admin = self.clients["admin"]

        # Operator forbidden on office routes
        self.assertEqual(op.get("/office").status_code, 403)
        self.assertEqual(op.get(f"/office/sheets/{s1}").status_code, 403)
        self.assertEqual(op.get(f"/office/sheets/{s1}/excel").status_code, 403)
        self.assertEqual(op.post(f"/office/sheets/{s1}/pickup", data={"takeover": "false"}).status_code, 403)
        self.assertEqual(op.post(f"/office/sheets/{s1}/confirm", data={"version": "1"}).status_code, 403)

        # Admin can review sheets through both browser and API routes.
        self.assertEqual(admin.post(f"/office/sheets/{s1}/pickup", data={"takeover": "false"}, follow_redirects=False).status_code, 303)
        self.assertEqual(admin.post(f"/office/sheets/{s1}/confirm", data={"version": "1"}, follow_redirects=False).status_code, 303)
        download = admin.get(f"/api/sheets/{s1}/excel")
        self.assertEqual(download.status_code, 200)
        self.assertIn("_meta", load_workbook(io.BytesIO(download.content)).sheetnames)

        # Admin CAN view sheet numbers / detail page, which logs admin_open_sheet event
        res_admin_view = admin.get(f"/office/sheets/{s1}")
        self.assertEqual(res_admin_view.status_code, 200)

        events = list_events(conn, sheet_id=s1)
        admin_events = [e for e in events if e["action"] == "admin_open_sheet"]
        self.assertEqual(len(admin_events), 1)
        self.assertEqual(admin_events[0]["user_id"], self.users["admin"])

    def test_confirming_a_sheet_a_colleague_already_confirmed_stays_on_the_page(self):
        """Two clerks have the sheet open; the second "Đúng, xác nhận" got a raw JSON page. It must come
        back to the sheet with the reason, and a reviewed sheet offers no confirm button."""
        conn = self._get_conn()
        s1 = create_sheet(
            conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-10-21", shift=1, page_no=1,
            status="needs_review", version=1, status_since=datetime.now(timezone.utc).isoformat(),
        )
        conn.close()
        emp1, emp2 = self.clients["employee"], self.clients["employee2"]
        self.assertIn(f'action="/office/sheets/{s1}/confirm"', emp1.get(f"/office/sheets/{s1}").text)

        self.assertEqual(emp2.post(f"/office/sheets/{s1}/confirm", data={"version": 1}, follow_redirects=False).status_code, 303)
        res = emp1.post(f"/office/sheets/{s1}/confirm", data={"version": 1})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Biểu mẫu này đã được xác nhận", res.text)
        self.assertNotIn('{"error"', res.text)
        self.assertNotIn(f'action="/office/sheets/{s1}/confirm"', res.text)

        # The API keeps its JSON answer.
        self.assertEqual(emp1.post(f"/api/sheets/{s1}/confirm", json={"version": 1}).status_code, 409)

    def test_sheet_page_offers_only_the_actions_the_user_may_take(self):
        """Admins can review sheets; confirmation appears only for sheets waiting for review."""
        conn = self._get_conn()
        ids = {status: create_sheet(conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-10-22",
                                    shift=1, page_no=n, status=status, version=1,
                                    status_since=datetime.now(timezone.utc).isoformat())
               for n, status in enumerate(("needs_review", "in_review", "received", "reading", "failed", "reviewed"), 1)}
        conn.close()
        # Admins see the review controls and can download a real workbook.
        page = self.clients["admin"].get(f"/office/sheets/{ids['needs_review']}").text
        self.assertIn("Biểu mẫu Máy T1", page)
        self.assertIn("Thao tác xử lý biểu mẫu", page)
        for action in ("/pickup", "/confirm", f"/office/sheets/{ids['needs_review']}/excel"):
            self.assertIn(action, page)
        self.assertEqual(self.clients["admin"].get(f"/office/sheets/{ids['needs_review']}/excel").status_code, 200)
        self.assertIn(f"/office/sheets/{ids['needs_review']}/excel", self.clients["employee"].get(f"/office/sheets/{ids['needs_review']}").text)
        for status, sheet_id in ids.items():
            page = self.clients["admin"].get(f"/office/sheets/{sheet_id}").text
            self.assertEqual(f'action="/office/sheets/{sheet_id}/confirm"' in page, status in ("needs_review", "in_review"), status)
        self.assertIn("/pickup", self.clients["boss"].get(f"/office/sheets/{ids['needs_review']}").text)
