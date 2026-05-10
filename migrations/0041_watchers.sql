-- 0041_watchers.sql
-- Diff-watchers: register a target plus agent plus budget. The sweeper
-- periodically fingerprints the target and only fires (and bills) the
-- paid agent when the fingerprint changes. Pure cron is the
-- on_change_policy='always' case.

CREATE TABLE IF NOT EXISTS watchers (
    watcher_id            TEXT PRIMARY KEY,
    owner_user_id         TEXT NOT NULL,
    caller_wallet_id      TEXT NOT NULL,
    agent_id              TEXT NOT NULL,

    target_kind           TEXT NOT NULL,
    target_url            TEXT NOT NULL,
    target_meta_json      TEXT NOT NULL DEFAULT '{}',

    on_change_policy      TEXT NOT NULL DEFAULT 'on_change',
    tick_interval_seconds INTEGER NOT NULL DEFAULT 900,

    budget_per_day_cents  INTEGER NOT NULL DEFAULT 0,
    spend_today_cents     INTEGER NOT NULL DEFAULT 0,
    spend_window_date     TEXT NOT NULL,

    delivery_webhook_url  TEXT,
    delivery_email        TEXT,
    delivery_secret       TEXT,

    payload_json          TEXT NOT NULL DEFAULT '{}',

    status                TEXT NOT NULL DEFAULT 'active',
    consecutive_errors    INTEGER NOT NULL DEFAULT 0,
    last_fingerprint      TEXT,
    last_fingerprint_at   TEXT,
    last_fired_job_id     TEXT,
    last_error            TEXT,
    next_check_at         TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_watchers_due
    ON watchers(status, next_check_at);
CREATE INDEX IF NOT EXISTS idx_watchers_owner
    ON watchers(owner_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_watchers_agent
    ON watchers(agent_id);

CREATE TABLE IF NOT EXISTS watcher_runs (
    run_id              TEXT PRIMARY KEY,
    watcher_id          TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    fingerprint         TEXT,
    fingerprint_changed INTEGER NOT NULL DEFAULT 0,
    fired_job_id        TEXT,
    skip_reason         TEXT,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_watcher_runs_wid
    ON watcher_runs(watcher_id, started_at DESC);
