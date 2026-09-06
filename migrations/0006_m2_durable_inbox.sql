CREATE UNIQUE INDEX m1_handoff_publications_exact_identity
ON m1_handoff_publications(job_id, source_revision, handoff_id);

CREATE TABLE m2_worker_registry (
    registry_key TEXT PRIMARY KEY CHECK (registry_key = 'GLOBAL'),
    root_schema_version TEXT NOT NULL CHECK (root_schema_version = 'm2-worker-registry-v1'),
    registry_revision INTEGER NOT NULL CHECK (registry_revision >= 0),
    canonical_root_json TEXT NOT NULL CHECK (
        json_valid(canonical_root_json) = 1
        AND json_type(canonical_root_json) = 'object'
        AND werkcrew_canonical_json(canonical_root_json) IS canonical_root_json
    ),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_root_json) IS content_sha256
    ),
    CHECK (json_extract(canonical_root_json, '$.document_type') IS 'WorkerIdentityRegistry'),
    CHECK (json_extract(canonical_root_json, '$.schema_version') IS root_schema_version),
    CHECK (json_extract(canonical_root_json, '$.payload.registry_revision') IS registry_revision)
);

CREATE TABLE m2_worker_identities (
    worker_id TEXT PRIMARY KEY CHECK (length(trim(worker_id)) > 0),
    registry_key TEXT NOT NULL DEFAULT 'GLOBAL'
        REFERENCES m2_worker_registry(registry_key),
    CHECK (registry_key = 'GLOBAL')
);

CREATE TABLE m2_job_execution_roots (
    job_id TEXT PRIMARY KEY REFERENCES canonical_jobs(job_id)
        CHECK (length(trim(job_id)) > 0),
    root_schema_version TEXT NOT NULL CHECK (root_schema_version = 'm2-job-execution-root-v1'),
    job_execution_revision INTEGER NOT NULL CHECK (job_execution_revision >= 0),
    canonical_root_json TEXT NOT NULL CHECK (
        json_valid(canonical_root_json) = 1
        AND json_type(canonical_root_json) = 'object'
        AND werkcrew_canonical_json(canonical_root_json) IS canonical_root_json
    ),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_root_json) IS content_sha256
    ),
    CHECK (json_extract(canonical_root_json, '$.document_type') IS 'M2JobExecutionRoot'),
    CHECK (json_extract(canonical_root_json, '$.schema_version') IS root_schema_version),
    CHECK (json_extract(canonical_root_json, '$.payload.job_id') IS job_id),
    CHECK (json_extract(canonical_root_json, '$.payload.job_execution_revision') IS job_execution_revision)
);

CREATE TABLE m2_task_routes (
    task_id TEXT PRIMARY KEY CHECK (length(trim(task_id)) > 0),
    job_id TEXT NOT NULL REFERENCES m2_job_execution_roots(job_id),
    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
    source_handoff_id TEXT NOT NULL CHECK (length(trim(source_handoff_id)) > 0),
    definition_version TEXT NOT NULL CHECK (length(trim(definition_version)) > 0),
    supersedes_task_id TEXT REFERENCES m2_task_routes(task_id),
    route_json TEXT NOT NULL CHECK (
        json_valid(route_json) = 1
        AND json_type(route_json) = 'object'
        AND werkcrew_canonical_json(route_json) IS route_json
    ),
    route_sha256 TEXT NOT NULL CHECK (
        length(route_sha256) = 64
        AND route_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(route_json) IS route_sha256
    ),
    FOREIGN KEY (job_id, source_revision, source_handoff_id)
        REFERENCES m1_handoff_publications(job_id, source_revision, handoff_id),
    CHECK (supersedes_task_id IS NULL OR supersedes_task_id <> task_id)
);

CREATE TABLE m2_plan_day_roots (
    plan_day_id TEXT PRIMARY KEY CHECK (length(trim(plan_day_id)) > 0),
    root_schema_version TEXT NOT NULL CHECK (root_schema_version = 'm2-plan-day-root-v1'),
    worker_id TEXT NOT NULL REFERENCES m2_worker_identities(worker_id),
    business_date TEXT NOT NULL CHECK (
        length(business_date) = 10 AND date(business_date) IS business_date
    ),
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'ISSUED', 'ACTIVE', 'DAY_CLOSED')),
    plan_day_revision INTEGER NOT NULL CHECK (plan_day_revision >= 0),
    canonical_root_json TEXT NOT NULL CHECK (
        json_valid(canonical_root_json) = 1
        AND json_type(canonical_root_json) = 'object'
        AND werkcrew_canonical_json(canonical_root_json) IS canonical_root_json
    ),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_root_json) IS content_sha256
    ),
    CHECK (json_extract(canonical_root_json, '$.document_type') IS 'PlanDayRoot'),
    CHECK (json_extract(canonical_root_json, '$.schema_version') IS root_schema_version),
    CHECK (json_extract(canonical_root_json, '$.payload.plan_day_id') IS plan_day_id),
    CHECK (json_extract(canonical_root_json, '$.payload.worker_id') IS worker_id),
    CHECK (json_extract(canonical_root_json, '$.payload.business_date') IS business_date),
    CHECK (json_extract(canonical_root_json, '$.payload.status') IS status),
    CHECK (json_extract(canonical_root_json, '$.payload.plan_day_revision') IS plan_day_revision)
);

