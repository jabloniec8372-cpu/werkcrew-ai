CREATE TABLE canonical_jobs (
    job_id TEXT PRIMARY KEY CHECK (length(trim(job_id)) > 0),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state = 'RECEIVED'),
    created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
    updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
);

CREATE TABLE canonical_job_intake (
    job_id TEXT PRIMARY KEY REFERENCES canonical_jobs(job_id),
    intake_source TEXT NOT NULL CHECK (length(trim(intake_source)) > 0),
    raw_text TEXT,
    raw_payload TEXT,
    received_at TEXT NOT NULL CHECK (length(trim(received_at)) > 0)
);

CREATE TABLE canonical_job_facts (
    job_id TEXT NOT NULL REFERENCES canonical_jobs(job_id),
    fact_name TEXT NOT NULL CHECK (fact_name IN (
        'CLIENT_REFERENCE',
        'CLIENT_NAME',
        'ADDRESS',
        'PHONE',
        'EMAIL',
        'SCOPE',
        'MATERIALS',
        'PREFERRED_CONTACT_CHANNEL'
    )),
    revision INTEGER NOT NULL CHECK (revision > 0),
    fact_value TEXT,
    knowledge_state TEXT NOT NULL CHECK (
        knowledge_state IN ('KNOWN', 'UNKNOWN')
    ),
    verification_state TEXT NOT NULL CHECK (
        verification_state IN ('UNVERIFIED', 'VERIFIED')
    ),
    provenance_source TEXT NOT NULL
        CHECK (length(trim(provenance_source)) > 0),
    recorded_at TEXT NOT NULL CHECK (length(trim(recorded_at)) > 0),
    PRIMARY KEY (job_id, fact_name, revision),
    CHECK (
        (knowledge_state = 'KNOWN' AND fact_value IS NOT NULL
            AND length(trim(fact_value)) > 0)
        OR
        (knowledge_state = 'UNKNOWN' AND fact_value IS NULL)
    ),
    CHECK (
        knowledge_state = 'KNOWN' OR verification_state = 'UNVERIFIED'
    )
);

CREATE INDEX ix_canonical_job_facts_current
    ON canonical_job_facts(job_id, fact_name, revision DESC);
