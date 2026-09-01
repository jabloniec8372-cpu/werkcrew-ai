ALTER TABLE canonical_jobs
    ADD COLUMN activity_state TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (activity_state IN ('ACTIVE', 'DORMANT'));

CREATE TABLE m1_operations (
    source_namespace TEXT NOT NULL CHECK (length(trim(source_namespace)) > 0),
    ingress_event_id TEXT NOT NULL CHECK (length(trim(ingress_event_id)) > 0),
    operation_kind TEXT NOT NULL CHECK (operation_kind IN (
        'CREATE_JOB',
        'RECORD_FOLLOW_UP',
        'MARK_DORMANT',
        'WAKE_JOB',
        'BIND_CONVERSATION'
    )),
    fingerprint_version TEXT NOT NULL
        CHECK (fingerprint_version = 'm1-operation-v1'),
    fingerprint_sha256 TEXT NOT NULL
        CHECK (length(fingerprint_sha256) = 64),
    canonical_request_json TEXT NOT NULL,
    received_at TEXT NOT NULL CHECK (length(trim(received_at)) > 0),
    completed_at TEXT NOT NULL CHECK (length(trim(completed_at)) > 0),
    outcome_code TEXT NOT NULL CHECK (length(trim(outcome_code)) > 0),
    job_id TEXT,
    result_json TEXT NOT NULL,
    PRIMARY KEY (source_namespace, ingress_event_id)
);

CREATE TABLE m1_operation_conflicts (
    source_namespace TEXT NOT NULL,
    ingress_event_id TEXT NOT NULL,
    conflicting_fingerprint_sha256 TEXT NOT NULL
        CHECK (length(conflicting_fingerprint_sha256) = 64),
    fingerprint_version TEXT NOT NULL
        CHECK (fingerprint_version = 'm1-operation-v1'),
    canonical_request_json TEXT NOT NULL,
    received_at TEXT NOT NULL CHECK (length(trim(received_at)) > 0),
    result_json TEXT NOT NULL,
    PRIMARY KEY (
        source_namespace,
        ingress_event_id,
        conflicting_fingerprint_sha256
    ),
    FOREIGN KEY (source_namespace, ingress_event_id)
        REFERENCES m1_operations(source_namespace, ingress_event_id)
);

CREATE TABLE m1_adapter_receipts (
    source_namespace TEXT NOT NULL CHECK (length(trim(source_namespace)) > 0),
    receipt_id TEXT NOT NULL CHECK (length(trim(receipt_id)) > 0),
    ingress_event_id TEXT NOT NULL CHECK (length(trim(ingress_event_id)) > 0),
    minted_at TEXT NOT NULL CHECK (length(trim(minted_at)) > 0),
    PRIMARY KEY (source_namespace, receipt_id),
    UNIQUE (source_namespace, ingress_event_id)
);

CREATE TABLE m1_conversation_jobs (
    source_namespace TEXT NOT NULL CHECK (length(trim(source_namespace)) > 0),
    conversation_id TEXT NOT NULL CHECK (length(trim(conversation_id)) > 0),
    job_id TEXT NOT NULL REFERENCES canonical_jobs(job_id),
    associated_at TEXT NOT NULL CHECK (length(trim(associated_at)) > 0),
    operation_source_namespace TEXT NOT NULL,
    operation_ingress_event_id TEXT NOT NULL,
    PRIMARY KEY (source_namespace, conversation_id, job_id),
    FOREIGN KEY (operation_source_namespace, operation_ingress_event_id)
        REFERENCES m1_operations(source_namespace, ingress_event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX ix_m1_conversation_jobs_lookup
    ON m1_conversation_jobs(source_namespace, conversation_id, job_id);

CREATE TABLE m1_follow_up_evidence (
    evidence_id TEXT PRIMARY KEY CHECK (length(trim(evidence_id)) > 0),
    job_id TEXT NOT NULL REFERENCES canonical_jobs(job_id),
    source_namespace TEXT NOT NULL CHECK (length(trim(source_namespace)) > 0),
    ingress_event_id TEXT NOT NULL CHECK (length(trim(ingress_event_id)) > 0),
    conversation_id TEXT,
    raw_text TEXT,
    raw_payload TEXT,
    occurred_at TEXT,
    received_at TEXT NOT NULL CHECK (length(trim(received_at)) > 0),
    UNIQUE (source_namespace, ingress_event_id),
    FOREIGN KEY (source_namespace, ingress_event_id)
        REFERENCES m1_operations(source_namespace, ingress_event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX ix_m1_follow_up_evidence_job
    ON m1_follow_up_evidence(job_id, received_at, evidence_id);

CREATE TABLE m1_lifecycle_history (
    lifecycle_event_id TEXT PRIMARY KEY
        CHECK (length(trim(lifecycle_event_id)) > 0),
    job_id TEXT NOT NULL REFERENCES canonical_jobs(job_id),
    job_sequence INTEGER NOT NULL CHECK (job_sequence > 0),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'FOLLOW_UP_RECORDED',
        'JOB_MARKED_DORMANT',
        'JOB_WOKEN'
    )),
    state_dimension TEXT NOT NULL CHECK (state_dimension = 'ACTIVITY'),
    from_activity_state TEXT CHECK (
        from_activity_state IS NULL
        OR from_activity_state IN ('ACTIVE', 'DORMANT')
    ),
    to_activity_state TEXT CHECK (
        to_activity_state IS NULL
        OR to_activity_state IN ('ACTIVE', 'DORMANT')
    ),
    server_timestamp TEXT NOT NULL CHECK (length(trim(server_timestamp)) > 0),
    occurred_at TEXT,
    actor_id TEXT NOT NULL CHECK (length(trim(actor_id)) > 0),
    actor_source TEXT NOT NULL CHECK (length(trim(actor_source)) > 0),
    reason_code TEXT CHECK (reason_code IN (
        'NO_CUSTOMER_RESPONSE',
        'CUSTOMER_PAUSED',
        'OWNER_PAUSED',
        'OTHER'
    )),
    reason_detail TEXT,
    operation_source_namespace TEXT NOT NULL,
    operation_ingress_event_id TEXT NOT NULL,
    evidence_id TEXT REFERENCES m1_follow_up_evidence(evidence_id),
    UNIQUE (job_id, job_sequence),
    UNIQUE (
        operation_source_namespace,
        operation_ingress_event_id,
        event_type
    ),
    FOREIGN KEY (operation_source_namespace, operation_ingress_event_id)
        REFERENCES m1_operations(source_namespace, ingress_event_id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        (event_type = 'FOLLOW_UP_RECORDED'
            AND from_activity_state IS NULL
            AND to_activity_state IS NULL
            AND reason_code IS NULL
            AND evidence_id IS NOT NULL)
        OR
        (event_type = 'JOB_MARKED_DORMANT'
            AND from_activity_state = 'ACTIVE'
            AND to_activity_state = 'DORMANT'
            AND reason_code IS NOT NULL)
        OR
        (event_type = 'JOB_WOKEN'
            AND from_activity_state = 'DORMANT'
            AND to_activity_state = 'ACTIVE'
            AND reason_code IS NULL)
    ),
    CHECK (
        reason_code IS NULL
        OR reason_code <> 'OTHER'
        OR length(trim(reason_detail)) > 0
    )
);

ALTER TABLE canonical_job_facts
    ADD COLUMN follow_up_evidence_id TEXT
    REFERENCES m1_follow_up_evidence(evidence_id);

CREATE INDEX ix_canonical_job_facts_evidence
    ON canonical_job_facts(follow_up_evidence_id);
