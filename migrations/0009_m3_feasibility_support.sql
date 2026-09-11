-- M3-C0 authoritative feasibility evidence only. No candidate or plan mutation.
CREATE TABLE m3_feasibility_m8_configurations (
    m8_configuration_fingerprint TEXT NOT NULL PRIMARY KEY CHECK (
        length(m8_configuration_fingerprint) = 64
        AND m8_configuration_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'm8-feasibility-configuration-v1'
    ),
    service_catalog_version TEXT NOT NULL,
    planning_profile_version TEXT NOT NULL,
    skill_matrix_version TEXT NOT NULL,
    vehicle_policy_version TEXT NOT NULL,
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS m8_configuration_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version') IS schema_version
        AND json_extract(canonical_semantic_json, '$.service_catalog_version')
            IS service_catalog_version
        AND json_extract(canonical_semantic_json, '$.planning_profile_version')
            IS planning_profile_version
        AND json_extract(canonical_semantic_json, '$.skill_matrix_version')
            IS skill_matrix_version
        AND json_extract(canonical_semantic_json, '$.vehicle_policy_version')
            IS vehicle_policy_version
    )
) WITHOUT ROWID;

CREATE TRIGGER m3_feasibility_m8_configurations_no_update
BEFORE UPDATE ON m3_feasibility_m8_configurations
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility M8 provenance is immutable');
END;

CREATE TRIGGER m3_feasibility_m8_configurations_no_delete
BEFORE DELETE ON m3_feasibility_m8_configurations
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility M8 provenance is append-only');
END;

CREATE TRIGGER m3_feasibility_m8_configurations_no_replace
BEFORE INSERT ON m3_feasibility_m8_configurations
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_m8_configurations
    WHERE m8_configuration_fingerprint = NEW.m8_configuration_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility M8 provenance cannot be replaced');
END;

CREATE TABLE m3_feasibility_worker_registry_provenance (
    worker_registry_provenance_id TEXT NOT NULL PRIMARY KEY CHECK (
        worker_registry_provenance_id IS
            'm3-feasibility-worker-registry-' || worker_registry_provenance_fingerprint
    ),
    worker_registry_provenance_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(worker_registry_provenance_fingerprint) = 64
        AND worker_registry_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'm3-feasibility-worker-registry-provenance-v1'
    ),
    worker_registry_schema_version TEXT NOT NULL CHECK (
        worker_registry_schema_version = 'm2-worker-registry-v1'
    ),
    worker_registry_revision INTEGER NOT NULL UNIQUE CHECK (
        typeof(worker_registry_revision) = 'integer'
        AND worker_registry_revision >= 0
    ),
    worker_registry_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(worker_registry_fingerprint) = 64
        AND worker_registry_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_worker_registry_json TEXT NOT NULL CHECK (
        json_valid(canonical_worker_registry_json) = 1
        AND werkcrew_canonical_json(canonical_worker_registry_json)
            IS canonical_worker_registry_json
        AND werkcrew_sha256(canonical_worker_registry_json)
            IS worker_registry_fingerprint
        AND json_extract(canonical_worker_registry_json, '$.document_type')
            IS 'WorkerIdentityRegistry'
        AND json_extract(canonical_worker_registry_json, '$.schema_version')
            IS worker_registry_schema_version
        AND json_extract(
                canonical_worker_registry_json, '$.payload.registry_revision'
            ) IS worker_registry_revision
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS worker_registry_provenance_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_schema_version'
            ) IS worker_registry_schema_version
        AND json_extract(canonical_semantic_json, '$.worker_registry_revision')
            IS worker_registry_revision
        AND json_extract(canonical_semantic_json, '$.worker_registry_fingerprint')
            IS worker_registry_fingerprint
        AND json_extract(
                canonical_semantic_json, '$.canonical_worker_registry_json'
            ) IS canonical_worker_registry_json
    )
) WITHOUT ROWID;

