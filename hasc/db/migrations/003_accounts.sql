-- Accounts migration: add owner, last seen, temp password expiry, and enforce NOCASE usernames and single owner

ALTER TABLE users ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN last_seen_at TEXT;
ALTER TABLE users ADD COLUMN temp_password_expires_at TEXT;

CREATE UNIQUE INDEX users_username_nocase ON users(username COLLATE NOCASE);

CREATE UNIQUE INDEX users_one_owner ON users(is_owner) WHERE is_owner = 1;

UPDATE users SET is_owner = 1 WHERE id = (
    SELECT id FROM users
    WHERE role = 'admin' AND active = 1
    ORDER BY id ASC
    LIMIT 1
);
