"""Two people loading pages at the same moment must both get them.

Found by the end-user test of v1.3.1: with two requests in flight, the website answered 500,
"SQLite objects created in a thread can only be used in that same thread". FastAPI opens the
request's connection (get_db) in one worker thread and runs the page in another; one at a time
the same idle thread is reused, so only parallel requests failed.
"""
from concurrent.futures import ThreadPoolExecutor
import gc
import http.cookiejar
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import urllib.request

import uvicorn

from hasc.db import create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestParallelRequests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        conn = get_connection(root / "hasc.db")
        create_user(conn, "nv1", hash_password("nv1_pass1"), role="employee", active=1)
        conn.close()
        app = create_app(db_path=root / "hasc.db", photos_dir=root / "photos")

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="critical"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _status(self, opener, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if body is not None:
            request.data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
        try:
            with opener.open(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_parallel_page_loads_and_logins_all_succeed(self):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertEqual(self._status(opener, "/api/login", {"username": "nv1", "password": "nv1_pass1"}), 200)

        def page(_):
            return self._status(opener, "/office")

        def login(_):
            fresh = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            return self._status(fresh, "/api/login", {"username": "nv1", "password": "nv1_pass1"})

        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(page, range(40)))
            logins = list(pool.map(login, range(8)))
        self.assertEqual(pages, [200] * 40)
        self.assertEqual(logins, [200] * 8)


if __name__ == "__main__":
    unittest.main()
