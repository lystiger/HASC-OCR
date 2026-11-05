from datetime import datetime, timezone
import sqlite3
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password_hash: str,
    role: str,
    active: int = 1,
    must_change_password: int = 0,
    created_at: str | None = None,
) -> int:
    if created_at is None:
        created_at = now_iso()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, active, must_change_password, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, password_hash, role, active, must_change_password, created_at),
        )
        return cursor.lastrowid


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
    return cursor.fetchone()


def update_user_password(
    conn: sqlite3.Connection,
    user_id: int,
    password_hash: str,
    must_change_password: int = 0,
) -> None:
    with conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = ?
            WHERE id = ?
            """,
            (password_hash, must_change_password, user_id),
        )


def set_user_active(conn: sqlite3.Connection, user_id: int, active: int) -> None:
    with conn:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (active, user_id))


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cursor = conn.execute("SELECT * FROM users ORDER BY id ASC")
    return cursor.fetchall()


# -----------------------------------------------------------------------------
# Sessions
# -----------------------------------------------------------------------------


def create_session(
    conn: sqlite3.Connection,
    session_id: str,
    user_id: int,
    expires_at: str,
    created_at: str | None = None,
) -> sqlite3.Row:
    if created_at is None:
        created_at = now_iso()
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (id, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, user_id, created_at, expires_at),
        )
    return get_session(conn, session_id)


def get_session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    return cursor.fetchone()


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def clean_expired_sessions(conn: sqlite3.Connection, now: str | None = None) -> int:
    if now is None:
        now = now_iso()
    with conn:
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return cursor.rowcount


# -----------------------------------------------------------------------------
# Sheets
# -----------------------------------------------------------------------------


def create_sheet(
    conn: sqlite3.Connection,
    form: str,
    machine: str,
    production_date: str,
    shift: int,
    page_no: int = 1,
    status: str = "received",
    version: int = 1,
    picked_up_by: int | None = None,
    status_since: str | None = None,
) -> int:
    if status_since is None:
        status_since = now_iso()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO sheets (
                form, machine, production_date, shift, page_no,
                status, version, picked_up_by, status_since
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (form, machine, production_date, shift, page_no, status, version, picked_up_by, status_since),
        )
        return cursor.lastrowid


def get_sheet_by_id(conn: sqlite3.Connection, sheet_id: int) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM sheets WHERE id = ?", (sheet_id,))
    return cursor.fetchone()


def find_sheet(
    conn: sqlite3.Connection,
    form: str,
    machine: str,
    production_date: str,
    shift: int,
    page_no: int = 1,
) -> sqlite3.Row | None:
    cursor = conn.execute(
        """
        SELECT * FROM sheets
        WHERE form = ? AND machine = ? AND production_date = ? AND shift = ? AND page_no = ?
        """,
        (form, machine, production_date, shift, page_no),
    )
    return cursor.fetchone()


def update_sheet_status(
    conn: sqlite3.Connection,
    sheet_id: int,
    status: str,
    picked_up_by: int | None = None,
    status_since: str | None = None,
) -> None:
    if status_since is None:
        status_since = now_iso()
    with conn:
        conn.execute(
            """
            UPDATE sheets
            SET status = ?, picked_up_by = ?, status_since = ?
            WHERE id = ?
            """,
            (status, picked_up_by, status_since, sheet_id),
        )


def increment_sheet_version(conn: sqlite3.Connection, sheet_id: int) -> int:
    with conn:
        conn.execute("UPDATE sheets SET version = version + 1 WHERE id = ?", (sheet_id,))
        cursor = conn.execute("SELECT version FROM sheets WHERE id = ?", (sheet_id,))
        row = cursor.fetchone()
        return row["version"] if row else 1


def list_sheets(
    conn: sqlite3.Connection,
    machine: str | None = None,
    production_date: str | None = None,
    shift: int | None = None,
    status: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM sheets"
    clauses: list[str] = []
    params: list[Any] = []

    if machine is not None:
        clauses.append("machine = ?")
        params.append(machine)
    if production_date is not None:
        clauses.append("production_date = ?")
        params.append(production_date)
    if shift is not None:
        clauses.append("shift = ?")
        params.append(shift)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY production_date DESC, shift DESC, page_no ASC"

    cursor = conn.execute(query, params)
    return cursor.fetchall()


# -----------------------------------------------------------------------------
# Photos
# -----------------------------------------------------------------------------


def create_photo(
    conn: sqlite3.Connection,
    sheet_id: int,
    path: str,
    sha256: str,
    small_copy_path: str | None = None,
    uploaded_by: int | None = None,
    uploaded_at: str | None = None,
    taken_at: str | None = None,
    upload_ms: int | None = None,
    quality_warnings: str | None = None,
) -> int:
    if uploaded_at is None:
        uploaded_at = now_iso()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO photos (
                sheet_id, path, small_copy_path, sha256,
                uploaded_by, uploaded_at, taken_at, upload_ms, quality_warnings
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sheet_id, path, small_copy_path, sha256, uploaded_by, uploaded_at, taken_at, upload_ms, quality_warnings),
        )
        return cursor.lastrowid


def get_photo_by_id(conn: sqlite3.Connection, photo_id: int) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
    return cursor.fetchone()


def get_photos_for_sheet(conn: sqlite3.Connection, sheet_id: int) -> list[sqlite3.Row]:
    cursor = conn.execute("SELECT * FROM photos WHERE sheet_id = ? ORDER BY id ASC", (sheet_id,))
    return cursor.fetchall()


def find_photo_by_sha256(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM photos WHERE sha256 = ?", (sha256,))
    return cursor.fetchone()


def mark_photo_invalid(conn: sqlite3.Connection, photo_id: int, invalid_by: int) -> None:
    with conn:
        conn.execute(
            "UPDATE photos SET invalid = 1, invalid_by = ? WHERE id = ?",
            (invalid_by, photo_id),
        )


def update_photo_small_copy_path(
    conn: sqlite3.Connection, photo_id: int, small_copy_path: str
) -> None:
    with conn:
        conn.execute(
            "UPDATE photos SET small_copy_path = ? WHERE id = ?",
            (small_copy_path, photo_id),
        )


# -----------------------------------------------------------------------------
# Read jobs
# -----------------------------------------------------------------------------


def create_read_job(
    conn: sqlite3.Connection,
    photo_id: int,
    status: str = "queued",
    reader_name: str | None = None,
    queued_at: str | None = None,
) -> int:
    if queued_at is None:
        queued_at = now_iso()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO read_jobs (photo_id, status, reader_name, queued_at)
            VALUES (?, ?, ?, ?)
            """,
            (photo_id, status, reader_name, queued_at),
        )
        return cursor.lastrowid


