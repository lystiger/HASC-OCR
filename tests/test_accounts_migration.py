from datetime import datetime, timezone
from pathlib import Path
import gc
import shutil
import sqlite3
import tempfile
import unittest

from hasc.db import (
    MigrationError,
    create_user,
    get_applied_migrations,
    get_connection,
    get_migrations_dir,
    get_user_by_username,
    migrate,
)


class TestAccountsMigration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def test_fresh_database(self):
        """A fresh database applies all migrations including 003_accounts.sql with columns and indexes."""
        conn = get_connection(self.db_path, auto_migrate=True)
        try:
            applied = get_applied_migrations(conn)
            self.assertIn("001_initial_schema.sql", applied)
            self.assertIn("002_sheet_layout.sql", applied)
            self.assertIn("003_accounts.sql", applied)

            # Check columns on users table
            columns = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            self.assertIn("is_owner", columns)
            self.assertEqual(columns["is_owner"]["dflt_value"], "0")
            self.assertEqual(columns["is_owner"]["notnull"], 1)

            self.assertIn("last_seen_at", columns)
            self.assertIn("temp_password_expires_at", columns)

            # Check indexes
            indexes = {
                row["name"]
                for row in conn.execute("PRAGMA index_list(users)").fetchall()
            }
            self.assertIn("users_username_nocase", indexes)
            self.assertIn("users_one_owner", indexes)
        finally:
            conn.close()

    def test_owner_is_lowest_id_active_admin(self):
        """On migration, owner is marked as the active admin with the lowest id."""
        custom_mig_dir = Path(self.temp_dir.name) / "migs"
        custom_mig_dir.mkdir()
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)

        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            migrate(conn, migrations_dir=custom_mig_dir)

            # Insert users before 003:
            # 1: inactive admin
            # 2: active employee
            # 3: active admin (lowest id among active admins)
            # 4: active admin (higher id)
            create_user(conn, "admin_inactive", "hash", "admin", active=0)
            create_user(conn, "employee1", "hash", "employee", active=1)
            create_user(conn, "admin_first_active", "hash", "admin", active=1)
            create_user(conn, "admin_second_active", "hash", "admin", active=1)

            # Now add 003_accounts.sql and migrate
            shutil.copy(get_migrations_dir() / "003_accounts.sql", custom_mig_dir)
            applied = migrate(conn, migrations_dir=custom_mig_dir)
            self.assertEqual(applied, ["003_accounts.sql"])

            users = {
                row["username"]: row["is_owner"]
                for row in conn.execute("SELECT username, is_owner FROM users").fetchall()
            }
            self.assertEqual(users["admin_inactive"], 0)
            self.assertEqual(users["employee1"], 0)
            self.assertEqual(users["admin_first_active"], 1)
            self.assertEqual(users["admin_second_active"], 0)
        finally:
            conn.close()

    def test_no_active_admin_gives_no_owner(self):
        """When there is no active admin, migration marks no owner."""
        custom_mig_dir = Path(self.temp_dir.name) / "migs"
        custom_mig_dir.mkdir()
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)

        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            migrate(conn, migrations_dir=custom_mig_dir)

            create_user(conn, "admin_inactive", "hash", "admin", active=0)
            create_user(conn, "boss1", "hash", "boss", active=1)
            create_user(conn, "emp1", "hash", "employee", active=1)

            shutil.copy(get_migrations_dir() / "003_accounts.sql", custom_mig_dir)
            applied = migrate(conn, migrations_dir=custom_mig_dir)
            self.assertEqual(applied, ["003_accounts.sql"])

            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_owner = 1"
            ).fetchone()[0]
            self.assertEqual(count, 0)
        finally:
            conn.close()

    def test_mixed_case_usernames_stop_migration_and_name_both(self):
        """Mixed-case usernames differing only by case stop 003_accounts.sql and name both."""
        custom_mig_dir = Path(self.temp_dir.name) / "migs"
        custom_mig_dir.mkdir()
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)

        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            migrate(conn, migrations_dir=custom_mig_dir)

            # Insert case-differing usernames allowed under schema 001
            create_user(conn, "Hung", "hash1", "admin", active=1)
            create_user(conn, "hung", "hash2", "employee", active=1)

            shutil.copy(get_migrations_dir() / "003_accounts.sql", custom_mig_dir)
            with self.assertRaises(MigrationError) as ctx:
                migrate(conn, migrations_dir=custom_mig_dir)

            err_msg = str(ctx.exception)
            self.assertIn("Hung", err_msg)
            self.assertIn("hung", err_msg)

            # 003_accounts.sql should not be recorded as applied
            applied = get_applied_migrations(conn)
            self.assertNotIn("003_accounts.sql", applied)
        finally:
            conn.close()

    def test_vietnamese_letters_are_not_a_case_collision(self):
        """NOCASE folds A-Z only, so Đức and đức do not stop 003_accounts.sql."""
        custom_mig_dir = Path(self.temp_dir.name) / "migs"
        custom_mig_dir.mkdir()
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)

        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            migrate(conn, migrations_dir=custom_mig_dir)
            create_user(conn, "Đức", "hash1", "admin", active=1)
            create_user(conn, "đức", "hash2", "employee", active=1)

            shutil.copy(get_migrations_dir() / "003_accounts.sql", custom_mig_dir)
            applied = migrate(conn, migrations_dir=custom_mig_dir)
            self.assertEqual(applied, ["003_accounts.sql"])
        finally:
            conn.close()

    def test_pre_check_does_not_run_for_other_migrations(self):
        """The case-insensitivity pre-check runs only for 003_accounts.sql, not others."""
        custom_mig_dir = Path(self.temp_dir.name) / "migs"
        custom_mig_dir.mkdir()
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)

        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            migrate(conn, migrations_dir=custom_mig_dir)

            # Insert mixed-case usernames
            create_user(conn, "Alice", "hash1", "employee", active=1)
            create_user(conn, "alice", "hash2", "employee", active=1)

            # Migration 002 is not 003_accounts.sql; pre-check must not fire
            shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)
            applied = migrate(conn, migrations_dir=custom_mig_dir)
            self.assertEqual(applied, ["002_sheet_layout.sql"])
        finally:
            conn.close()

    def test_second_owner_rejected(self):
        """Partial unique index ensures only one row can have is_owner = 1."""
        conn = get_connection(self.db_path, auto_migrate=True)
        try:
            u1 = create_user(conn, "admin1", "h", "admin", active=1)
            u2 = create_user(conn, "admin2", "h", "admin", active=1)
            u3 = create_user(conn, "emp1", "h", "employee", active=1)

            # Mark first owner
            conn.execute("UPDATE users SET is_owner = 1 WHERE id = ?", (u1,))

            # Multiple users with is_owner = 0 is fine
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_owner = 0"
                ).fetchone()[0],
                2,
            )

            # Marking second user as owner fails
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE users SET is_owner = 1 WHERE id = ?", (u2,))

            # Inserting second user as owner fails
            now = datetime.now(timezone.utc).isoformat()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, role, active, must_change_password, created_at, is_owner)
                    VALUES (?, ?, ?, 1, 0, ?, 1)
                    """,
                    ("admin3", "h", "admin", now),
                )
        finally:
            conn.close()

    def test_case_insensitive_duplicate_rejected(self):
        """Creating an account with a case-variant of an existing username is rejected."""
        conn = get_connection(self.db_path, auto_migrate=True)
        try:
            create_user(conn, "hung", "hash", "employee", active=1)

            # Same username differing only by case fails unique index
            with self.assertRaises(sqlite3.IntegrityError):
                create_user(conn, "Hung", "hash2", "employee", active=1)

            with self.assertRaises(sqlite3.IntegrityError):
                create_user(conn, "HUNG", "hash3", "boss", active=1)
        finally:
            conn.close()

    def test_case_insensitive_lookup(self):
        """Username lookup matches case-insensitively using COLLATE NOCASE."""
        conn = get_connection(self.db_path, auto_migrate=True)
        try:
            user_id = create_user(conn, "hung", "hash", "employee", active=1)

            by_exact = get_user_by_username(conn, "hung")
            self.assertIsNotNone(by_exact)
            self.assertEqual(by_exact["id"], user_id)

            by_capital = get_user_by_username(conn, "Hung")
            self.assertIsNotNone(by_capital)
            self.assertEqual(by_capital["id"], user_id)

            by_all_caps = get_user_by_username(conn, "HUNG")
            self.assertIsNotNone(by_all_caps)
            self.assertEqual(by_all_caps["id"], user_id)

            by_nonexistent = get_user_by_username(conn, "other")
            self.assertIsNone(by_nonexistent)
        finally:
            conn.close()

    def test_criterion_16_migration_marks_oldest_active_admin_and_second_owner_rejected(self):
        """Criterion 16: migration marks the oldest active admin; a second owner cannot be stored."""
        custom_mig_dir = Path(self.temp_dir.name) / "migs16"
        custom_mig_dir.mkdir()
        shutil.copy(get_migrations_dir() / "001_initial_schema.sql", custom_mig_dir)
        shutil.copy(get_migrations_dir() / "002_sheet_layout.sql", custom_mig_dir)

        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            migrate(conn, migrations_dir=custom_mig_dir)
            create_user(conn, "inactive_admin", "hash", "admin", active=0)
            u_oldest_active = create_user(conn, "oldest_active_admin", "hash", "admin", active=1)
            u_younger_active = create_user(conn, "younger_active_admin", "hash", "admin", active=1)

            shutil.copy(get_migrations_dir() / "003_accounts.sql", custom_mig_dir)
            migrate(conn, migrations_dir=custom_mig_dir)

            row = conn.execute("SELECT id, is_owner FROM users WHERE id = ?", (u_oldest_active,)).fetchone()
            self.assertEqual(row["is_owner"], 1)

            # Second owner cannot be stored
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE users SET is_owner = 1 WHERE id = ?", (u_younger_active,))
        finally:
            conn.close()

    def test_criterion_9_case_insensitive_duplicate_rejected_and_login_lookup(self):
        """Criterion 9: Hung cannot be created when hung exists, and hung can be found by typing Hung."""
        conn = get_connection(self.db_path, auto_migrate=True)
        try:
            u_id = create_user(conn, "hung", "hash", "employee", active=1)
            with self.assertRaises(sqlite3.IntegrityError):
                create_user(conn, "Hung", "hash2", "employee", active=1)

            found = get_user_by_username(conn, "Hung")
            self.assertIsNotNone(found)
            self.assertEqual(found["id"], u_id)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
