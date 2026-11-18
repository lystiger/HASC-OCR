-- Daily summary (P151): one row per production date the bot posted, or tried to post, to the office group.
-- The row is written before the send, so a crash mid-send never leads to a second message for that day.
CREATE TABLE IF NOT EXISTS zalo_summary (
    production_date TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sending', 'waiting', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_try_at TEXT,
    sent_at TEXT
);
