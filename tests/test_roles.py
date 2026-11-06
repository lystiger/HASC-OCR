from pathlib import Path
import gc
import tempfile
import unittest

from fastapi.testclient import TestClient

from hasc.db import create_sheet, create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestSection4RoleTable(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)

        conn = get_connection(self.db_path)
        # Create users for all 4 roles
        self.users = {
            "operator": create_user(conn, "op_user", hash_password("pass"), role="operator", active=1),
            "employee": create_user(conn, "emp_user", hash_password("pass"), role="employee", active=1),
            "boss": create_user(conn, "boss_user", hash_password("pass"), role="boss", active=1),
            "admin": create_user(conn, "admin_user", hash_password("pass"), role="admin", active=1),
        }
        create_sheet(conn, form="BMMT01-2022/HASC", machine="T1", production_date="2026-10-19", shift=1,
                     page_no=1, status="needs_review", version=1)

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

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _assert_allowed(self, role: str, res, msg: str = ""):
        self.assertNotEqual(res.status_code, 403, f"Role {role} should be allowed: {msg} (got 403)")
        self.assertIn(res.status_code, (200, 201, 202, 204), f"Role {role} expected success status, got {res.status_code}: {msg}")

    def _assert_allowed_unbuilt(self, role: str, res, msg: str = ""):
        """Not built in S1: the role check passes and the endpoint answers 501."""
        self.assertEqual(res.status_code, 501, f"Role {role} should pass the role check: {msg} (got {res.status_code})")

    def _assert_forbidden(self, role: str, res, msg: str = ""):
        self.assertEqual(res.status_code, 403, f"Role {role} should be forbidden (403): {msg} (got {res.status_code})")
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "forbidden")

    def test_role_table_row_1_upload_photo_and_limits(self):
        """Action 1: Upload a photo, see printed limits: Operator=Yes, Employee=No, Boss=No, Admin=No"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.get("/api/limits")
            if role == "operator":
                self._assert_allowed(role, res, "GET /api/limits")
            else:
                self._assert_forbidden(role, res, "GET /api/limits")

    def test_role_table_row_2_see_own_uploads(self):
        """Action 2: See status of own uploads: Operator=Yes, others=No/n/a"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.get("/api/uploads/mine")
            if role == "operator":
                self._assert_allowed(role, res, "GET /api/uploads/mine")
            else:
                self._assert_forbidden(role, res, "GET /api/uploads/mine")

    def test_role_table_row_3_see_all_sheets_and_photos(self):
        """Action 3: See status of all sheets and photos: Operator=No, Employee=Yes, Boss=Yes, Admin=Yes"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.get("/api/sheets")
            if role in ["employee", "boss", "admin"]:
                self._assert_allowed(role, res, "GET /api/sheets")
            else:
                self._assert_forbidden(role, res, "GET /api/sheets")

    def test_role_table_row_4_review_pickup_confirm(self):
        """Action 4: Pick up, download, correct, re-upload, confirm, retry, hand-enter: Office=Yes, Operator=No, Admin=No"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.post("/api/sheets/1/pickup", json={"takeover": True})  # the employee picks it up first
            if role in ["employee", "boss"]:
                self._assert_allowed(role, res, "POST /api/sheets/1/pickup")
            else:
                self._assert_forbidden(role, res, "POST /api/sheets/1/pickup")

    def test_unbuilt_review_endpoints_pass_the_role_check_and_answer_501(self):
        for role in ["employee", "boss"]:
            client = self.clients[role]
            for res, name in ((client.get("/api/shifts/excel"), "GET /api/shifts/excel"),
                              (client.post("/api/sheets/1/excel"), "POST /api/sheets/1/excel"),
                              (client.get("/api/sheets/1/template"), "GET /api/sheets/1/template")):
                self._assert_allowed_unbuilt(role, res, name)

    def test_role_table_row_5_mark_invalid(self):
        """Action 5: Mark an upload invalid: Office=Yes, Operator=No, Admin=No"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.post("/api/photos/1/invalid", json={"reason": "Blurry"})
            if role in ["employee", "boss"]:
                self._assert_allowed_unbuilt(role, res, "POST /api/photos/1/invalid")
            else:
                self._assert_forbidden(role, res, "POST /api/photos/1/invalid")

    def test_role_table_row_6_mark_not_running(self):
        """Action 6: Mark a machine or shift not running: Operator=Yes, Office=Yes, Admin=No"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.post(
                "/api/not-running",
                json={"machine": "T1", "production_date": "2026-10-19", "shift": 1, "reason": "Bảo trì"},
            )
            if role in ["operator", "employee", "boss"]:
                self._assert_allowed_unbuilt(role, res, "POST /api/not-running")
            else:
                self._assert_forbidden(role, res, "POST /api/not-running")

    def test_role_table_row_7_see_sheet_numbers(self):
        """Action 7: See sheet numbers: Operator=No, Office=Yes, Admin=Yes (each opening logged)"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.get("/api/sheets/1")
            if role in ["employee", "boss", "admin"]:
                self._assert_allowed(role, res, "GET /api/sheets/1")
            else:
                self._assert_forbidden(role, res, "GET /api/sheets/1")

    def test_role_table_row_8_see_edit_history(self):
        """Action 8: See edit history: Operator=No, Employee=Own, Boss=Everyone, Admin=Own"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.get("/api/history")
            if role in ["employee", "boss", "admin"]:
                self._assert_allowed_unbuilt(role, res, "GET /api/history")
            else:
                self._assert_forbidden(role, res, "GET /api/history")

    def test_role_table_row_9_manage_users(self):
        """Action 9: Add or deactivate accounts, reset passwords, log out any account: Admin=Yes, others=No"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.post("/api/admin/users", json={"username": f"user_by_{role}", "role": "employee"})
            if role == "admin":
                self._assert_allowed(role, res, "POST /api/admin/users")
            else:
                self._assert_forbidden(role, res, "POST /api/admin/users")

    def test_role_table_row_10_see_statistics(self):
        """Action 10: See statistics: Operator=No, Employee=No, Boss=Overdue only, Admin=Yes"""
        for role in ["operator", "employee", "boss", "admin"]:
            client = self.clients[role]
            res = client.get("/api/admin/stats")
            if role in ["boss", "admin"]:
                self._assert_allowed_unbuilt(role, res, "GET /api/admin/stats")
            else:
                self._assert_forbidden(role, res, "GET /api/admin/stats")

    def test_criterion_6_operator_cannot_retrieve_sheets_or_excel(self):
        """Criterion 6: An operator cannot retrieve any sheet's numbers or Excel file through any endpoint."""
        op_client = self.clients["operator"]
        for endpoint in [
            "/api/sheets",
            "/api/sheets/1",
            "/api/sheets/1/excel",
            "/api/shifts/excel",
            "/api/sheets/1/template",
        ]:
            res = op_client.get(endpoint)
            self._assert_forbidden("operator", res, f"Endpoint {endpoint}")