CREATE TABLE m2_assignment_routes (
    assignment_id TEXT PRIMARY KEY CHECK (length(trim(assignment_id)) > 0),
    job_id TEXT NOT NULL REFERENCES m2_job_execution_roots(job_id),
    task_id TEXT NOT NULL REFERENCES m2_task_routes(task_id),
    definition_version TEXT NOT NULL CHECK (length(trim(definition_version)) > 0),
    assignment_kind TEXT NOT NULL CHECK (assignment_kind IN ('SINGLE', 'CREW')),
    lead_worker_id TEXT REFERENCES m2_worker_identities(worker_id),
    supersedes_assignment_id TEXT REFERENCES m2_assignment_routes(assignment_id),
    route_json TEXT NOT NULL CHECK (
        json_valid(route_json) = 1
        AND json_type(route_json) = 'object'
        AND werkcrew_canonical_json(route_json) IS route_json
    ),
    route_sha256 TEXT NOT NULL CHECK (
        length(route_sha256) = 64
        AND route_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(route_json) IS route_sha256
    ),
    CHECK (supersedes_assignment_id IS NULL OR supersedes_assignment_id <> assignment_id)
);

CREATE TABLE m2_assignment_members (
    assignment_id TEXT NOT NULL REFERENCES m2_assignment_routes(assignment_id),
    worker_id TEXT NOT NULL REFERENCES m2_worker_identities(worker_id),
    PRIMARY KEY (assignment_id, worker_id)
);

CREATE TABLE m2_assignment_plan_days (
    assignment_id TEXT NOT NULL REFERENCES m2_assignment_routes(assignment_id),
    plan_day_id TEXT NOT NULL REFERENCES m2_plan_day_roots(plan_day_id),
    PRIMARY KEY (assignment_id, plan_day_id)
);

CREATE TABLE m2_directive_roots (
    directive_id TEXT PRIMARY KEY CHECK (length(trim(directive_id)) > 0),
    root_schema_version TEXT NOT NULL CHECK (root_schema_version = 'm2-directive-root-v1'),
    worker_id TEXT NOT NULL REFERENCES m2_worker_identities(worker_id),
    job_id TEXT REFERENCES m2_job_execution_roots(job_id),
    task_id TEXT REFERENCES m2_task_routes(task_id),
    assignment_id TEXT REFERENCES m2_assignment_routes(assignment_id),
    plan_day_id TEXT REFERENCES m2_plan_day_roots(plan_day_id),
    stream_kind TEXT NOT NULL CHECK (stream_kind IN ('WORKER', 'JOB', 'TASK', 'ASSIGNMENT', 'PLAN_DAY')),
    stream_id TEXT NOT NULL CHECK (length(trim(stream_id)) > 0),
    issuance_sequence INTEGER NOT NULL CHECK (issuance_sequence > 0),
    supersedes_directive_id TEXT REFERENCES m2_directive_roots(directive_id),
    directive_type TEXT NOT NULL CHECK (
        directive_type IN (
            'INFO_NOTICE', 'ACTION_REQUIRED', 'STOP_DIRECTIVE',
            'END_OF_DAY_OPTIONS', 'WHY_EXPLAINED'
        )
    ),
    directive_class TEXT NOT NULL CHECK (directive_class IN ('ACTION', 'STOP', 'INFO')),
    issued_at TEXT NOT NULL CHECK (length(trim(issued_at)) > 0),
    escalation_due_at TEXT,
    proposed_plan_reference TEXT,
    delivery_evidence TEXT NOT NULL CHECK (
        delivery_evidence IN ('QUEUED', 'CHANNEL_ACCEPTED', 'ACKED')
    ),
    acknowledged_event_id TEXT,
    exception_event_id TEXT,
    e1_escalated INTEGER NOT NULL CHECK (e1_escalated IN (0, 1)),
    stop_in_force INTEGER NOT NULL CHECK (stop_in_force IN (0, 1)),
    directive_revision INTEGER NOT NULL CHECK (directive_revision >= 0),
    canonical_root_json TEXT NOT NULL CHECK (
        json_valid(canonical_root_json) = 1
        AND json_type(canonical_root_json) = 'object'
        AND werkcrew_canonical_json(canonical_root_json) IS canonical_root_json
    ),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_root_json) IS content_sha256
    ),
    UNIQUE (worker_id, stream_kind, stream_id, issuance_sequence),
    CHECK (supersedes_directive_id IS NULL OR supersedes_directive_id <> directive_id),
    CHECK (
        (plan_day_id IS NOT NULL
         AND stream_kind = 'PLAN_DAY' AND stream_id IS plan_day_id)
        OR
        (plan_day_id IS NULL AND assignment_id IS NOT NULL
         AND stream_kind = 'ASSIGNMENT' AND stream_id IS assignment_id)
        OR
        (plan_day_id IS NULL AND assignment_id IS NULL AND task_id IS NOT NULL
         AND stream_kind = 'TASK' AND stream_id IS task_id)
        OR
        (plan_day_id IS NULL AND assignment_id IS NULL AND task_id IS NULL
         AND job_id IS NOT NULL
         AND stream_kind = 'JOB' AND stream_id IS job_id)
        OR
        (plan_day_id IS NULL AND assignment_id IS NULL AND task_id IS NULL
         AND job_id IS NULL
         AND stream_kind = 'WORKER' AND stream_id IS worker_id)
    ),
    CHECK (json_extract(canonical_root_json, '$.document_type') IS 'DirectiveRoot'),
    CHECK (json_extract(canonical_root_json, '$.schema_version') IS root_schema_version),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.directive_id') IS directive_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.worker_id') IS worker_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.job_id') IS job_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.task_id') IS task_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.assignment_id') IS assignment_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.plan_day_id') IS plan_day_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.issuance_sequence') IS issuance_sequence),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.supersedes_directive_id') IS supersedes_directive_id),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.directive_type') IS directive_type),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.directive_class') IS directive_class),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.issued_at') IS issued_at),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.escalation_due_at') IS escalation_due_at),
    CHECK (json_extract(canonical_root_json, '$.payload.definition.proposed_plan_reference') IS proposed_plan_reference),
    CHECK (json_extract(canonical_root_json, '$.payload.delivery_evidence') IS delivery_evidence),
    CHECK (json_extract(canonical_root_json, '$.payload.acknowledged_event_id') IS acknowledged_event_id),
    CHECK (json_extract(canonical_root_json, '$.payload.exception_event_id') IS exception_event_id),
    CHECK (json_extract(canonical_root_json, '$.payload.e1_escalated') IS e1_escalated),
    CHECK (json_extract(canonical_root_json, '$.payload.stop_in_force') IS stop_in_force),
    CHECK (json_extract(canonical_root_json, '$.payload.directive_revision') IS directive_revision)
);