CREATE TRIGGER m3_feasibility_worker_registry_provenance_current_authority
BEFORE INSERT ON m3_feasibility_worker_registry_provenance
WHEN NOT EXISTS (
    SELECT 1 FROM m2_worker_registry
    WHERE registry_key = 'GLOBAL'
      AND root_schema_version = NEW.worker_registry_schema_version
      AND registry_revision = NEW.worker_registry_revision
      AND content_sha256 = NEW.worker_registry_fingerprint
      AND canonical_root_json = NEW.canonical_worker_registry_json
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility worker registry provenance must capture current M2 authority');
END;

CREATE TRIGGER m3_feasibility_worker_registry_provenance_no_update
BEFORE UPDATE ON m3_feasibility_worker_registry_provenance
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility worker registry provenance is immutable');
END;

CREATE TRIGGER m3_feasibility_worker_registry_provenance_no_delete
BEFORE DELETE ON m3_feasibility_worker_registry_provenance
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility worker registry provenance is append-only');
END;

CREATE TRIGGER m3_feasibility_worker_registry_provenance_no_replace
BEFORE INSERT ON m3_feasibility_worker_registry_provenance
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_worker_registry_provenance
    WHERE worker_registry_provenance_id = NEW.worker_registry_provenance_id
       OR worker_registry_provenance_fingerprint =
            NEW.worker_registry_provenance_fingerprint
       OR worker_registry_revision = NEW.worker_registry_revision
       OR worker_registry_fingerprint = NEW.worker_registry_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility worker registry provenance cannot be replaced');
END;

CREATE TABLE m3_feasibility_source_records (
    source_record_id TEXT NOT NULL PRIMARY KEY CHECK (
        source_record_id IS 'm3-feasibility-source-' || source_fingerprint
    ),
    source_kind TEXT NOT NULL CHECK (source_kind IN (
        'TASK_CONSTRAINT',
        'PLAN_SCHEDULE',
        'WORKER_AVAILABILITY',
        'TASK_READINESS',
        'VEHICLE_AVAILABILITY',
        'ROUTE'
    )),
    subject_fingerprint TEXT NOT NULL CHECK (
        length(subject_fingerprint) = 64
        AND subject_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    source_revision INTEGER NOT NULL CHECK (
        typeof(source_revision) = 'integer' AND source_revision >= 0
    ),
    previous_source_record_id TEXT REFERENCES m3_feasibility_source_records(source_record_id),
    ledger_generation INTEGER NOT NULL UNIQUE CHECK (
        typeof(ledger_generation) = 'integer' AND ledger_generation > 0
    ),
    schema_version TEXT NOT NULL CHECK (schema_version = 'm3-feasibility-source-v1'),
    source_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(source_fingerprint) = 64
        AND source_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS source_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_kind') IS source_kind
        AND json_extract(canonical_semantic_json, '$.subject_fingerprint') IS subject_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_revision') IS source_revision
        AND json_extract(canonical_semantic_json, '$.previous_source_record_id') IS previous_source_record_id
        AND json_extract(canonical_semantic_json, '$.schema_version') IS schema_version
    ),
    UNIQUE(source_kind, subject_fingerprint, source_revision),
    CHECK (
        (source_revision = 0 AND previous_source_record_id IS NULL)
        OR (source_revision > 0 AND previous_source_record_id IS NOT NULL)
    )
) WITHOUT ROWID;

CREATE INDEX ix_m3_feasibility_source_head
ON m3_feasibility_source_records(source_kind, subject_fingerprint, source_revision);

CREATE TABLE m3_feasibility_worker_registry_captures (
    worker_registry_capture_id TEXT NOT NULL PRIMARY KEY CHECK (
        worker_registry_capture_id IS
            'm3-feasibility-worker-registry-capture-'
            || worker_registry_capture_fingerprint
    ),
    worker_registry_capture_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(worker_registry_capture_fingerprint) = 64
        AND worker_registry_capture_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    ledger_generation INTEGER NOT NULL UNIQUE CHECK (
        typeof(ledger_generation) = 'integer' AND ledger_generation > 0
    ),
    capture_kind TEXT NOT NULL CHECK (
        capture_kind = 'WORKER_REGISTRY_PROVENANCE'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'm3-feasibility-worker-registry-capture-v1'
    ),
    worker_registry_provenance_id TEXT NOT NULL UNIQUE
        REFERENCES m3_feasibility_worker_registry_provenance(
            worker_registry_provenance_id
        ),
    worker_registry_provenance_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_provenance_fingerprint) = 64
        AND worker_registry_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND worker_registry_provenance_id IS
            'm3-feasibility-worker-registry-'
            || worker_registry_provenance_fingerprint
    ),
    worker_registry_revision INTEGER NOT NULL CHECK (
        typeof(worker_registry_revision) = 'integer'
        AND worker_registry_revision >= 0
    ),
    worker_registry_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_fingerprint) = 64
        AND worker_registry_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS worker_registry_capture_fingerprint
        AND json_extract(canonical_semantic_json, '$.ledger_generation')
            IS ledger_generation
        AND json_extract(canonical_semantic_json, '$.capture_kind')
            IS capture_kind
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_provenance_id'
            ) IS worker_registry_provenance_id
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_provenance_fingerprint'
            ) IS worker_registry_provenance_fingerprint
        AND json_extract(canonical_semantic_json, '$.worker_registry_revision')
            IS worker_registry_revision
        AND json_extract(canonical_semantic_json, '$.worker_registry_fingerprint')
            IS worker_registry_fingerprint
    )
) WITHOUT ROWID;

