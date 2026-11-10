-- AI page (P149): what each read cost, and the admin's monthly budget.
ALTER TABLE read_jobs ADD COLUMN cost_usd REAL;
ALTER TABLE read_jobs ADD COLUMN prompt_tokens INTEGER;
ALTER TABLE read_jobs ADD COLUMN completion_tokens INTEGER;

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_read_jobs_finished_at ON read_jobs(finished_at);