CREATE TABLE m2_input_inbox (
    input_namespace TEXT NOT NULL CHECK (input_namespace IN ('FIELD_EVENT', 'SYSTEM_SIGNAL')),
    input_id TEXT NOT NULL CHECK (length(trim(input_id)) > 0),
    input_schema_version TEXT NOT NULL CHECK (
        input_schema_version IN ('m2-field-event-envelope-v1', 'm2-system-signal-v1')
    ),
    canonical_input_json TEXT NOT NULL CHECK (
        json_valid(canonical_input_json) = 1
        AND json_type(canonical_input_json) = 'object'
        AND werkcrew_canonical_json(canonical_input_json) IS canonical_input_json
    ),
    payload_sha256 TEXT NOT NULL CHECK (
        length(payload_sha256) = 64
        AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_input_json) IS payload_sha256
    ),
    presented_server_event_id TEXT,
    claimed_server_event_id TEXT,
    server_claim_owner_input_id TEXT,
    raw_job_id TEXT,
    raw_task_id TEXT,
    raw_assignment_id TEXT,
    raw_plan_day_id TEXT,
    raw_directive_id TEXT,
    raw_against_event_id TEXT,
    policy_context_json TEXT NOT NULL CHECK (
        json_valid(policy_context_json) = 1
        AND json_type(policy_context_json) = 'object'
        AND werkcrew_canonical_json(policy_context_json) IS policy_context_json
    ),
    policy_context_sha256 TEXT NOT NULL CHECK (
        length(policy_context_sha256) = 64
        AND policy_context_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(policy_context_json) IS policy_context_sha256
    ),
    rule_version TEXT NOT NULL CHECK (length(trim(rule_version)) > 0),
    first_received_at TEXT NOT NULL CHECK (length(trim(first_received_at)) > 0),
    processing_status TEXT NOT NULL CHECK (processing_status IN ('RECEIVED', 'COMPLETED')),
    routing_json TEXT,
    routing_sha256 TEXT,
    precondition_vector_json TEXT,
    precondition_vector_sha256 TEXT,
    reduction_input_proof_json TEXT,
    reduction_input_proof_sha256 TEXT,
    resulting_revision_vector_json TEXT,
    resulting_revision_vector_sha256 TEXT,
    outcome TEXT CHECK (outcome IN ('APPLIED', 'REPORTED', 'NOOP', 'REJECTED')),
    reason_codes_json TEXT,
    missing_requirements_json TEXT,
    response_effects_json TEXT,
    emitted_effects_json TEXT,
    appended_receipt_json TEXT,
    appended_receipt_sha256 TEXT,
    completed_at TEXT,
    PRIMARY KEY (input_namespace, input_id),
    CHECK (
        (input_namespace = 'FIELD_EVENT'
         AND input_schema_version = 'm2-field-event-envelope-v1'
         AND presented_server_event_id IS NOT NULL
         AND length(trim(presented_server_event_id)) > 0)
        OR
        (input_namespace = 'SYSTEM_SIGNAL'
         AND input_schema_version = 'm2-system-signal-v1'
         AND presented_server_event_id IS NULL
         AND claimed_server_event_id IS NULL)
    ),
    CHECK (claimed_server_event_id IS NULL OR claimed_server_event_id IS presented_server_event_id),
    CHECK (server_claim_owner_input_id IS NULL OR length(trim(server_claim_owner_input_id)) > 0),
    CHECK (raw_job_id IS NULL OR length(trim(raw_job_id)) > 0),
    CHECK (raw_task_id IS NULL OR length(trim(raw_task_id)) > 0),
    CHECK (raw_assignment_id IS NULL OR length(trim(raw_assignment_id)) > 0),
    CHECK (raw_plan_day_id IS NULL OR length(trim(raw_plan_day_id)) > 0),
    CHECK (raw_directive_id IS NULL OR length(trim(raw_directive_id)) > 0),
    CHECK (raw_against_event_id IS NULL OR length(trim(raw_against_event_id)) > 0),
    CHECK (
        (routing_json IS NULL AND routing_sha256 IS NULL)
        OR (json_valid(routing_json) = 1
            AND werkcrew_canonical_json(routing_json) IS routing_json
            AND length(routing_sha256) = 64
            AND routing_sha256 NOT GLOB '*[^0-9a-f]*'
            AND werkcrew_sha256(routing_json) IS routing_sha256)
    ),
    CHECK (
        (precondition_vector_json IS NULL AND precondition_vector_sha256 IS NULL)
        OR (json_valid(precondition_vector_json) = 1
            AND werkcrew_canonical_json(precondition_vector_json) IS precondition_vector_json
            AND length(precondition_vector_sha256) = 64
            AND precondition_vector_sha256 NOT GLOB '*[^0-9a-f]*'
            AND werkcrew_sha256(precondition_vector_json) IS precondition_vector_sha256)
    ),
    CHECK (
        (reduction_input_proof_json IS NULL AND reduction_input_proof_sha256 IS NULL)
        OR (json_valid(reduction_input_proof_json) = 1
            AND werkcrew_canonical_json(reduction_input_proof_json)
                IS reduction_input_proof_json
            AND length(reduction_input_proof_sha256) = 64
            AND reduction_input_proof_sha256 NOT GLOB '*[^0-9a-f]*'
            AND werkcrew_sha256(reduction_input_proof_json)
                IS reduction_input_proof_sha256)
    ),
    CHECK (
        (resulting_revision_vector_json IS NULL AND resulting_revision_vector_sha256 IS NULL)
        OR (json_valid(resulting_revision_vector_json) = 1
            AND werkcrew_canonical_json(resulting_revision_vector_json) IS resulting_revision_vector_json
            AND length(resulting_revision_vector_sha256) = 64
            AND resulting_revision_vector_sha256 NOT GLOB '*[^0-9a-f]*'
            AND werkcrew_sha256(resulting_revision_vector_json) IS resulting_revision_vector_sha256)
    ),
    CHECK (
        (appended_receipt_json IS NULL AND appended_receipt_sha256 IS NULL)
        OR (json_valid(appended_receipt_json) = 1
            AND werkcrew_canonical_json(appended_receipt_json) IS appended_receipt_json
            AND length(appended_receipt_sha256) = 64
            AND appended_receipt_sha256 NOT GLOB '*[^0-9a-f]*'
            AND werkcrew_sha256(appended_receipt_json) IS appended_receipt_sha256)
    ),
    CHECK (
        reason_codes_json IS NULL
        OR (json_valid(reason_codes_json) = 1
            AND werkcrew_canonical_json(reason_codes_json) IS reason_codes_json)
    ),
    CHECK (
        missing_requirements_json IS NULL
        OR (json_valid(missing_requirements_json) = 1
            AND werkcrew_canonical_json(missing_requirements_json)
                IS missing_requirements_json)
    ),
    CHECK (
        response_effects_json IS NULL
        OR (json_valid(response_effects_json) = 1
            AND werkcrew_canonical_json(response_effects_json)
                IS response_effects_json)
    ),
    CHECK (
        emitted_effects_json IS NULL
        OR (json_valid(emitted_effects_json) = 1
            AND werkcrew_canonical_json(emitted_effects_json)
                IS emitted_effects_json)
    ),
    CHECK (
        processing_status = 'RECEIVED'
        OR (
            routing_json IS NOT NULL
            AND precondition_vector_json IS NOT NULL
            AND reduction_input_proof_json IS NOT NULL
            AND resulting_revision_vector_json IS NOT NULL
            AND outcome IS NOT NULL
            AND reason_codes_json IS NOT NULL
            AND missing_requirements_json IS NOT NULL
            AND response_effects_json IS NOT NULL
            AND emitted_effects_json IS NOT NULL
            AND completed_at IS NOT NULL
            AND (
                (input_namespace = 'FIELD_EVENT'
                 AND server_claim_owner_input_id IS NULL
                 AND claimed_server_event_id IS presented_server_event_id
                 AND appended_receipt_json IS NOT NULL)
                OR
                (input_namespace = 'FIELD_EVENT'
                 AND server_claim_owner_input_id IS NOT NULL
                 AND claimed_server_event_id IS NULL
                 AND outcome = 'REJECTED'
                 AND appended_receipt_json IS NULL)
                OR
                (input_namespace = 'SYSTEM_SIGNAL'
                 AND appended_receipt_json IS NULL)
            )
        )
    )
);

