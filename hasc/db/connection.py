import os
from pathlib import Path
import sqlite3

from hasc.db.migrations import migrate


def get_default_db_path() -> Path:
    env_path = os.environ.get("HASC_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path.cwd() / "hasc.db"


def get_default_photos_dir(
    photos_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Path:
    """Return the absolute path to the photos directory.

    Precedence:
    1. Explicit photos_dir argument
    2. HASC_PHOTOS_DIR environment variable
    3. Photos folder derived from explicit db_path argument
    4. Photos folder derived from HASC_DB_PATH environment variable
    5. Fallback directory outside the repository (~/.local/share/hasc/photos)
    """
    if photos_dir is not None and str(photos_dir).strip():
        return Path(photos_dir).resolve()

    env_photos_dir = os.environ.get("HASC_PHOTOS_DIR")
    if env_photos_dir and env_photos_dir.strip():
        return Path(env_photos_dir).resolve()

    if db_path is not None and str(db_path).strip() not in ("", ":memory:"):
        return (Path(db_path).resolve().parent / "photos").resolve()

    env_db_path = os.environ.get("HASC_DB_PATH")
    if env_db_path and env_db_path.strip() not in ("", ":memory:"):
        return (Path(env_db_path).resolve().parent / "photos").resolve()

    return (Path.home() / ".local" / "share" / "hasc" / "photos").resolve()


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
