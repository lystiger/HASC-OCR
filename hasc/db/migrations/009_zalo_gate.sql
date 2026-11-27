-- Zalo intake (zalo-intake spec R0-R5): the gate turns a delivered inbox photo into a filed sheet, a waiting
-- photo with a reason, not_form, or (staging) staging_checked. SQLite cannot widen a CHECK in place, so the
-- inbox is rebuilt keeping every id and its AUTOINCREMENT counter.
CREATE TABLE zalo_inbox_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    chat_type TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT,
    sender_name TEXT,
    staging INTEGER NOT NULL,
    command INTEGER NOT NULL,
    sent_at TEXT,
    received_at TEXT NOT NULL,
    photo_url TEXT,
    status TEXT NOT NULL CHECK (status IN ('received', 'delivered', 'duplicate', 'rejected', 'failed',
                                           'filed', 'waiting', 'not_form', 'staging_checked')),
    attempts INTEGER NOT NULL DEFAULT 0,
    redeliveries INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    path TEXT,
    sha256 TEXT,
    size_bytes INTEGER,
    delivered_at TEXT,
    gate_reply TEXT,  -- JSON of the kept gate keys, never the raw reply
    gate_attempts INTEGER NOT NULL DEFAULT 0,  -- malformed gate replies for this row
    reason TEXT,  -- Vietnamese reason for waiting / not_form; for staging, the would-be reason
    outcome TEXT,  -- new / resend / back / waiting / not_form; for staging, what would have happened
    production_date TEXT,
    shift INTEGER,
    page_no INTEGER,  -- a resend keeps the paper's front page
    sheet_id INTEGER REFERENCES sheets(id) ON DELETE SET NULL,
    photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    gated_at TEXT,
    UNIQUE (chat_id, message_id)
);

INSERT INTO zalo_inbox_new (id, chat_id, chat_type, message_id, sender_id, sender_name, staging, command, sent_at,
                            received_at, photo_url, status, attempts, redeliveries, last_error, path, sha256,
                            size_bytes, delivered_at)
SELECT id, chat_id, chat_type, message_id, sender_id, sender_name, staging, command, sent_at,
       received_at, photo_url, status, attempts, redeliveries, last_error, path, sha256,
       size_bytes, delivered_at
FROM zalo_inbox;

-- The old counter can be above MAX(id) when the newest rows were deleted; carry it over so ids are never reused.
DELETE FROM sqlite_sequence WHERE name = 'zalo_inbox_new';
UPDATE sqlite_sequence SET name = 'zalo_inbox_new' WHERE name = 'zalo_inbox';

DROP TABLE zalo_inbox;
ALTER TABLE zalo_inbox_new RENAME TO zalo_inbox;

CREATE INDEX idx_zalo_inbox_received_at ON zalo_inbox(received_at);
CREATE INDEX idx_zalo_inbox_sha256 ON zalo_inbox(sha256);
CREATE INDEX idx_zalo_inbox_status ON zalo_inbox(status);

-- Set when the gate already told which way up the photo is, so the full read skips its own turn call.
ALTER TABLE photos ADD COLUMN turn_checked INTEGER NOT NULL DEFAULT 0;

-- Every gate dispatch, failed ones included: the daily cap counts these, so a restart cannot reset it.
CREATE TABLE gate_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_id INTEGER NOT NULL REFERENCES zalo_inbox(id),
    at TEXT NOT NULL,  -- UTC ISO dispatch time
    ok INTEGER NOT NULL,
    cost_usd REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    error TEXT
);
CREATE INDEX idx_gate_calls_at ON gate_calls(at);