CREATE UNIQUE INDEX m2_input_inbox_claimed_server_event_id
ON m2_input_inbox(claimed_server_event_id)
WHERE claimed_server_event_id IS NOT NULL;

CREATE INDEX m2_input_inbox_status
ON m2_input_inbox(processing_status, input_namespace, input_id);

CREATE TABLE m2_input_conflicts (
    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_namespace TEXT NOT NULL,
    input_id TEXT NOT NULL,
    conflict_kind TEXT NOT NULL CHECK (
        conflict_kind IN ('INPUT_ID_PAYLOAD_MISMATCH', 'SERVER_EVENT_ID_COLLISION')
    ),
    attempted_input_json TEXT NOT NULL CHECK (
        json_valid(attempted_input_json) = 1
        AND werkcrew_canonical_json(attempted_input_json) IS attempted_input_json
    ),
    attempted_payload_sha256 TEXT NOT NULL CHECK (
        length(attempted_payload_sha256) = 64
        AND attempted_payload_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(attempted_input_json) IS attempted_payload_sha256
    ),
    owner_input_id TEXT NOT NULL CHECK (length(trim(owner_input_id)) > 0),
    recorded_at TEXT NOT NULL CHECK (length(trim(recorded_at)) > 0),
    UNIQUE (
        input_namespace, input_id, conflict_kind,
        attempted_payload_sha256, owner_input_id
    ),
    FOREIGN KEY (input_namespace, input_id)
        REFERENCES m2_input_inbox(input_namespace, input_id)
);

