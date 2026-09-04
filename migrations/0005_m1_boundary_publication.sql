ALTER TABLE canonical_jobs
    ADD COLUMN source_revision INTEGER NOT NULL DEFAULT 1
    CHECK (source_revision > 0);

CREATE TRIGGER canonical_jobs_source_revision_monotonic
BEFORE UPDATE OF source_revision ON canonical_jobs
WHEN NEW.source_revision <> OLD.source_revision + 1
BEGIN
    SELECT RAISE(ABORT, 'canonical source_revision must advance by one');
END;

CREATE TABLE m1_handoff_publications (
    job_id TEXT NOT NULL REFERENCES canonical_jobs(job_id),
    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
    handoff_id TEXT NOT NULL UNIQUE CHECK (length(trim(handoff_id)) > 0),
    projection_schema_version TEXT NOT NULL
        CHECK (projection_schema_version = 'm1-m2-handoff-v1'),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_content_json TEXT NOT NULL
        CHECK (length(trim(canonical_content_json)) > 0),
    published_at TEXT NOT NULL CHECK (length(trim(published_at)) > 0),
    PRIMARY KEY (job_id, source_revision)
);

CREATE TRIGGER m1_handoff_publications_no_replace
BEFORE INSERT ON m1_handoff_publications
WHEN EXISTS (
    SELECT 1
    FROM m1_handoff_publications
    WHERE (job_id = NEW.job_id AND source_revision = NEW.source_revision)
       OR handoff_id = NEW.handoff_id
)
BEGIN
    SELECT RAISE(ABORT, 'M1 handoff publications cannot be replaced');
END;

