-- 0055_workspaces_content_purged_at.sql
-- v0.1: auto-content-deletion for expired and old-sealed workspaces.
--
-- The v0 sweeper only marked workspaces 'expired' but left their BLOB
-- content in workspace_artifacts forever. The KNOWN DEBT comment in
-- core/workspaces.py called this out. v0.1 adds a second sweeper pass
-- that NULLs content past the retention window.
--
-- content_purged_at: when the sweeper nulled all artifact content for
-- this workspace. Once set, the sweeper skips the workspace on future
-- passes. NULL means "content is still present (or never had any)".
-- Metadata and the seal manifest are retained indefinitely so external
-- verifiers can still fetch them after the bytes are gone.
--
-- Retention windows (configurable via env in core/workspaces.py):
--   expired -> 7 days after expires_at -> content nulled
--   sealed  -> 90 days after sealed_at -> content nulled (manifest kept)

ALTER TABLE workspaces ADD COLUMN content_purged_at TEXT NULL;