CREATE TRIGGER m3_feasibility_worker_registry_captures_append
BEFORE INSERT ON m3_feasibility_worker_registry_captures
WHEN NEW.ledger_generation <> COALESCE((
        SELECT max(ledger_generation) + 1
        FROM (
            SELECT ledger_generation FROM m3_feasibility_source_records
            UNION ALL
            SELECT ledger_generation
            FROM m3_feasibility_worker_registry_captures
        )
    ), 1)
    OR NOT EXISTS (
        SELECT 1
        FROM m3_feasibility_worker_registry_provenance AS provenance
        JOIN m2_worker_registry AS current_registry
          ON current_registry.registry_key = 'GLOBAL'
         AND current_registry.root_schema_version =
                provenance.worker_registry_schema_version
         AND current_registry.registry_revision =
                provenance.worker_registry_revision
         AND current_registry.content_sha256 =
                provenance.worker_registry_fingerprint
         AND current_registry.canonical_root_json =
                provenance.canonical_worker_registry_json
        WHERE provenance.worker_registry_provenance_id =
                NEW.worker_registry_provenance_id
          AND provenance.worker_registry_provenance_fingerprint =
                NEW.worker_registry_provenance_fingerprint
          AND provenance.worker_registry_revision =
                NEW.worker_registry_revision
          AND provenance.worker_registry_fingerprint =
                NEW.worker_registry_fingerprint
    )
BEGIN
    SELECT RAISE(ABORT, 'M3 worker registry capture must append current authority to the feasibility ledger');
END;

CREATE TRIGGER m3_feasibility_worker_registry_captures_no_update
BEFORE UPDATE ON m3_feasibility_worker_registry_captures
BEGIN
    SELECT RAISE(ABORT, 'M3 worker registry capture is immutable');
END;

CREATE TRIGGER m3_feasibility_worker_registry_captures_no_delete
BEFORE DELETE ON m3_feasibility_worker_registry_captures
BEGIN
    SELECT RAISE(ABORT, 'M3 worker registry capture is append-only');
END;

CREATE TRIGGER m3_feasibility_worker_registry_captures_no_replace
BEFORE INSERT ON m3_feasibility_worker_registry_captures
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_worker_registry_captures
    WHERE worker_registry_capture_id = NEW.worker_registry_capture_id
       OR worker_registry_capture_fingerprint =
            NEW.worker_registry_capture_fingerprint
       OR ledger_generation = NEW.ledger_generation
       OR worker_registry_provenance_id = NEW.worker_registry_provenance_id
)
BEGIN
    SELECT RAISE(ABORT, 'M3 worker registry capture cannot be replaced');
END;

CREATE TRIGGER m3_feasibility_sources_append
BEFORE INSERT ON m3_feasibility_source_records
WHEN NEW.ledger_generation <> COALESCE((
        SELECT max(ledger_generation) + 1
        FROM (
            SELECT ledger_generation FROM m3_feasibility_source_records
            UNION ALL
            SELECT ledger_generation
            FROM m3_feasibility_worker_registry_captures
        )
    ), 1)
    OR NEW.source_revision <> COALESCE((
        SELECT max(source_revision) + 1
        FROM m3_feasibility_source_records
        WHERE source_kind = NEW.source_kind
          AND subject_fingerprint = NEW.subject_fingerprint
    ), 0)
    OR (NEW.source_revision > 0 AND NOT EXISTS (
        SELECT 1
        FROM m3_feasibility_source_records
        WHERE source_record_id = NEW.previous_source_record_id
          AND source_kind = NEW.source_kind
          AND subject_fingerprint = NEW.subject_fingerprint
          AND source_revision = NEW.source_revision - 1
    ))
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source must append to its exact subject parent');
END;

