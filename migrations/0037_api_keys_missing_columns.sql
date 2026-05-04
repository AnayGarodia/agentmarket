-- 0037_api_keys_missing_columns.sql
-- Adds columns present in init_auth_db() inline DDL (core/auth/schema.py) but
-- never captured as migration files. Required for PostgreSQL where init_auth_db()
-- is a no-op and migrations are the sole schema source.

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS max_spend_cents INTEGER CHECK(max_spend_cents >= 0);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS per_job_cap_cents INTEGER CHECK(per_job_cap_cents >= 0);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_at TEXT;