CREATE TABLE m2_evidence_identities (
    evidence_id TEXT PRIMARY KEY CHECK (length(trim(evidence_id)) > 0),
    evidence_kind TEXT NOT NULL CHECK (
        evidence_kind IN ('PHOTO', 'VOICE', 'TEXT', 'MEASUREMENT')
    ),
    content_reference TEXT NOT NULL CHECK (length(trim(content_reference)) > 0),
    owner_job_id TEXT REFERENCES m2_job_execution_roots(job_id),
    owner_task_id TEXT REFERENCES m2_task_routes(task_id),
    owner_assignment_id TEXT REFERENCES m2_assignment_routes(assignment_id),
    owner_stage_id TEXT,
    canonical_evidence_json TEXT NOT NULL CHECK (
        json_valid(canonical_evidence_json) = 1
        AND werkcrew_canonical_json(canonical_evidence_json) IS canonical_evidence_json
    ),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_evidence_json) IS content_sha256
    ),
    CHECK (owner_task_id IS NULL OR owner_job_id IS NOT NULL),
    CHECK (owner_assignment_id IS NULL OR owner_task_id IS NOT NULL),
    CHECK (owner_job_id IS NULL OR length(trim(owner_job_id)) > 0),
    CHECK (owner_task_id IS NULL OR length(trim(owner_task_id)) > 0),
    CHECK (owner_assignment_id IS NULL OR length(trim(owner_assignment_id)) > 0),
    CHECK (owner_stage_id IS NULL OR length(trim(owner_stage_id)) > 0)
);

CREATE TABLE m2_evidence_usages (
    input_namespace TEXT NOT NULL,
    input_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES m2_evidence_identities(evidence_id),
    job_id TEXT,
    task_id TEXT,
    assignment_id TEXT,
    stage_id TEXT,
    scope_json TEXT NOT NULL CHECK (
        json_valid(scope_json) = 1
        AND werkcrew_canonical_json(scope_json) IS scope_json
    ),
    scope_sha256 TEXT NOT NULL CHECK (
        length(scope_sha256) = 64
        AND scope_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(scope_json) IS scope_sha256
    ),
    PRIMARY KEY (input_namespace, input_id, evidence_id),
    FOREIGN KEY (input_namespace, input_id)
        REFERENCES m2_input_inbox(input_namespace, input_id),
    CHECK (job_id IS NULL OR length(trim(job_id)) > 0),
    CHECK (task_id IS NULL OR length(trim(task_id)) > 0),
    CHECK (assignment_id IS NULL OR length(trim(assignment_id)) > 0),
    CHECK (stage_id IS NULL OR length(trim(stage_id)) > 0)
);

CREATE TABLE m2_effect_outbox (
    input_namespace TEXT NOT NULL,
    input_id TEXT NOT NULL,
    effect_ordinal INTEGER NOT NULL CHECK (effect_ordinal >= 0),
    effect_type TEXT NOT NULL CHECK (length(trim(effect_type)) > 0),
    canonical_effect_json TEXT NOT NULL CHECK (
        json_valid(canonical_effect_json) = 1
        AND werkcrew_canonical_json(canonical_effect_json) IS canonical_effect_json
    ),
    effect_sha256 TEXT NOT NULL CHECK (
        length(effect_sha256) = 64
        AND effect_sha256 NOT GLOB '*[^0-9a-f]*'
        AND werkcrew_sha256(canonical_effect_json) IS effect_sha256
    ),
    dispatch_status TEXT NOT NULL CHECK (
        dispatch_status IN ('RECORDED', 'DISPATCHING', 'DISPATCHED', 'FAILED')
    ),
    recorded_at TEXT NOT NULL CHECK (length(trim(recorded_at)) > 0),
    dispatched_at TEXT,
    PRIMARY KEY (input_namespace, input_id, effect_ordinal),
    FOREIGN KEY (input_namespace, input_id)
        REFERENCES m2_input_inbox(input_namespace, input_id)
);

-- SQLite REPLACE deletes the conflicting row before inserting its replacement.
-- DELETE triggers caused by REPLACE depend on connection-local recursive_triggers,
-- so every durable identity also rejects duplicate INSERT directly.
CREATE TRIGGER m2_worker_registry_no_duplicate_insert
BEFORE INSERT ON m2_worker_registry
WHEN EXISTS (
    SELECT 1 FROM m2_worker_registry
    WHERE registry_key = NEW.registry_key
)
BEGIN SELECT RAISE(ABORT, 'M2 worker registry identity already exists'); END;

CREATE TRIGGER m2_worker_identities_no_duplicate_insert
BEFORE INSERT ON m2_worker_identities
WHEN EXISTS (
    SELECT 1 FROM m2_worker_identities
    WHERE worker_id = NEW.worker_id
)
BEGIN SELECT RAISE(ABORT, 'M2 worker identity already exists'); END;

CREATE TRIGGER m2_job_execution_roots_no_duplicate_insert
BEFORE INSERT ON m2_job_execution_roots
WHEN EXISTS (
    SELECT 1 FROM m2_job_execution_roots
    WHERE job_id = NEW.job_id
)
BEGIN SELECT RAISE(ABORT, 'M2 job execution root identity already exists'); END;

CREATE TRIGGER m2_task_routes_no_duplicate_insert
BEFORE INSERT ON m2_task_routes
WHEN EXISTS (
    SELECT 1 FROM m2_task_routes
    WHERE task_id = NEW.task_id
)
BEGIN SELECT RAISE(ABORT, 'M2 task route identity already exists'); END;

CREATE TRIGGER m2_plan_day_roots_no_duplicate_insert
BEFORE INSERT ON m2_plan_day_roots
WHEN EXISTS (
    SELECT 1 FROM m2_plan_day_roots
    WHERE plan_day_id = NEW.plan_day_id
)
BEGIN SELECT RAISE(ABORT, 'M2 plan day root identity already exists'); END;

CREATE TRIGGER m2_assignment_routes_no_duplicate_insert
BEFORE INSERT ON m2_assignment_routes
WHEN EXISTS (
    SELECT 1 FROM m2_assignment_routes
    WHERE assignment_id = NEW.assignment_id
)
BEGIN SELECT RAISE(ABORT, 'M2 assignment route identity already exists'); END;

