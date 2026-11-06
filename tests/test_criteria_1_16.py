"""
Hardening and criteria coverage tests for HASC 1.1.0 (Job A).
Covers acceptance criteria 1 through 16 of spec-1.1.0.md.

Criteria-to-Test Mapping Table
==============================
| Criterion | Spec Requirement Summary | Test Method in this file |
|---|---|---|
| Criterion 1 | Admin adds employee; appears active; temp password shown once; reloading page does not show it | test_criterion_1_add_employee_temporary_password_shown_once_and_absent_on_reload |
| Criterion 2 | New user logs in with temp password, lands on change-password; other pages 303 redirect; other API calls return 403 password_change_required; relogin with temp password before change works | test_criterion_2_temporary_password_first_login_303_redirects_and_403_api |
| Criterion 3 | After user sets password, temp password fails at login, user reaches office frame | test_criterion_3_password_change_invalidates_temp_password_and_reaches_office_frame |
| Criterion 4 | Temp password not used within 72 h fails at login; reset makes a new one that works | test_criterion_4_temporary_password_72h_boundary_and_admin_reset |
| Criterion 5 | Reset, log-out, role change or deactivation ends account's open sessions: next request to login page | test_criterion_5_session_ending_on_reset_logout_role_change_and_deactivation |
| Criterion 6 | Office session still expires after 8 hours | test_criterion_6_office_session_expires_after_8_hours |
| Criterion 7 | While using site shows online; 6 min after last request shows offline with last seen time | test_criterion_7_online_status_and_last_seen_threshold |
| Criterion 8 | Non-owner admin cannot give/remove admin, reset/logout/deactivate/change role of admin; nobody deactivates/demotes owner; nobody acts on self from screen; list marks owner | test_criterion_8_owner_rules_every_action_and_is_owner_flag |
| Criterion 9 | Hung cannot be created when hung exists, active or deactivated; hung can log in typing Hung | test_criterion_9_username_case_insensitivity_and_no_reuse |
| Criterion 10 | Username with space, Vietnamese letter, <3 or >32 chars refused with Vietnamese message | test_criterion_10_username_format_validation_with_vietnamese_errors |
| Criterion 11 | Each account action writes history entry admin can see; employee/boss gets 403 | test_criterion_11_account_action_history_and_role_access |
| Criterion 12 | Role picker offers no operator; API refuses operator in create/change role; existing operator logs in and uploads | test_criterion_12_operator_role_refused_in_picker_and_api |
| Criterion 13 | Employee, boss, admin can upload photo from Tải ảnh lên, sheet records them as uploader | test_criterion_13_employee_boss_and_admin_can_upload_photo |
| Criterion 14 | Sidebar shows Tài khoản to admins only; non-admin requesting accounts page or GET /api/admin/users gets 403 | test_criterion_14_sidebar_shows_accounts_to_admins_only_and_403_for_others |
| Criterion 15 | Accounts screen and frame work at phone width (360 px): viewport meta tag and folded menu markup | test_criterion_15_phone_width_viewport_and_folded_menu_markup |
| Criterion 16 | Fresh DB with create_user.py --owner has one owner; running again moves flag; migration marks oldest active admin; second owner rejected | test_criterion_16_create_user_owner_flag_transfer_and_migration_oldest_admin |
| Migration | Mixed-case usernames migration without collision succeeds; with collision stops and names both | test_migration_mixed_case_usernames_without_conflicts_and_with_conflicts |
| Boundary | Exact 72 h boundary behavior (71h59m succeeds, 72h01s fails with 401 & Vietnamese text) | test_temporary_password_72h_exact_boundary |
| Auth Flow | 303 redirect for HTML pages vs 403 JSON for API calls under temporary password | test_temporary_password_303_vs_403_behavior |
"""

from datetime import datetime, timedelta, timezone
import gc
import io
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from hasc.db import (
    MigrationError,
    create_session,
    create_sheet,
    create_user,
    get_applied_migrations,
    get_connection,
    get_migrations_dir,
    get_user_by_id,
    get_user_by_username,
    migrate,
    set_user_active,
)
from hasc.web.accounts import set_or_create_owner
from hasc.web.app import create_app
from hasc.web.cli import main as cli_main
from hasc.web.security import hash_password, now, verify_password


