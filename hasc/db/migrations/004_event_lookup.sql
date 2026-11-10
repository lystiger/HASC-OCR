-- Account requests are looked up by action and target (P148): keep that off a full events scan.
CREATE INDEX IF NOT EXISTS idx_events_action_field ON events(action, field);
