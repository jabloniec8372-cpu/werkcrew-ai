PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    address_status TEXT NOT NULL CHECK (address_status IN ('CONFIRMED', 'UNCONFIRMED')),
    address_json TEXT NOT NULL,
    latitude TEXT,
    longitude TEXT,
    coordinate_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_instances (
    workflow_instance_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
    state TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_workflow_job ON workflow_instances(job_id);

CREATE TABLE IF NOT EXISTS session_bindings (
    workflow_instance_id TEXT PRIMARY KEY REFERENCES workflow_instances(workflow_instance_id),
    session_id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_workers (
    worker_id TEXT PRIMARY KEY,
    skill_ids_json TEXT NOT NULL,
    availability_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_vehicles (
    vehicle_id TEXT PRIMARY KEY,
    availability_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_assignments (
    assignment_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    workflow_instance_id TEXT NOT NULL REFERENCES workflow_instances(workflow_instance_id),
    scheduled_task_id TEXT NOT NULL,
    work_item_name TEXT NOT NULL,
    worker_id TEXT NOT NULL REFERENCES dispatch_workers(worker_id),
    required_skill_ids_json TEXT NOT NULL,
    vehicle_id TEXT REFERENCES dispatch_vehicles(vehicle_id),
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    hard_deadline TEXT,
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, scheduled_task_id)
);
CREATE INDEX IF NOT EXISTS ix_calendar_worker_time ON calendar_assignments(worker_id, start_at, end_at);

CREATE TABLE IF NOT EXISTS material_readiness (
    job_id TEXT NOT NULL,
    scheduled_task_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('READY', 'EXPECTED', 'BLOCKED')),
    available_at TEXT,
    blocking INTEGER NOT NULL CHECK (blocking IN (0, 1)),
    source TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(job_id, scheduled_task_id),
    FOREIGN KEY(job_id, scheduled_task_id)
      REFERENCES calendar_assignments(job_id, scheduled_task_id)
);

CREATE TABLE IF NOT EXISTS route_snapshots (
    route_snapshot_id TEXT PRIMARY KEY,
    origin_reference TEXT NOT NULL,
    origin_fingerprint TEXT NOT NULL,
    destination_reference TEXT NOT NULL,
    destination_fingerprint TEXT NOT NULL,
    transport_mode TEXT NOT NULL,
    departure_time_basis TEXT NOT NULL,
    distance_meters INTEGER NOT NULL CHECK (distance_meters >= 0),
    travel_duration_seconds INTEGER NOT NULL CHECK (travel_duration_seconds >= 0),
    provider TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('VALID', 'FAILED')),
    input_fingerprint TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS replan_proposals (
    proposal_id TEXT PRIMARY KEY,
    initiating_workflow_instance_id TEXT NOT NULL REFERENCES workflow_instances(workflow_instance_id),
    status TEXT NOT NULL CHECK (status IN ('PENDING_OWNER_APPROVAL', 'APPROVED', 'REJECTED', 'STALE', 'APPLIED')),
    content_json TEXT NOT NULL,
    proposal_fingerprint TEXT NOT NULL UNIQUE,
    expected_workflow_revisions_json TEXT NOT NULL,
    expected_calendar_revisions_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS replan_proposal_jobs (
    proposal_id TEXT NOT NULL REFERENCES replan_proposals(proposal_id),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    workflow_instance_id TEXT NOT NULL REFERENCES workflow_instances(workflow_instance_id),
    PRIMARY KEY(proposal_id, job_id)
);

CREATE TABLE IF NOT EXISTS replan_proposal_tasks (
    proposal_id TEXT NOT NULL REFERENCES replan_proposals(proposal_id),
    assignment_id TEXT NOT NULL REFERENCES calendar_assignments(assignment_id),
    PRIMARY KEY(proposal_id, assignment_id)
);

CREATE TABLE IF NOT EXISTS pending_gates (
    gate_id TEXT PRIMARY KEY,
    workflow_instance_id TEXT NOT NULL REFERENCES workflow_instances(workflow_instance_id),
    gate_type TEXT NOT NULL CHECK (gate_type IN ('PLAN_OWNER_REVIEW', 'REPLAN_OWNER_REVIEW')),
    proposal_id TEXT REFERENCES replan_proposals(proposal_id),
    gate_fingerprint TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    interrupt_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RESUMING', 'RESOLVED', 'INVALIDATED')),
    response_fingerprint TEXT,
    decision_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(proposal_id)
);

CREATE TABLE IF NOT EXISTS owner_decisions (
    decision_id TEXT PRIMARY KEY,
    gate_id TEXT NOT NULL UNIQUE REFERENCES pending_gates(gate_id),
    workflow_instance_id TEXT NOT NULL REFERENCES workflow_instances(workflow_instance_id),
    proposal_id TEXT REFERENCES replan_proposals(proposal_id),
    action TEXT NOT NULL,
    selected_plan_id TEXT,
    decision_fingerprint TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_records (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_trace_events (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    workflow_instance_id TEXT NOT NULL REFERENCES workflow_instances(workflow_instance_id),
    actor TEXT NOT NULL CHECK (actor IN ('AGENT', 'HUMAN', 'SYSTEM', 'TOOL')),
    action TEXT NOT NULL,
    redacted_input_summary TEXT NOT NULL,
    result_summary TEXT NOT NULL,
    previous_state TEXT,
    next_state TEXT,
    reason_code TEXT,
    rule_version TEXT,
    external_data_source TEXT,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    gate_id TEXT,
    owner_decision_id TEXT,
    proposal_id TEXT,
    operation_id TEXT,
    correlation_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trace_job ON agent_trace_events(job_id, timestamp, event_id);
CREATE INDEX IF NOT EXISTS ix_trace_correlation ON agent_trace_events(correlation_id);
