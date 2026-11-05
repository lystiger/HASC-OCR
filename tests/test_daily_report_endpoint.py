from datetime import date, datetime, timedelta
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from hasc.db import create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestDailyReportEndpoint(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos_dir = Path(self.temp_dir.name) / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)

        conn = get_connection(self.db_path)
        self.users = {
            "operator": create_user(conn, "op_user", hash_password("pass"), role="operator", active=1),
            "employee": create_user(conn, "emp_user", hash_password("pass"), role="employee", active=1),
            "boss": create_user(conn, "boss_user", hash_password("pass"), role="boss", active=1),
            "admin": create_user(conn, "admin_user", hash_password("pass"), role="admin", active=1),
        }

        self.clients = {}
        for role, username in [
            ("operator", "op_user"),
            ("employee", "emp_user"),
            ("boss", "boss_user"),
            ("admin", "admin_user"),
        ]:
            client = TestClient(self.app)
            res = client.post("/api/login", json={"username": username, "password": "pass"})
            self.assertEqual(res.status_code, 200, f"Login failed for {role}")
            self.clients[role] = client

        self.unauth_client = TestClient(self.app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_roles_allowed_for_employee_boss_admin(self):
        """employee, boss, and admin may access daily report."""
        target_date = "2026-11-04"
        for role in ("employee", "boss", "admin"):
            client = self.clients[role]
            res = client.get(f"/api/days/{target_date}/report")
            self.assertEqual(res.status_code, 200, f"Role {role} should be allowed (got {res.status_code})")
            # Verify valid workbook returned
            wb = load_workbook(io.BytesIO(res.content))
            self.assertIn("Báo cáo", wb.sheetnames)

    def test_roles_forbidden_for_operator(self):
        """operator role receives 403 with spec error shape."""
        client = self.clients["operator"]
        res = client.get("/api/days/2026-11-04/report")
        self.assertEqual(res.status_code, 403)
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "forbidden")
        self.assertIn("message", data["error"])

    def test_unauthenticated_request_receives_401(self):
        """Unauthenticated caller receives 401 with spec error shape."""
        res = self.unauth_client.get("/api/days/2026-11-04/report")
        self.assertEqual(res.status_code, 401)
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "unauthorized")
        self.assertIn("message", data["error"])

    def test_filename_and_content_type(self):
        """Returns xlsx content type and filename HASC_BaoCaoNgay_{YYYY-MM-DD}.xlsx."""
        target_date = "2026-11-04"
        expected_filename = f"HASC_BaoCaoNgay_{target_date}.xlsx"
        expected_content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        client = self.clients["employee"]
        res = client.get(f"/api/days/{target_date}/report")
        self.assertEqual(res.status_code, 200)

        # Check content type
        self.assertTrue(res.headers.get("content-type", "").startswith(expected_content_type))

        # Check Content-Disposition header
        content_disp = res.headers.get("content-disposition", "")
        self.assertIn(f'filename="{expected_filename}"', content_disp)

        # Check content is valid openpyxl workbook
        wb = load_workbook(io.BytesIO(res.content))
        self.assertEqual(wb["Báo cáo"]["A1"].value, "BÁO CÁO SẢN XUẤT NGÀY")

    def test_bad_date_returns_spec_error(self):
        """Bad dates return a 404/422 spec error."""
        client = self.clients["employee"]
        bad_dates = ["not-a-date", "2026-02-30", "2026-13-01", "2026-1-1", "2026-99-99"]
        for bad in bad_dates:
            res = client.get(f"/api/days/{bad}/report")
            self.assertIn(res.status_code, (404, 422), f"Expected 404 or 422 for bad date {bad}, got {res.status_code}")
            data = res.json()
            self.assertIn("error", data)
            self.assertIn("code", data["error"])
            self.assertIn("message", data["error"])

    def test_office_link_and_date_picker(self):
        """Office screen contains a Vietnamese link with a date picker defaulting to yesterday."""
        client = self.clients["employee"]
        res = client.get("/office")
        self.assertEqual(res.status_code, 200)
        html = res.text

        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()

        # Date picker defaulting to yesterday
        self.assertIn('type="date"', html)
        self.assertIn(f'value="{yesterday}"', html)

        # A plain GET form, so it works without JavaScript (ADR 7)
        self.assertIn('action="/office/report"', html)
        res = client.get(f"/office/report?date={yesterday}", follow_redirects=False)
        self.assertEqual(res.headers["location"], f"/api/days/{yesterday}/report")
        # Vietnamese link text / label
        self.assertTrue("Báo cáo ngày" in html or "Báo cáo sản xuất ngày" in html)