CREATE TRIGGER m3_feasibility_task_binding_immutable
BEFORE INSERT ON m3_feasibility_source_records
WHEN NEW.source_kind = 'TASK_CONSTRAINT'
  AND EXISTS (
      SELECT 1
      FROM m3_feasibility_source_records AS prior
      WHERE prior.source_kind = NEW.source_kind
        AND prior.subject_fingerprint = NEW.subject_fingerprint
        AND json_extract(prior.canonical_semantic_json, '$.m8_sku')
            IS NOT json_extract(NEW.canonical_semantic_json, '$.m8_sku')
  )
BEGIN
    SELECT RAISE(ABORT, 'Canonical task M8 binding is immutable');
END;

CREATE TRIGGER m3_feasibility_schedule_exact_revision
BEFORE INSERT ON m3_feasibility_source_records
WHEN NEW.source_kind = 'PLAN_SCHEDULE'
  AND NOT EXISTS (
      SELECT 1
      FROM m3_plan_revisions
      WHERE company_plan_id = json_extract(
              NEW.canonical_semantic_json, '$.company_plan_id'
          )
        AND revision = json_extract(
              NEW.canonical_semantic_json, '$.base_plan_revision'
          )
        AND revision_id = json_extract(
              NEW.canonical_semantic_json, '$.base_plan_revision_id'
          )
        AND content_sha256 = json_extract(
              NEW.canonical_semantic_json, '$.base_plan_revision_fingerprint'
          )
  )
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility schedule must bind one exact PlanRevision');
END;

CREATE TRIGGER m3_feasibility_sources_no_update
BEFORE UPDATE ON m3_feasibility_source_records
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source evidence is immutable');
END;

CREATE TRIGGER m3_feasibility_sources_no_delete
BEFORE DELETE ON m3_feasibility_source_records
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source evidence is append-only');
END;

CREATE TRIGGER m3_feasibility_sources_no_replace
BEFORE INSERT ON m3_feasibility_source_records
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_source_records
    WHERE source_record_id = NEW.source_record_id
       OR source_fingerprint = NEW.source_fingerprint
       OR (
           source_kind = NEW.source_kind
           AND subject_fingerprint = NEW.subject_fingerprint
           AND source_revision = NEW.source_revision
       )
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source evidence cannot be replaced');
END;

CREATE TABLE m3_feasibility_source_selection_cuts (
    source_cut_id TEXT NOT NULL PRIMARY KEY CHECK (
        source_cut_id IS 'm3-feasibility-source-cut-' || source_cut_fingerprint
    ),
    source_cut_generation INTEGER NOT NULL CHECK (
        typeof(source_cut_generation) = 'integer' AND source_cut_generation >= 0
    ),
    source_cut_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(source_cut_fingerprint) = 64
        AND source_cut_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version = 'm3-feasibility-source-cut-v1'
    ),
    evaluation_input_id TEXT NOT NULL REFERENCES m3_evaluation_inputs(evaluation_input_id),
    evaluation_input_fingerprint TEXT NOT NULL CHECK (
        length(evaluation_input_fingerprint) = 64
        AND evaluation_input_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    company_plan_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL CHECK (
        typeof(base_plan_revision) = 'integer' AND base_plan_revision >= 0
    ),
    base_plan_revision_id TEXT NOT NULL REFERENCES m3_plan_revisions(revision_id),
    base_plan_revision_fingerprint TEXT NOT NULL CHECK (
        length(base_plan_revision_fingerprint) = 64
        AND base_plan_revision_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    worker_registry_provenance_id TEXT NOT NULL
        REFERENCES m3_feasibility_worker_registry_provenance(
            worker_registry_provenance_id
        ),
    worker_registry_provenance_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_provenance_fingerprint) = 64
        AND worker_registry_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND worker_registry_provenance_id IS
            'm3-feasibility-worker-registry-' || worker_registry_provenance_fingerprint
    ),
    worker_registry_capture_id TEXT NOT NULL
        REFERENCES m3_feasibility_worker_registry_captures(
            worker_registry_capture_id
        ),
    worker_registry_capture_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_capture_fingerprint) = 64
        AND worker_registry_capture_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND worker_registry_capture_id IS
            'm3-feasibility-worker-registry-capture-'
            || worker_registry_capture_fingerprint
    ),
    worker_registry_capture_generation INTEGER NOT NULL CHECK (
        typeof(worker_registry_capture_generation) = 'integer'
        AND worker_registry_capture_generation > 0
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS source_cut_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_cut_generation')
            IS source_cut_generation
        AND json_extract(canonical_semantic_json, '$.schema_version') IS schema_version
        AND json_extract(canonical_semantic_json, '$.evaluation_input_id')
            IS evaluation_input_id
        AND json_extract(canonical_semantic_json, '$.evaluation_input_fingerprint')
            IS evaluation_input_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_plan_id') IS company_plan_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision')
            IS base_plan_revision
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_id')
            IS base_plan_revision_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_fingerprint')
            IS base_plan_revision_fingerprint
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_provenance_id'
            ) IS worker_registry_provenance_id
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_provenance_fingerprint'
            ) IS worker_registry_provenance_fingerprint
        AND json_extract(canonical_semantic_json, '$.worker_registry_capture_id')
            IS worker_registry_capture_id
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_capture_fingerprint'
            ) IS worker_registry_capture_fingerprint
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_capture_generation'
            ) IS worker_registry_capture_generation
    ),
    FOREIGN KEY (company_plan_id, base_plan_revision)
        REFERENCES m3_plan_revisions(company_plan_id, revision)
) WITHOUT ROWID;