def get_read_job_by_id(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    cursor = conn.execute("SELECT * FROM read_jobs WHERE id = ?", (job_id,))
    return cursor.fetchone()


def get_next_queued_job(conn: sqlite3.Connection) -> sqlite3.Row | None:
    cursor = conn.execute(
        """
        SELECT * FROM read_jobs
        WHERE status = 'queued'
        ORDER BY queued_at ASC, id ASC
        LIMIT 1
        """
    )
    return cursor.fetchone()


def update_read_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str | None = None,
    attempts: int | None = None,
    last_error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    updates: list[str] = []
    params: list[Any] = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if attempts is not None:
        updates.append("attempts = ?")
        params.append(attempts)
    if last_error is not None:
        updates.append("last_error = ?")
        params.append(last_error)
    if started_at is not None:
        updates.append("started_at = ?")
        params.append(started_at)
    if finished_at is not None:
        updates.append("finished_at = ?")
        params.append(finished_at)

    if not updates:
        return

    params.append(job_id)
    with conn:
        conn.execute(f"UPDATE read_jobs SET {', '.join(updates)} WHERE id = ?", params)


def get_read_jobs_for_photo(conn: sqlite3.Connection, photo_id: int) -> list[sqlite3.Row]:
    cursor = conn.execute("SELECT * FROM read_jobs WHERE photo_id = ? ORDER BY id ASC", (photo_id,))
    return cursor.fetchall()


# -----------------------------------------------------------------------------
# Page values
# -----------------------------------------------------------------------------


def upsert_page_value(
    conn: sqlite3.Connection,
    sheet_id: int,
    field: str,
    value: str | None,
    source_photo_id: int | None = None,
    flag: str | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO page_values (sheet_id, field, value, source_photo_id, flag)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sheet_id, field) DO UPDATE SET
                value = excluded.value,
                source_photo_id = excluded.source_photo_id,
                flag = excluded.flag
            """,
            (sheet_id, field, value, source_photo_id, flag),
        )


def get_page_values(conn: sqlite3.Connection, sheet_id: int) -> list[sqlite3.Row]:
    cursor = conn.execute("SELECT * FROM page_values WHERE sheet_id = ?", (sheet_id,))
    return cursor.fetchall()


# -----------------------------------------------------------------------------
# Rows & Cells
# -----------------------------------------------------------------------------


def create_row(
    conn: sqlite3.Connection,
    sheet_id: int,
    row_key: str,
    position: int,
    reviewed: int = 0,
    invalid: int = 0,
) -> int:
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO rows (sheet_id, row_key, position, reviewed, invalid)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sheet_id, row_key, position, reviewed, invalid),
        )
        return cursor.lastrowid


def get_rows_for_sheet(
    conn: sqlite3.Connection, sheet_id: int, include_invalid: bool = False
) -> list[sqlite3.Row]:
    if include_invalid:
        cursor = conn.execute("SELECT * FROM rows WHERE sheet_id = ? ORDER BY position ASC", (sheet_id,))
    else:
        cursor = conn.execute("SELECT * FROM rows WHERE sheet_id = ? AND invalid = 0 ORDER BY position ASC", (sheet_id,))
    return cursor.fetchall()


def upsert_cell(
    conn: sqlite3.Connection,
    row_id: int,
    field: str,
    value: str | None,
    inherited: int = 0,
    source: str = "ai",
    source_photo_id: int | None = None,
    flag: str = "none",
    note: str | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO cells (row_id, field, value, inherited, source, source_photo_id, flag, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id, field) DO UPDATE SET
                value = excluded.value,
                inherited = excluded.inherited,
                source = excluded.source,
                source_photo_id = excluded.source_photo_id,
                flag = excluded.flag,
                note = excluded.note
            """,
            (row_id, field, value, inherited, source, source_photo_id, flag, note),
        )


