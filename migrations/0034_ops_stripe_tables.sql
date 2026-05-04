-- 0034_ops_stripe_tables.sql
-- Ops/idempotency and Stripe bookkeeping tables previously created inline by
-- server/persistence/ops_schema.py. Adding as a migration so they exist in
-- PostgreSQL (which skips the inline init path).

CREATE TABLE IF NOT EXISTS stripe_sessions (
    session_id    TEXT PRIMARY KEY,
    wallet_id     TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    processed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    session_id    TEXT PRIMARY KEY,
    wallet_id     TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK(status IN ('processing', 'processed', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_status_updated
    ON stripe_webhook_events(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS idempotency_requests (
    request_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT NOT NULL,
    scope            TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    request_hash     TEXT NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('in_progress', 'completed')),
    response_status  INTEGER,
    response_body    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(owner_id, scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_updated ON idempotency_requests(updated_at DESC)
