import os
from pathlib import Path
import sqlite3

from hasc.db.migrations import migrate


def get_default_db_path() -> Path:
    env_path = os.environ.get("HASC_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path.cwd() / "hasc.db"


def get_connection(
    db_path: str | Path | None = None,
    *,
    auto_migrate: bool = True,
    timeout: float = 10.0,
) -> sqlite3.Connection:
    """Return a SQLite connection in WAL mode with foreign keys enabled."""
    if db_path is None:
        db_path = get_default_db_path()

    if isinstance(db_path, Path):
        db_path = str(db_path)

    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")

    if auto_migrate:
        migrate(conn)

    return conn


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Connect to database and ensure all migrations are applied."""
    return get_connection(db_path, auto_migrate=True)
