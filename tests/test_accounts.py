from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from hasc.db import (
    create_event,
    create_session,
    create_user,
    get_connection,
    get_session,
    get_user_by_username,
    list_events,
)
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestAccounts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        self.client = TestClient(self.app)

        conn = get_connection(self.db_path)
        # Create test users
        self.admin_id = create_user(
            conn,
            username="admin_user",
            password_hash=hash_password("admin_pass"),
            role="admin",
            active=1,
            must_change_password=0,
        )
        self.op_id = create_user(
            conn,
            username="operator1",
            password_hash=hash_password("op_pass"),
            role="operator",
            active=1,
            must_change_password=0,
        )
        self.temp_user_id = create_user(
            conn,
            username="new_operator",
            password_hash=hash_password("temp_pass"),
            role="operator",
            active=1,
            must_change_password=1,
        )
        self.deactivated_id = create_user(
            conn,
            username="deactivated_user",
            password_hash=hash_password("pass123"),
            role="employee",
            active=0,
            must_change_password=0,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_api_login_success_and_cookie_attributes(self):
        res = self.client.post(
            "/api/login",
            json={"username": "operator1", "password": "op_pass"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["role"], "operator")
        self.assertFalse(data["must_change_password"])

        # Check cookie attributes
        cookie_header = res.headers.get("set-cookie", "")
        self.assertIn("hasc_session=", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("samesite=lax", cookie_header.lower())

    def test_api_login_wrong_credentials(self):
        res = self.client.post(
            "/api/login",
            json={"username": "operator1", "password": "wrong_password"},
        )
        self.assertEqual(res.status_code, 401)
        data = res.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "invalid_credentials")
        self.assertTrue(len(data["error"]["message"]) > 0)

    def test_criterion_4_deactivated_account_cannot_login_and_past_events_remain(self):
        """Criterion 4: A deactivated account cannot log in, and its past events remain."""
        conn = get_connection(self.db_path)
        # Record a past event for this user
        create_event(conn, user_id=self.deactivated_id, action="test_action", detail="past event")

        # Attempt API login
        res = self.client.post(
            "/api/login",
            json={"username": "deactivated_user", "password": "pass123"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "account_deactivated")

        # Attempt HTML login
        res_html = self.client.post(
            "/login",
            data={"username": "deactivated_user", "password": "pass123"},
        )
        self.assertEqual(res_html.status_code, 403)
        self.assertIn("vô hiệu hóa", res_html.text)

        # Verify past events still remain intact
        events = list_events(conn, user_id=self.deactivated_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "test_action")

    def test_criterion_1_temporary_password_must_change_before_anything_else(self):
        """Criterion 1: A new account must change its temporary password before doing anything else."""
        # 1. Login with temporary password
        res = self.client.post(
            "/api/login",
            json={"username": "new_operator", "password": "temp_pass"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["must_change_password"])

        # 2. Try to access an endpoint (e.g. /api/limits) - must be forbidden with 403
        res_limits = self.client.get("/api/limits")
        self.assertEqual(res_limits.status_code, 403)
        self.assertEqual(res_limits.json()["error"]["code"], "password_change_required")

        # 3. HTML navigation should redirect to /change-password
        res_html = self.client.get("/", follow_redirects=False)
        self.assertIn(res_html.status_code, (302, 303, 307))
        self.assertEqual(res_html.headers["location"], "/change-password")

        # 4. Change password via POST /api/password
        res_pw = self.client.post(
            "/api/password",
            json={"old": "temp_pass", "new": "NewSecurePassword456!"},
        )
        self.assertEqual(res_pw.status_code, 204)

        # 5. Now limits endpoint is accessible
        res_limits_after = self.client.get("/api/limits")
        self.assertEqual(res_limits_after.status_code, 200)

    def test_criterion_3_admin_logout_ends_sessions_on_next_request(self):
        """Criterion 3: The admin's 'log out' ends that account's sessions on its next request."""
        # 1. Operator logs in with client1
        client1 = TestClient(self.app)
        res_op = client1.post("/api/login", json={"username": "operator1", "password": "op_pass"})
        self.assertEqual(res_op.status_code, 200)

        # Verify client1 works
        res_limits1 = client1.get("/api/limits")
        self.assertEqual(res_limits1.status_code, 200)

        # 2. Admin logs in with client_admin
        client_admin = TestClient(self.app)
        res_admin = client_admin.post("/api/login", json={"username": "admin_user", "password": "admin_pass"})
        self.assertEqual(res_admin.status_code, 200)

        # 3. Admin calls POST /api/admin/users/{id}/logout
        res_logout = client_admin.post(f"/api/admin/users/{self.op_id}/logout")
        self.assertEqual(res_logout.status_code, 204)

        # 4. Operator's next request must fail with 401 unauthorized
        res_limits2 = client1.get("/api/limits")
        self.assertEqual(res_limits2.status_code, 401)
        self.assertEqual(res_limits2.json()["error"]["code"], "unauthorized")

    def test_user_logout_ends_session(self):
        res = self.client.post("/api/login", json={"username": "operator1", "password": "op_pass"})
        self.assertEqual(res.status_code, 200)

        # Verify active
        self.assertEqual(self.client.get("/api/limits").status_code, 200)

        # Logout via API
        res_logout = self.client.post("/api/logout")
        self.assertIn(res_logout.status_code, (200, 204))

        # Next request must be 401
        self.assertEqual(self.client.get("/api/limits").status_code, 401)

    def test_expired_session_in_db_returns_401(self):
        conn = get_connection(self.db_path)
        past_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        create_session(conn, "expired_session_123", self.op_id, expires_at=past_time)

        self.client.cookies.set("hasc_session", "expired_session_123")
        res = self.client.get("/api/limits")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["error"]["code"], "unauthorized")
