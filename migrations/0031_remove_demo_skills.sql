-- Remove legacy demo / smoke-test skill rows from the registry. The
-- in-memory blocklist that used to hide them has been deleted, so the
-- rows themselves are dropped to keep every read path consistent.
DELETE FROM agents
WHERE LOWER(TRIM(COALESCE(name, ''))) IN (
    'reverse_string', 'reverse string',
    'echo_skill', 'echo skill',
    'json_validator', 'json validator'
);
