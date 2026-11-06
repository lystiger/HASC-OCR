from pathlib import Path
import gc
import tempfile
import unittest

from fastapi.testclient import TestClient

from hasc.db import get_applied_migrations, get_connection
from hasc.web.app import create_app


class TestAppFactory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.app = create_app(db_path=self.db_path)

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def test_startup_runs_migrations(self):
        with TestClient(self.app) as client:
            # During startup/lifespan, migrations must be executed
            conn = get_connection(self.db_path, auto_migrate=False)
            applied = get_applied_migrations(conn)
            self.assertIn("001_initial_schema.sql", applied)

    def test_static_files_mounted(self):
        with TestClient(self.app) as client:
            res = client.get("/static/css/app.css")
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/css", res.headers.get("content-type", ""))
            self.assertIn("--color-action", res.text)

    def test_templates_mounted_and_login_page_renders_vietnamese(self):
        with TestClient(self.app) as client:
            res = client.get("/login")
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/html", res.headers.get("content-type", ""))
            self.assertIn("Đăng nhập", res.text)
            self.assertIn("Tên đăng nhập", res.text)
            self.assertIn("Mật khẩu", res.text)

    def test_criterion_10_plain_http_redirects_to_https(self):
        """Criterion 10: A plain HTTP request does not reach a login form or carry a password."""
        app_secure = create_app(db_path=self.db_path, enforce_https=True)
        # Using base_url with http scheme
        client = TestClient(app_secure, base_url="http://testserver")
        res = client.get("/login", follow_redirects=False)
        self.assertIn(res.status_code, (301, 307, 308))
        self.assertTrue(res.headers["location"].startswith("https://"))
