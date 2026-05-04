-- Purge the six deprecated LLM-only wrappers (sunset 2026-07-26).
-- They were excluded from the curated catalog and force-suspended at startup.
-- This migration deletes any lingering registry rows so the IDs are reclaimed.
DELETE FROM agents WHERE agent_id IN (
    '5896576f-bbe6-59e4-83c1-5106002e7d10',  -- github_fetcher
    '3e133b66-3bc6-5003-9b64-3284b28a60c6',  -- pr_reviewer
    'f515323c-7df2-5742-ac06-bc38b59a40cb',  -- test_generator
    'ce9504a3-74c8-51a5-913e-6ae55787abc8',  -- spec_writer
    '48c24ce5-d9cb-5f76-9e2f-fce1878f8c4c',  -- changelog_agent
    'd11ddab1-bcca-55de-8b00-c9efadc69c79'   -- package_finder
);
