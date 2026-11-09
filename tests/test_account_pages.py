from datetime import datetime, timezone
import gc
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from fastapi.testclient import TestClient

from hasc.db import create_user, get_connection
from hasc.web.accounts import ROLE_LABELS, set_or_create_owner
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestAccountPages(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.photos_dir = Path(self.temp_dir.name) / "photos"
        self.photos_dir.mkdir(parents=True, exist_ok=True)

        self.app = create_app(db_path=self.db_path, photos_dir=self.photos_dir)
        conn = get_connection(self.db_path)

        # 1. Create owner admin
        self.owner_id, _, _ = set_or_create_owner(conn, "owner_user", password="password123")
        # Ensure password is verified and must_change_password cleared for test convenience
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password("password123"), self.owner_id),
        )
        conn.commit()

        # 2. Create normal admin, employee, boss, and operator
        self.admin_id = create_user(
            conn, "normal_admin", hash_password("password123"), role="admin", active=1, is_owner=0
        )
        self.employee_id = create_user(
            conn, "emp_user", hash_password("password123"), role="employee", active=1, is_owner=0
        )
        self.boss_id = create_user(
            conn, "boss_user", hash_password("password123"), role="boss", active=1, is_owner=0
        )
        self.operator_id = create_user(
            conn, "op_user", hash_password("password123"), role="operator", active=1, is_owner=0
        )

        conn.close()

        # Login test clients
        self.clients: dict[str, TestClient] = {}
        for role_key, uname in [
            ("owner", "owner_user"),
            ("admin", "normal_admin"),
            ("employee", "emp_user"),
            ("boss", "boss_user"),
            ("operator", "op_user"),
        ]:
            c = TestClient(self.app)
            res = c.post("/api/login", json={"username": uname, "password": "password123"})
            self.assertEqual(res.status_code, 200, f"Login failed for {uname}: {res.text}")
            self.clients[role_key] = c

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def _get_conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    # -------------------------------------------------------------------------
    # 1. Admin access
    # -------------------------------------------------------------------------

    def test_admin_access(self):
        """Admin can access /admin/accounts and /admin/accounts/history."""
        for client_key in ("owner", "admin"):
            client = self.clients[client_key]

            # GET /admin/accounts
            res = client.get("/admin/accounts")
            self.assertEqual(res.status_code, 200)
            self.assertIn("Quản lý tài khoản", res.text)
            self.assertIn("Danh sách tài khoản", res.text)
            self.assertIn("Thêm tài khoản mới", res.text)
            self.assertIn("owner_user", res.text)
            self.assertIn("normal_admin", res.text)
            self.assertIn("emp_user", res.text)

            # GET /admin/accounts/partial (htmx table)
            res_partial = client.get("/admin/accounts/partial")
            self.assertEqual(res_partial.status_code, 200)
            self.assertIn("accounts-table", res_partial.text)
            self.assertNotIn("<!doctype html>", res_partial.text)

            # GET /admin/accounts/history
            res_hist = client.get("/admin/accounts/history")
            self.assertEqual(res_hist.status_code, 200)
            self.assertIn("Lịch sử thao tác tài khoản", res_hist.text)

            # GET /admin/accounts/history/partial
            res_hist_partial = client.get("/admin/accounts/history/partial")
            self.assertEqual(res_hist_partial.status_code, 200)
            self.assertNotIn("<!doctype html>", res_hist_partial.text)

    def test_criterion_admin_access(self):
        """Alias for criterion coverage."""
        self.test_admin_access()

    # -------------------------------------------------------------------------
    # 2. Non-admin refused
    # -------------------------------------------------------------------------

    def test_non_admin_refused(self):
        """Non-admin roles (employee, boss, operator) are refused with 403."""
        for client_key in ("employee", "boss", "operator"):
            client = self.clients[client_key]

            res_accounts = client.get("/admin/accounts")
            self.assertEqual(res_accounts.status_code, 403, f"{client_key} should get 403 on /admin/accounts")

            res_partial = client.get("/admin/accounts/partial")
            self.assertEqual(res_partial.status_code, 403, f"{client_key} should get 403 on /admin/accounts/partial")

            res_hist = client.get("/admin/accounts/history")
            self.assertEqual(res_hist.status_code, 403, f"{client_key} should get 403 on /admin/accounts/history")

            res_hist_partial = client.get("/admin/accounts/history/partial")
            self.assertEqual(res_hist_partial.status_code, 403)

            res_post = client.post(
                "/admin/accounts",
                data={"action": "add", "username": "hack_user", "role": "employee"},
            )
            self.assertEqual(res_post.status_code, 403)

        # Unauthenticated access
        anon = TestClient(self.app)
        res_anon = anon.get("/admin/accounts", follow_redirects=False)
        self.assertIn(res_anon.status_code, (303, 401))

    def test_criterion_non_admin_refused(self):
        """Alias for criterion coverage."""
        self.test_non_admin_refused()

    # -------------------------------------------------------------------------
    # 3. Confirmation text names the account
    # -------------------------------------------------------------------------

    def test_confirmation_text_names_the_account(self):
        """Every action on another account asks for confirmation first, naming the account."""
        owner = self.clients["owner"]
        res = owner.get("/admin/accounts")
        self.assertEqual(res.status_code, 200)
        html = res.text

        # Target account: emp_user
        # Confirmation prompts must contain emp_user
        self.assertIn("emp_user", html)

        # Look for confirmation text naming emp_user in action attributes
        # e.g., onsubmit or hx-confirm or data-confirm
        confirm_matches = re.findall(r'(?:onsubmit|hx-confirm|data-confirm)="[^"]*emp_user[^"]*"', html)
        self.assertGreater(len(confirm_matches), 0, "Markup must contain confirmations naming emp_user")

        # Specific action confirmations
        self.assertTrue(
            any("đặt lại mật khẩu" in m.lower() and "emp_user" in m for m in confirm_matches),
            "Reset confirmation must name emp_user",
        )
        self.assertTrue(
            any("vai trò" in m.lower() and "emp_user" in m for m in confirm_matches),
            "Role change confirmation must name emp_user",
        )
        self.assertTrue(
            any("vô hiệu hóa" in m.lower() and "emp_user" in m for m in confirm_matches),
            "Deactivate confirmation must name emp_user",
        )
        self.assertTrue(
            any("đăng xuất" in m.lower() and "emp_user" in m for m in confirm_matches),
            "Logout confirmation must name emp_user",
        )

        # Now execute an action and check that the resulting confirmation/success notice names the account
        res_deact = owner.post(
            "/admin/accounts",
            data={"action": "deactivate", "user_id": self.employee_id},
        )
        self.assertEqual(res_deact.status_code, 200)
        self.assertIn("emp_user", res_deact.text)
        self.assertIn("Đã vô hiệu hóa", res_deact.text)

        # After deactivation, reactivate confirmation should name emp_user
        self.assertIn("kích hoạt lại", res_deact.text.lower())
        self.assertIn("emp_user", res_deact.text)

        # Execute reactivate
        res_react = owner.post(
            "/admin/accounts",
            data={"action": "reactivate", "user_id": self.employee_id},
        )
        self.assertEqual(res_react.status_code, 200)
        self.assertIn("emp_user", res_react.text)
        self.assertIn("Đã kích hoạt lại", res_react.text)

        # Execute role change
        res_role = owner.post(
            "/admin/accounts",
            data={"action": "role", "user_id": self.employee_id, "role": "boss"},
        )
        self.assertEqual(res_role.status_code, 200)
        self.assertIn("emp_user", res_role.text)
        self.assertIn("Đã đổi vai trò", res_role.text)

        # Execute logout
        res_logout = owner.post(
            "/admin/accounts",
            data={"action": "logout", "user_id": self.employee_id},
        )
        self.assertEqual(res_logout.status_code, 200)
        self.assertIn("emp_user", res_logout.text)
        self.assertIn("Đã đăng xuất", res_logout.text)

    def test_criterion_confirmation_text_names_the_account(self):
        """Alias for criterion coverage."""
        self.test_confirmation_text_names_the_account()

    # -------------------------------------------------------------------------
    # 4. Temp password shown once and absent on reload
    # -------------------------------------------------------------------------

    def test_temp_password_shown_once_and_absent_on_reload(self):
        """Add and reset show a temporary password once; reloading the page does not show it."""
        admin = self.clients["owner"]

        # 1. Add employee
        res_add = admin.post(
            "/admin/accounts",
            data={"action": "add", "username": "hung_temp", "role": "employee"},
        )
        self.assertEqual(res_add.status_code, 200)
        self.assertIn("hung_temp", res_add.text)
        self.assertIn("Mật khẩu tạm thời", res_add.text)

        # Extract temporary password from response
        match = re.search(r'class="temp-password[^"]*"[^>]*>\s*([A-Za-z0-9]+)\s*<', res_add.text)
        self.assertIsNotNone(match, "Temporary password must be rendered in response")
        temp_pw = match.group(1)
        self.assertGreater(len(temp_pw), 5)

        # 2. Reload page (GET /admin/accounts)
        res_reload = admin.get("/admin/accounts")
        self.assertEqual(res_reload.status_code, 200)
        self.assertIn("hung_temp", res_reload.text)
        self.assertNotIn(temp_pw, res_reload.text, "Temporary password must NOT appear on page reload")

        # 3. Also verify reset password behaves identically
        conn = self._get_conn()
        cur = conn.execute("SELECT id FROM users WHERE username = 'hung_temp'")
        hung_id = cur.fetchone()["id"]
        conn.close()

        res_reset = admin.post(
            "/admin/accounts",
            data={"action": "reset", "user_id": hung_id},
        )
        self.assertEqual(res_reset.status_code, 200)
        self.assertIn("Mật khẩu tạm thời", res_reset.text)

        match_reset = re.search(r'class="temp-password[^"]*"[^>]*>\s*([A-Za-z0-9]+)\s*<', res_reset.text)
        self.assertIsNotNone(match_reset)
        reset_temp_pw = match_reset.group(1)
        self.assertGreater(len(reset_temp_pw), 5)

        # Reload page after reset
        res_reload2 = admin.get("/admin/accounts")
        self.assertEqual(res_reload2.status_code, 200)
        self.assertNotIn(reset_temp_pw, res_reload2.text, "Reset temp password must NOT appear on reload")

    def test_criterion_temp_password_shown_once_and_absent_on_reload(self):
        """Alias for criterion coverage."""
        self.test_temp_password_shown_once_and_absent_on_reload()

    # -------------------------------------------------------------------------
    # 5. Forbidden owner actions not offered
    # -------------------------------------------------------------------------

    def test_forbidden_owner_actions_not_offered(self):
        """Actions forbidden by owner rules are not offered in the rendered screen."""
        owner = self.clients["owner"]
        normal = self.clients["admin"]

        # A. Owner viewing screen:
        # Owner cannot perform actions on themselves
        res_owner_view = owner.get("/admin/accounts")
        self.assertEqual(res_owner_view.status_code, 200)
        owner_html = res_owner_view.text

        # Find owner row
        # In owner_user row, there should be NO reset, role change, deactivate, or logout forms
        owner_row_match = re.search(r'data-username="owner_user"[\s\S]*?</tr>', owner_html)
        self.assertIsNotNone(owner_row_match)
        owner_row = owner_row_match.group(0)
        self.assertIn("Chủ sở hữu", owner_row)
        self.assertNotIn('name="action" value="reset"', owner_row)
        self.assertNotIn('name="action" value="role"', owner_row)
        self.assertNotIn('name="action" value="deactivate"', owner_row)
        self.assertNotIn('name="action" value="logout"', owner_row)

        # Owner CAN see Quản trị in Add form
        self.assertIn('<option value="admin">Quản trị</option>', owner_html)
        # But NO operator in Add form
        self.assertNotIn('<option value="operator"', owner_html)

        # B. Non-owner admin viewing screen:
        res_normal_view = normal.get("/admin/accounts")
        self.assertEqual(res_normal_view.status_code, 200)
        normal_html = res_normal_view.text

        # 1. On owner row: no actions offered
        owner_row_for_normal = re.search(r'data-username="owner_user"[\s\S]*?</tr>', normal_html).group(0)
        self.assertIn("Chủ sở hữu", owner_row_for_normal)
        self.assertNotIn('name="action" value="reset"', owner_row_for_normal)
        self.assertNotIn('name="action" value="role"', owner_row_for_normal)
        self.assertNotIn('name="action" value="deactivate"', owner_row_for_normal)
        self.assertNotIn('name="action" value="logout"', owner_row_for_normal)

        # 2. On self row (normal_admin): no actions offered
        self_row_for_normal = re.search(r'data-username="normal_admin"[\s\S]*?</tr>', normal_html).group(0)
        self.assertNotIn('name="action" value="reset"', self_row_for_normal)
        self.assertNotIn('name="action" value="role"', self_row_for_normal)
        self.assertNotIn('name="action" value="deactivate"', self_row_for_normal)
        self.assertNotIn('name="action" value="logout"', self_row_for_normal)

        # 3. In Add form: non-owner CANNOT create admin or operator
        add_form_normal = re.search(r'<form[^>]*class="add-account-form"[\s\S]*?</form>', normal_html).group(0)
        self.assertNotIn('value="admin"', add_form_normal)
        self.assertNotIn('value="operator"', add_form_normal)
        self.assertIn('value="employee"', add_form_normal)
        self.assertIn('value="boss"', add_form_normal)

        # 4. On employee row for normal admin:
        # Can manage employee, but available roles for change do NOT include admin or operator
        emp_row_normal = re.search(r'data-username="emp_user"[\s\S]*?</tr>', normal_html).group(0)
        self.assertIn('name="action" value="reset"', emp_row_normal)
        self.assertIn('name="action" value="deactivate"', emp_row_normal)
        self.assertNotIn('value="admin"', emp_row_normal)
        self.assertNotIn('value="operator"', emp_row_normal)

        # 5. Direct POST attempt by normal_admin on owner account must return 403
        res_forbidden = normal.post(
            "/admin/accounts",
            data={"action": "reset", "user_id": self.owner_id},
        )
        self.assertEqual(res_forbidden.status_code, 403)

    def test_criterion_forbidden_owner_actions_not_offered(self):
        """Alias for criterion coverage."""
        self.test_forbidden_owner_actions_not_offered()

    # -------------------------------------------------------------------------
    # 6. P142 labels
    # -------------------------------------------------------------------------

    def test_the_p142_labels(self):
        """Roles on accounts screen use P142 labels:
        employee: Văn phòng, boss: Ban giám đốc, admin: Quản trị, operator: Xưởng.
        Role picker offers no operator.
        """
        admin = self.clients["owner"]
        res = admin.get("/admin/accounts")
        self.assertEqual(res.status_code, 200)
        html = res.text

        # All 4 labels must be present on accounts screen
        self.assertIn("Văn phòng", html)
        self.assertIn("Ban giám đốc", html)
        self.assertIn("Quản trị", html)
        self.assertIn("Xưởng", html)

        # Role select in Add Account form offers NO operator
        add_form = re.search(r'<form[^>]*class="add-account-form"[\s\S]*?</form>', html).group(0)
        self.assertNotIn("operator", add_form)
        self.assertNotIn("Xưởng", add_form)

        # Also verify in ROLE_LABELS mapping
        self.assertEqual(ROLE_LABELS["employee"], "Văn phòng")
        self.assertEqual(ROLE_LABELS["boss"], "Ban giám đốc")
        self.assertEqual(ROLE_LABELS["admin"], "Quản trị")
        self.assertEqual(ROLE_LABELS["operator"], "Xưởng")

    def test_criterion_the_p142_labels(self):
        """Alias for criterion coverage."""
        self.test_the_p142_labels()

    # -------------------------------------------------------------------------
    # 7. History order
    # -------------------------------------------------------------------------

    def test_history_order(self):
        """Account history lists account events newest first."""
        owner = self.clients["owner"]

        # Step 1: Add user
        owner.post(
            "/admin/accounts",
            data={"action": "add", "username": "order_user", "role": "employee"},
        )
        conn = self._get_conn()
        target_id = conn.execute("SELECT id FROM users WHERE username = 'order_user'").fetchone()["id"]
        conn.close()

        # Step 2: Role change
        owner.post(
            "/admin/accounts",
            data={"action": "role", "user_id": target_id, "role": "boss"},
        )

        # Step 3: Deactivate
        owner.post(
            "/admin/accounts",
            data={"action": "deactivate", "user_id": target_id},
        )

        # Step 4: Reset password
        owner.post(
            "/admin/accounts",
            data={"action": "reset", "user_id": target_id},
        )

        # Fetch history page
        res = owner.get("/admin/accounts/history")
        self.assertEqual(res.status_code, 200)
        html = res.text

        # Find event actions in the order they appear in the HTML table
        action_positions = []
        for action_name in ["account_reset", "account_deactivate", "account_role", "account_add"]:
            pos = html.find(f"badge--{action_name}")
            self.assertNotEqual(pos, -1, f"Expected event {action_name} in history")
            action_positions.append((action_name, pos))

        # Check newest-first order:
        # reset (step 4) should appear before deactivate (step 3),
        # before role (step 2), before add (step 1)
        for i in range(len(action_positions) - 1):
            curr_action, curr_pos = action_positions[i]
            next_action, next_pos = action_positions[i + 1]
            self.assertLess(
                curr_pos,
                next_pos,
                f"Event {curr_action} should appear before {next_action} (newest first)",
            )

    def test_criterion_history_order(self):
        """Alias for criterion coverage."""
        self.test_history_order()

    # -------------------------------------------------------------------------
    # Criterion 15: Phone width markup
    # -------------------------------------------------------------------------

    def test_criterion_15_accounts_page_phone_width(self):
        """The accounts screen and frame work at phone width (360 px):
        viewport meta tag and folded menu's markup present.
        """
        owner = self.clients["owner"]
        for path in ("/admin/accounts", "/admin/accounts/history"):
            res = owner.get(path)
            self.assertEqual(res.status_code, 200)
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1.0"', res.text)
            self.assertIn('class="menu-toggle-btn"', res.text)
            self.assertIn('id="menu-toggle"', res.text)
            self.assertIn('class="table-wrap"', res.text)