CREATE TRIGGER m2_assignment_members_no_duplicate_insert
BEFORE INSERT ON m2_assignment_members
WHEN EXISTS (
    SELECT 1 FROM m2_assignment_members
    WHERE assignment_id = NEW.assignment_id AND worker_id = NEW.worker_id
)
BEGIN SELECT RAISE(ABORT, 'M2 assignment membership identity already exists'); END;

CREATE TRIGGER m2_assignment_plan_days_no_duplicate_insert
BEFORE INSERT ON m2_assignment_plan_days
WHEN EXISTS (
    SELECT 1 FROM m2_assignment_plan_days
    WHERE assignment_id = NEW.assignment_id
      AND plan_day_id = NEW.plan_day_id
)
BEGIN SELECT RAISE(ABORT, 'M2 assignment plan identity already exists'); END;

CREATE TRIGGER m2_directive_roots_no_duplicate_insert
BEFORE INSERT ON m2_directive_roots
WHEN EXISTS (
    SELECT 1 FROM m2_directive_roots
    WHERE directive_id = NEW.directive_id
       OR (
           worker_id = NEW.worker_id
           AND stream_kind = NEW.stream_kind
           AND stream_id = NEW.stream_id
           AND issuance_sequence = NEW.issuance_sequence
       )
)
BEGIN SELECT RAISE(ABORT, 'M2 directive identity or stream sequence already exists'); END;

CREATE TRIGGER m2_input_inbox_no_duplicate_insert
BEFORE INSERT ON m2_input_inbox
WHEN EXISTS (
    SELECT 1 FROM m2_input_inbox
    WHERE (input_namespace = NEW.input_namespace AND input_id = NEW.input_id)
       OR (
           NEW.claimed_server_event_id IS NOT NULL
           AND claimed_server_event_id = NEW.claimed_server_event_id
       )
)
BEGIN SELECT RAISE(ABORT, 'M2 inbox or server-event identity already exists'); END;

CREATE TRIGGER m2_input_conflicts_no_duplicate_insert
BEFORE INSERT ON m2_input_conflicts
WHEN EXISTS (
    SELECT 1 FROM m2_input_conflicts
    WHERE (NEW.conflict_id IS NOT NULL AND conflict_id = NEW.conflict_id)
       OR (
           input_namespace = NEW.input_namespace
           AND input_id = NEW.input_id
           AND conflict_kind = NEW.conflict_kind
           AND attempted_payload_sha256 = NEW.attempted_payload_sha256
           AND owner_input_id = NEW.owner_input_id
       )
)
BEGIN SELECT RAISE(ABORT, 'M2 input conflict identity already exists'); END;

CREATE TRIGGER m2_evidence_identities_no_duplicate_insert
BEFORE INSERT ON m2_evidence_identities
WHEN EXISTS (
    SELECT 1 FROM m2_evidence_identities
    WHERE evidence_id = NEW.evidence_id
)
BEGIN SELECT RAISE(ABORT, 'M2 evidence identity already exists'); END;

CREATE TRIGGER m2_evidence_usages_no_duplicate_insert
BEFORE INSERT ON m2_evidence_usages
WHEN EXISTS (
    SELECT 1 FROM m2_evidence_usages
    WHERE input_namespace = NEW.input_namespace
      AND input_id = NEW.input_id
      AND evidence_id = NEW.evidence_id
)
BEGIN SELECT RAISE(ABORT, 'M2 evidence usage identity already exists'); END;

CREATE TRIGGER m2_effect_outbox_no_duplicate_insert
BEFORE INSERT ON m2_effect_outbox
WHEN EXISTS (
    SELECT 1 FROM m2_effect_outbox
    WHERE input_namespace = NEW.input_namespace
      AND input_id = NEW.input_id
      AND effect_ordinal = NEW.effect_ordinal
)
BEGIN SELECT RAISE(ABORT, 'M2 outbox effect identity already exists'); END;

CREATE TRIGGER m2_worker_registry_no_delete BEFORE DELETE ON m2_worker_registry
BEGIN SELECT RAISE(ABORT, 'M2 worker registry cannot be deleted'); END;
CREATE TRIGGER m2_worker_registry_revision_guard BEFORE UPDATE ON m2_worker_registry
WHEN NEW.registry_key <> OLD.registry_key
  OR NEW.root_schema_version <> OLD.root_schema_version
  OR NEW.registry_revision <> OLD.registry_revision + 1
BEGIN SELECT RAISE(ABORT, 'M2 worker registry update must advance one revision'); END;

CREATE TRIGGER m2_job_execution_roots_no_delete BEFORE DELETE ON m2_job_execution_roots
BEGIN SELECT RAISE(ABORT, 'M2 job execution roots cannot be deleted'); END;
CREATE TRIGGER m2_job_execution_roots_revision_guard BEFORE UPDATE ON m2_job_execution_roots
WHEN NEW.job_id <> OLD.job_id
  OR NEW.root_schema_version <> OLD.root_schema_version
  OR NEW.job_execution_revision <> OLD.job_execution_revision + 1
BEGIN SELECT RAISE(ABORT, 'M2 job root update must advance one revision'); END;

CREATE TRIGGER m2_plan_day_roots_no_delete BEFORE DELETE ON m2_plan_day_roots
BEGIN SELECT RAISE(ABORT, 'M2 plan day roots cannot be deleted'); END;
CREATE TRIGGER m2_plan_day_roots_revision_guard BEFORE UPDATE ON m2_plan_day_roots
WHEN NEW.plan_day_id <> OLD.plan_day_id
  OR NEW.root_schema_version <> OLD.root_schema_version
  OR NEW.worker_id <> OLD.worker_id
  OR NEW.business_date <> OLD.business_date
  OR json_extract(NEW.canonical_root_json, '$.payload.start_at')
     IS NOT json_extract(OLD.canonical_root_json, '$.payload.start_at')
  OR NEW.plan_day_revision <> OLD.plan_day_revision + 1