CREATE TRIGGER m1_handoff_publications_validate_projection
BEFORE INSERT ON m1_handoff_publications
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM canonical_jobs WHERE job_id = NEW.job_id
        )
        THEN RAISE(ABORT, 'M1 handoff job does not exist')
    END;

    SELECT CASE
        WHEN NEW.source_revision <> (
            SELECT source_revision
            FROM canonical_jobs
            WHERE job_id = NEW.job_id
        )
        THEN RAISE(ABORT, 'M1 handoff source_revision is not current')
    END;

    SELECT CASE
        WHEN json_valid(NEW.canonical_content_json) <> 1
        THEN RAISE(ABORT, 'M1 handoff content is not valid JSON')
    END;

    SELECT CASE
        WHEN json_type(NEW.canonical_content_json) <> 'object'
        THEN RAISE(ABORT, 'M1 handoff content must be a JSON object')
    END;

    SELECT CASE
        WHEN werkcrew_canonical_json(NEW.canonical_content_json)
             IS NOT NEW.canonical_content_json
        THEN RAISE(ABORT, 'M1 handoff content is not canonical JSON')
    END;

    SELECT CASE
        WHEN werkcrew_sha256(NEW.canonical_content_json)
             IS NOT NEW.content_sha256
        THEN RAISE(ABORT, 'M1 handoff content hash does not match')
    END;

    SELECT CASE
        WHEN (SELECT COUNT(*) FROM json_each(NEW.canonical_content_json)) <> 6
          OR EXISTS (
              SELECT 1
              FROM json_each(NEW.canonical_content_json)
              WHERE key NOT IN (
                  'activity_state',
                  'facts',
                  'job_id',
                  'lifecycle_state',
                  'schema_version',
                  'source_revision'
              )
          )
        THEN RAISE(ABORT, 'M1 handoff projection fields do not match schema')
    END;

    SELECT CASE
        WHEN json_type(NEW.canonical_content_json, '$.job_id') <> 'text'
          OR json_type(NEW.canonical_content_json, '$.source_revision') <> 'integer'
          OR json_type(NEW.canonical_content_json, '$.schema_version') <> 'text'
          OR json_type(NEW.canonical_content_json, '$.lifecycle_state') <> 'text'
          OR json_type(NEW.canonical_content_json, '$.activity_state') <> 'text'
          OR json_extract(NEW.canonical_content_json, '$.job_id')
             IS NOT NEW.job_id
          OR json_extract(NEW.canonical_content_json, '$.source_revision')
             IS NOT NEW.source_revision
          OR json_extract(NEW.canonical_content_json, '$.schema_version')
             IS NOT NEW.projection_schema_version
          OR json_extract(NEW.canonical_content_json, '$.lifecycle_state')
             IS NOT (
                 SELECT lifecycle_state
                 FROM canonical_jobs
                 WHERE job_id = NEW.job_id
             )
          OR json_extract(NEW.canonical_content_json, '$.activity_state')
             IS NOT (
                 SELECT activity_state
                 FROM canonical_jobs
                 WHERE job_id = NEW.job_id
             )
        THEN RAISE(ABORT, 'M1 handoff projection does not match canonical job')
    END;

    SELECT CASE
        WHEN json_type(NEW.canonical_content_json, '$.facts') <> 'array'
        THEN RAISE(ABORT, 'M1 handoff facts must be a JSON array')
    END;

    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM json_each(NEW.canonical_content_json, '$.facts') AS item
            WHERE json_type(item.value) <> 'object'
               OR (SELECT COUNT(*) FROM json_each(item.value)) <> 7
               OR EXISTS (
                   SELECT 1
                   FROM json_each(item.value)
                   WHERE key NOT IN (
                       'evidence_id',
                       'knowledge_state',
                       'name',
                       'provenance_source',
                       'revision',
                       'value',
                       'verification_state'
                   )
               )
        )
        THEN RAISE(ABORT, 'M1 handoff fact fields do not match schema')
    END;

    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM json_each(NEW.canonical_content_json, '$.facts') AS earlier
            JOIN json_each(NEW.canonical_content_json, '$.facts') AS later
              ON CAST(earlier.key AS INTEGER) < CAST(later.key AS INTEGER)
            WHERE json_extract(earlier.value, '$.name')
                  > json_extract(later.value, '$.name')
        )
        THEN RAISE(ABORT, 'M1 handoff facts are not sorted by name')
    END;

    SELECT CASE
        WHEN json_array_length(NEW.canonical_content_json, '$.facts') <> (
            SELECT COUNT(*)
            FROM canonical_job_facts AS fact
            WHERE fact.job_id = NEW.job_id
              AND fact.revision = (
                  SELECT MAX(current.revision)
                  FROM canonical_job_facts AS current
                  WHERE current.job_id = fact.job_id
                    AND current.fact_name = fact.fact_name
              )
        )
        THEN RAISE(ABORT, 'M1 handoff fact count does not match canonical facts')
    END;

    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM canonical_job_facts AS fact
            WHERE fact.job_id = NEW.job_id
              AND fact.revision = (
                  SELECT MAX(current.revision)
                  FROM canonical_job_facts AS current
                  WHERE current.job_id = fact.job_id
                    AND current.fact_name = fact.fact_name
              )
              AND (
                  SELECT COUNT(*)
                  FROM json_each(
                      NEW.canonical_content_json,
                      '$.facts'
                  ) AS item
                  WHERE json_type(item.value, '$.name') = 'text'
                    AND json_type(item.value, '$.revision') = 'integer'
                    AND json_extract(item.value, '$.name') IS fact.fact_name
                    AND json_extract(item.value, '$.revision') IS fact.revision
                    AND json_extract(item.value, '$.value') IS fact.fact_value
                    AND json_extract(item.value, '$.knowledge_state')
                        IS fact.knowledge_state
                    AND json_extract(item.value, '$.verification_state')
                        IS fact.verification_state
                    AND json_extract(item.value, '$.provenance_source')
                        IS fact.provenance_source
                    AND json_extract(item.value, '$.evidence_id')
                        IS fact.follow_up_evidence_id
              ) <> 1
        )
        THEN RAISE(ABORT, 'M1 handoff facts do not match canonical facts')
    END;
END;

CREATE TRIGGER m1_handoff_publications_no_update
BEFORE UPDATE ON m1_handoff_publications
BEGIN
    SELECT RAISE(ABORT, 'M1 handoff publications are immutable');
END;

CREATE TRIGGER m1_handoff_publications_no_delete
BEFORE DELETE ON m1_handoff_publications
BEGIN
    SELECT RAISE(ABORT, 'M1 handoff publications are append-only');
END;