def get_cells_for_row(conn: sqlite3.Connection, row_id: int) -> list[sqlite3.Row]:
    cursor = conn.execute("SELECT * FROM cells WHERE row_id = ?", (row_id,))
    return cursor.fetchall()


def get_sheet_data(conn: sqlite3.Connection, sheet_id: int, include_invalid: bool = False) -> list[dict]:
    rows = get_rows_for_sheet(conn, sheet_id, include_invalid=include_invalid)
    result = []
    for r in rows:
        cells = get_cells_for_row(conn, r["id"])
        cells_dict = {}
        for c in cells:
            cells_dict[c["field"]] = {
                "value": c["value"],
                "inherited": bool(c["inherited"]),
                "source": c["source"],
                "source_photo_id": c["source_photo_id"],
                "flag": c["flag"],
                "note": c["note"],
            }
        result.append({
            "id": r["id"],
            "sheet_id": r["sheet_id"],
            "row_key": r["row_key"],
            "position": r["position"],
            "reviewed": bool(r["reviewed"]),
            "invalid": bool(r["invalid"]),
            "cells": cells_dict,
        })
    return result


# -----------------------------------------------------------------------------
# Not Running
# -----------------------------------------------------------------------------


def mark_not_running(
    conn: sqlite3.Connection,
    machine: str,
    production_date: str,
    shift: int,
    reason: str,
    marked_by: int | None = None,
    marked_at: str | None = None,
) -> None:
    if marked_at is None:
        marked_at = now_iso()
    with conn:
        conn.execute(
            """
            INSERT INTO not_running (machine, production_date, shift, reason, marked_by, marked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(machine, production_date, shift) DO UPDATE SET
                reason = excluded.reason,
                marked_by = excluded.marked_by,
                marked_at = excluded.marked_at
            """,
            (machine, production_date, shift, reason, marked_by, marked_at),
        )


def get_not_running(
    conn: sqlite3.Connection, machine: str, production_date: str, shift: int
) -> sqlite3.Row | None:
    cursor = conn.execute(
        """
        SELECT * FROM not_running
        WHERE machine = ? AND production_date = ? AND shift = ?
        """,
        (machine, production_date, shift),
    )
    return cursor.fetchone()


def list_not_running(
    conn: sqlite3.Connection, production_date: str | None = None
) -> list[sqlite3.Row]:
    if production_date is not None:
        cursor = conn.execute(
            "SELECT * FROM not_running WHERE production_date = ? ORDER BY machine ASC, shift ASC",
            (production_date,),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM not_running ORDER BY production_date DESC, machine ASC, shift ASC"
        )
    return cursor.fetchall()


# -----------------------------------------------------------------------------
# Events
# -----------------------------------------------------------------------------


def create_event(
    conn: sqlite3.Connection,
    user_id: int | None,
    action: str,
    sheet_id: int | None = None,
    row_id: int | None = None,
    field: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    detail: str | None = None,
    at: str | None = None,
) -> int:
    if at is None:
        at = now_iso()
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO events (
                at, user_id, action, sheet_id, row_id, field,
                old_value, new_value, detail
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (at, user_id, action, sheet_id, row_id, field, old_value, new_value, detail),
        )
        return cursor.lastrowid


def list_events(
    conn: sqlite3.Connection,
    sheet_id: int | None = None,
    user_id: int | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT * FROM events"
    clauses: list[str] = []
    params: list[Any] = []

    if sheet_id is not None:
        clauses.append("sheet_id = ?")
        params.append(sheet_id)
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY at DESC, id DESC"

    cursor = conn.execute(query, params)
    return cursor.fetchall()
