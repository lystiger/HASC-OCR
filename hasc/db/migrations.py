from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class MigrationError(ValueError, RuntimeError):
    """Raised when a migration pre-check or execution fails."""
    pass


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _check_003_case_insensitive_usernames(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not table_exists:
        return

    cursor = conn.execute("SELECT username FROM users ORDER BY id ASC")
    seen: dict[str, list[str]] = {}
    for (username,) in cursor.fetchall():
        # SQLite NOCASE folds A-Z only, so Vietnamese letters such as Đ/đ are not a collision.
        seen.setdefault(username.translate(_ASCII_LOWER), []).append(username)

    duplicates = [names for names in seen.values() if len(names) > 1]
    if duplicates:
        conflicting_names = [name for group in duplicates for name in group]
        names_str = ", ".join(conflicting_names)
        raise MigrationError(
            f"Usernames differ only by case: {names_str}. "
            f"Tên đăng nhập trùng nhau khi không phân biệt chữ hoa chữ thường: {names_str}."
        )


def get_migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def get_applied_migrations(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC")
    return [row[0] for row in cursor.fetchall()]


def _statements(sql: str):
    """Split a migration file into statements, so they can run inside one locked transaction
    (executescript would commit first and drop the lock)."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    if any(l.strip() and not l.strip().startswith("--") for l in buffer.splitlines()):
        raise MigrationError(f"Unfinished SQL statement at the end of a migration: {buffer.strip()[:80]}")


def migrate(
    conn: sqlite3.Connection, migrations_dir: Path | None = None
) -> list[str]:
    """Apply any unapplied SQL migration files in numerical/alphabetical order."""
    if migrations_dir is None:
        migrations_dir = get_migrations_dir()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )

    applied = set(get_applied_migrations(conn))
    applied_now: list[str] = []

    if not migrations_dir.is_dir():
        return applied_now

    migration_files = sorted(migrations_dir.glob("*.sql"))
    for file in migration_files:
        version = file.name
        if version in applied:
            continue
        # The website and the worker start together after an update and both migrate. The write lock
        # makes the second one wait, and the check inside it sees the first one's work.
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                conn.rollback()
                applied.add(version)
                continue
            if version == "003_accounts.sql":
                _check_003_case_insensitive_usernames(conn)
            for statement in _statements(file.read_text(encoding="utf-8")):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        applied.add(version)
        applied_now.append(version)

    return applied_now