CREATE TRIGGER m3_feasibility_source_cuts_exact_capture
BEFORE INSERT ON m3_feasibility_source_selection_cuts
WHEN NEW.source_cut_generation <> COALESCE((
        SELECT max(ledger_generation)
        FROM (
            SELECT ledger_generation FROM m3_feasibility_source_records
            UNION ALL
            SELECT ledger_generation
            FROM m3_feasibility_worker_registry_captures
        )
    ), 0)
    OR NOT EXISTS (
        SELECT 1 FROM m3_evaluation_inputs
        WHERE evaluation_input_id = NEW.evaluation_input_id
          AND evaluation_input_fingerprint = NEW.evaluation_input_fingerprint
          AND company_plan_id = NEW.company_plan_id
          AND base_plan_revision = NEW.base_plan_revision
          AND base_plan_revision_id = NEW.base_plan_revision_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM m3_plan_revisions
        WHERE company_plan_id = NEW.company_plan_id
          AND revision = NEW.base_plan_revision
          AND revision_id = NEW.base_plan_revision_id
          AND content_sha256 = NEW.base_plan_revision_fingerprint
    )
    OR NOT EXISTS (
        SELECT 1
        FROM m3_feasibility_worker_registry_captures AS capture
        JOIN m3_feasibility_worker_registry_provenance AS provenance
          ON provenance.worker_registry_provenance_id =
                capture.worker_registry_provenance_id
         AND provenance.worker_registry_provenance_fingerprint =
                capture.worker_registry_provenance_fingerprint
         AND provenance.worker_registry_revision =
                capture.worker_registry_revision
         AND provenance.worker_registry_fingerprint =
                capture.worker_registry_fingerprint
        JOIN m2_worker_registry AS current_registry
          ON current_registry.registry_key = 'GLOBAL'
         AND current_registry.root_schema_version =
                provenance.worker_registry_schema_version
         AND current_registry.registry_revision =
                provenance.worker_registry_revision
         AND current_registry.content_sha256 =
                provenance.worker_registry_fingerprint
         AND current_registry.canonical_root_json =
                provenance.canonical_worker_registry_json
        WHERE provenance.worker_registry_provenance_id =
                NEW.worker_registry_provenance_id
          AND provenance.worker_registry_provenance_fingerprint =
                NEW.worker_registry_provenance_fingerprint
          AND capture.worker_registry_capture_id =
                NEW.worker_registry_capture_id
          AND capture.worker_registry_capture_fingerprint =
                NEW.worker_registry_capture_fingerprint
          AND capture.ledger_generation =
                NEW.worker_registry_capture_generation
          AND capture.ledger_generation <= NEW.source_cut_generation
    )
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source cut must capture the current ledger and exact input');
END;

CREATE TRIGGER m3_feasibility_source_cuts_no_update
BEFORE UPDATE ON m3_feasibility_source_selection_cuts
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source cut is immutable');
END;

