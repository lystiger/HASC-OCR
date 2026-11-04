from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from hasc.db import get_connection, get_user_by_username
from hasc.web.cli import main as cli_main
from hasc.web.security import verify_password


class TestCreateUserCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_create_admin_with_password(self):
        ret = cli_main([
            "--username", "admin1",
            "--role", "admin",
            "--password", "AdminSecret123!",
            "--db", self.db_path,
            "--no-temporary",
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        user = get_user_by_username(conn, "admin1")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")
        self.assertEqual(user["must_change_password"], 0)
        self.assertTrue(verify_password("AdminSecret123!", user["password_hash"]))

    def test_cli_create_user_with_generated_temporary_password(self):
        ret = cli_main([
            "--username", "operator_tmp",
            "--role", "operator",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        user = get_user_by_username(conn, "operator_tmp")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "operator")
        self.assertEqual(user["must_change_password"], 1)

    def test_cli_duplicate_username_fails(self):
        cli_main([
            "--username", "user1",
            "--role", "employee",
            "--password", "pass123",
            "--db", self.db_path,
        ])
        ret = cli_main([
            "--username", "user1",
            "--role", "employee",
            "--password", "pass456",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret, 0)

    def test_script_executable(self):
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "create_user.py"
        self.assertTrue(script_path.exists())
        res = subprocess.run(
            [sys.executable, str(script_path), "--username", "script_user", "--role", "boss", "--password", "pwd", "--db", self.db_path],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        conn = get_connection(self.db_path)
        user = get_user_by_username(conn, "script_user")
        self.assertIsNotNone(user)
