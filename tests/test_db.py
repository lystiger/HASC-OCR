from datetime import datetime, timedelta, timezone
from pathlib import Path
import gc
import sqlite3
import tempfile
import unittest

from hasc.db import (
    clean_expired_sessions,
    create_event,
    create_photo,
    create_read_job,
    create_row,
    create_session,
    create_sheet,
    create_user,
    delete_session,
    delete_user_sessions,
    find_photo_by_sha256,
    find_sheet,
    get_applied_migrations,
    get_cells_for_row,
    get_connection,
    get_next_queued_job,
    get_not_running,
    get_page_values,
    get_photo_by_id,
    get_photos_for_sheet,
    get_read_job_by_id,
    get_read_jobs_for_photo,
    get_rows_for_sheet,
    get_session,
    get_sheet_by_id,
    get_sheet_data,
    get_user_by_id,
    get_user_by_username,
    increment_sheet_version,
    init_db,
    list_events,
    list_not_running,
    list_sheets,
    list_users,
    mark_not_running,
    mark_photo_invalid,
    migrate,
    set_user_active,
    update_read_job,
    update_sheet_status,
    update_user_password,
    upsert_cell,
    upsert_page_value,
)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.conn = get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        gc.collect()  # Windows cannot delete a database file a leftover connection still holds
        self.temp_dir.cleanup()

    def test_wal_mode_and_foreign_keys_on(self):
        mode = self.conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

        fk = self.conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        self.assertEqual(fk, 1)

    def test_schema_migrations_tracked(self):
        applied = get_applied_migrations(self.conn)
        self.assertIn("001_initial_schema.sql", applied)

        # Running migrate again returns no new migrations
        second_run = migrate(self.conn)
        self.assertEqual(second_run, [])

    def test_custom_migration_file_applied(self):
        custom_dir = Path(self.temp_dir.name) / "custom_migrations"
        custom_dir.mkdir()
        (custom_dir / "001_initial_schema.sql").write_text("SELECT 1;", encoding="utf-8")
        (custom_dir / "002_extra.sql").write_text(
            "CREATE TABLE extra_table (id INTEGER PRIMARY KEY);", encoding="utf-8"
        )

        applied = migrate(self.conn, migrations_dir=custom_dir)
        self.assertEqual(applied, ["002_extra.sql"])
        tables = [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='extra_table'"
            ).fetchall()
        ]
        self.assertEqual(tables, ["extra_table"])

    def test_user_queries_and_constraints(self):
        user_id = create_user(self.conn, "operator1", "hash123", "operator")
        self.assertIsInstance(user_id, int)

        user = get_user_by_id(self.conn, user_id)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "operator1")
        self.assertEqual(user["role"], "operator")
        self.assertEqual(user["active"], 1)
        self.assertEqual(user["must_change_password"], 0)

        # Unique username
        with self.assertRaises(sqlite3.IntegrityError):
            create_user(self.conn, "operator1", "hash456", "employee")

        # Invalid role
        with self.assertRaises(sqlite3.IntegrityError):
            create_user(self.conn, "badrole", "hash456", "superuser")

        # Update password
        update_user_password(self.conn, user_id, "newhash", must_change_password=1)
        user = get_user_by_id(self.conn, user_id)
        self.assertEqual(user["password_hash"], "newhash")
        self.assertEqual(user["must_change_password"], 1)

        # Deactivate
        set_user_active(self.conn, user_id, 0)
        user = get_user_by_username(self.conn, "operator1")
        self.assertEqual(user["active"], 0)

        # List users
        users = list_users(self.conn)
        self.assertEqual(len(users), 1)

    def test_session_lifecycle_and_cleanup(self):
        user_id = create_user(self.conn, "boss1", "secret", "boss")
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=8)).isoformat()

        session = create_session(self.conn, "sess_abc123", user_id, expires_at)
        self.assertEqual(session["id"], "sess_abc123")
        self.assertEqual(session["user_id"], user_id)

        # Foreign key enforcement on invalid user
        with self.assertRaises(sqlite3.IntegrityError):
            create_session(self.conn, "sess_bad", 99999, expires_at)

        # Retrieve session
        fetched = get_session(self.conn, "sess_abc123")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], "sess_abc123")

        # Expired session cleanup
        expired_time = (now - timedelta(hours=1)).isoformat()
        create_session(self.conn, "sess_expired", user_id, expired_time)
        deleted_count = clean_expired_sessions(self.conn, now=now.isoformat())
        self.assertEqual(deleted_count, 1)
        self.assertIsNone(get_session(self.conn, "sess_expired"))
        self.assertIsNotNone(get_session(self.conn, "sess_abc123"))

        # Admin log out clears user sessions
        delete_user_sessions(self.conn, user_id)
        self.assertIsNone(get_session(self.conn, "sess_abc123"))

    def test_sheet_queries_and_natural_key(self):
        user_id = create_user(self.conn, "admin1", "pw", "admin")
        sheet_id = create_sheet(
            self.conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
        )
        self.assertIsInstance(sheet_id, int)

        sheet = get_sheet_by_id(self.conn, sheet_id)
        self.assertEqual(sheet["status"], "received")
        self.assertEqual(sheet["version"], 1)

        # Unique sheet natural key constraint
        with self.assertRaises(sqlite3.IntegrityError):
            create_sheet(
                self.conn,
                form="BMMT01-2022/HASC",
                machine="T1",
                production_date="2026-10-19",
                shift=1,
                page_no=1,
            )

        # Lookup by natural key
        found = find_sheet(
            self.conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-19",
            shift=1,
            page_no=1,
        )
        self.assertEqual(found["id"], sheet_id)

        # Update status and pick up
        update_sheet_status(self.conn, sheet_id, "in_review", picked_up_by=user_id)
        updated = get_sheet_by_id(self.conn, sheet_id)
        self.assertEqual(updated["status"], "in_review")
        self.assertEqual(updated["picked_up_by"], user_id)

        # Version increment
        new_version = increment_sheet_version(self.conn, sheet_id)
        self.assertEqual(new_version, 2)

        # List sheets with filter
        sheets = list_sheets(self.conn, machine="T1", shift=1)
        self.assertEqual(len(sheets), 1)
        self.assertEqual(len(list_sheets(self.conn, machine="T3")), 0)

    def test_photos_with_taken_at_and_sha256(self):
        sheet_id = create_sheet(
            self.conn,
            form="BMMT01-2022/HASC",
            machine="T3",
            production_date="2026-10-20",
            shift=2,
        )
        user_id = create_user(self.conn, "op2", "pw", "operator")

        taken_at_str = "2026-10-20T14:32:00Z"
        photo_id = create_photo(
            self.conn,
            sheet_id=sheet_id,
            path="photos/20261020_T3_01.jpg",
            sha256="abc123sha256hash",
            uploaded_by=user_id,
            taken_at=taken_at_str,
            upload_ms=250,
            quality_warnings="blurry",
        )

        photo = get_photo_by_id(self.conn, photo_id)
        self.assertIsNotNone(photo)
        self.assertEqual(photo["taken_at"], taken_at_str)
        self.assertEqual(photo["sha256"], "abc123sha256hash")
        self.assertEqual(photo["quality_warnings"], "blurry")
        self.assertEqual(photo["invalid"], 0)

        # Find by sha256
        found = find_photo_by_sha256(self.conn, "abc123sha256hash")
        self.assertEqual(found["id"], photo_id)

        # Mark invalid
        office_user = create_user(self.conn, "office1", "pw", "employee")
        mark_photo_invalid(self.conn, photo_id, invalid_by=office_user)
        updated_photo = get_photo_by_id(self.conn, photo_id)
        self.assertEqual(updated_photo["invalid"], 1)
        self.assertEqual(updated_photo["invalid_by"], office_user)

    def test_read_jobs_flow(self):
        sheet_id = create_sheet(
            self.conn,
            form="BMMT01-2022/HASC",
            machine="T6",
            production_date="2026-10-21",
            shift=1,
        )
        photo_id = create_photo(
            self.conn,
            sheet_id=sheet_id,
            path="photos/test.jpg",
            sha256="fakehash",
        )

        job_id = create_read_job(self.conn, photo_id=photo_id, reader_name="gemini-3.8-flash")
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempts"], 0)

        next_job = get_next_queued_job(self.conn)
        self.assertIsNotNone(next_job)
        self.assertEqual(next_job["id"], job_id)

        # Update job to running then done
        now = datetime.now(timezone.utc).isoformat()
        update_read_job(self.conn, job_id, status="running", attempts=1, started_at=now)
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["attempts"], 1)

        update_read_job(self.conn, job_id, status="done", finished_at=now)
        job = get_read_job_by_id(self.conn, job_id)
        self.assertEqual(job["status"], "done")

        # Invalid job status rejected
        with self.assertRaises(sqlite3.IntegrityError):
            update_read_job(self.conn, job_id, status="unsupported_status")

    def test_page_values_rows_and_cells_hierarchy(self):
        sheet_id = create_sheet(
            self.conn,
            form="BMMT01-2022/HASC",
            machine="T1",
            production_date="2026-10-22",
            shift=1,
        )
        photo_id = create_photo(
            self.conn,
            sheet_id=sheet_id,
            path="photos/test2.jpg",
            sha256="fakehash2",
        )

        # Page values
        upsert_page_value(self.conn, sheet_id, "day", "22", source_photo_id=photo_id)
        upsert_page_value(self.conn, sheet_id, "day", "23", source_photo_id=photo_id)  # update
        pv = get_page_values(self.conn, sheet_id)
        self.assertEqual(len(pv), 1)
        self.assertEqual(pv[0]["value"], "23")

        # Rows and cells
        row_id = create_row(self.conn, sheet_id, row_key="101", position=1)
        upsert_cell(
            self.conn,
            row_id,
            field="gross_kg",
            value="15.8",
            inherited=0,
            source="ai",
            source_photo_id=photo_id,
        )
        upsert_cell(
            self.conn,
            row_id,
            field="core_kg",
            value="2.2",
            inherited=0,
            source="ai",
            flag="doubt",
            note="uncertain handwriting",
        )

        cells = get_cells_for_row(self.conn, row_id)
        self.assertEqual(len(cells), 2)

        data = get_sheet_data(self.conn, sheet_id)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["row_key"], "101")
        self.assertEqual(data[0]["cells"]["gross_kg"]["value"], "15.8")
        self.assertEqual(data[0]["cells"]["core_kg"]["flag"], "doubt")

        # Foreign key cascade: deleting sheet deletes rows, cells, photos, jobs
        self.conn.execute("DELETE FROM sheets WHERE id = ?", (sheet_id,))
        self.assertEqual(len(get_rows_for_sheet(self.conn, sheet_id)), 0)
        self.assertEqual(len(get_cells_for_row(self.conn, row_id)), 0)
        self.assertEqual(len(get_photos_for_sheet(self.conn, sheet_id)), 0)
        self.assertEqual(len(get_read_jobs_for_photo(self.conn, photo_id)), 0)

    def test_not_running_and_events(self):
        user_id = create_user(self.conn, "op_mark", "pw", "operator")

        # Mark not running
        mark_not_running(
            self.conn,
            machine="T2",
            production_date="2026-10-23",
            shift=1,
            reason="Heater breakdown",
            marked_by=user_id,
        )
        nr = get_not_running(self.conn, "T2", "2026-10-23", 1)
        self.assertIsNotNone(nr)
        self.assertEqual(nr["reason"], "Heater breakdown")

        # Upsert reason
        mark_not_running(
            self.conn,
            machine="T2",
            production_date="2026-10-23",
            shift=1,
            reason="Heater repair ongoing",
            marked_by=user_id,
        )
        nr = get_not_running(self.conn, "T2", "2026-10-23", 1)
        self.assertEqual(nr["reason"], "Heater repair ongoing")

        # Events
        event_id = create_event(
            self.conn,
            user_id=user_id,
            action="mark_not_running",
            detail="T2 ca 1 off",
        )
        self.assertIsInstance(event_id, int)
        events = list_events(self.conn, user_id=user_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "mark_not_running")


if __name__ == "__main__":
    unittest.main()