BEGIN SELECT RAISE(ABORT, 'M2 plan root update must advance one revision'); END;

CREATE TRIGGER m2_directive_roots_no_delete BEFORE DELETE ON m2_directive_roots
BEGIN SELECT RAISE(ABORT, 'M2 directive roots cannot be deleted'); END;
CREATE TRIGGER m2_directive_roots_revision_guard BEFORE UPDATE ON m2_directive_roots
WHEN NEW.directive_id <> OLD.directive_id
  OR NEW.root_schema_version <> OLD.root_schema_version
  OR NEW.worker_id IS NOT OLD.worker_id
  OR NEW.job_id IS NOT OLD.job_id
  OR NEW.task_id IS NOT OLD.task_id
  OR NEW.assignment_id IS NOT OLD.assignment_id
  OR NEW.plan_day_id IS NOT OLD.plan_day_id
  OR NEW.stream_kind IS NOT OLD.stream_kind
  OR NEW.stream_id IS NOT OLD.stream_id
  OR NEW.issuance_sequence IS NOT OLD.issuance_sequence
  OR NEW.supersedes_directive_id IS NOT OLD.supersedes_directive_id
  OR NEW.directive_type IS NOT OLD.directive_type
  OR NEW.directive_class IS NOT OLD.directive_class
  OR NEW.issued_at IS NOT OLD.issued_at
  OR NEW.escalation_due_at IS NOT OLD.escalation_due_at
  OR NEW.proposed_plan_reference IS NOT OLD.proposed_plan_reference
  OR NEW.directive_revision <> OLD.directive_revision + 1
BEGIN SELECT RAISE(ABORT, 'M2 directive definition is immutable and revision must advance once'); END;

CREATE TRIGGER m2_task_routes_validate_supersession BEFORE INSERT ON m2_task_routes
WHEN NEW.supersedes_task_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM m2_task_routes AS prior
        WHERE prior.task_id = NEW.supersedes_task_id
          AND prior.job_id = NEW.job_id
          AND prior.source_revision <= NEW.source_revision
    ) THEN RAISE(ABORT, 'task supersession must reference same-job history') END;
END;

CREATE TRIGGER m2_assignment_routes_validate BEFORE INSERT ON m2_assignment_routes
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM m2_task_routes AS task
        WHERE task.task_id = NEW.task_id
          AND task.job_id = NEW.job_id
          AND task.definition_version = NEW.definition_version
    ) THEN RAISE(ABORT, 'assignment route must match its exact task route') END;
    SELECT CASE WHEN NEW.supersedes_assignment_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM m2_assignment_routes AS prior
        JOIN m2_task_routes AS prior_task ON prior_task.task_id = prior.task_id
        JOIN m2_task_routes AS current_task ON current_task.task_id = NEW.task_id
        WHERE prior.assignment_id = NEW.supersedes_assignment_id
          AND prior.job_id = NEW.job_id
          AND (
              prior.task_id = NEW.task_id
              OR current_task.supersedes_task_id = prior.task_id
          )
    ) THEN RAISE(ABORT, 'assignment supersession must reference compatible same-job history') END;
END;

CREATE TRIGGER m2_assignment_members_no_update BEFORE UPDATE ON m2_assignment_members
BEGIN SELECT RAISE(ABORT, 'assignment membership is append-only'); END;
CREATE TRIGGER m2_assignment_members_no_delete BEFORE DELETE ON m2_assignment_members
BEGIN SELECT RAISE(ABORT, 'assignment membership is append-only'); END;
CREATE TRIGGER m2_assignment_plan_days_no_update BEFORE UPDATE ON m2_assignment_plan_days
BEGIN SELECT RAISE(ABORT, 'assignment plan links are append-only'); END;
CREATE TRIGGER m2_assignment_plan_days_no_delete BEFORE DELETE ON m2_assignment_plan_days
BEGIN SELECT RAISE(ABORT, 'assignment plan links are append-only'); END;
CREATE TRIGGER m2_task_routes_no_update BEFORE UPDATE ON m2_task_routes
BEGIN SELECT RAISE(ABORT, 'task routes are append-only'); END;
CREATE TRIGGER m2_task_routes_no_delete BEFORE DELETE ON m2_task_routes
BEGIN SELECT RAISE(ABORT, 'task routes are append-only'); END;
CREATE TRIGGER m2_assignment_routes_no_update BEFORE UPDATE ON m2_assignment_routes
BEGIN SELECT RAISE(ABORT, 'assignment routes are append-only'); END;
CREATE TRIGGER m2_assignment_routes_no_delete BEFORE DELETE ON m2_assignment_routes
BEGIN SELECT RAISE(ABORT, 'assignment routes are append-only'); END;
CREATE TRIGGER m2_worker_identities_no_update BEFORE UPDATE ON m2_worker_identities
BEGIN SELECT RAISE(ABORT, 'worker identities are append-only'); END;
CREATE TRIGGER m2_worker_identities_no_delete BEFORE DELETE ON m2_worker_identities
BEGIN SELECT RAISE(ABORT, 'worker identities are append-only'); END;

CREATE TRIGGER m2_directive_roots_validate_supersession BEFORE INSERT ON m2_directive_roots
WHEN NEW.supersedes_directive_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM m2_directive_roots AS prior
        WHERE prior.directive_id = NEW.supersedes_directive_id
          AND prior.worker_id = NEW.worker_id
          AND prior.stream_kind = NEW.stream_kind
          AND prior.stream_id = NEW.stream_id
          AND prior.issuance_sequence < NEW.issuance_sequence
    ) THEN RAISE(ABORT, 'directive supersession must reference earlier compatible stream') END;
