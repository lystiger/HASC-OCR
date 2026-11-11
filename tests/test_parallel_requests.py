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
import urllib.parse
import urllib.request

import uvicorn

from hasc.db import create_user, get_connection
from hasc.web.app import create_app
from hasc.web.security import hash_password


class TestParallelRequests(unittest.TestCase):
    def setUp(self):
        # Cleanups run in reverse order and even when setUp fails: stop the server, then delete the files.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(gc.collect)  # Windows cannot delete a database file a leftover connection still holds
        root = Path(self.temp_dir.name)
        conn = get_connection(root / "hasc.db")
        create_user(conn, "nv1", hash_password("nv1_pass1"), role="employee", active=1)
        conn.close()
        app = create_app(db_path=root / "hasc.db", photos_dir=root / "photos")

        # The server takes over this socket, so no other process can grab the port in between.
        self.sock = socket.socket()
        self.addCleanup(self.sock.close)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="critical"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        for _ in range(200):
            if self.server.started:
                break
            time.sleep(0.05)
        self.assertTrue(self.server.started, "the test server did not start")

    def _stop_server(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise AssertionError("the test server did not stop")

    def _get(self, opener, path, body=None):
        """(status, final path): a lost session would redirect to /login and still answer 200."""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if body is not None:
            request.data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, urllib.parse.urlsplit(response.geturl()).path
        except urllib.error.HTTPError as error:
            return error.code, path

    def test_parallel_page_loads_and_logins_all_succeed(self):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login = {"username": "nv1", "password": "nv1_pass1"}
        self.assertEqual(self._get(opener, "/api/login", login), (200, "/api/login"))

        def page(_):
            return self._get(opener, "/office")

        def login_again(_):
            fresh = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            return self._get(fresh, "/api/login", login)

        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(page, range(40)))
            logins = list(pool.map(login_again, range(8)))
        self.assertEqual(pages, [(200, "/office")] * 40)
        self.assertEqual(logins, [(200, "/api/login")] * 8)


if __name__ == "__main__":
    unittest.main()
