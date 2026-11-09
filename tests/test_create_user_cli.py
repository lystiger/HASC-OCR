from datetime import datetime, timezone
import gc
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from hasc.db import (
    create_session,
    create_user,
    get_connection,
    get_session,
    get_user_by_username,
)
from hasc.web.cli import main as cli_main
from hasc.web.security import (
    hash_password,
    now,
    verify_password,
)


class TestCreateUserOwnerCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        gc.collect()
        self.temp_dir.cleanup()

    def test_criterion_16_create_new_owner(self):
        """A fresh database with create_user.py --owner creates an owner admin with a temporary password."""
        ret = cli_main([
            "--username", "owner_initial",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            user = get_user_by_username(conn, "owner_initial")
            self.assertIsNotNone(user)
            self.assertEqual(user["role"], "admin")
            self.assertEqual(user["is_owner"], 1)
            self.assertEqual(user["active"], 1)
            self.assertEqual(user["must_change_password"], 1)
            self.assertIsNotNone(user["temp_password_expires_at"])

            # Verify expiry is within ~72 hours
            exp = datetime.fromisoformat(user["temp_password_expires_at"].replace("Z", "+00:00"))
            current = now()
            hours_diff = (exp - current).total_seconds() / 3600
            self.assertGreater(hours_diff, 71.0)
            self.assertLessEqual(hours_diff, 72.1)

            # Exactly one owner in database
            count = conn.execute("SELECT COUNT(*) FROM users WHERE is_owner = 1").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_create_new_owner_with_role_admin_specified(self):
        """--owner with --role admin succeeds."""
        ret = cli_main([
            "--username", "admin_explicit",
            "--role", "admin",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            user = get_user_by_username(conn, "admin_explicit")
            self.assertIsNotNone(user)
            self.assertEqual(user["is_owner"], 1)
            self.assertEqual(user["role"], "admin")
        finally:
            conn.close()

    def test_move_owner_to_existing_admin(self):
        """--owner moves the owner flag to an existing admin and sets a temporary password."""
        conn = get_connection(self.db_path)
        try:
            admin1_id = create_user(
                conn,
                username="admin1",
                password_hash=hash_password("Pass1"),
                role="admin",
                active=1,
                is_owner=1,
            )
            admin2_id = create_user(
                conn,
                username="admin2",
                password_hash=hash_password("Pass2"),
                role="admin",
                active=1,
                is_owner=0,
                must_change_password=0,
            )
        finally:
            conn.close()

        ret = cli_main([
            "--username", "admin2",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            admin1 = get_user_by_username(conn, "admin1")
            admin2 = get_user_by_username(conn, "admin2")

            self.assertEqual(admin1["is_owner"], 0)
            self.assertEqual(admin2["is_owner"], 1)
            self.assertEqual(admin2["must_change_password"], 1)
            self.assertIsNotNone(admin2["temp_password_expires_at"])
            # Old password should no longer match
            self.assertFalse(verify_password("Pass2", admin2["password_hash"]))

            # Exactly one owner exists
            count = conn.execute("SELECT COUNT(*) FROM users WHERE is_owner = 1").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_old_owner_loses_flag_in_same_transaction(self):
        """When moving owner to another admin, the old owner loses the flag in the same transaction."""
        conn = get_connection(self.db_path)
        try:
            create_user(
                conn,
                username="first_owner",
                password_hash=hash_password("First"),
                role="admin",
                active=1,
                is_owner=1,
            )
            create_user(
                conn,
                username="second_owner",
                password_hash=hash_password("Second"),
                role="admin",
                active=1,
                is_owner=0,
            )
        finally:
            conn.close()

        ret = cli_main([
            "--username", "second_owner",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            old_owner = get_user_by_username(conn, "first_owner")
            new_owner = get_user_by_username(conn, "second_owner")

            self.assertEqual(old_owner["is_owner"], 0)
            self.assertEqual(new_owner["is_owner"], 1)

            owners = conn.execute("SELECT username FROM users WHERE is_owner = 1").fetchall()
            self.assertEqual(len(owners), 1)
            self.assertEqual(owners[0]["username"], "second_owner")
        finally:
            conn.close()

    def test_failure_partway_leaves_old_owner_in_place(self):
        """A failure part-way during owner transfer leaves the old owner in place."""
        conn = get_connection(self.db_path)
        try:
            create_user(
                conn,
                username="current_owner",
                password_hash=hash_password("OwnerSecret1"),
                role="admin",
                active=1,
                is_owner=1,
            )
            admin_target_id = create_user(
                conn,
                username="admin_target",
                password_hash=hash_password("TargetSecret2"),
                role="admin",
                active=1,
                is_owner=0,
                must_change_password=0,
            )
            # Add an SQLite trigger that fails specifically on updating admin_target
            conn.execute(f"""
                CREATE TRIGGER fail_target_update
                BEFORE UPDATE ON users
                FOR EACH ROW WHEN NEW.id = {admin_target_id}
                BEGIN
                    SELECT RAISE(ABORT, 'Simulated mid-transaction failure');
                END;
            """)
        finally:
            conn.close()

        # Run CLI transfer to admin_target - should fail due to mid-transaction error
        ret = cli_main([
            "--username", "admin_target",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret, 0)

        # Verify old owner is still in place and no changes survived the rollback
        conn = get_connection(self.db_path)
        try:
            old_owner = get_user_by_username(conn, "current_owner")
            target = get_user_by_username(conn, "admin_target")

            self.assertEqual(old_owner["is_owner"], 1, "Old owner must retain is_owner=1")
            self.assertEqual(target["is_owner"], 0, "Target must not have is_owner=1")
            self.assertEqual(target["must_change_password"], 0, "Target password state must be untouched")
            self.assertTrue(verify_password("TargetSecret2", target["password_hash"]))

            count = conn.execute("SELECT COUNT(*) FROM users WHERE is_owner = 1").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_target_sessions_end(self):
        """Moving the owner flag ends the target's open sessions."""
        conn = get_connection(self.db_path)
        try:
            owner_id = create_user(
                conn,
                username="old_admin",
                password_hash=hash_password("Old1"),
                role="admin",
                active=1,
                is_owner=1,
            )
            target_id = create_user(
                conn,
                username="new_admin",
                password_hash=hash_password("New2"),
                role="admin",
                active=1,
                is_owner=0,
            )

            # Create sessions for both users
            exp = (now().replace(microsecond=0)).isoformat()
            create_session(conn, "target_session_1", target_id, exp)
            create_session(conn, "target_session_2", target_id, exp)
            create_session(conn, "old_owner_session", owner_id, exp)

            self.assertIsNotNone(get_session(conn, "target_session_1"))
            self.assertIsNotNone(get_session(conn, "target_session_2"))
            self.assertIsNotNone(get_session(conn, "old_owner_session"))
        finally:
            conn.close()

        ret = cli_main([
            "--username", "new_admin",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            # Target's sessions must be deleted
            self.assertIsNone(get_session(conn, "target_session_1"))
            self.assertIsNone(get_session(conn, "target_session_2"))

            # Old owner's session remains untouched (only target's sessions end per spec)
            self.assertIsNotNone(get_session(conn, "old_owner_session"))
        finally:
            conn.close()

    def test_failure_partway_preserves_target_sessions(self):
        """A failure part-way leaves target sessions intact."""
        conn = get_connection(self.db_path)
        try:
            create_user(
                conn,
                username="owner_user",
                password_hash=hash_password("P1"),
                role="admin",
                active=1,
                is_owner=1,
            )
            target_id = create_user(
                conn,
                username="target_user",
                password_hash=hash_password("P2"),
                role="admin",
                active=1,
                is_owner=0,
            )
            exp = now().isoformat()
            create_session(conn, "sess_keep", target_id, exp)

            conn.execute(f"""
                CREATE TRIGGER fail_target_update_2
                BEFORE UPDATE ON users
                FOR EACH ROW WHEN NEW.id = {target_id}
                BEGIN
                    SELECT RAISE(ABORT, 'Simulated mid-transaction failure');
                END;
            """)
        finally:
            conn.close()

        ret = cli_main([
            "--username", "target_user",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            # Target's session must still exist after rollback
            self.assertIsNotNone(get_session(conn, "sess_keep"))
            owner = get_user_by_username(conn, "owner_user")
            self.assertEqual(owner["is_owner"], 1)
        finally:
            conn.close()

    def test_owner_password_recovery(self):
        """If owner loses password, create_user.py --owner recovers password, ends sessions, and keeps owner."""
        conn = get_connection(self.db_path)
        try:
            owner_id = create_user(
                conn,
                username="lost_pw_owner",
                password_hash=hash_password("OldLostPassword"),
                role="admin",
                active=1,
                is_owner=1,
                must_change_password=0,
            )
            exp = now().isoformat()
            create_session(conn, "lost_owner_session", owner_id, exp)
        finally:
            conn.close()

        ret = cli_main([
            "--username", "lost_pw_owner",
            "--owner",
            "--password", "NewRecoveryPass123",
            "--db", self.db_path,
        ])
        self.assertEqual(ret, 0)

        conn = get_connection(self.db_path)
        try:
            owner = get_user_by_username(conn, "lost_pw_owner")
            self.assertEqual(owner["is_owner"], 1)
            self.assertEqual(owner["must_change_password"], 1)
            self.assertIsNotNone(owner["temp_password_expires_at"])
            self.assertTrue(verify_password("NewRecoveryPass123", owner["password_hash"]))
            self.assertIsNone(get_session(conn, "lost_owner_session"))
        finally:
            conn.close()

    def test_cannot_make_non_admin_owner(self):
        """--owner refuses to make non-admin accounts owner."""
        conn = get_connection(self.db_path)
        try:
            create_user(
                conn,
                username="emp_user",
                password_hash=hash_password("Pass"),
                role="employee",
                active=1,
                is_owner=0,
            )
            create_user(
                conn,
                username="real_owner",
                password_hash=hash_password("Owner"),
                role="admin",
                active=1,
                is_owner=1,
            )
        finally:
            conn.close()

        # Attempt to make employee owner fails
        ret = cli_main([
            "--username", "emp_user",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret, 0)

        # Attempt with conflicting role fails
        ret_conflict = cli_main([
            "--username", "emp_user",
            "--role", "employee",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret_conflict, 0)

        conn = get_connection(self.db_path)
        try:
            emp = get_user_by_username(conn, "emp_user")
            owner = get_user_by_username(conn, "real_owner")
            self.assertEqual(emp["is_owner"], 0)
            self.assertEqual(emp["role"], "employee")
            self.assertEqual(owner["is_owner"], 1)
        finally:
            conn.close()

    def test_invalid_username_refused(self):
        """Invalid usernames are rejected with an error."""
        ret = cli_main([
            "--username", "invalid user with space",
            "--owner",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret, 0)

        ret2 = cli_main([
            "--username", "no",  # shorter than 3 chars
            "--owner",
            "--db", self.db_path,
        ])
        self.assertNotEqual(ret2, 0)

    def test_criterion_16_fresh_db_has_one_owner_and_second_owner_rejected(self):
        """Criterion 16: fresh db with create_user.py --owner has one owner; second cannot be stored; running again moves flag."""
        # 1. Fresh database with create_user.py --owner has exactly one owner
        ret1 = cli_main(["--username", "owner1", "--owner", "--db", self.db_path])
        self.assertEqual(ret1, 0)

        conn = get_connection(self.db_path)
        try:
            owners = conn.execute("SELECT id, username FROM users WHERE is_owner = 1").fetchall()
            self.assertEqual(len(owners), 1)
            self.assertEqual(owners[0]["username"], "owner1")

            # 2. Second owner cannot be stored (users_one_owner partial index blocks it)
            with self.assertRaises(sqlite3.IntegrityError):
                with conn:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, role, is_owner) VALUES (?, ?, ?, ?)",
                        ("owner2", "hash", "admin", 1),
                    )

            # 3. Running create_user.py --owner again moves the flag to owner2
            create_user(conn, "owner2", hash_password("pw"), "admin", active=1, is_owner=0)
        finally:
            conn.close()

        ret2 = cli_main(["--username", "owner2", "--owner", "--db", self.db_path])
        self.assertEqual(ret2, 0)

        conn = get_connection(self.db_path)
        try:
            owners = conn.execute("SELECT id, username FROM users WHERE is_owner = 1").fetchall()
            self.assertEqual(len(owners), 1)
            self.assertEqual(owners[0]["username"], "owner2")
            old = get_user_by_username(conn, "owner1")
            self.assertEqual(old["is_owner"], 0)
        finally:
            conn.close()

    def test_script_create_user_py_with_owner_executable(self):
        """scripts/create_user.py runs successfully as an executable script with --owner."""
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "create_user.py"
        self.assertTrue(script_path.exists())

        res = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--username", "script_owner",
                "--owner",
                "--db", self.db_path,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")

        conn = get_connection(self.db_path)
        try:
            user = get_user_by_username(conn, "script_owner")
            self.assertIsNotNone(user)
            self.assertEqual(user["is_owner"], 1)
            self.assertEqual(user["role"], "admin")
            self.assertEqual(user["must_change_password"], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
