-- Zalo inbox (LysStack roadmap, Zalo staging integration): photos the receive-only bot got from
-- allowed groups. A photo waits here, unassigned, until its machine, date, shift and page are known.
-- Caption and chat text are never stored; `command` only records whether the caption held /capture.
CREATE TABLE IF NOT EXISTS zalo_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT,
    sender_name TEXT,
    staging INTEGER NOT NULL,  -- 1 for the staging group: kept out of pilot totals
    command INTEGER NOT NULL,  -- 1 when the caption held /capture; 0 is an ordinary photo
    sent_at TEXT,
    received_at TEXT NOT NULL,
    photo_url TEXT,  -- signed source URL, cleared once the photo is saved; never shown
    status TEXT NOT NULL CHECK (status IN ('received', 'delivered', 'duplicate', 'rejected', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    redeliveries INTEGER NOT NULL DEFAULT 0,  -- the same message delivered again by Zalo
    last_error TEXT,
    path TEXT,
    sha256 TEXT,
    size_bytes INTEGER,
    delivered_at TEXT,
    UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_zalo_inbox_received_at ON zalo_inbox(received_at);
CREATE INDEX IF NOT EXISTS idx_zalo_inbox_sha256 ON zalo_inbox(sha256);

-- One row: the receiver's own health, so the AI page can tell an idle group from a dead receiver.
CREATE TABLE IF NOT EXISTS zalo_receiver (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at TEXT,
    heartbeat_at TEXT,
    last_ok_at TEXT,
    last_error TEXT,
    last_error_at TEXT,
    ignored_events INTEGER NOT NULL DEFAULT 0
);
