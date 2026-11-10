"""Each user's own Tài khoản page (P148), and the admins' view of reset requests. Made-up accounts only."""
import gc
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from hasc.db import create_user, get_connection, get_user_by_id
from hasc.web import self_account
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestSelfAccount(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)
        conn = get_connection(self.db_path)
        self.ids = {
            name: create_user(conn, name, hash_password("pass"), role=role, active=1)
            for name, role in (("op_user", "operator"), ("emp_user", "employee"),
                               ("boss_user", "boss"), ("admin_user", "admin"))
        }
        conn.execute("UPDATE users SET is_owner = 1 WHERE id = ?", (self.ids["admin_user"],))
        conn.commit()
        conn.close()
        self_account._forgot_seen.clear()

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _client(self, username, password="pass"):
        client = TestClient(self.app)
        res = client.post("/api/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return client

    def _user(self, name):
        conn = get_connection(self.db_path)
        try:
            return dict(get_user_by_id(conn, self.ids[name]))
        finally:
            conn.close()

    def _events(self, action):
        conn = get_connection(self.db_path)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM events WHERE action = ? ORDER BY id", (action,))]
        finally:
            conn.close()

    def test_every_role_opens_its_own_page_with_role_shown(self):
        for name, label in (("op_user", "Xưởng"), ("emp_user", "Văn phòng"),
                            ("boss_user", "Ban giám đốc"), ("admin_user", "Quản trị")):
            with self.subTest(name=name):
                res = self._client(name).get("/account")
                self.assertEqual(res.status_code, 200)
                self.assertIn("Tài khoản của tôi", res.text)
                self.assertIn(name, res.text)
                self.assertIn(label, res.text)
                self.assertIn('action="/account/username"', res.text)
                self.assertNotIn('name="role"', res.text)  # no way to pick a role here

    def test_rename_changes_username_logs_it_and_keeps_role(self):
        client = self._client("emp_user")
        res = client.post("/account/username", data={"username": "Mai.Le", "role": "admin"}, follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        user = self._user("emp_user")
        self.assertEqual(user["username"], "mai.le")
        self.assertEqual(user["role"], "employee")  # an extra role field is ignored
        events = self._events("account_rename")
        self.assertEqual([(e["old_value"], e["new_value"], e["user_id"]) for e in events],
                         [("emp_user", "mai.le", self.ids["emp_user"])])
        self.assertEqual(client.get("/account").status_code, 200)  # the session survives the rename
        self._client("mai.le")
        self.assertEqual(TestClient(self.app).post("/api/login", json={"username": "emp_user", "password": "pass"}).status_code, 401)

    def test_rename_refuses_taken_and_invalid_names(self):
        client = self._client("emp_user")
        for name in ("BOSS_USER", "ab", "has space", "đức"):
            with self.subTest(name=name):
                res = client.post("/account/username", data={"username": name})
                self.assertEqual(res.status_code, 400)
                self.assertIn("alert--error", res.text)
        self.assertEqual(self._user("emp_user")["username"], "emp_user")
        self.assertEqual(self._events("account_rename"), [])

    def test_password_change_needs_old_password_and_logs_it(self):
        client = self._client("boss_user")
        bad = client.post("/account/password", data={"old_password": "wrong", "new_password": "n3w-pass", "confirm_password": "n3w-pass"})
        self.assertEqual(bad.status_code, 400)
        mismatch = client.post("/account/password", data={"old_password": "pass", "new_password": "a", "confirm_password": "b"})
        self.assertEqual(mismatch.status_code, 400)
        ok = client.post("/account/password", data={"old_password": "pass", "new_password": "n3w-pass", "confirm_password": "n3w-pass"}, follow_redirects=False)
        self.assertEqual(ok.status_code, 303)
        self._client("boss_user", "n3w-pass")
        self.assertEqual(len(self._events("account_password")), 1)

    def test_reset_request_reaches_admins_once_and_clears_after_reset(self):
        client = self._client("emp_user")
        for _ in range(2):
            self.assertEqual(client.post("/account/reset-request", follow_redirects=False).status_code, 303)
        self.assertEqual(len(self._events("account_reset_request")), 1)  # one waiting request, not two
        self.assertIn("Đang chờ", client.get("/account").text)

        admin = self._client("admin_user")
        page = admin.get("/admin/accounts").text
        self.assertIn("Yêu cầu đặt lại mật khẩu", page)
        self.assertIn('class="card reset-requests"', page)

        res = admin.post("/admin/accounts", data={"action": "reset", "user_id": self.ids["emp_user"]})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('class="card reset-requests"', admin.get("/admin/accounts").text)

    def test_forgot_password_from_login_page_without_revealing_accounts(self):
        anon = TestClient(self.app)
        self.assertIn('href="/forgot-password"', anon.get("/login").text)
        known = anon.post("/forgot-password", data={"username": "op_user"})
        unknown = anon.post("/forgot-password", data={"username": "nobody_here"})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertIn("Đã gửi yêu cầu", known.text)
        self.assertIn("Đã gửi yêu cầu", unknown.text)
        events = self._events("account_reset_request")
        self.assertEqual([e["field"] for e in events], [f"user:{self.ids['op_user']}"])

    def test_account_actions_sit_in_one_menu_per_row(self):
        page = self._client("admin_user").get("/admin/accounts").text
        self.assertIn('class="row-menu"', page)
        self.assertIn("Thao tác ▾", page)
        self.assertIn("[!document.querySelector('.row-menu[open]')]", page)  # polling waits while a menu is open

    def test_temporary_password_users_must_change_it_before_using_the_page(self):
        conn = get_connection(self.db_path)
        conn.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (self.ids["emp_user"],))
        conn.commit()
        conn.close()
        client = self._client("emp_user")
        res = client.get("/account", follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/change-password")
        res = client.post("/account/username", data={"username": "sneaky"}, follow_redirects=False)
        self.assertNotEqual(res.status_code, 200)
        self.assertEqual(self._user("emp_user")["username"], "emp_user")

    def test_own_password_change_logs_out_other_sessions_but_not_this_one(self):
        phone, laptop = self._client("emp_user"), self._client("emp_user")
        res = laptop.post("/account/password", data={"old_password": "pass", "new_password": "n3w-pass",
                                                     "confirm_password": "n3w-pass"}, follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertEqual(laptop.get("/account").status_code, 200)
        self.assertEqual(phone.get("/api/sheets").status_code, 401)

    def test_forgot_password_is_rate_limited_per_address(self):
        anon = TestClient(self.app)
        for _ in range(self_account.FORGOT_LIMIT):
            anon.post("/forgot-password", data={"username": "nobody"})
        res = anon.post("/forgot-password", data={"username": "op_user"})
        self.assertIn("Đã gửi yêu cầu", res.text)  # same answer, but nothing recorded
        self.assertEqual(self._events("account_reset_request"), [])

    def test_owner_request_says_how_to_reset_on_the_server(self):
        conn = get_connection(self.db_path)
        conn.execute("UPDATE users SET is_owner = 0")
        conn.execute("UPDATE users SET is_owner = 1 WHERE id = ?", (self.ids["boss_user"],))
        conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (self.ids["boss_user"],))
        conn.commit()
        conn.close()
        self._client("boss_user").post("/account/reset-request")
        page = self._client("admin_user").get("/admin/accounts").text
        self.assertIn("scripts/create_user.py --owner", page)


if __name__ == "__main__":
    unittest.main()
