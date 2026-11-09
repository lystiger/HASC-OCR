"""The admins' update banner and version (spec 1.1.0 job C, P146), with made-up status files."""
from datetime import datetime, timedelta, timezone
import gc
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from hasc.db import create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password
from hasc.web.update_status import read_update_status

BANNER = "Có phiên bản mới"
NOTE = "Không kiểm tra được bản cập nhật"


def _iso(moment: datetime) -> str:
    return moment.isoformat()


class TestReadUpdateStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "update-status.json"
        self.now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, data, bom=False):
        text = data if isinstance(data, str) else json.dumps(data)
        self.path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

    def test_newer_release_is_available(self):
        self._write({"installed": "v1.0.4", "latest": "v1.1.0", "available": True,
                     "checked_at": _iso(self.now), "ok_at": _iso(self.now), "error": None})
        status = read_update_status(str(self.path), now=self.now)
        self.assertEqual(status, {"available": True, "latest": "v1.1.0", "installed": "v1.0.4", "error": None})

    def test_up_to_date_shows_nothing(self):
        self._write({"installed": "v1.1.0", "latest": "v1.1.0", "available": False,
                     "ok_at": _iso(self.now), "error": None})
        self.assertIsNone(read_update_status(str(self.path), now=self.now))

    def test_missing_or_broken_file_shows_nothing(self):
        self.assertIsNone(read_update_status(str(self.path), now=self.now))
        self._write("{not json")
        self.assertIsNone(read_update_status(str(self.path), now=self.now))
        self._write("[1, 2]")
        self.assertIsNone(read_update_status(str(self.path), now=self.now))
        self.assertIsNone(read_update_status("", now=self.now))

    def test_byte_order_mark_is_tolerated(self):
        self._write({"installed": "v1.0.4", "latest": "v1.1.0", "available": True, "error": None}, bom=True)
        self.assertTrue(read_update_status(str(self.path), now=self.now)["available"])

    def test_recent_error_stays_quiet(self):
        self._write({"installed": "v1.1.0", "latest": "v1.1.0", "available": False,
                     "ok_at": _iso(self.now - timedelta(days=2)), "error": "GitHub refused the request"})
        self.assertIsNone(read_update_status(str(self.path), now=self.now))

    def test_stale_error_is_shown(self):
        self._write({"installed": "v1.1.0", "latest": "v1.1.0", "available": False,
                     "ok_at": _iso(self.now - timedelta(days=4)), "error": "GitHub refused the request"})
        self.assertEqual(read_update_status(str(self.path), now=self.now)["error"], "GitHub refused the request")

    def test_never_checked_error_is_shown(self):
        self._write({"installed": "v1.1.0", "latest": None, "available": False, "ok_at": None,
                     "error": "No GitHub token yet: run HASC update once as an administrator."})
        self.assertIn("No GitHub token", read_update_status(str(self.path), now=self.now)["error"])


class TestUpdateBannerPages(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.status = Path(self.temp_dir.name) / "update-status.json"
        self.status.write_text(json.dumps({
            "installed": "v1.0.4", "latest": "v1.1.0", "available": True,
            "ok_at": datetime.now(timezone.utc).isoformat(), "error": None,
        }))
        with patch.dict(os.environ, {"HASC_UPDATE_STATUS": str(self.status), "HASC_VERSION": "v1.0.4"}):
            self.app = create_app(db_path=self.db_path)
        conn = get_connection(self.db_path)
        for name, role in (("emp_user", "employee"), ("boss_user", "boss"), ("admin_user", "admin")):
            create_user(conn, name, hash_password("pass"), role=role, active=1)
        conn.commit()
        conn.close()
        self.env = patch.dict(os.environ, {"HASC_UPDATE_STATUS": str(self.status)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _page(self, username: str) -> str:
        client = TestClient(self.app)
        self.assertEqual(client.post("/api/login", json={"username": username, "password": "pass"}).status_code, 200)
        res = client.get("/office")
        self.assertEqual(res.status_code, 200)
        return res.text

    def test_admin_sees_the_banner_and_the_version(self):
        html = self._page("admin_user")
        self.assertIn(BANNER, html)
        self.assertIn("v1.1.0", html)
        self.assertIn("Phiên bản v1.0.4", html)

    def test_other_roles_see_neither(self):
        for username in ("emp_user", "boss_user"):
            with self.subTest(username=username):
                html = self._page(username)
                self.assertNotIn(BANNER, html)
                self.assertNotIn("Phiên bản v1.0.4", html)

    def test_no_banner_when_up_to_date(self):
        self.status.write_text(json.dumps({"installed": "v1.1.0", "latest": "v1.1.0", "available": False,
                                           "ok_at": datetime.now(timezone.utc).isoformat(), "error": None}))
        self.assertNotIn(BANNER, self._page("admin_user"))

    def test_broken_file_does_not_break_the_page(self):
        self.status.write_text("{broken")
        html = self._page("admin_user")
        self.assertNotIn(BANNER, html)
        self.assertNotIn(NOTE, html)

    def test_stale_error_is_a_quiet_note_for_admins(self):
        self.status.write_text(json.dumps({"installed": "v1.1.0", "latest": "v1.1.0", "available": False,
                                           "ok_at": None, "error": "No GitHub token yet"}))
        self.assertIn(NOTE, self._page("admin_user"))
        self.assertNotIn(NOTE, self._page("emp_user"))


if __name__ == "__main__":
    unittest.main()
