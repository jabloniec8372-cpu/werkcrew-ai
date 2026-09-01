ALTER TABLE schema_migrations RENAME TO schema_migrations_legacy;

CREATE TABLE schema_migrations (
    migration_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE CHECK (version > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    checksum_sha256 TEXT NOT NULL CHECK (length(checksum_sha256) = 64),
    applied_at TEXT NOT NULL
);

INSERT INTO schema_migrations(
    migration_id,
    version,
    name,
    checksum_sha256,
    applied_at
)
SELECT
    '0001_m7_persistent_dispatch',
    version,
    'm7_persistent_dispatch',
    '2cbb90268d7a8ecd0ec7682e1265d70da2a700ca3399687bdc0b2c825e3af1fb',
    applied_at
FROM schema_migrations_legacy
WHERE version = 1;

DROP TABLE schema_migrations_legacy;
