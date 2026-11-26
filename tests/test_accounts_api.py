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
    get_session,
    get_user_by_id,
    get_user_by_username,
    list_events,
)
from hasc.web.app import create_app
from hasc.web.security import (
    generate_session_id,
    get_session_duration,
    hash_password,
    now,
)


class TestAccountsAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        self.client = TestClient(self.app)

        conn = get_connection(self.db_path)
        # 1. Owner Admin
        self.owner_id = create_user(
            conn,
            username="owner_admin",
            password_hash=hash_password("owner_pass"),
            role="admin",
            active=1,
            must_change_password=0,
            is_owner=1,
        )

        # 2. Non-owner Admin
        self.admin_id = create_user(
            conn,
            username="sub_admin",
            password_hash=hash_password("admin_pass"),
            role="admin",
            active=1,
            must_change_password=0,
            is_owner=0,
        )

        # 3. Third Admin (for non-owner admin action tests)
        self.admin3_id = create_user(
            conn,
            username="third_admin",
            password_hash=hash_password("admin3_pass"),
            role="admin",
            active=1,
            must_change_password=0,
            is_owner=0,
        )

        # 4. Employee
        self.emp_id = create_user(
            conn,
            username="emp_user",
            password_hash=hash_password("emp_pass"),
            role="employee",
            active=1,
            must_change_password=0,
            is_owner=0,
        )

        # 5. Boss
        self.boss_id = create_user(
            conn,
            username="boss_user",
            password_hash=hash_password("boss_pass"),
            role="boss",
            active=1,
            must_change_password=0,
            is_owner=0,
        )

        # 6. Operator
        self.op_id = create_user(
            conn,
            username="op_user",
            password_hash=hash_password("op_pass"),
            role="operator",
            active=1,
            must_change_password=0,
            is_owner=0,
        )

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def _login(self, username: str, password: str) -> TestClient:
        c = TestClient(self.app)
        res = c.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, f"Login failed for {username}")
        return c

    # -------------------------------------------------------------------------
    # Spec Acceptance Criteria Tests
    # -------------------------------------------------------------------------

    def test_criterion_1_add_employee_temporary_password_shown_once(self):
        """Criterion 1: The admin adds an employee. The account appears in the list as active,
        and a temporary password is shown once; reloading the page does not show it again.
        """
        owner_client = self._login("owner_admin", "owner_pass")

        # 1. Admin adds an employee
        res_add = owner_client.post(
            "/api/admin/users",
            json={"username": "new_staff", "role": "employee"},
        )
        self.assertEqual(res_add.status_code, 201)
        data = res_add.json()
        self.assertIn("temporary_password", data)
        self.assertTrue(len(data["temporary_password"]) >= 8)
        new_id = data["id"]

        # 2. Account appears in GET /api/admin/users as active
        res_list = owner_client.get("/api/admin/users")
        self.assertEqual(res_list.status_code, 200)
        users = res_list.json()
        target = next((u for u in users if u["id"] == new_id), None)
        self.assertIsNotNone(target)
        self.assertEqual(target["username"], "new_staff")
        self.assertEqual(target["role"], "employee")
        self.assertEqual(target["active"], 1)
        self.assertTrue(target["must_change_password"])
        self.assertFalse(target["is_owner"])

        # 3. Temporary password is shown once: it does NOT appear in GET /api/admin/users
        self.assertNotIn("temporary_password", target)
        self.assertNotIn("password", target)
        self.assertNotIn("password_hash", target)

    def test_criterion_7_online_status_and_last_seen(self):
        """Criterion 7: While a user is using the site, the accounts screen shows them online;
        6 minutes after their last request it shows them offline, with that time as last seen.
        """
        t0 = datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc)
        with patch("hasc.web.security.now", return_value=t0):
            emp_client = self._login("emp_user", "emp_pass")
            # User makes a request to update last_seen_at
            res_req = emp_client.get("/api/sheets")
            self.assertEqual(res_req.status_code, 200)

            owner_client = self._login("owner_admin", "owner_pass")
            res_list = owner_client.get("/api/admin/users")
            self.assertEqual(res_list.status_code, 200)
            emp_info = next(u for u in res_list.json() if u["id"] == self.emp_id)
            self.assertTrue(emp_info["online"])
            self.assertEqual(emp_info["last_seen_at"], t0.isoformat())

        # 6 minutes later: offline, last_seen_at remains t0
        t1 = t0 + timedelta(minutes=6)
        with patch("hasc.web.security.now", return_value=t1):
            owner_client = self._login("owner_admin", "owner_pass")
            res_list2 = owner_client.get("/api/admin/users")
            self.assertEqual(res_list2.status_code, 200)
            emp_info2 = next(u for u in res_list2.json() if u["id"] == self.emp_id)
            self.assertFalse(emp_info2["online"])
            self.assertEqual(emp_info2["last_seen_at"], t0.isoformat())

    def test_criterion_8_owner_rules_and_admin_restrictions(self):
        """Criterion 8: A non-owner admin cannot give or remove the admin role, or reset, log out,
        deactivate or change the role of any admin account (the owner and themselves included):
        the API returns 403 and nothing changes. Nobody can deactivate or demote the owner,
        and nobody can change their own role, deactivate, reset or log out themselves from the screen.
        The list marks the owner (is_owner in the API).
        """
        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")
        conn = get_connection(self.db_path)

        # 1. Non-owner admin cannot give admin role
        res_give = admin_client.post(
            "/api/admin/users",
            json={"username": "cannot_be_admin", "role": "admin"},
        )
        self.assertEqual(res_give.status_code, 403)
        self.assertIsNone(get_user_by_username(conn, "cannot_be_admin"))

        res_promote = admin_client.patch(
            f"/api/admin/users/{self.emp_id}",
            json={"role": "admin"},
        )
        self.assertEqual(res_promote.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.emp_id)["role"], "employee")

        # 2. Non-owner admin cannot remove admin role from another admin
        res_demote = admin_client.patch(
            f"/api/admin/users/{self.admin3_id}",
            json={"role": "employee"},
        )
        self.assertEqual(res_demote.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.admin3_id)["role"], "admin")

        # 3. Non-owner admin cannot reset, log out, deactivate, or change role of any admin account
        for target_id in (self.admin3_id, self.owner_id):
            # Reset
            res = admin_client.patch(f"/api/admin/users/{target_id}", json={"reset_password": True})
            self.assertEqual(res.status_code, 403)
            # Log out
            res = admin_client.post(f"/api/admin/users/{target_id}/logout")
            self.assertEqual(res.status_code, 403)
            # Deactivate
            res = admin_client.patch(f"/api/admin/users/{target_id}", json={"active": 0})
            self.assertEqual(res.status_code, 403)
            self.assertEqual(get_user_by_id(conn, target_id)["active"], 1)
            # Change role
            res = admin_client.patch(f"/api/admin/users/{target_id}", json={"role": "boss"})
            self.assertEqual(res.status_code, 403)
            self.assertEqual(get_user_by_id(conn, target_id)["role"], "admin")

        # 4. Nobody can deactivate or demote the owner (tested with owner themselves)
        res_self_deact = owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 0})
        self.assertEqual(res_self_deact.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["active"], 1)

        res_self_demote = owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"role": "boss"})
        self.assertEqual(res_self_demote.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["role"], "admin")

        # 5. Nobody can change their own role, deactivate, reset or log out themselves
        for client, uid in ((admin_client, self.admin_id), (owner_client, self.owner_id)):
            res = client.patch(f"/api/admin/users/{uid}", json={"role": "boss"})
            self.assertEqual(res.status_code, 403)
            res = client.patch(f"/api/admin/users/{uid}", json={"active": 0})
            self.assertEqual(res.status_code, 403)
            res = client.patch(f"/api/admin/users/{uid}", json={"reset_password": True})
            self.assertEqual(res.status_code, 403)
            res = client.post(f"/api/admin/users/{uid}/logout")
            self.assertEqual(res.status_code, 403)

        # 6. The list marks the owner (is_owner in the API)
        res_list = owner_client.get("/api/admin/users")
        self.assertEqual(res_list.status_code, 200)
        items = res_list.json()
        owner_item = next(u for u in items if u["id"] == self.owner_id)
        sub_admin_item = next(u for u in items if u["id"] == self.admin_id)
        self.assertTrue(owner_item["is_owner"])
        self.assertFalse(sub_admin_item["is_owner"])

    def test_criterion_9_username_case_insensitivity_and_no_reuse(self):
        """Criterion 9: Hung cannot be created when hung exists, active or deactivated,
        and hung can log in by typing Hung.
        """
        owner_client = self._login("owner_admin", "owner_pass")

        # 1. Create hung
        res_create = owner_client.post(
            "/api/admin/users",
            json={"username": "hung", "role": "employee"},
        )
        self.assertEqual(res_create.status_code, 201)
        hung_pwd = res_create.json()["temporary_password"]
        hung_id = res_create.json()["id"]

        # 2. Cannot create Hung while hung exists
        res_dup = owner_client.post(
            "/api/admin/users",
            json={"username": "Hung", "role": "employee"},
        )
        self.assertEqual(res_dup.status_code, 400)
        self.assertEqual(res_dup.json()["error"]["code"], "duplicate_username")

        # 3. hung can log in by typing Hung
        res_login = self.client.post(
            "/api/login",
            json={"username": "Hung", "password": hung_pwd},
        )
        self.assertEqual(res_login.status_code, 200)

        # 4. Deactivate hung
        res_deact = owner_client.patch(f"/api/admin/users/{hung_id}", json={"active": 0})
        self.assertEqual(res_deact.status_code, 200)

        # 5. Cannot create Hung or hung even after deactivation
        for name in ("hung", "Hung"):
            res_after = owner_client.post(
                "/api/admin/users",
                json={"username": name, "role": "employee"},
            )
            self.assertEqual(res_after.status_code, 400)
            self.assertEqual(res_after.json()["error"]["code"], "duplicate_username")

    def test_criterion_10_username_format_validation(self):
        """Criterion 10: A username with a space, a Vietnamese letter, or fewer than 3
        or more than 32 characters is refused with a Vietnamese message.
        """
        owner_client = self._login("owner_admin", "owner_pass")

        bad_usernames = [
            ("hu ng", "khoảng trắng"),
            (" hung", "khoảng trắng"),
            ("hung ", "khoảng trắng"),
            ("hưng", "a-z"),
            ("nguyễn", "a-z"),
            ("ab", "3 đến 32"),
            ("a" * 33, "3 đến 32"),
            ("user@mail", "a-z"),
        ]

        for username, hint in bad_usernames:
            res = owner_client.post(
                "/api/admin/users",
                json={"username": username, "role": "employee"},
            )
            self.assertEqual(res.status_code, 400, f"Expected 400 for username {username!r}")
            err = res.json()["error"]
            self.assertEqual(err["code"], "invalid_username")
            self.assertTrue(len(err["message"]) > 0)
            self.assertIn(hint, err["message"].lower())

        # Valid usernames succeed
        res_valid = owner_client.post(
            "/api/admin/users",
            json={"username": "valid.user_1", "role": "employee"},
        )
        self.assertEqual(res_valid.status_code, 201)

    def test_criterion_11_account_action_history_and_role_access(self):
        """Criterion 11: Each account action writes a history entry an admin can see;
        an employee or boss requesting the history gets 403.
        """
        owner_client = self._login("owner_admin", "owner_pass")

        # 1. Action: Add
        res_add = owner_client.post(
            "/api/admin/users",
            json={"username": "audit_emp", "role": "employee"},
        )
        self.assertEqual(res_add.status_code, 201)
        audit_id = res_add.json()["id"]

        # 2. Action: Role change
        res_role = owner_client.patch(
            f"/api/admin/users/{audit_id}",
            json={"role": "boss"},
        )
        self.assertEqual(res_role.status_code, 200)

        # 3. Action: Reset
        res_reset = owner_client.patch(
            f"/api/admin/users/{audit_id}",
            json={"reset_password": True},
        )
        self.assertEqual(res_reset.status_code, 200)

        # 4. Action: Deactivate
        res_deact = owner_client.patch(
            f"/api/admin/users/{audit_id}",
            json={"active": 0},
        )
        self.assertEqual(res_deact.status_code, 200)

        # 5. Action: Reactivate
        res_react = owner_client.patch(
            f"/api/admin/users/{audit_id}",
            json={"active": 1},
        )
        self.assertEqual(res_react.status_code, 200)

        # 6. Action: Log out
        res_logout = owner_client.post(f"/api/admin/users/{audit_id}/logout")
        self.assertEqual(res_logout.status_code, 204)

        # Admin fetches events: newest first
        res_events = owner_client.get("/api/admin/users/events")
        self.assertEqual(res_events.status_code, 200)
        events = res_events.json()
        self.assertTrue(len(events) >= 6)

        target_events = [e for e in events if e["field"] == f"user:{audit_id}"]
        # Expect order (newest first): logout, reactivate, deactivate, reset, role, add
        expected_actions = [
            "account_logout",
            "account_reactivate",
            "account_deactivate",
            "account_reset",
            "account_role",
            "account_add",
        ]
        actual_actions = [e["action"] for e in target_events[:6]]
        self.assertEqual(actual_actions, expected_actions)

        # Check values
        add_ev = next(e for e in target_events if e["action"] == "account_add")
        self.assertEqual(add_ev["new_value"], "employee")
        self.assertEqual(add_ev["actor_username"], "owner_admin")
        self.assertEqual(add_ev["target_username"], "audit_emp")

        role_ev = next(e for e in target_events if e["action"] == "account_role")
        self.assertEqual(role_ev["old_value"], "employee")
        self.assertEqual(role_ev["new_value"], "boss")

        deact_ev = next(e for e in target_events if e["action"] == "account_deactivate")
        self.assertEqual(deact_ev["old_value"], "1")
        self.assertEqual(deact_ev["new_value"], "0")

        react_ev = next(e for e in target_events if e["action"] == "account_reactivate")
        self.assertEqual(react_ev["old_value"], "0")
        self.assertEqual(react_ev["new_value"], "1")

        reset_ev = next(e for e in target_events if e["action"] == "account_reset")
        self.assertIsNone(reset_ev["old_value"])
        self.assertIsNone(reset_ev["new_value"])

        logout_ev = next(e for e in target_events if e["action"] == "account_logout")
        self.assertIsNone(logout_ev["old_value"])
        self.assertIsNone(logout_ev["new_value"])

        # Non-admins get 403 on GET /api/admin/users/events
        for user, pwd in (("emp_user", "emp_pass"), ("boss_user", "boss_pass"), ("op_user", "op_pass")):
            other_client = self._login(user, pwd)
            res_forbidden = other_client.get("/api/admin/users/events")
            self.assertEqual(res_forbidden.status_code, 403)
            self.assertEqual(res_forbidden.json()["error"]["code"], "forbidden")

    def test_admin_can_create_and_assign_operator_role(self):
        """Admins can create and assign operator accounts; existing operators still work."""
        owner_client = self._login("owner_admin", "owner_pass")

        # 1. API accepts operator when creating an account
        res_create = owner_client.post(
            "/api/admin/users",
            json={"username": "new_operator", "role": "operator"},
        )
        self.assertEqual(res_create.status_code, 201)

        # 2. API accepts operator when changing a role
        res_patch = owner_client.patch(
            f"/api/admin/users/{self.emp_id}",
            json={"role": "operator"},
        )
        self.assertEqual(res_patch.status_code, 200)
        self.assertEqual(res_patch.json()["role"], "operator")

        # 3. Existing operator account still logs in
        op_client = self._login("op_user", "op_pass")
        # And has access to operator functions
        res_limits = op_client.get("/api/limits")
        self.assertEqual(res_limits.status_code, 200)

    def test_criterion_14_admin_only_accounts_access(self):
        """Criterion 14: The sidebar shows Tài khoản to admins only; a non-admin requesting
        the accounts page or GET /api/admin/users gets 403.
        """
        # Non-admins: employee, boss, operator
        for user, pwd in (("emp_user", "emp_pass"), ("boss_user", "boss_pass"), ("op_user", "op_pass")):
            c = self._login(user, pwd)
            res = c.get("/api/admin/users")
            self.assertEqual(res.status_code, 403, f"Expected 403 for {user}")
            self.assertEqual(res.json()["error"]["code"], "forbidden")

        # Unauthenticated gets 401
        res_unauth = self.client.get("/api/admin/users")
        self.assertEqual(res_unauth.status_code, 401)

    # -------------------------------------------------------------------------
    # Owner Rules For Every Action (Against Owner & Self-Actions)
    # Actions: add, reset, role change, deactivate, reactivate, log-out
    # -------------------------------------------------------------------------

    def test_owner_rules_action_add_against_owner_and_self(self):
        """Owner rule: add action.
        - Non-owner admin cannot create an admin account (403).
        - Non-owner admin cannot create account with owner's username (400 duplicate).
        - Owner cannot create account with owner's username (400 duplicate).
        - Owner can create an admin account (201).
        """
        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")

        # Non-owner admin creates admin -> 403
        res = admin_client.post("/api/admin/users", json={"username": "test_admin", "role": "admin"})
        self.assertEqual(res.status_code, 403)

        # Against owner: non-owner tries to add account with owner username -> 400 duplicate
        res = admin_client.post("/api/admin/users", json={"username": "owner_admin", "role": "employee"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "duplicate_username")

        # Owner acting on self: owner adds account with own username -> 400 duplicate
        res = owner_client.post("/api/admin/users", json={"username": "owner_admin", "role": "employee"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "duplicate_username")

        # Owner creates admin account -> 201
        res = owner_client.post("/api/admin/users", json={"username": "new_admin_by_owner", "role": "admin"})
        self.assertEqual(res.status_code, 201)

    def test_owner_rules_action_reset_against_owner_and_self(self):
        """Owner rule: reset action.
        - Against owner: non-owner admin cannot reset owner password (403).
        - As owner acting on self: owner cannot reset own password from screen (403).
        - Non-owner admin cannot reset own password from screen (403).
        - Non-owner admin cannot reset another admin (403).
        - Owner can reset another admin (200).
        """
        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")
        conn = get_connection(self.db_path)
        owner_hash_before = get_user_by_id(conn, self.owner_id)["password_hash"]

        # Against owner
        res = admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"reset_password": True})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["password_hash"], owner_hash_before)

        # Owner acting on self
        res = owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"reset_password": True})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["password_hash"], owner_hash_before)

        # Non-owner acting on self
        res = admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"reset_password": True})
        self.assertEqual(res.status_code, 403)

        # Non-owner acting on other admin
        res = admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"reset_password": True})
        self.assertEqual(res.status_code, 403)

        # Owner resets another admin -> 200
        res = owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"reset_password": True})
        self.assertEqual(res.status_code, 200)
        self.assertIn("temporary_password", res.json())

    def test_owner_rules_action_role_change_against_owner_and_self(self):
        """Owner rule: role change action.
        - Against owner: non-owner admin cannot demote owner (403).
        - As owner acting on self: owner cannot change own role (403).
        - Non-owner admin cannot change own role (403).
        - Non-owner admin cannot change role of another admin (403).
        - Non-owner admin cannot promote anyone to admin (403).
        - Owner can change role of another admin (200).
        """
        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")
        conn = get_connection(self.db_path)

        # Against owner
        res = admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"role": "boss"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["role"], "admin")

        # Owner acting on self
        res = owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"role": "employee"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["role"], "admin")

        # Non-owner admin acting on self
        res = admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"role": "boss"})
        self.assertEqual(res.status_code, 403)

        # Non-owner admin demoting other admin
        res = admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"role": "boss"})
        self.assertEqual(res.status_code, 403)

        # Non-owner admin promoting employee to admin
        res = admin_client.patch(f"/api/admin/users/{self.emp_id}", json={"role": "admin"})
        self.assertEqual(res.status_code, 403)

        # Owner changes role of another admin -> 200
        res = owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"role": "boss"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get_user_by_id(conn, self.admin3_id)["role"], "boss")

    def test_owner_rules_action_deactivate_against_owner_and_self(self):
        """Owner rule: deactivate action.
        - Against owner: non-owner admin cannot deactivate owner (403).
        - As owner acting on self: owner cannot deactivate themselves (403).
        - Non-owner admin cannot deactivate themselves (403).
        - Non-owner admin cannot deactivate another admin (403).
        - Owner can deactivate another admin (200).
        """
        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")
        conn = get_connection(self.db_path)

        # Against owner
        res = admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 0})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["active"], 1)

        # Owner acting on self
        res = owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 0})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(get_user_by_id(conn, self.owner_id)["active"], 1)

        # Non-owner admin acting on self
        res = admin_client.patch(f"/api/admin/users/{self.admin_id}", json={"active": 0})
        self.assertEqual(res.status_code, 403)

        # Non-owner admin deactivating another admin
        res = admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 0})
        self.assertEqual(res.status_code, 403)

        # Owner deactivates another admin -> 200
        res = owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 0})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get_user_by_id(conn, self.admin3_id)["active"], 0)

    def test_owner_rules_action_reactivate_against_owner_and_self(self):
        """Owner rule: reactivate action.
        - Against owner: cannot alter owner active status (403).
        - As owner acting on self: cannot alter own active status (403).
        - Non-owner admin cannot reactivate an admin (403).
        - Owner can reactivate a deactivated admin (200).
        - Non-owner admin can reactivate a deactivated employee (200).
        """
        conn = get_connection(self.db_path)
        # Deactivate admin3 and emp_id directly for setup
        from hasc.db import set_user_active
        set_user_active(conn, self.admin3_id, 0)
        set_user_active(conn, self.emp_id, 0)

        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")

        # Against owner
        res = admin_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 1})
        self.assertEqual(res.status_code, 403)

        # Owner acting on self
        res = owner_client.patch(f"/api/admin/users/{self.owner_id}", json={"active": 1})
        self.assertEqual(res.status_code, 403)

        # Non-owner admin reactivates another admin -> 403
        res = admin_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 1})
        self.assertEqual(res.status_code, 403)

        # Owner reactivates admin3 -> 200
        res = owner_client.patch(f"/api/admin/users/{self.admin3_id}", json={"active": 1})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get_user_by_id(conn, self.admin3_id)["active"], 1)

        # Non-owner admin reactivates employee -> 200
        res = admin_client.patch(f"/api/admin/users/{self.emp_id}", json={"active": 1})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get_user_by_id(conn, self.emp_id)["active"], 1)

    def test_owner_rules_action_logout_against_owner_and_self(self):
        """Owner rule: log out action.
        - Against owner: non-owner admin cannot log out owner via admin endpoint (403).
        - As owner acting on self: owner cannot log out themselves via admin endpoint (403).
        - Non-owner admin cannot log out themselves via admin endpoint (403).
        - Non-owner admin cannot log out another admin (403).
        - Owner can log out another admin (204).
        - Non-owner admin can log out an employee (204).
        """
        admin_client = self._login("sub_admin", "admin_pass")
        owner_client = self._login("owner_admin", "owner_pass")

        # Against owner
        res = admin_client.post(f"/api/admin/users/{self.owner_id}/logout")
        self.assertEqual(res.status_code, 403)
        # Owner's session is still valid
        self.assertEqual(owner_client.get("/api/admin/users").status_code, 200)

        # Owner acting on self
        res = owner_client.post(f"/api/admin/users/{self.owner_id}/logout")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(owner_client.get("/api/admin/users").status_code, 200)

        # Non-owner admin acting on self
        res = admin_client.post(f"/api/admin/users/{self.admin_id}/logout")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(admin_client.get("/api/admin/users").status_code, 200)

        # Non-owner admin logging out another admin
        admin3_client = self._login("third_admin", "admin3_pass")
        res = admin_client.post(f"/api/admin/users/{self.admin3_id}/logout")
        self.assertEqual(res.status_code, 403)
        # Admin3 session still active
        self.assertEqual(admin3_client.get("/api/admin/users").status_code, 200)

        # Owner logs out admin3 -> 204
        res = owner_client.post(f"/api/admin/users/{self.admin3_id}/logout")
        self.assertEqual(res.status_code, 204)
        # Admin3 session is ended
        self.assertEqual(admin3_client.get("/api/admin/users").status_code, 401)

        # Non-owner admin logs out employee -> 204
        emp_client = self._login("emp_user", "emp_pass")
        res = admin_client.post(f"/api/admin/users/{self.emp_id}/logout")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(emp_client.get("/api/sheets").status_code, 401)

    def test_criterion_5_session_ending_for_account_actions(self):
        """Criterion 5: A reset, log-out, role change or deactivation ends that account's open sessions."""
        admin_client = self._login("sub_admin", "admin_pass")

        # 1. Reset ends sessions
        emp1_client = self._login("emp_user", "emp_pass")
        self.assertEqual(emp1_client.get("/api/sheets").status_code, 200)
        res_reset = admin_client.patch(f"/api/admin/users/{self.emp_id}", json={"reset_password": True})
        self.assertEqual(res_reset.status_code, 200)
        new_pwd = res_reset.json()["temporary_password"]
        self.assertEqual(emp1_client.get("/api/sheets").status_code, 401)

        # Log in again with new password and change it
        emp1_client.post("/api/login", json={"username": "emp_user", "password": new_pwd})
        emp1_client.post("/api/password", json={"old": new_pwd, "new": "permanent_pass1"})

        # 2. Role change ends sessions
        emp2_client = self._login("emp_user", "permanent_pass1")
        self.assertEqual(emp2_client.get("/api/sheets").status_code, 200)
        res_role = admin_client.patch(f"/api/admin/users/{self.emp_id}", json={"role": "boss"})
        self.assertEqual(res_role.status_code, 200)
        self.assertEqual(emp2_client.get("/api/sheets").status_code, 401)

        # 3. Log out ends sessions
        boss_client = self._login("emp_user", "permanent_pass1")
        self.assertEqual(boss_client.get("/api/sheets").status_code, 200)
        res_logout = admin_client.post(f"/api/admin/users/{self.emp_id}/logout")
        self.assertEqual(res_logout.status_code, 204)
        self.assertEqual(boss_client.get("/api/sheets").status_code, 401)

        # 4. Deactivation ends sessions
        boss_client2 = self._login("emp_user", "permanent_pass1")
        self.assertEqual(boss_client2.get("/api/sheets").status_code, 200)
        res_deact = admin_client.patch(f"/api/admin/users/{self.emp_id}", json={"active": 0})
        self.assertEqual(res_deact.status_code, 200)
        self.assertEqual(boss_client2.get("/api/sheets").status_code, 401)