CREATE TRIGGER m3_feasibility_source_cuts_no_delete
BEFORE DELETE ON m3_feasibility_source_selection_cuts
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source cut is append-only');
END;

CREATE TRIGGER m3_feasibility_source_cuts_no_replace
BEFORE INSERT ON m3_feasibility_source_selection_cuts
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_source_selection_cuts
    WHERE source_cut_id = NEW.source_cut_id
       OR source_cut_fingerprint = NEW.source_cut_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility source cut cannot be replaced');
END;

CREATE TABLE m3_feasibility_support_snapshots (
    support_snapshot_id TEXT NOT NULL PRIMARY KEY CHECK (
        support_snapshot_id IS 'm3-feasibility-support-' || support_fingerprint
    ),
    support_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(support_fingerprint) = 64
        AND support_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (schema_version = 'm3-feasibility-support-v1'),
    rule_version TEXT NOT NULL CHECK (rule_version = 'm3-feasibility-evidence-selection-v1'),
    evaluation_input_id TEXT NOT NULL REFERENCES m3_evaluation_inputs(evaluation_input_id),
    evaluation_input_fingerprint TEXT NOT NULL CHECK (
        length(evaluation_input_fingerprint) = 64
        AND evaluation_input_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    company_plan_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL CHECK (
        typeof(base_plan_revision) = 'integer' AND base_plan_revision >= 0
    ),
    base_plan_revision_id TEXT NOT NULL REFERENCES m3_plan_revisions(revision_id),
    base_plan_revision_fingerprint TEXT NOT NULL CHECK (
        length(base_plan_revision_fingerprint) = 64
        AND base_plan_revision_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    source_cut_id TEXT NOT NULL
        REFERENCES m3_feasibility_source_selection_cuts(source_cut_id),
    source_cut_generation INTEGER NOT NULL CHECK (
        typeof(source_cut_generation) = 'integer' AND source_cut_generation >= 0
    ),
    source_cut_fingerprint TEXT NOT NULL CHECK (
        length(source_cut_fingerprint) = 64
        AND source_cut_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    worker_registry_provenance_id TEXT NOT NULL
        REFERENCES m3_feasibility_worker_registry_provenance(
            worker_registry_provenance_id
        ),
    worker_registry_provenance_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_provenance_fingerprint) = 64
        AND worker_registry_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND worker_registry_provenance_id IS
            'm3-feasibility-worker-registry-' || worker_registry_provenance_fingerprint
    ),
    worker_registry_capture_id TEXT NOT NULL
        REFERENCES m3_feasibility_worker_registry_captures(
            worker_registry_capture_id
        ),
    worker_registry_capture_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_capture_fingerprint) = 64
        AND worker_registry_capture_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND worker_registry_capture_id IS
            'm3-feasibility-worker-registry-capture-'
            || worker_registry_capture_fingerprint
    ),
    worker_registry_capture_generation INTEGER NOT NULL CHECK (
        typeof(worker_registry_capture_generation) = 'integer'
        AND worker_registry_capture_generation > 0
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS support_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version') IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version') IS rule_version
        AND json_extract(canonical_semantic_json, '$.evaluation_input_id') IS evaluation_input_id
        AND json_extract(canonical_semantic_json, '$.evaluation_input_fingerprint')
            IS evaluation_input_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_plan_id') IS company_plan_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision')
            IS base_plan_revision
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_id')
            IS base_plan_revision_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_fingerprint')
            IS base_plan_revision_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_cut_id') IS source_cut_id
        AND json_extract(canonical_semantic_json, '$.source_cut_generation')
            IS source_cut_generation
        AND json_extract(canonical_semantic_json, '$.source_cut_fingerprint')
            IS source_cut_fingerprint
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_provenance_id'
            ) IS worker_registry_provenance_id
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_provenance_fingerprint'
            ) IS worker_registry_provenance_fingerprint
        AND json_extract(canonical_semantic_json, '$.worker_registry_capture_id')
            IS worker_registry_capture_id
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_capture_fingerprint'
            ) IS worker_registry_capture_fingerprint
        AND json_extract(
                canonical_semantic_json, '$.worker_registry_capture_generation'
            ) IS worker_registry_capture_generation
    ),
    FOREIGN KEY (company_plan_id, base_plan_revision)
        REFERENCES m3_plan_revisions(company_plan_id, revision)
) WITHOUT ROWID;

