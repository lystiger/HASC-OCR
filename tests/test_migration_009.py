"""Migration 009 on a database populated at 008 (zalo-intake spec criterion 17)."""
import gc
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from hasc.db import get_connection
from hasc.db.migrations import get_migrations_dir, migrate

OLD_STATUSES = ("received", "delivered", "duplicate", "rejected", "failed")
OLD_COLUMNS = ("id, chat_id, chat_type, message_id, sender_id, sender_name, staging, command, sent_at, received_at, "
               "photo_url, status, attempts, redeliveries, last_error, path, sha256, size_bytes, delivered_at")


class Migration009(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.root = Path(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(gc.collect)
        self.old_dir = self.root / "upto008"
        self.old_dir.mkdir()
        for sql in get_migrations_dir().glob("*.sql"):
            if sql.name < "009":
                shutil.copy(sql, self.old_dir)
        self.conn = get_connection(self.root / "hasc.db", auto_migrate=False)
        self.addCleanup(self.conn.close)
        migrate(self.conn, self.old_dir)
        self.populate()

    def populate(self):
        c = self.conn
        sheet = c.execute("""INSERT INTO sheets (form, machine, production_date, shift, page_no, status_since)
                             VALUES ('BMMT01-2022/HASC', 'T6', '2026-10-19', 1, 1, '2026-10-19T08:00:00+00:00')""").lastrowid
        photo = c.execute("""INSERT INTO photos (sheet_id, path, sha256, uploaded_at, turn)
                             VALUES (?, '/p/a.jpg', 'aa', '2026-10-19T08:00:00+00:00', 180)""", (sheet,)).lastrowid
        c.execute("INSERT INTO read_jobs (photo_id, status, queued_at) VALUES (?, 'done', '2026-10-19T08:00:00+00:00')",
                  (photo,))
        for n, status in enumerate((*OLD_STATUSES, "delivered"), 1):
            c.execute("""
                INSERT INTO zalo_inbox (chat_id, chat_type, message_id, sender_id, sender_name, staging, command, sent_at,
                    received_at, photo_url, status, attempts, redeliveries, last_error, path, sha256, size_bytes, delivered_at)
                VALUES (?, 'PRIVATE', ?, ?, 'Tổ trưởng', ?, 0, '2026-10-19T08:00:00+00:00', '2026-10-19T08:00:01+00:00',
                        NULL, ?, 1, ?, ?, ?, ?, 10, '2026-10-19T08:00:02+00:00')
            """, (f"c{n}", f"m{n}", f"u{n}", n % 2, status, n, "lỗi" if status == "failed" else None,
                  f"/p/z{n}.jpg", f"sha{n}"))
        c.execute("DELETE FROM zalo_inbox WHERE id = 6")  # the counter now runs ahead of MAX(id)
        c.commit()

    def state(self):
        tables = {"sheets": "*", "photos": "id, sheet_id, path, sha256, turn", "read_jobs": "*",
                  "zalo_inbox": OLD_COLUMNS}
        return {t: [tuple(r) for r in self.conn.execute(f"SELECT {cols} FROM {t} ORDER BY 1")]
                for t, cols in tables.items()}

    def schema(self):
        return [tuple(r) for r in self.conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")]

    def test_upgrade_keeps_every_row_and_id_and_allows_the_gate_statuses(self):
        before = self.state()
        self.assertEqual(migrate(self.conn), ["009_zalo_gate.sql"])
        self.assertEqual(self.state(), before)
        self.assertEqual([r[0] for r in self.conn.execute("PRAGMA foreign_key_check")], [])
        self.assertEqual(self.conn.execute("SELECT DISTINCT gate_attempts FROM zalo_inbox").fetchall()[0][0], 0)
        self.assertEqual(self.conn.execute("SELECT turn_checked FROM photos").fetchone()[0], 0)
        indexes = {r[1] for r in self.conn.execute("PRAGMA index_list(zalo_inbox)")}
        self.assertTrue({"idx_zalo_inbox_received_at", "idx_zalo_inbox_sha256"} <= indexes)
        for status in ("filed", "waiting", "not_form", "staging_checked"):
            new_id = self.conn.execute("""
                INSERT INTO zalo_inbox (chat_id, chat_type, message_id, staging, command, received_at, status)
                VALUES ('c', 'PRIVATE', ?, 0, 0, '2026-10-20T00:00:00+00:00', ?)
            """, (status, status)).lastrowid
            self.assertGreater(new_id, 6)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO zalo_inbox (chat_id, chat_type, message_id, staging, command, received_at,
                                 status) VALUES ('c', 'PRIVATE', 'x', 0, 0, 'now', 'lost')""")
        self.conn.execute("INSERT INTO gate_calls (inbox_id, at, ok) VALUES (1, '2026-10-20T00:00:00+00:00', 1)")

    def test_failed_009_leaves_the_008_database_unchanged(self):
        broken = self.root / "broken"
        shutil.copytree(self.old_dir, broken)
        sql = (get_migrations_dir() / "009_zalo_gate.sql").read_text(encoding="utf-8")
        (broken / "009_zalo_gate.sql").write_text(sql + "\nINSERT INTO no_such_table VALUES (1);\n", encoding="utf-8")
        before, schema = self.state(), self.schema()
        with self.assertRaises(sqlite3.OperationalError):
            migrate(self.conn, broken)
        self.assertEqual((self.state(), self.schema()), (before, schema))
        self.assertNotIn("009_zalo_gate.sql", [r[0] for r in self.conn.execute("SELECT version FROM schema_migrations")])
        self.assertEqual(self.conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'zalo_inbox'").fetchone()[0], 6)


if __name__ == "__main__":
    unittest.main()
