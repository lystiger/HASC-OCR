from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class MigrationError(ValueError, RuntimeError):
    """Raised when a migration pre-check or execution fails."""
    pass


def _check_003_case_insensitive_usernames(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if not table_exists:
        return

    cursor = conn.execute("SELECT username FROM users ORDER BY id ASC")
    seen: dict[str, list[str]] = {}
    for (username,) in cursor.fetchall():
        seen.setdefault(username.lower(), []).append(username)

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
        if version not in applied:
            if version == "003_accounts.sql":
                _check_003_case_insensitive_usernames(conn)
            sql = file.read_text(encoding="utf-8")
            with conn:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
            applied.add(version)
            applied_now.append(version)

    return applied_now