CREATE TRIGGER m3_feasibility_support_exact_input_revision
BEFORE INSERT ON m3_feasibility_support_snapshots
WHEN NOT EXISTS (
        SELECT 1 FROM m3_evaluation_inputs
        WHERE evaluation_input_id = NEW.evaluation_input_id
          AND evaluation_input_fingerprint = NEW.evaluation_input_fingerprint
          AND company_plan_id = NEW.company_plan_id
          AND base_plan_revision = NEW.base_plan_revision
          AND base_plan_revision_id = NEW.base_plan_revision_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM m3_plan_revisions
        WHERE company_plan_id = NEW.company_plan_id
          AND revision = NEW.base_plan_revision
          AND revision_id = NEW.base_plan_revision_id
          AND content_sha256 = NEW.base_plan_revision_fingerprint
    )
    OR NOT EXISTS (
        SELECT 1 FROM m3_feasibility_source_selection_cuts
        WHERE source_cut_id = NEW.source_cut_id
          AND source_cut_generation = NEW.source_cut_generation
          AND source_cut_fingerprint = NEW.source_cut_fingerprint
          AND worker_registry_provenance_id =
                NEW.worker_registry_provenance_id
          AND worker_registry_provenance_fingerprint =
                NEW.worker_registry_provenance_fingerprint
          AND worker_registry_capture_id = NEW.worker_registry_capture_id
          AND worker_registry_capture_fingerprint =
                NEW.worker_registry_capture_fingerprint
          AND worker_registry_capture_generation =
                NEW.worker_registry_capture_generation
          AND evaluation_input_id = NEW.evaluation_input_id
          AND evaluation_input_fingerprint = NEW.evaluation_input_fingerprint
          AND company_plan_id = NEW.company_plan_id
          AND base_plan_revision = NEW.base_plan_revision
          AND base_plan_revision_id = NEW.base_plan_revision_id
          AND base_plan_revision_fingerprint = NEW.base_plan_revision_fingerprint
    )
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support must bind exact EvaluationInput and PlanRevision');
END;

CREATE TRIGGER m3_feasibility_support_no_update
BEFORE UPDATE ON m3_feasibility_support_snapshots
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support snapshot is immutable');
END;

CREATE TRIGGER m3_feasibility_support_no_delete
BEFORE DELETE ON m3_feasibility_support_snapshots
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support snapshot is append-only');
END;

CREATE TRIGGER m3_feasibility_support_no_replace
BEFORE INSERT ON m3_feasibility_support_snapshots
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_support_snapshots
    WHERE support_snapshot_id = NEW.support_snapshot_id
       OR support_fingerprint = NEW.support_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support snapshot cannot be replaced');
END;

CREATE TABLE m3_feasibility_support_source_bindings (
    support_snapshot_id TEXT NOT NULL
        REFERENCES m3_feasibility_support_snapshots(support_snapshot_id),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN (
        'TASK_CONSTRAINT',
        'PLAN_SCHEDULE',
        'WORKER_AVAILABILITY',
        'TASK_READINESS',
        'VEHICLE_AVAILABILITY',
        'ROUTE'
    )),
    subject_fingerprint TEXT NOT NULL CHECK (
        length(subject_fingerprint) = 64
        AND subject_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    source_record_id TEXT REFERENCES m3_feasibility_source_records(source_record_id),
    PRIMARY KEY (support_snapshot_id, evidence_kind, subject_fingerprint)
) WITHOUT ROWID;

CREATE TRIGGER m3_feasibility_support_bindings_no_update
BEFORE UPDATE ON m3_feasibility_support_source_bindings
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support source binding is immutable');
END;

CREATE TRIGGER m3_feasibility_support_bindings_no_delete
BEFORE DELETE ON m3_feasibility_support_source_bindings
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support source binding is append-only');
END;

CREATE TRIGGER m3_feasibility_support_bindings_no_replace
BEFORE INSERT ON m3_feasibility_support_source_bindings
WHEN EXISTS (
    SELECT 1 FROM m3_feasibility_support_source_bindings
    WHERE support_snapshot_id = NEW.support_snapshot_id
      AND evidence_kind = NEW.evidence_kind
      AND subject_fingerprint = NEW.subject_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'M3 feasibility support source binding cannot be replaced');
END;