def make_test_table_jpeg() -> bytes:
    """Generate a valid test image with table grid lines for photo upload testing."""
    img = Image.new("L", (900, 700), 255)
    for y in range(50, 650, 30):
        img.paste(0, (50, y, 850, y + 2))
    for x in range(50, 850, 50):
        img.paste(0, (x, 50, x + 2, 650))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class TestCriteriaCoverageHardening(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos_dir = Path(self.temp_dir.name) / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)
        self.client = TestClient(self.app)

        conn = get_connection(self.db_path)
        try:
            # 1. Create owner admin (is_owner = 1)
            self.owner_id, _, _ = set_or_create_owner(conn, "owner_admin", password="OwnerPass123")
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, is_owner = 1 WHERE id = ?",
                (hash_password("OwnerPass123"), self.owner_id),
            )

            # 2. Create non-owner admin (is_owner = 0)
            self.admin_id = create_user(
                conn,
                username="plain_admin",
                password_hash=hash_password("PlainAdminPass123"),
                role="admin",
                active=1,
                must_change_password=0,
                is_owner=0,
            )

            # 3. Create second non-owner admin for isolation testing
            self.admin3_id = create_user(
                conn,
                username="other_admin",
                password_hash=hash_password("OtherAdminPass123"),
                role="admin",
                active=1,
                must_change_password=0,
                is_owner=0,
            )

            # 4. Create employee, boss, and operator accounts
            self.employee_id = create_user(
                conn,
                username="emp_user",
                password_hash=hash_password("EmployeePass123"),
                role="employee",
                active=1,
                must_change_password=0,
                is_owner=0,
            )
            self.boss_id = create_user(
                conn,
                username="boss_user",
                password_hash=hash_password("BossPass123"),
                role="boss",
                active=1,
                must_change_password=0,
                is_owner=0,
            )
            self.operator_id = create_user(
                conn,
                username="op_user",
                password_hash=hash_password("OperatorPass123"),
                role="operator",
                active=1,
                must_change_password=0,
                is_owner=0,
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def _login(self, client: TestClient, username: str, password: str) -> None:
        """Helper to log in a client session."""
        res = client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, f"Login failed for {username}: {res.text}")

    # =========================================================================
    # Acceptance Criteria 1 to 16
    # =========================================================================

    def test_criterion_1_add_employee_temporary_password_shown_once_and_absent_on_reload(self):
        """Criterion 1: The admin adds an employee. The account appears in the list as active,
        and a temporary password is shown once; reloading the page does not show it again.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        # 1. Admin adds an employee from HTML screen (POST /admin/accounts)
        post_res = admin_client.post(
            "/admin/accounts",
            data={"action": "add", "username": "emp_new1", "role": "employee"},
        )
        self.assertEqual(post_res.status_code, 200)
        content_html = post_res.text

        # Account appears in the list as active ("Hoạt động")
        self.assertIn("emp_new1", content_html)
        self.assertIn("Hoạt động", content_html)

        # Temporary password is shown once
        self.assertIn("Mật khẩu tạm thời", content_html)
        self.assertIn("temp-password-box", content_html)

        # Extract the temporary password text shown in the banner
        temp_pwd_marker = 'class="temp-password num"'
        self.assertIn(temp_pwd_marker, content_html)
        temp_pwd = content_html.split(temp_pwd_marker)[1].split(">")[1].split("<")[0].strip()
        self.assertTrue(len(temp_pwd) >= 8)

        # 2. Reloading the page (GET /admin/accounts) does not show it again
        reload_res = admin_client.get("/admin/accounts")
        self.assertEqual(reload_res.status_code, 200)
        reload_html = reload_res.text

        # Account is still in the list as active
        self.assertIn("emp_new1", reload_html)
        self.assertIn("Hoạt động", reload_html)

        # Temporary password banner is NOT present on reload
        self.assertNotIn("temp-password-box", reload_html)
        self.assertNotIn(temp_pwd, reload_html)

    def test_criterion_2_temporary_password_first_login_303_redirects_and_403_api(self):
        """Criterion 2: The new user logs in with it and lands on the change-password page.
        Any other page redirects back there, any other API call returns 403 password_change_required,
        and logging in again with the temporary password before changing it still works.
        """
        # Create user via API with temporary password
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")
        create_res = admin_client.post(
            "/api/admin/users",
            json={"username": "emp_temp2", "role": "employee"},
        )
        self.assertEqual(create_res.status_code, 201)
        temp_pwd = create_res.json()["temporary_password"]

        # 1. New user logs in with temporary password on HTML login page
        new_client = TestClient(self.app)
        login_res = new_client.post(
            "/login",
            data={"username": "emp_temp2", "password": temp_pwd},
            follow_redirects=False,
        )
        # Lands on the change-password page via 303 redirect
        self.assertEqual(login_res.status_code, 303)
        self.assertEqual(login_res.headers.get("location"), "/change-password")

        # Following redirect to /change-password succeeds with 200
        change_page_res = new_client.get("/change-password")
        self.assertEqual(change_page_res.status_code, 200)
        self.assertIn("Đổi mật khẩu", change_page_res.text)

        # 2. Any other page redirects back to /change-password with 303
        for path in ("/", "/office", "/admin/accounts"):
            page_res = new_client.get(path, follow_redirects=False)
            self.assertEqual(
                page_res.status_code,
                303,
                f"Page {path} should 303 redirect to /change-password, got {page_res.status_code}",
            )
            self.assertEqual(page_res.headers.get("location"), "/change-password")

        # 3. Any other API call returns 403 password_change_required
        for api_path in ("/api/sheets", "/api/admin/users", "/api/days/2026-11-06/report"):
            api_res = new_client.get(api_path)
            self.assertEqual(
                api_res.status_code,
                403,
                f"API {api_path} should return 403, got {api_res.status_code}",
            )
            err_body = api_res.json()
            self.assertEqual(err_body.get("error", {}).get("code"), "password_change_required")

        # 4. Logging in again with the temporary password before changing it still works
        second_client = TestClient(self.app)
        second_login_res = second_client.post(
            "/login",
            data={"username": "emp_temp2", "password": temp_pwd},
            follow_redirects=False,
        )
        self.assertEqual(second_login_res.status_code, 303)
        self.assertEqual(second_login_res.headers.get("location"), "/change-password")

    def test_criterion_3_password_change_invalidates_temp_password_and_reaches_office_frame(self):
        """Criterion 3: After the user sets a password, the temporary password fails at login,
        and the user reaches the office frame.
        """
        # Create user with temp password
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")
        create_res = admin_client.post(
            "/api/admin/users",
            json={"username": "emp_temp3", "role": "employee"},
        )
        self.assertEqual(create_res.status_code, 201)
        temp_pwd = create_res.json()["temporary_password"]

        # Log in with temp password
        user_client = TestClient(self.app)
        user_client.post(
            "/login",
            data={"username": "emp_temp3", "password": temp_pwd},
            follow_redirects=False,
        )

        # Set a new personal password on /change-password
        change_res = user_client.post(
            "/change-password",
            data={
                "old_password": temp_pwd,
                "new_password": "NewPermanentPass456",
                "confirm_password": "NewPermanentPass456",
            },
            follow_redirects=False,
        )
        self.assertEqual(change_res.status_code, 303)
        self.assertEqual(change_res.headers.get("location"), "/")

        # 1. Temporary password fails at login
        client_temp_retry = TestClient(self.app)
        retry_html = client_temp_retry.post(
            "/login",
            data={"username": "emp_temp3", "password": temp_pwd},
        )
        self.assertEqual(retry_html.status_code, 401)

        retry_api = client_temp_retry.post(
            "/api/login",
            json={"username": "emp_temp3", "password": temp_pwd},
        )
        self.assertEqual(retry_api.status_code, 401)

        # 2. Login with new password succeeds and user reaches the office frame
        client_new = TestClient(self.app)
        login_new = client_new.post(
            "/login",
            data={"username": "emp_temp3", "password": "NewPermanentPass456"},
            follow_redirects=False,
        )
        self.assertEqual(login_new.status_code, 303)
        self.assertEqual(login_new.headers.get("location"), "/")

        frame_res = client_new.get("/")
        self.assertEqual(frame_res.status_code, 200)
        # Office frame elements present: user name, logout link, office navigation
        self.assertIn("emp_temp3", frame_res.text)
        self.assertIn("/logout", frame_res.text)
        self.assertIn("Hồ sơ", frame_res.text)
        self.assertIn("Tải ảnh lên", frame_res.text)

    def test_criterion_4_temporary_password_72h_boundary_and_admin_reset(self):
        """Criterion 4: A temporary password not used within 72 hours fails at login;
        a reset makes a new one that works.
        """
        t0 = datetime(2026, 11, 6, 8, 0, 0, tzinfo=timezone.utc)
        with patch("hasc.web.security.now", return_value=t0):
            admin_client = TestClient(self.app)
            self._login(admin_client, "owner_admin", "OwnerPass123")
            create_res = admin_client.post(
                "/api/admin/users",
                json={"username": "emp_expire4", "role": "employee"},
            )
            self.assertEqual(create_res.status_code, 201)
            temp_pwd1 = create_res.json()["temporary_password"]
            emp_id = create_res.json()["id"]

        # Within 72h (t0 + 71h 59m): login succeeds
        t_before = t0 + timedelta(hours=71, minutes=59)
        with patch("hasc.web.security.now", return_value=t_before):
            c_ok = TestClient(self.app)
            res_ok = c_ok.post("/api/login", json={"username": "emp_expire4", "password": temp_pwd1})
            self.assertEqual(res_ok.status_code, 200)

        # Beyond 72h (t0 + 72h 01m): login fails with Vietnamese message
        t_after = t0 + timedelta(hours=72, minutes=1)
        with patch("hasc.web.security.now", return_value=t_after):
            c_expired = TestClient(self.app)
            # API login fails 401
            res_api = c_expired.post("/api/login", json={"username": "emp_expire4", "password": temp_pwd1})
            self.assertEqual(res_api.status_code, 401)
            self.assertIn("Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.", res_api.json()["error"]["message"])

            # HTML login fails 401
            res_html = c_expired.post("/login", data={"username": "emp_expire4", "password": temp_pwd1})
            self.assertEqual(res_html.status_code, 401)
            self.assertIn("Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.", res_html.text)

            # Admin logs in at t_after and resets the account password via PATCH reset_password: True
            admin_after = TestClient(self.app)
            self._login(admin_after, "owner_admin", "OwnerPass123")
            reset_res = admin_after.patch(f"/api/admin/users/{emp_id}", json={"reset_password": True})
            self.assertEqual(reset_res.status_code, 200)
            temp_pwd2 = reset_res.json()["temporary_password"]
            self.assertNotEqual(temp_pwd1, temp_pwd2)

            # New temporary password works immediately
            c_reset = TestClient(self.app)
            login_reset = c_reset.post("/api/login", json={"username": "emp_expire4", "password": temp_pwd2})
            self.assertEqual(login_reset.status_code, 200)
            self.assertTrue(login_reset.json()["must_change_password"])

    def test_criterion_5_session_ending_on_reset_logout_role_change_and_deactivation(self):
        """Criterion 5: A reset, log-out, role change or deactivation ends that account's open sessions:
        its next request goes to the login page.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        actions = [
            ("reset", lambda u_id: admin_client.patch(f"/api/admin/users/{u_id}", json={"reset_password": True})),
            ("logout", lambda u_id: admin_client.post(f"/api/admin/users/{u_id}/logout")),
            ("role", lambda u_id: admin_client.patch(f"/api/admin/users/{u_id}", json={"role": "boss"})),
            ("deactivate", lambda u_id: admin_client.patch(f"/api/admin/users/{u_id}", json={"active": 0})),
        ]

        for action_name, action_fn in actions:
            with self.subTest(action=action_name):
                # Create a fresh active user with settled password
                conn = get_connection(self.db_path)
                test_username = f"user_sess_{action_name}"
                u_id = create_user(
                    conn,
                    username=test_username,
                    password_hash=hash_password("Pass123"),
                    role="employee",
                    active=1,
                    must_change_password=0,
                )
                conn.close()

                # User logs in and establishes active session
                user_client = TestClient(self.app)
                login_res = user_client.post(
                    "/login",
                    data={"username": test_username, "password": "Pass123"},
                    follow_redirects=False,
                )
                self.assertEqual(login_res.status_code, 303)

                # Active request works
                before_req = user_client.get("/")
                self.assertEqual(before_req.status_code, 200)

                # Admin executes the session-terminating action
                action_res = action_fn(u_id)
                self.assertIn(action_res.status_code, (200, 204), f"Admin action {action_name} failed: {action_res.text}")

                # Next request goes to login page (303 redirect)
                after_req = user_client.get("/", follow_redirects=False)
                self.assertEqual(
                    after_req.status_code,
                    303,
                    f"Action {action_name} did not terminate session; got {after_req.status_code}",
                )
                self.assertEqual(after_req.headers.get("location"), "/login")

    def test_criterion_6_office_session_expires_after_8_hours(self):
        """Criterion 6: An office session still expires after 8 hours (spec.md criterion 2 for office stays)."""
        t0 = datetime(2026, 11, 6, 8, 0, 0, tzinfo=timezone.utc)
        with patch("hasc.web.security.now", return_value=t0):
            emp_client = TestClient(self.app)
            self._login(emp_client, "emp_user", "EmployeePass123")

            # At t0: session is valid
            res0 = emp_client.get("/")
            self.assertEqual(res0.status_code, 200)

        # At t0 + 7 hours 59 minutes: session still valid
        t_active = t0 + timedelta(hours=7, minutes=59)
        with patch("hasc.web.security.now", return_value=t_active):
            res_active = emp_client.get("/")
            self.assertEqual(res_active.status_code, 200)

        # At t0 + 8 hours 01 minute: session expired -> redirects to /login
        t_expired = t0 + timedelta(hours=8, minutes=1)
        with patch("hasc.web.security.now", return_value=t_expired):
            res_expired = emp_client.get("/", follow_redirects=False)
            self.assertEqual(res_expired.status_code, 303)
            self.assertEqual(res_expired.headers.get("location"), "/login")

    def test_criterion_7_online_status_and_last_seen_threshold(self):
        """Criterion 7: While a user is using the site, the accounts screen shows them online;
        6 minutes after their last request it shows them offline, with that time as last seen.
        """
        t0 = datetime(2026, 11, 6, 9, 0, 0, tzinfo=timezone.utc)
        admin_client = TestClient(self.app)

        with patch("hasc.web.security.now", return_value=t0):
            self._login(admin_client, "owner_admin", "OwnerPass123")

            emp_client = TestClient(self.app)
            self._login(emp_client, "emp_user", "EmployeePass123")
            emp_client.get("/")  # Active request sets last_seen_at

            # Accounts screen at t0 shows emp_user online
            list_res0 = admin_client.get("/api/admin/users")
            self.assertEqual(list_res0.status_code, 200)
            users0 = {u["username"]: u for u in list_res0.json()}
            self.assertTrue(users0["emp_user"]["online"])
            last_seen_time = users0["emp_user"]["last_seen_at"]
            self.assertIsNotNone(last_seen_time)

        # At t0 + 4 minutes (<= 5 min): still online
        t_4m = t0 + timedelta(minutes=4)
        with patch("hasc.web.security.now", return_value=t_4m):
            list_res_4m = admin_client.get("/api/admin/users")
            users_4m = {u["username"]: u for u in list_res_4m.json()}
            self.assertTrue(users_4m["emp_user"]["online"])

        # At t0 + 6 minutes (> 5 min without new request): offline, last seen unchanged
        t_6m = t0 + timedelta(minutes=6)
        with patch("hasc.web.security.now", return_value=t_6m):
            list_res_6m = admin_client.get("/api/admin/users")
            users_6m = {u["username"]: u for u in list_res_6m.json()}
            self.assertFalse(users_6m["emp_user"]["online"])
            self.assertEqual(users_6m["emp_user"]["last_seen_at"], last_seen_time)

    def test_criterion_8_owner_rules_every_action_and_is_owner_flag(self):
        """Criterion 8: A non-owner admin cannot give or remove the admin role,
        or reset, log out, deactivate or change the role of any admin account (the owner and themselves included):
        the API returns 403 and nothing changes. Nobody can deactivate or demote the owner,
        and nobody can change their own role, deactivate, reset or log out themselves from the screen.
        The list marks the owner (is_owner in the API).
        """
        owner_client = TestClient(self.app)
        self._login(owner_client, "owner_admin", "OwnerPass123")

        plain_admin_client = TestClient(self.app)
        self._login(plain_admin_client, "plain_admin", "PlainAdminPass123")

        # ---------------------------------------------------------------------
        # 1. Action: ADD ACCOUNT
        # ---------------------------------------------------------------------
        # Non-owner admin cannot give admin role
        res_plain_add_admin = plain_admin_client.post(
            "/api/admin/users",
            json={"username": "new_admin_attempt", "role": "admin"},
        )
        self.assertEqual(res_plain_add_admin.status_code, 403)

        # Owner CAN add admin
        res_owner_add_admin = owner_client.post(
            "/api/admin/users",
            json={"username": "new_admin_ok", "role": "admin"},
        )
        self.assertEqual(res_owner_add_admin.status_code, 201)
        sub_admin_id = res_owner_add_admin.json()["id"]

        # ---------------------------------------------------------------------
        # 2. Action: RESET PASSWORD (via PATCH reset_password: True)
        # ---------------------------------------------------------------------
        # Self-reset refused (owner and plain admin)
        self.assertEqual(owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"reset_password": True}).status_code, 403)
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"reset_password": True}).status_code, 403)

        # Nobody can reset owner
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"reset_password": True}).status_code, 403)

        # Non-owner admin cannot reset any admin
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"reset_password": True}).status_code, 403)

        # Plain admin CAN reset employee/boss
        res_plain_reset_emp = plain_admin_client.patch(f"/api/admin/users/{self.employee_id}", json={"reset_password": True})
        self.assertEqual(res_plain_reset_emp.status_code, 200)
        self.assertIn("temporary_password", res_plain_reset_emp.json())

        # Owner CAN reset non-owner admin (other_admin)
        res_owner_reset = owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"reset_password": True})
        self.assertEqual(res_owner_reset.status_code, 200)
        self.assertIn("temporary_password", res_owner_reset.json())

        # ---------------------------------------------------------------------
        # 3. Action: CHANGE ROLE
        # ---------------------------------------------------------------------
        # Nobody can demote owner (neither self nor plain admin)
        self.assertEqual(owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"role": "employee"}).status_code, 403)
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"role": "employee"}).status_code, 403)

        # Nobody can change own role
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"role": "boss"}).status_code, 403)

        # Non-owner admin cannot change role of any admin
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{sub_admin_id}", json={"role": "boss"}).status_code, 403)

        # Non-owner admin cannot promote anyone to admin
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.employee_id}", json={"role": "admin"}).status_code, 403)

        # Owner CAN promote employee to admin and demote non-owner admin to boss
        self.assertEqual(owner_client.patch(f"/api/admin/users/{self.employee_id}", json={"role": "admin"}).status_code, 200)
        self.assertEqual(owner_client.patch(f"/api/admin/users/{sub_admin_id}", json={"role": "boss"}).status_code, 200)

        # Plain admin CAN change role between employee and boss
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.boss_id}", json={"role": "employee"}).status_code, 200)

        # ---------------------------------------------------------------------
        # 4. Action: DEACTIVATE
        # ---------------------------------------------------------------------
        # Nobody can deactivate owner (neither self nor plain admin)
        self.assertEqual(owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 0}).status_code, 403)
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 0}).status_code, 403)

        # Nobody can deactivate self
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"active": 0}).status_code, 403)

        # Non-owner admin cannot deactivate any admin
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 0}).status_code, 403)

        # Owner CAN deactivate non-owner admin
        self.assertEqual(owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 0}).status_code, 200)

        # Plain admin CAN deactivate boss/employee
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.boss_id}", json={"active": 0}).status_code, 200)

        # ---------------------------------------------------------------------
        # 5. Action: REACTIVATE
        # ---------------------------------------------------------------------
        # Nobody can reactivate self via admin endpoint
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"active": 1}).status_code, 403)

        # Non-owner admin cannot reactivate an admin account
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 1}).status_code, 403)

        # Owner CAN reactivate admin
        self.assertEqual(owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 1}).status_code, 200)

        # Plain admin CAN reactivate boss/employee
        self.assertEqual(plain_admin_client.patch(f"/api/admin/users/{self.boss_id}", json={"active": 1}).status_code, 200)

        # ---------------------------------------------------------------------
        # 6. Action: LOG OUT
        # ---------------------------------------------------------------------
        # Nobody can log out owner via admin endpoint
        self.assertEqual(owner_client.post(f"/api/admin/users/{self.owner_id}/logout").status_code, 403)
        self.assertEqual(plain_admin_client.post(f"/api/admin/users/{self.owner_id}/logout").status_code, 403)

        # Nobody can log out self via admin endpoint
        self.assertEqual(plain_admin_client.post(f"/api/admin/users/{self.admin_id}/logout").status_code, 403)

        # Non-owner admin cannot log out other admin
        self.assertEqual(plain_admin_client.post(f"/api/admin/users/{self.admin3_id}/logout").status_code, 403)

        # Owner CAN log out other admin
        self.assertEqual(owner_client.post(f"/api/admin/users/{self.admin3_id}/logout").status_code, 204)

        # Plain admin CAN log out employee/boss
        self.assertEqual(plain_admin_client.post(f"/api/admin/users/{self.boss_id}/logout").status_code, 204)

        # ---------------------------------------------------------------------
        # 7. Check is_owner mark in the user list
        # ---------------------------------------------------------------------
        list_res = owner_client.get("/api/admin/users")
        self.assertEqual(list_res.status_code, 200)
        users = {u["username"]: u for u in list_res.json()}
        self.assertEqual(users["owner_admin"]["is_owner"], 1)
        self.assertEqual(users["plain_admin"]["is_owner"], 0)

    def test_criterion_9_username_case_insensitivity_and_no_reuse(self):
        """Criterion 9: Hung cannot be created when hung exists, active or deactivated,
        and hung can log in by typing Hung.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        # 1. Create hung
        create_res = admin_client.post(
            "/api/admin/users",
            json={"username": "hung", "role": "employee"},
        )
        self.assertEqual(create_res.status_code, 201)
        temp_pwd = create_res.json()["temporary_password"]
        hung_id = create_res.json()["id"]

        # 2. Hung cannot be created when hung is active
        res_dup_active = admin_client.post(
            "/api/admin/users",
            json={"username": "Hung", "role": "employee"},
        )
        self.assertEqual(res_dup_active.status_code, 400)
        self.assertEqual(res_dup_active.json()["error"]["code"], "duplicate_username")

        # 3. Deactivate hung
        deact_res = admin_client.patch(f"/api/admin/users/{hung_id}", json={"active": 0})
        self.assertEqual(deact_res.status_code, 200)

        # Hung cannot be created even when hung is deactivated (never reused)
        res_dup_deact = admin_client.post(
            "/api/admin/users",
            json={"username": "Hung", "role": "employee"},
        )
        self.assertEqual(res_dup_deact.status_code, 400)
        self.assertEqual(res_dup_deact.json()["error"]["code"], "duplicate_username")

        # 4. Reactivate hung
        react_res = admin_client.patch(f"/api/admin/users/{hung_id}", json={"active": 1})
        self.assertEqual(react_res.status_code, 200)

        # 5. hung can log in by typing Hung
        hung_client = TestClient(self.app)
        login_res = hung_client.post(
            "/api/login",
            json={"username": "Hung", "password": temp_pwd},
        )
        self.assertEqual(login_res.status_code, 200)

    def test_criterion_10_username_format_validation_with_vietnamese_errors(self):
        """Criterion 10: A username with a space, a Vietnamese letter,
        or fewer than 3 or more than 32 characters is refused with a Vietnamese message.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        invalid_cases = [
            ("user name", "Tên đăng nhập không được chứa khoảng trắng"),
            ("hùng", "Tên đăng nhập chỉ được chứa các ký tự a-z, 0-9, dấu chấm và gạch dưới"),
            ("ab", "Tên đăng nhập phải có từ 3 đến 32 ký tự"),
            ("a" * 33, "Tên đăng nhập phải có từ 3 đến 32 ký tự"),
        ]

        for uname, expected_msg in invalid_cases:
            with self.subTest(username=uname):
                res = admin_client.post(
                    "/api/admin/users",
                    json={"username": uname, "role": "employee"},
                )
                self.assertEqual(res.status_code, 400)
                err_msg = res.json()["error"]["message"]
                self.assertEqual(err_msg, expected_msg)

        # Valid characters: 3-32 characters from a-z, 0-9, . and _
        valid_res = admin_client.post(
            "/api/admin/users",
            json={"username": "valid.user_123", "role": "employee"},
        )
        self.assertEqual(valid_res.status_code, 201)

    def test_criterion_11_account_action_history_and_role_access(self):
        """Criterion 11: Each account action writes a history entry an admin can see;
        an employee or boss requesting the history gets 403.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        # Perform account actions to populate history
        add_res = admin_client.post("/api/admin/users", json={"username": "audit_emp", "role": "employee"})
        self.assertEqual(add_res.status_code, 201)
        audit_id = add_res.json()["id"]

        admin_client.patch(f"/api/admin/users/{audit_id}", json={"reset_password": True})
        admin_client.patch(f"/api/admin/users/{audit_id}", json={"role": "boss"})
        admin_client.patch(f"/api/admin/users/{audit_id}", json={"active": 0})
        admin_client.patch(f"/api/admin/users/{audit_id}", json={"active": 1})
        admin_client.post(f"/api/admin/users/{audit_id}/logout")

        # 1. Admin can see history via GET /api/admin/users/events and HTML /admin/accounts/history
        events_res = admin_client.get("/api/admin/users/events")
        self.assertEqual(events_res.status_code, 200)
        events = events_res.json()
        self.assertTrue(len(events) >= 6)

        actions_seen = {e["action"] for e in events}
        expected_actions = {
            "account_add",
            "account_reset",
            "account_role",
            "account_deactivate",
            "account_reactivate",
            "account_logout",
        }
        self.assertTrue(expected_actions.issubset(actions_seen))

        # Passwords never appear in any event
        for ev in events:
            self.assertNotIn("password", ev.get("old_value", "") or "")
            self.assertNotIn("password", ev.get("new_value", "") or "")

        # HTML history page accessible to admin
        html_res = admin_client.get("/admin/accounts/history")
        self.assertEqual(html_res.status_code, 200)
        self.assertIn("Lịch sử thao tác tài khoản", html_res.text)

        # 2. Employee or boss requesting history gets 403
        emp_client = TestClient(self.app)
        self._login(emp_client, "emp_user", "EmployeePass123")
        self.assertEqual(emp_client.get("/api/admin/users/events").status_code, 403)
        self.assertEqual(emp_client.get("/admin/accounts/history").status_code, 403)

        boss_client = TestClient(self.app)
        self._login(boss_client, "boss_user", "BossPass123")
        self.assertEqual(boss_client.get("/api/admin/users/events").status_code, 403)
        self.assertEqual(boss_client.get("/admin/accounts/history").status_code, 403)

    def test_criterion_12_operator_role_refused_in_picker_and_api(self):
        """Criterion 12: The role picker offers no operator, and the API refuses operator
        in both creating an account and changing a role; an existing operator account still logs in and uploads.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        # 1. Role picker on HTML accounts page offers no operator
        page_res = admin_client.get("/admin/accounts")
        self.assertEqual(page_res.status_code, 200)
        self.assertNotIn('<option value="operator"', page_res.text)

        # 2. API refuses operator in creating an account
        create_res = admin_client.post(
            "/api/admin/users",
            json={"username": "op_new", "role": "operator"},
        )
        self.assertEqual(create_res.status_code, 400)

        # 3. API refuses operator in changing role
        role_res = admin_client.patch(
            f"/api/admin/users/{self.employee_id}",
            json={"role": "operator"},
        )
        self.assertEqual(role_res.status_code, 400)

        # 4. Existing operator account still logs in and uploads
        op_client = TestClient(self.app)
        self._login(op_client, "op_user", "OperatorPass123")

        jpeg_data = make_test_table_jpeg()
        upload_res = op_client.post(
            "/api/photos",
            data={"machine": "T1", "production_date": "2026-11-06", "shift": 1, "page_no": 1},
            files={"file": ("operator_test.jpg", jpeg_data, "image/jpeg")},
        )
        self.assertEqual(upload_res.status_code, 201)

    def test_criterion_13_employee_boss_and_admin_can_upload_photo(self):
        """Criterion 13: An employee, a boss and an admin can upload a photo from Tải ảnh lên,
        and the sheet records them as the uploader.
        """
        jpeg_data = make_test_table_jpeg()

        for role_name, uname, pwd, uid, page_num in [
            ("employee", "emp_user", "EmployeePass123", self.employee_id, 1),
            ("boss", "boss_user", "BossPass123", self.boss_id, 2),
            ("admin", "owner_admin", "OwnerPass123", self.owner_id, 3),
        ]:
            with self.subTest(role=role_name):
                c = TestClient(self.app)
                self._login(c, uname, pwd)

                upload_res = c.post(
                    "/api/photos",
                    data={"machine": "T1", "production_date": "2026-11-06", "shift": 1, "page_no": page_num},
                    files={"file": (f"{role_name}.jpg", jpeg_data, "image/jpeg")},
                )
                self.assertEqual(upload_res.status_code, 201, f"{role_name} upload failed: {upload_res.text}")
                photo_id = upload_res.json()["photo_id"]

                conn = get_connection(self.db_path)
                p_row = conn.execute("SELECT uploaded_by FROM photos WHERE id = ?", (photo_id,)).fetchone()
                self.assertEqual(p_row["uploaded_by"], uid)
                conn.close()

    def test_criterion_14_sidebar_shows_accounts_to_admins_only_and_403_for_others(self):
        """Criterion 14: The sidebar shows Tài khoản to admins only;
        a non-admin requesting the accounts page or GET /api/admin/users gets 403.
        """
        # Admin gets "Tài khoản" in the sidebar
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")
        admin_frame = admin_client.get("/")
        self.assertEqual(admin_frame.status_code, 200)
        self.assertIn("Tài khoản", admin_frame.text)
        self.assertIn("/admin/accounts", admin_frame.text)

        # Employee does NOT get "Tài khoản" in sidebar, and gets 403 on requests
        emp_client = TestClient(self.app)
        self._login(emp_client, "emp_user", "EmployeePass123")
        emp_frame = emp_client.get("/")
        self.assertEqual(emp_frame.status_code, 200)
        self.assertNotIn("/admin/accounts", emp_frame.text)
        self.assertEqual(emp_client.get("/admin/accounts").status_code, 403)
        self.assertEqual(emp_client.get("/api/admin/users").status_code, 403)

        # Boss does NOT get "Tài khoản" in sidebar, and gets 403 on requests
        boss_client = TestClient(self.app)
        self._login(boss_client, "boss_user", "BossPass123")
        boss_frame = boss_client.get("/")
        self.assertEqual(boss_frame.status_code, 200)
        self.assertNotIn("/admin/accounts", boss_frame.text)
        self.assertEqual(boss_client.get("/admin/accounts").status_code, 403)
        self.assertEqual(boss_client.get("/api/admin/users").status_code, 403)

    def test_criterion_15_phone_width_viewport_and_folded_menu_markup(self):
        """Criterion 15: The accounts screen and the frame work at phone width (360 px) without sideways scrolling:
        a test checks the viewport meta tag and the folded menu's markup; the verifier checks the layout at 360 px.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        # 1. Check viewport meta tag on both office frame and accounts page
        frame_res = admin_client.get("/office")
        self.assertEqual(frame_res.status_code, 200)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0"', frame_res.text)

        accounts_res = admin_client.get("/admin/accounts")
        self.assertEqual(accounts_res.status_code, 200)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0"', accounts_res.text)

        # 2. Check folded mobile menu markup (toggle button)
        self.assertIn('class="menu-toggle-btn"', accounts_res.text)
        self.assertIn('id="menu-toggle"', accounts_res.text)
        self.assertIn('class="menu-toggle-btn"', frame_res.text)

        # 3. Check table responsive container present on accounts page
        self.assertIn('class="table-wrap"', accounts_res.text)

    def test_criterion_16_create_user_owner_flag_transfer_and_migration_oldest_admin(self):
        """Criterion 16: A fresh database with create_user.py --owner has exactly one owner;
        running it again for another admin moves the flag;
        the migration on an existing database marks the oldest active admin;
        a second owner cannot be stored.
        """
        fresh_db = Path(self.temp_dir.name) / "fresh_cli.db"

        # 1. Fresh database with create_user.py --owner has exactly one owner
        code1 = cli_main(["--username", "owner_cli_1", "--owner", "--db", str(fresh_db)])
        self.assertEqual(code1, 0)

        conn = get_connection(fresh_db)
        try:
            owners = conn.execute("SELECT username, is_owner FROM users WHERE is_owner = 1").fetchall()
            self.assertEqual(len(owners), 1)
            self.assertEqual(owners[0]["username"], "owner_cli_1")

            # 2. Running it again for another admin moves the flag
            code2 = cli_main(["--username", "owner_cli_2", "--owner", "--db", str(fresh_db)])
            self.assertEqual(code2, 0)

            owners2 = conn.execute("SELECT username, is_owner FROM users WHERE is_owner = 1").fetchall()
            self.assertEqual(len(owners2), 1)
            self.assertEqual(owners2[0]["username"], "owner_cli_2")

            old_owner = conn.execute("SELECT is_owner FROM users WHERE username = 'owner_cli_1'").fetchone()
            self.assertEqual(old_owner["is_owner"], 0)

            # 3. A second owner cannot be stored (sqlite3.IntegrityError)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE users SET is_owner = 1 WHERE username = 'owner_cli_1'")
        finally:
            conn.close()

        # 4. Migration on an existing database marks the oldest active admin
        mig_db = Path(self.temp_dir.name) / "mig_test.db"
        custom_mig_dir = Path(self.temp_dir.name) / "custom_migs"
        custom_mig_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)

        conn_mig = get_connection(mig_db, auto_migrate=False)
        try:
            migrate(conn_mig, migrations_dir=custom_mig_dir)

            # Insert users before 003:
            # 1: inactive admin
            # 2: active employee
            # 3: oldest active admin
            # 4: younger active admin
            create_user(conn_mig, "admin_inactive", "hash", "admin", active=0)
            create_user(conn_mig, "emp_active", "hash", "employee", active=1)
            create_user(conn_mig, "oldest_active_admin", "hash", "admin", active=1)
            create_user(conn_mig, "younger_active_admin", "hash", "admin", active=1)

            # Apply 003_accounts.sql
            shutil.copy(get_migrations_dir() / "003_accounts.sql", custom_mig_dir)
            applied = migrate(conn_mig, migrations_dir=custom_mig_dir)
            self.assertEqual(applied, ["003_accounts.sql"])

            owners_mig = conn_mig.execute("SELECT username, is_owner FROM users WHERE is_owner = 1").fetchall()
            self.assertEqual(len(owners_mig), 1)
            self.assertEqual(owners_mig[0]["username"], "oldest_active_admin")
        finally:
            conn_mig.close()

    # =========================================================================
    # Additional Verification Cases Requested in Prompt
    # =========================================================================

    def test_migration_mixed_case_usernames_without_conflicts_and_with_conflicts(self):
        """Check migration behavior on a database with mixed-case usernames:
        - When mixed-case usernames do not conflict (e.g. 'UserOne', 'AdminTwo'), migration succeeds,
          original casing is preserved, and login lookup is case-insensitive.
        - When mixed-case usernames conflict (e.g. 'Hung' and 'hung'), migration halts with MigrationError
          naming both conflicting usernames, and 003 is not applied.
        """
        # Case A: Non-conflicting mixed-case usernames
        db_path_a = Path(self.temp_dir.name) / "mig_nocollision.db"
        mig_dir_a = Path(self.temp_dir.name) / "migs_a"
        mig_dir_a.mkdir(parents=True, exist_ok=True)
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", mig_dir_a)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", mig_dir_a)

        conn_a = get_connection(db_path_a, auto_migrate=False)
        try:
            migrate(conn_a, migrations_dir=mig_dir_a)
            create_user(conn_a, "AdminAlice", hash_password("AlicePass"), "admin", active=1)
            create_user(conn_a, "WorkerBob", hash_password("BobPass"), "employee", active=1)

            shutil.copy(get_migrations_dir() / "003_accounts.sql", mig_dir_a)
            applied_a = migrate(conn_a, migrations_dir=mig_dir_a)
            self.assertEqual(applied_a, ["003_accounts.sql"])

            # Casing preserved verbatim
            rows = {r["username"]: r for r in conn_a.execute("SELECT username, is_owner FROM users").fetchall()}
            self.assertIn("AdminAlice", rows)
            self.assertIn("WorkerBob", rows)
            self.assertEqual(rows["AdminAlice"]["is_owner"], 1)

            # Lookup is case-insensitive
            found = get_user_by_username(conn_a, "adminalice")
            self.assertIsNotNone(found)
            self.assertEqual(found["username"], "AdminAlice")
        finally:
            conn_a.close()

        # Case B: Conflicting mixed-case usernames differing only by case
        db_path_b = Path(self.temp_dir.name) / "mig_collision.db"
        mig_dir_b = Path(self.temp_dir.name) / "migs_b"
        mig_dir_b.mkdir(parents=True, exist_ok=True)
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", mig_dir_b)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", mig_dir_b)

        conn_b = get_connection(db_path_b, auto_migrate=False)
        try:
            migrate(conn_b, migrations_dir=mig_dir_b)
            create_user(conn_b, "Hung", hash_password("pass1"), "admin", active=1)
            create_user(conn_b, "hung", hash_password("pass2"), "employee", active=1)

            shutil.copy(get_migrations_dir() / "003_accounts.sql", mig_dir_b)
            with self.assertRaises(MigrationError) as ctx:
                migrate(conn_b, migrations_dir=mig_dir_b)

            err_msg = str(ctx.exception)
            self.assertIn("Hung", err_msg)
            self.assertIn("hung", err_msg)

            # 003_accounts.sql was not applied
            applied = get_applied_migrations(conn_b)
            self.assertNotIn("003_accounts.sql", applied)
        finally:
            conn_b.close()

    def test_temporary_password_72h_exact_boundary(self):
        """Check exact 72 h boundary behavior:
        - At t0 + 71h 59m: login succeeds.
        - At t0 + 72h 00m 01s: login fails with 401 and Vietnamese message on both HTML and API.
        """
        t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)
        admin_client = TestClient(self.app)

        with patch("hasc.web.security.now", return_value=t0):
            self._login(admin_client, "owner_admin", "OwnerPass123")
            res = admin_client.post(
                "/api/admin/users",
                json={"username": "emp_boundary", "role": "employee"},
            )
            self.assertEqual(res.status_code, 201)
            temp_pwd = res.json()["temporary_password"]

        # Exactly 1 minute before 72h boundary: login succeeds
        t_before = t0 + timedelta(hours=71, minutes=59)
        with patch("hasc.web.security.now", return_value=t_before):
            c1 = TestClient(self.app)
            res1 = c1.post("/api/login", json={"username": "emp_boundary", "password": temp_pwd})
            self.assertEqual(res1.status_code, 200)

        # 1 second past 72h boundary: login fails
        t_after = t0 + timedelta(hours=72, seconds=1)
        with patch("hasc.web.security.now", return_value=t_after):
            c2 = TestClient(self.app)
            res_api = c2.post("/api/login", json={"username": "emp_boundary", "password": temp_pwd})
            self.assertEqual(res_api.status_code, 401)
            self.assertEqual(
                res_api.json()["error"]["message"],
                "Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.",
            )

            res_html = c2.post("/login", data={"username": "emp_boundary", "password": temp_pwd})
            self.assertEqual(res_html.status_code, 401)
            self.assertIn("Mật khẩu tạm đã hết hạn. Hãy nhờ Quản trị đặt lại.", res_html.text)

    def test_temporary_password_303_vs_403_behavior(self):
        """Check 303 vs 403 behavior for temporary passwords:
        - While user has temporary password, every HTML page except /change-password and /logout
          redirects with 303 to /change-password.
        - Every API call except changing password and logging out returns 403 password_change_required.
        - Exempt routes (/login, /logout, /change-password, /static/*, POST /api/password, POST /api/logout)
          are never redirected and function normally.
        """
        admin_client = TestClient(self.app)
        self._login(admin_client, "owner_admin", "OwnerPass123")

        create_res = admin_client.post(
            "/api/admin/users",
            json={"username": "emp_flow", "role": "employee"},
        )
        self.assertEqual(create_res.status_code, 201)
        temp_pwd = create_res.json()["temporary_password"]

        # User logs in with temporary password
        c = TestClient(self.app)
        login_res = c.post(
            "/login",
            data={"username": "emp_flow", "password": temp_pwd},
            follow_redirects=False,
        )
        self.assertEqual(login_res.status_code, 303)
        self.assertEqual(login_res.headers.get("location"), "/change-password")

        # HTML routes redirect 303
        for html_path in ("/", "/office", "/admin/accounts"):
            r = c.get(html_path, follow_redirects=False)
            self.assertEqual(r.status_code, 303, f"{html_path} should redirect with 303")
            self.assertEqual(r.headers.get("location"), "/change-password")

        # API routes return 403 JSON
        for api_path in ("/api/sheets", "/api/admin/users", "/api/days/2026-11-06/report"):
            r = c.get(api_path)
            self.assertEqual(r.status_code, 403, f"{api_path} should return 403")
            self.assertEqual(r.json()["error"]["code"], "password_change_required")

        # Exempt routes:
        # /change-password returns 200
        self.assertEqual(c.get("/change-password").status_code, 200)

        # /static/* returns 200
        self.assertEqual(c.get("/static/css/app.css").status_code, 200)

        # POST /api/password works without 403 password_change_required
        pw_res = c.post(
            "/api/password",
            json={"old": temp_pwd, "new": "BrandNewPass789"},
        )
        self.assertEqual(pw_res.status_code, 204)

        # POST /api/logout works without 403
        self.assertEqual(c.post("/api/logout").status_code, 204)


if __name__ == "__main__":
    unittest.main()
