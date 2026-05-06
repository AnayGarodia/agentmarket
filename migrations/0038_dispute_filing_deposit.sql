-- Backfill dispute filing-deposit column for databases created before
-- dispute deposits shipped. PostgreSQL production does not run the SQLite
-- init_disputes_db ALTER path, so this must be a migration.
ALTER TABLE disputes ADD COLUMN filing_deposit_cents INTEGER NOT NULL DEFAULT 0;
