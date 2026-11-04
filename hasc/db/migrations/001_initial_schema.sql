-- Initial schema matching spec section 6 data model

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('operator', 'employee', 'boss', 'admin')),
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    form TEXT NOT NULL,
    machine TEXT NOT NULL,
    production_date TEXT NOT NULL,
    shift INTEGER NOT NULL,
    page_no INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'received' CHECK(status IN ('received', 'reading', 'needs_review', 'in_review', 'reviewed', 'failed')),
    version INTEGER NOT NULL DEFAULT 1,
    picked_up_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status_since TEXT NOT NULL,
    CONSTRAINT uq_sheets UNIQUE (form, machine, production_date, shift, page_no)
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    small_copy_path TEXT,
    sha256 TEXT NOT NULL,
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at TEXT NOT NULL,
    taken_at TEXT,
    upload_ms INTEGER,
    quality_warnings TEXT,
    invalid INTEGER NOT NULL DEFAULT 0,
    invalid_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS read_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'retry_wait', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    reader_name TEXT,
    queued_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS page_values (
    sheet_id INTEGER NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value TEXT,
    source_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    flag TEXT,
    PRIMARY KEY (sheet_id, field)
);

CREATE TABLE IF NOT EXISTS rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id INTEGER NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    row_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    invalid INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cells (
    row_id INTEGER NOT NULL REFERENCES rows(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value TEXT,
    inherited INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'ai' CHECK(source IN ('ai', 'person', 'hand')),
    source_photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    flag TEXT NOT NULL DEFAULT 'none' CHECK(flag IN ('none', 'doubt', 'rule', 'unreadable', 'disputed')),
    note TEXT,
    PRIMARY KEY (row_id, field)
);

CREATE TABLE IF NOT EXISTS not_running (
    machine TEXT NOT NULL,
    production_date TEXT NOT NULL,
    shift INTEGER NOT NULL,
    reason TEXT NOT NULL,
    marked_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    marked_at TEXT NOT NULL,
    PRIMARY KEY (machine, production_date, shift)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    sheet_id INTEGER REFERENCES sheets(id) ON DELETE SET NULL,
    row_id INTEGER REFERENCES rows(id) ON DELETE SET NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sheets_lookup ON sheets(machine, production_date, shift);
CREATE INDEX IF NOT EXISTS idx_sheets_status ON sheets(status);
CREATE INDEX IF NOT EXISTS idx_photos_sheet_id ON photos(sheet_id);
CREATE INDEX IF NOT EXISTS idx_photos_sha256 ON photos(sha256);
CREATE INDEX IF NOT EXISTS idx_read_jobs_photo_id ON read_jobs(photo_id);
CREATE INDEX IF NOT EXISTS idx_read_jobs_status ON read_jobs(status);
CREATE INDEX IF NOT EXISTS idx_rows_sheet_id ON rows(sheet_id);
CREATE INDEX IF NOT EXISTS idx_cells_row_id ON cells(row_id);
CREATE INDEX IF NOT EXISTS idx_events_sheet_id ON events(sheet_id);
CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
