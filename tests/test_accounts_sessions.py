from datetime import datetime, timedelta, timezone
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from hasc.db import (
    create_session,
    create_user,
    get_connection,
    get_user_by_id,
    get_user_by_username,
)
from hasc.web.app import create_app
from hasc.web.security import (
    hash_password,
    is_online,
    now,
    temp_password_expires_at,
)


class TestAccountsSessions(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        self.client = TestClient(self.app)

        conn = get_connection(self.db_path)
        # Create an initial admin user
        self.admin_id = create_user(
            conn,
            username="admin_user",
            password_hash=hash_password("admin_pass"),
            role="admin",
            active=1,
            must_change_password=0,
            is_owner=1,
        )

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def test_criterion_2_temporary_password_first_login_and_redirects(self):
        """Criterion 2: The new user logs in with it and lands on the change-password page.
        Any other page redirects back there, any other API call returns 403 password_change_required,
        and logging in again with the temporary password before changing it still works.
        """
        conn = get_connection(self.db_path)
        user_id = create_user(
            conn,
            username="temp_employee",
            password_hash=hash_password("first_pass"),
            role="employee",
            active=1,
            must_change_password=1,
            temp_password_expires_at=temp_password_expires_at(),
        )

        user_client = TestClient(self.app)

        # 1. Login with temporary password via POST /login -> lands on /change-password (303)
        res_login = user_client.post(
            "/login",
            data={"username": "temp_employee", "password": "first_pass"},
            follow_redirects=False,
        )
        self.assertEqual(res_login.status_code, 303)
        self.assertEqual(res_login.headers["location"], "/change-password")

        # 2. While temporary password is active:
        # GET /login is NOT redirected to /change-password
        res_get_login = user_client.get("/login", follow_redirects=False)
        self.assertEqual(res_get_login.status_code, 200)

        # Logging in again with temporary password still works
        res_login_again = user_client.post(
            "/login",
            data={"username": "temp_employee", "password": "first_pass"},
            follow_redirects=False,
        )
        self.assertEqual(res_login_again.status_code, 303)
        self.assertEqual(res_login_again.headers["location"], "/change-password")

        # 3. Any other page redirects to /change-password with 303
        for page in ("/", "/office", "/office/sheets"):
            res_page = user_client.get(page, follow_redirects=False)
            self.assertEqual(
                res_page.status_code,
                303,
                f"Page {page} should redirect to /change-password with 303",
            )
            self.assertEqual(res_page.headers["location"], "/change-password")

        # 4. Any other API call returns 403 password_change_required
        res_api_limits = user_client.get("/api/limits")
        self.assertEqual(res_api_limits.status_code, 403)
        self.assertEqual(
            res_api_limits.json()["error"]["code"],
            "password_change_required",
        )

        res_api_sheets = user_client.get("/api/sheets")
        self.assertEqual(res_api_sheets.status_code, 403)
        self.assertEqual(
            res_api_sheets.json()["error"]["code"],
            "password_change_required",
        )

        # 5. Exempt pages:
        # /change-password page renders 200
        res_cp = user_client.get("/change-password")
        self.assertEqual(res_cp.status_code, 200)

        # /logout works
        res_logout = user_client.post("/logout", follow_redirects=False)
        self.assertEqual(res_logout.status_code, 303)
        self.assertEqual(res_logout.headers["location"], "/login")

    def test_criterion_3_user_sets_password_clears_temp_and_reaches_pages(self):
        """Criterion 3: After the user sets a password, the temporary password fails at login,
        and the user reaches the office frame.
        """
        conn = get_connection(self.db_path)
        user_id = create_user(
            conn,
            username="employee3",
            password_hash=hash_password("temp_pwd"),
            role="employee",
            active=1,
            must_change_password=1,
            temp_password_expires_at=temp_password_expires_at(),
        )

        user_client = TestClient(self.app)
        user_client.post("/api/login", json={"username": "employee3", "password": "temp_pwd"})

        # User changes password
        res_change = user_client.post(
            "/api/password",
            json={"old": "temp_pwd", "new": "Permanent123!"},
        )
        self.assertEqual(res_change.status_code, 204)

        # Database state: temp_password_expires_at cleared, must_change_password is 0
        u = get_user_by_id(conn, user_id)
        self.assertEqual(u["must_change_password"], 0)
        self.assertIsNone(u["temp_password_expires_at"])

        # Old temporary password fails at login
        client_new = TestClient(self.app)
        res_old = client_new.post(
            "/api/login",
            json={"username": "employee3", "password": "temp_pwd"},
        )
        self.assertEqual(res_old.status_code, 401)
        self.assertEqual(res_old.json()["error"]["code"], "invalid_credentials")

        # New permanent password works and reaches office pages
        res_new = client_new.post(
            "/api/login",
            json={"username": "employee3", "password": "Permanent123!"},
        )
        self.assertEqual(res_new.status_code, 200)
        self.assertFalse(res_new.json()["must_change_password"])

        res_office = client_new.get("/office", follow_redirects=False)
        self.assertEqual(res_office.status_code, 200)

        # Also test changing password via HTML form POST /change-password
        user2_id = create_user(
            conn,
            username="employee_html",
            password_hash=hash_password("temp_html"),
            role="employee",
            active=1,
            must_change_password=1,
            temp_password_expires_at=temp_password_expires_at(),
        )
        client_html = TestClient(self.app)
        client_html.post("/login", data={"username": "employee_html", "password": "temp_html"})
        res_cp_html = client_html.post(
            "/change-password",
            data={
                "old_password": "temp_html",
                "new_password": "NewPermanent456!",
                "confirm_password": "NewPermanent456!",
            },
            follow_redirects=False,
        )
        self.assertEqual(res_cp_html.status_code, 303)
        self.assertEqual(res_cp_html.headers["location"], "/")

        u2 = get_user_by_id(conn, user2_id)
        self.assertEqual(u2["must_change_password"], 0)
        self.assertIsNone(u2["temp_password_expires_at"])

    def test_criterion_4_temporary_password_expires_after_72_hours(self):
        """Criterion 4: A temporary password not used within 72 hours fails at login;
        a reset makes a new one that works.
        """
        t0 = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
        with patch("hasc.web.security.now", return_value=t0):
            # Admin creates user via API
            admin_client = TestClient(self.app)
            admin_client.post("/api/login", json={"username": "admin_user", "password": "admin_pass"})

            res_create = admin_client.post(
                "/api/admin/users",
                json={"username": "emp_expiry", "role": "employee"},
            )
            self.assertEqual(res_create.status_code, 201)
            temp_pwd = res_create.json()["temporary_password"]
            emp_id = res_create.json()["id"]

        # 1. Login within 72h succeeds (e.g. at t0 + 71h 59m)
        t_within = t0 + timedelta(hours=71, minutes=59)
        with patch("hasc.web.security.now", return_value=t_within):
            emp_client = TestClient(self.app)
            res_ok = emp_client.post(
                "/api/login",
                json={"username": "emp_expiry", "password": temp_pwd},
            )
            self.assertEqual(res_ok.status_code, 200)

        # 2. Login after 72h fails with Vietnamese message in both POST /login and POST /api/login
        t_expired = t0 + timedelta(hours=72, seconds=1)
        with patch("hasc.web.security.now", return_value=t_expired):
            emp_client = TestClient(self.app)

            # POST /api/login
            res_api = emp_client.post(
                "/api/login",
                json={"username": "emp_expiry", "password": temp_pwd},
            )
            self.assertEqual(res_api.status_code, 401)
            self.assertEqual(res_api.json()["error"]["code"], "temp_password_expired")
            self.assertEqual(
                res_api.json()["error"]["message"],
                "Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.",
            )

            # POST /login (HTML)
            res_html = emp_client.post(
                "/login",
                data={"username": "emp_expiry", "password": temp_pwd},
            )
            self.assertEqual(res_html.status_code, 401)
            self.assertIn("Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.", res_html.text)

        # 3. A reset makes a new one that works
        t_reset = t0 + timedelta(hours=73)
        with patch("hasc.web.security.now", return_value=t_reset):
            admin_client = TestClient(self.app)
            admin_client.post("/api/login", json={"username": "admin_user", "password": "admin_pass"})
            res_patch = admin_client.patch(
                f"/api/admin/users/{emp_id}",
                json={"reset_password": True},
            )
            self.assertEqual(res_patch.status_code, 200)
            new_temp_pwd = res_patch.json()["temporary_password"]
            self.assertNotEqual(new_temp_pwd, temp_pwd)

            # Old temporary password fails
            res_old = emp_client.post(
                "/api/login",
                json={"username": "emp_expiry", "password": temp_pwd},
            )
            self.assertEqual(res_old.status_code, 401)

            # New temporary password works at current time
            res_new = emp_client.post(
                "/api/login",
                json={"username": "emp_expiry", "password": new_temp_pwd},
            )
            self.assertEqual(res_new.status_code, 200)

    def test_criterion_5_reset_logout_role_change_or_deactivation_ends_sessions(self):
        """Criterion 5: A reset, log-out, role change or deactivation ends that account's open sessions:
        its next request goes to the login page.
        """
        conn = get_connection(self.db_path)
        emp_id = create_user(
            conn,
            username="emp5",
            password_hash=hash_password("mypass123"),
            role="employee",
            active=1,
            must_change_password=0,
        )

        admin_client = TestClient(self.app)
        admin_client.post("/api/login", json={"username": "admin_user", "password": "admin_pass"})

        # Case A: Reset password ends session
        emp_client = TestClient(self.app)
        emp_client.post("/api/login", json={"username": "emp5", "password": "mypass123"})
        self.assertEqual(emp_client.get("/office").status_code, 200)

        admin_client.patch(f"/api/admin/users/{emp_id}", json={"reset_password": True})

        # Next page request redirects to /login (303)
        res_after_reset = emp_client.get("/office", follow_redirects=False)
        self.assertEqual(res_after_reset.status_code, 303)
        self.assertEqual(res_after_reset.headers["location"], "/login")
        # Next API request returns 401
        self.assertEqual(emp_client.get("/api/limits").status_code, 401)

        # Restore password for next test
        u = get_user_by_id(conn, emp_id)
        from hasc.db import update_user_password
        update_user_password(conn, emp_id, hash_password("mypass123"), must_change_password=0)

        # Case B: Admin log-out ends session
        emp_client = TestClient(self.app)
        emp_client.post("/api/login", json={"username": "emp5", "password": "mypass123"})
        self.assertEqual(emp_client.get("/office").status_code, 200)

        admin_client.post(f"/api/admin/users/{emp_id}/logout")
        res_after_logout = emp_client.get("/office", follow_redirects=False)
        self.assertEqual(res_after_logout.status_code, 303)
        self.assertEqual(res_after_logout.headers["location"], "/login")

        # Case C: Role change ends session
        emp_client = TestClient(self.app)
        emp_client.post("/api/login", json={"username": "emp5", "password": "mypass123"})
        self.assertEqual(emp_client.get("/office").status_code, 200)

        admin_client.patch(f"/api/admin/users/{emp_id}", json={"role": "boss"})
        res_after_role = emp_client.get("/office", follow_redirects=False)
        self.assertEqual(res_after_role.status_code, 303)
        self.assertEqual(res_after_role.headers["location"], "/login")

        # Case D: Deactivation ends session
        emp_client = TestClient(self.app)
        emp_client.post("/api/login", json={"username": "emp5", "password": "mypass123"})
        self.assertEqual(emp_client.get("/office").status_code, 200)

        admin_client.patch(f"/api/admin/users/{emp_id}", json={"active": 0})
        res_after_deact = emp_client.get("/office", follow_redirects=False)
        self.assertEqual(res_after_deact.status_code, 303)
        self.assertEqual(res_after_deact.headers["location"], "/login")

    def test_criterion_6_office_session_expires_after_8_hours(self):
        """Criterion 6: An office session still expires after 8 hours (spec.md criterion 2 for office stays)."""
        t0 = datetime(2026, 11, 6, 8, 0, 0, tzinfo=timezone.utc)
        conn = get_connection(self.db_path)
        emp_id = create_user(
            conn,
            username="office_worker",
            password_hash=hash_password("pass_office"),
            role="employee",
            active=1,
            must_change_password=0,
        )

        with patch("hasc.web.security.now", return_value=t0):
            emp_client = TestClient(self.app)
            res_login = emp_client.post(
                "/api/login",
                json={"username": "office_worker", "password": "pass_office"},
            )
            self.assertEqual(res_login.status_code, 200)

        # Within 8 hours (7h 59m), session is still valid
        t_valid = t0 + timedelta(hours=7, minutes=59)
        with patch("hasc.web.security.now", return_value=t_valid):
            res_sheets = emp_client.get("/api/sheets")
            self.assertEqual(res_sheets.status_code, 200)
            res_office = emp_client.get("/office", follow_redirects=False)
            self.assertEqual(res_office.status_code, 200)

        # After 8 hours (8h + 1s), session has expired
        t_expired = t0 + timedelta(hours=8, seconds=1)
        with patch("hasc.web.security.now", return_value=t_expired):
            res_sheets_exp = emp_client.get("/api/sheets")
            self.assertEqual(res_sheets_exp.status_code, 401)
            self.assertEqual(res_sheets_exp.json()["error"]["code"], "unauthorized")

            res_page_exp = emp_client.get("/office", follow_redirects=False)
            self.assertEqual(res_page_exp.status_code, 303)
            self.assertEqual(res_page_exp.headers["location"], "/login")

    def test_criterion_7_online_status_and_last_seen_at_throttling(self):
        """Criterion 7: While a user is using the site, the accounts screen shows them online;
        6 minutes after their last request it shows them offline, with that time as last seen.
        Dependency writes last_seen_at at most once per minute.
        """
        conn = get_connection(self.db_path)
        emp_id = create_user(
            conn,
            username="online_user",
            password_hash=hash_password("secret"),
            role="employee",
            active=1,
            must_change_password=0,
        )

        emp_client = TestClient(self.app)
        emp_client.post("/api/login", json={"username": "online_user", "password": "secret"})

        t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)

        # 1. First request at t0: writes last_seen_at = t0
        with patch("hasc.web.security.now", return_value=t0):
            emp_client.get("/api/sheets")
            user_row = get_user_by_id(conn, emp_id)
            self.assertEqual(user_row["last_seen_at"], t0.isoformat())

        # 2. Request at t0 + 30s: < 60 seconds, does NOT write to DB
        t_30s = t0 + timedelta(seconds=30)
        with patch("hasc.web.security.now", return_value=t_30s):
            emp_client.get("/api/sheets")
            user_row = get_user_by_id(conn, emp_id)
            self.assertEqual(user_row["last_seen_at"], t0.isoformat())

        # 3. Request at t0 + 65s: >= 60 seconds, updates last_seen_at in DB
        t_65s = t0 + timedelta(seconds=65)
        with patch("hasc.web.security.now", return_value=t_65s):
            emp_client.get("/api/sheets")
            user_row = get_user_by_id(conn, emp_id)
            self.assertEqual(user_row["last_seen_at"], t_65s.isoformat())

        # 4. Check online status calculation:
        # At t_65s + 4 minutes (4 minutes after last request): is_online is True
        t_online = t_65s + timedelta(minutes=4)
        with patch("hasc.web.security.now", return_value=t_online):
            self.assertTrue(is_online(user_row["last_seen_at"]))

        # At t_65s + 6 minutes (6 minutes after last request): is_online is False
        t_offline = t_65s + timedelta(minutes=6)
        with patch("hasc.web.security.now", return_value=t_offline):
            self.assertFalse(is_online(user_row["last_seen_at"]))
            # and last_seen_at in DB still holds the exact time of the last request
            self.assertEqual(user_row["last_seen_at"], t_65s.isoformat())


if __name__ == "__main__":
    unittest.main()
