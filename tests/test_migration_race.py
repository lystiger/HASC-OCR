"""The website and the worker start together after an update and both run the migrations.

Seen on the Windows install check for v1.2.1: the worker failed with "duplicate column name: cost_usd"
because both processes read the applied list before either recorded 005.
"""
import gc
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from hasc.db import migrations
from hasc.db.connection import get_connection


class TestMigrationRace(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "race.db"

    def tearDown(self):
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def _columns(self, conn, table):
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    def test_a_process_with_a_stale_list_skips_what_another_applied(self):
        first = get_connection(self.db_path, auto_migrate=False)
        second = get_connection(self.db_path, auto_migrate=False)
        try:
            # The second process read the applied list before the first one migrated.
            with mock.patch.object(migrations, "get_applied_migrations", return_value=[]):
                migrations.migrate(first)
                self.assertEqual(migrations.migrate(second), [])
            self.assertEqual(self._columns(second, "read_jobs").count("cost_usd"), 1)
            versions = [r[0] for r in second.execute("SELECT version FROM schema_migrations")]
            self.assertEqual(len(versions), len(set(versions)))
        finally:
            first.close()
            second.close()

    def test_two_processes_migrating_at_once_both_succeed(self):
        errors = []
        # The database exists before the services start (the installer creates it), and is in WAL mode.
        get_connection(self.db_path, auto_migrate=False).close()
        start = threading.Barrier(2, timeout=30)

        def run():
            conn = get_connection(self.db_path, auto_migrate=False)
            try:
                start.wait()
                migrations.migrate(conn)
            except Exception as error:  # collected and asserted below
                errors.append(error)
            finally:
                conn.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        conn = get_connection(self.db_path, auto_migrate=False)
        try:
            files = sorted(p.name for p in migrations.get_migrations_dir().glob("*.sql"))
            self.assertEqual([r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")], files)
        finally:
            conn.close()

    def test_a_failing_migration_leaves_nothing_behind(self):
        mdir = Path(self.temp_dir.name) / "m"
        mdir.mkdir()
        (mdir / "001_a.sql").write_text("CREATE TABLE a (x INTEGER);\nCREATE TABLE a (y INTEGER);\n", encoding="utf-8")
        conn = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                migrations.migrate(conn, mdir)
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'a'").fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 0)
        finally:
            conn.close()

    def test_statements_split_with_comments_and_semicolons_in_strings(self):
        sql = "-- note; not a statement\nCREATE TABLE t (v TEXT DEFAULT 'a;b');\n\nINSERT INTO t VALUES ('x');\n-- trailing comment\n"
        self.assertEqual(len(list(migrations._statements(sql))), 2)


if __name__ == "__main__":
    unittest.main()
