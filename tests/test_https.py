"""Criterion 17 (spec-1.1.0): with HTTPS on, port 8080 only redirects (and serves the authority),
and session cookies are Secure."""
from pathlib import Path
import gc
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from hasc.db import create_user, get_connection
from hasc.web.app import create_app
from hasc.web.redirect import app as redirect_app
from hasc.web.security import hash_password


class TestRedirectApp(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(redirect_app, base_url="http://testserver:8080")

    def test_criterion_17_redirect_keeps_path_and_query(self):
        res = self.client.get("/office/report?date=2026-11-08&shift=1", follow_redirects=False)
        self.assertEqual(res.status_code, 308)
        self.assertEqual(res.headers["location"], "https://testserver/office/report?date=2026-11-08&shift=1")

    def test_criterion_17_redirect_drops_the_port_and_keeps_an_address(self):
        res = self.client.get("/login", headers={"host": "192.168.1.10:8080"}, follow_redirects=False)
        self.assertEqual(res.status_code, 308)
        self.assertEqual(res.headers["location"], "https://192.168.1.10/login")

    def test_criterion_17_never_reaches_a_login_form(self):
        for method, path, status in (("get", "/login", 308), ("get", "/", 308), ("post", "/login", 405), ("post", "/api/login", 405)):
            with self.subTest(method=method, path=path):
                res = getattr(self.client, method)(path, follow_redirects=False)
                self.assertEqual(res.status_code, status)
                self.assertNotIn("Mật khẩu", res.text)
                self.assertNotIn("<form", res.text)

    def test_criterion_17_bad_host_is_refused(self):
        res = self.client.get("/login", headers={"host": "evil.example/x"}, follow_redirects=False)
        self.assertEqual(res.status_code, 400)

    def test_criterion_17_ca_cert_is_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            ca = Path(tmp) / "ca.crt"
            ca.write_text("-----BEGIN CERTIFICATE-----\nmade-up\n-----END CERTIFICATE-----\n")
            with patch.dict(os.environ, {"HASC_CA_CERT": str(ca)}):
                res = self.client.get("/ca.crt", follow_redirects=False)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.headers["content-type"], "application/x-x509-ca-cert")
            self.assertIn("made-up", res.text)

    def test_criterion_17_missing_ca_cert_is_404(self):
        with patch.dict(os.environ, {"HASC_CA_CERT": "/nonexistent/ca.crt"}):
            res = self.client.get("/ca.crt", follow_redirects=False)
        self.assertEqual(res.status_code, 404)


class TestSecureCookies(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _login_cookie(self, environ: dict) -> str:
        with patch.dict(os.environ, environ):
            app = create_app(db_path=self.db_path)
        with TestClient(app, base_url="https://testserver") as client:
            conn = get_connection(self.db_path)
            try:
                if not conn.execute("SELECT 1 FROM users WHERE username = 'emp_cookie'").fetchone():
                    create_user(conn, "emp_cookie", hash_password("CookiePass123"), "employee")
                    conn.commit()
            finally:
                conn.close()
            res = client.post("/api/login", json={"username": "emp_cookie", "password": "CookiePass123"})
            self.assertEqual(res.status_code, 200)
            return res.headers["set-cookie"]

    def test_criterion_17_session_cookie_is_secure_with_https_on(self):
        self.assertIn("secure", self._login_cookie({"HASC_HTTPS": "1"}).lower())

    def test_session_cookie_is_not_secure_with_https_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HASC_HTTPS", None)
            cookie = self._login_cookie({})
        self.assertNotIn("secure", cookie.lower())


if __name__ == "__main__":
    unittest.main()