END;

CREATE TRIGGER m2_input_inbox_immutable BEFORE UPDATE ON m2_input_inbox
WHEN NEW.input_namespace IS NOT OLD.input_namespace
  OR NEW.input_id IS NOT OLD.input_id
  OR NEW.input_schema_version IS NOT OLD.input_schema_version
  OR NEW.canonical_input_json IS NOT OLD.canonical_input_json
  OR NEW.payload_sha256 IS NOT OLD.payload_sha256
  OR NEW.presented_server_event_id IS NOT OLD.presented_server_event_id
  OR NEW.claimed_server_event_id IS NOT OLD.claimed_server_event_id
  OR NEW.server_claim_owner_input_id IS NOT OLD.server_claim_owner_input_id
  OR NEW.raw_job_id IS NOT OLD.raw_job_id
  OR NEW.raw_task_id IS NOT OLD.raw_task_id
  OR NEW.raw_assignment_id IS NOT OLD.raw_assignment_id
  OR NEW.raw_plan_day_id IS NOT OLD.raw_plan_day_id
  OR NEW.raw_directive_id IS NOT OLD.raw_directive_id
  OR NEW.raw_against_event_id IS NOT OLD.raw_against_event_id
  OR NEW.policy_context_json IS NOT OLD.policy_context_json
  OR NEW.policy_context_sha256 IS NOT OLD.policy_context_sha256
  OR NEW.rule_version IS NOT OLD.rule_version
  OR NEW.first_received_at IS NOT OLD.first_received_at
  OR OLD.processing_status <> 'RECEIVED'
  OR NEW.processing_status <> 'COMPLETED'
BEGIN SELECT RAISE(ABORT, 'M2 inbox input is immutable or transition is invalid'); END;
CREATE TRIGGER m2_input_inbox_no_delete BEFORE DELETE ON m2_input_inbox
BEGIN SELECT RAISE(ABORT, 'M2 inbox is append-only'); END;

CREATE TRIGGER m2_input_conflicts_no_update BEFORE UPDATE ON m2_input_conflicts
BEGIN SELECT RAISE(ABORT, 'M2 input conflicts are immutable'); END;
CREATE TRIGGER m2_input_conflicts_no_delete BEFORE DELETE ON m2_input_conflicts
BEGIN SELECT RAISE(ABORT, 'M2 input conflicts are append-only'); END;
CREATE TRIGGER m2_evidence_identities_no_update BEFORE UPDATE ON m2_evidence_identities
BEGIN SELECT RAISE(ABORT, 'M2 evidence identities are immutable'); END;
CREATE TRIGGER m2_evidence_identities_no_delete BEFORE DELETE ON m2_evidence_identities
BEGIN SELECT RAISE(ABORT, 'M2 evidence identities are append-only'); END;
CREATE TRIGGER m2_evidence_identities_scope_guard
BEFORE INSERT ON m2_evidence_identities
BEGIN
    SELECT CASE WHEN NEW.owner_task_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM m2_task_routes AS task
        WHERE task.task_id = NEW.owner_task_id
          AND task.job_id = NEW.owner_job_id
    ) THEN RAISE(ABORT, 'evidence task owner must match its job') END;
    SELECT CASE WHEN NEW.owner_assignment_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM m2_assignment_routes AS assignment
        WHERE assignment.assignment_id = NEW.owner_assignment_id
          AND assignment.job_id = NEW.owner_job_id
          AND assignment.task_id = NEW.owner_task_id
    ) THEN RAISE(ABORT, 'evidence assignment owner must match its task') END;
END;
CREATE TRIGGER m2_evidence_usages_no_update BEFORE UPDATE ON m2_evidence_usages
BEGIN SELECT RAISE(ABORT, 'M2 evidence usages are immutable'); END;
CREATE TRIGGER m2_evidence_usages_no_delete BEFORE DELETE ON m2_evidence_usages
BEGIN SELECT RAISE(ABORT, 'M2 evidence usages are append-only'); END;

CREATE TRIGGER m2_effect_outbox_update_guard BEFORE UPDATE ON m2_effect_outbox
WHEN NEW.input_namespace IS NOT OLD.input_namespace
  OR NEW.input_id IS NOT OLD.input_id
  OR NEW.effect_ordinal IS NOT OLD.effect_ordinal
  OR NEW.effect_type IS NOT OLD.effect_type
  OR NEW.canonical_effect_json IS NOT OLD.canonical_effect_json
  OR NEW.effect_sha256 IS NOT OLD.effect_sha256
  OR NEW.recorded_at IS NOT OLD.recorded_at
  OR NOT (
      (OLD.dispatch_status = 'RECORDED' AND NEW.dispatch_status IN ('DISPATCHING', 'FAILED'))
      OR (OLD.dispatch_status = 'DISPATCHING' AND NEW.dispatch_status IN ('DISPATCHED', 'FAILED'))
      OR (OLD.dispatch_status = 'FAILED' AND NEW.dispatch_status = 'DISPATCHING')
  )
  OR (NEW.dispatch_status <> 'DISPATCHED' AND NEW.dispatched_at IS NOT NULL)
  OR (NEW.dispatch_status = 'DISPATCHED' AND NEW.dispatched_at IS NULL)
BEGIN SELECT RAISE(ABORT, 'M2 outbox payload is immutable'); END;
CREATE TRIGGER m2_effect_outbox_no_delete BEFORE DELETE ON m2_effect_outbox
BEGIN SELECT RAISE(ABORT, 'M2 effect outbox is append-only'); END;
