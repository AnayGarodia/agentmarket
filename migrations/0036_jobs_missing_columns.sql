-- 0036_jobs_missing_columns.sql
-- Adds columns that exist in the canonical _create_jobs_table() definition in
-- core/jobs/db.py but were never captured as migration files.
-- Required for PostgreSQL where init_jobs_db() is skipped.

ALTER TABLE jobs ADD COLUMN parent_job_id TEXT;
ALTER TABLE jobs ADD COLUMN parent_cascade_policy TEXT NOT NULL DEFAULT 'detach';
ALTER TABLE jobs ADD COLUMN clarification_timeout_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN clarification_timeout_policy TEXT NOT NULL DEFAULT 'fail';
ALTER TABLE jobs ADD COLUMN clarification_requested_at TEXT;
ALTER TABLE jobs ADD COLUMN clarification_deadline_at TEXT;
ALTER TABLE jobs ADD COLUMN output_verification_window_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN output_verification_status TEXT NOT NULL DEFAULT 'not_required';
ALTER TABLE jobs ADD COLUMN output_verification_deadline_at TEXT;
ALTER TABLE jobs ADD COLUMN output_verification_decided_at TEXT;
ALTER TABLE jobs ADD COLUMN output_verification_decision_owner_id TEXT;
ALTER TABLE jobs ADD COLUMN output_verification_reason TEXT;
ALTER TABLE jobs ADD COLUMN batch_id TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_batch_created ON jobs(batch_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_client_created ON jobs(client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_parent_created ON jobs(parent_job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_clarification_deadline ON jobs(status, clarification_deadline_at);
CREATE INDEX IF NOT EXISTS idx_jobs_output_verification_deadline ON jobs(output_verification_status, output_verification_deadline_at)
