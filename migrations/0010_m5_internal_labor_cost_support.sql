-- M5-A / M3-D0 authoritative Gen2 internal scheduled-labor-cost support.
-- No candidate cost arithmetic, customer pricing, ranking, authority, or APPLY.

CREATE TABLE m5_internal_cost_rules (
    rule_id TEXT NOT NULL PRIMARY KEY CHECK (
        rule_id IS 'm5-internal-cost-rule-' || rule_fingerprint
    ),
    rule_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(rule_fingerprint)=64
        AND rule_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    rule_revision INTEGER NOT NULL UNIQUE CHECK (
        typeof(rule_revision)='integer' AND rule_revision=0
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='m5-internal-labor-cost-rule-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='m5-internal-scheduled-labor-cost-v1'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS rule_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.rule_revision')
            IS rule_revision
        AND json_extract(canonical_semantic_json, '$.rate_semantics')
            IS 'modeled_internal_labor_cost_rate'
        AND json_extract(canonical_semantic_json, '$.currency') IS 'EUR'
        AND json_extract(canonical_semantic_json, '$.calculation_representation')
            IS 'Decimal'
        AND json_extract(canonical_semantic_json, '$.rate_scale') IS '0.01'
        AND json_extract(canonical_semantic_json, '$.final_money_scale') IS '0.01'
        AND json_extract(canonical_semantic_json, '$.rounding') IS 'ROUND_HALF_UP'
        AND json_extract(canonical_semantic_json, '$.automatic_fx') IS 'FORBIDDEN'
        AND json_extract(canonical_semantic_json, '$.component_scope')
            IS 'SCHEDULED_LABOR_ONLY'
    )
) WITHOUT ROWID;

CREATE TRIGGER m5_internal_cost_rules_no_update
BEFORE UPDATE ON m5_internal_cost_rules BEGIN
    SELECT RAISE(ABORT, 'M5 internal cost rule is immutable');
END;
CREATE TRIGGER m5_internal_cost_rules_no_delete
BEFORE DELETE ON m5_internal_cost_rules BEGIN
    SELECT RAISE(ABORT, 'M5 internal cost rule is append-only');
END;
CREATE TRIGGER m5_internal_cost_rules_no_replace
BEFORE INSERT ON m5_internal_cost_rules
WHEN EXISTS (SELECT 1 FROM m5_internal_cost_rules) BEGIN
    SELECT RAISE(ABORT, 'M5 internal cost rule cannot be replaced');
END;

CREATE TABLE m5_internal_labor_rate_sources (
    source_record_id TEXT NOT NULL PRIMARY KEY CHECK (
        source_record_id IS 'm5-internal-labor-rate-source-' || source_fingerprint
    ),
    source_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(source_fingerprint)=64
        AND source_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    worker_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (
        typeof(source_revision)='integer' AND source_revision>=0
    ),
    previous_source_record_id TEXT
        REFERENCES m5_internal_labor_rate_sources(source_record_id),
    effective_from TEXT,
    effective_until TEXT,
    worker_registry_revision INTEGER NOT NULL CHECK (
        typeof(worker_registry_revision)='integer'
        AND worker_registry_revision>=0
    ),
    worker_registry_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_fingerprint)=64
        AND worker_registry_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_worker_registry_json TEXT NOT NULL CHECK (
        json_valid(canonical_worker_registry_json)=1
        AND werkcrew_canonical_json(canonical_worker_registry_json)
            IS canonical_worker_registry_json
        AND werkcrew_sha256(canonical_worker_registry_json)
            IS worker_registry_fingerprint
        AND json_extract(canonical_worker_registry_json, '$.document_type')
            IS 'WorkerIdentityRegistry'
        AND json_extract(canonical_worker_registry_json, '$.schema_version')
            IS 'm2-worker-registry-v1'
        AND json_extract(
            canonical_worker_registry_json, '$.payload.registry_revision'
        ) IS worker_registry_revision
    ),
    rule_id TEXT NOT NULL REFERENCES m5_internal_cost_rules(rule_id),
    rule_fingerprint TEXT NOT NULL CHECK (
        length(rule_fingerprint)=64
        AND rule_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND rule_id IS 'm5-internal-cost-rule-' || rule_fingerprint
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='m5-internal-labor-rate-source-v1'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS source_fingerprint
        AND json_extract(canonical_semantic_json, '$.worker_id') IS worker_id
        AND json_extract(canonical_semantic_json, '$.source_revision')
            IS source_revision
        AND json_extract(canonical_semantic_json, '$.previous_source_record_id')
            IS previous_source_record_id
        AND json_extract(canonical_semantic_json, '$.effective_from')
            IS effective_from
        AND json_extract(canonical_semantic_json, '$.effective_until')
            IS effective_until
        AND json_extract(canonical_semantic_json, '$.worker_registry_revision')
            IS worker_registry_revision
        AND json_extract(canonical_semantic_json, '$.worker_registry_fingerprint')
            IS worker_registry_fingerprint
        AND json_extract(canonical_semantic_json, '$.canonical_worker_registry_json')
            IS canonical_worker_registry_json
        AND json_extract(canonical_semantic_json, '$.rule_id') IS rule_id
        AND json_extract(canonical_semantic_json, '$.rule_fingerprint')
            IS rule_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.configuration_id')
            IS 'gen2-internal-labor-cost-rates-v1'
        AND json_extract(canonical_semantic_json, '$.source_classification')
            IS 'GEN2_SYNTHETIC_COMPANY_CONFIGURATION'
        AND json_extract(canonical_semantic_json, '$.access_classification')
            IS 'INTERNAL_ONLY'
        AND json_extract(canonical_semantic_json, '$.rate_semantics')
            IS 'modeled_internal_labor_cost_rate'
        AND json_extract(canonical_semantic_json, '$.currency') IS 'EUR'
        AND json_extract(canonical_semantic_json, '$.cost_rule_version')
            IS 'm5-internal-scheduled-labor-cost-v1'
        AND json_type(canonical_semantic_json, '$.rate_amount')='text'
    ),
    UNIQUE(worker_id, source_revision),
    CHECK (
        (source_revision=0 AND previous_source_record_id IS NULL
            AND effective_from IS NULL)
        OR (source_revision>0 AND previous_source_record_id IS NOT NULL
            AND effective_from IS NOT NULL)
    ),
    CHECK (effective_until IS NULL OR effective_from IS NOT NULL)
) WITHOUT ROWID;

CREATE INDEX ix_m5_internal_labor_rate_source_head
ON m5_internal_labor_rate_sources(worker_id, source_revision);

CREATE TRIGGER m5_internal_labor_rate_sources_current_worker
BEFORE INSERT ON m5_internal_labor_rate_sources
WHEN NOT EXISTS (
        SELECT 1 FROM m2_worker_registry
        WHERE registry_key='GLOBAL'
          AND root_schema_version='m2-worker-registry-v1'
          AND registry_revision=NEW.worker_registry_revision
          AND content_sha256=NEW.worker_registry_fingerprint
          AND canonical_root_json=NEW.canonical_worker_registry_json
    )
    OR NOT EXISTS (
        SELECT 1 FROM m2_worker_identities WHERE worker_id=NEW.worker_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM m5_internal_cost_rules
        WHERE rule_id=NEW.rule_id AND rule_fingerprint=NEW.rule_fingerprint
    )
BEGIN
    SELECT RAISE(ABORT, 'M5 rate source must reference current canonical M2 worker identity and exact rule');
END;

CREATE TRIGGER m5_internal_labor_rate_sources_append
BEFORE INSERT ON m5_internal_labor_rate_sources
WHEN NEW.source_revision <> COALESCE((
        SELECT max(source_revision)+1
        FROM m5_internal_labor_rate_sources
        WHERE worker_id=NEW.worker_id
    ), 0)
    OR (NEW.source_revision>0 AND NOT EXISTS (
        SELECT 1 FROM m5_internal_labor_rate_sources
        WHERE source_record_id=NEW.previous_source_record_id
          AND worker_id=NEW.worker_id
          AND source_revision=NEW.source_revision-1
    ))
BEGIN
    SELECT RAISE(ABORT, 'M5 rate source must append to exact worker parent');
END;

CREATE TRIGGER m5_internal_labor_rate_sources_no_update
BEFORE UPDATE ON m5_internal_labor_rate_sources BEGIN
    SELECT RAISE(ABORT, 'M5 rate source is immutable');
END;
CREATE TRIGGER m5_internal_labor_rate_sources_no_delete
BEFORE DELETE ON m5_internal_labor_rate_sources BEGIN
    SELECT RAISE(ABORT, 'M5 rate source is append-only');
END;
CREATE TRIGGER m5_internal_labor_rate_sources_no_replace
BEFORE INSERT ON m5_internal_labor_rate_sources
WHEN EXISTS (
    SELECT 1 FROM m5_internal_labor_rate_sources
    WHERE source_record_id=NEW.source_record_id
       OR source_fingerprint=NEW.source_fingerprint
       OR (worker_id=NEW.worker_id AND source_revision=NEW.source_revision)
) BEGIN
    SELECT RAISE(ABORT, 'M5 rate source cannot be replaced');
END;

CREATE TABLE m5_internal_cost_source_captures (
    capture_id TEXT NOT NULL PRIMARY KEY CHECK (
        capture_id IS 'm5-internal-cost-source-capture-' || capture_fingerprint
    ),
    capture_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(capture_fingerprint)=64
        AND capture_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    ledger_generation INTEGER NOT NULL UNIQUE CHECK (
        typeof(ledger_generation)='integer' AND ledger_generation>0
    ),
    source_record_id TEXT NOT NULL UNIQUE
        REFERENCES m5_internal_labor_rate_sources(source_record_id),
    source_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(source_fingerprint)=64
        AND source_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND source_record_id IS
            'm5-internal-labor-rate-source-' || source_fingerprint
    ),
    worker_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK (
        typeof(source_revision)='integer' AND source_revision>=0
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='m5-internal-cost-source-capture-v1'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS capture_fingerprint
        AND json_extract(canonical_semantic_json, '$.ledger_generation')
            IS ledger_generation
        AND json_extract(canonical_semantic_json, '$.source_record_id')
            IS source_record_id
        AND json_extract(canonical_semantic_json, '$.source_fingerprint')
            IS source_fingerprint
        AND json_extract(canonical_semantic_json, '$.worker_id') IS worker_id
        AND json_extract(canonical_semantic_json, '$.source_revision')
            IS source_revision
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.configuration_id')
            IS 'gen2-internal-labor-cost-rates-v1'
        AND json_extract(canonical_semantic_json, '$.source_classification')
            IS 'GEN2_SYNTHETIC_COMPANY_CONFIGURATION'
        AND json_extract(canonical_semantic_json, '$.capture_kind')
            IS 'INTERNAL_LABOR_RATE_SOURCE'
    )
) WITHOUT ROWID;

CREATE TRIGGER m5_internal_cost_source_captures_append
BEFORE INSERT ON m5_internal_cost_source_captures
WHEN NEW.ledger_generation <> COALESCE((
        SELECT max(ledger_generation)+1
        FROM m5_internal_cost_source_captures
    ), 1)
    OR NOT EXISTS (
        SELECT 1 FROM m5_internal_labor_rate_sources
        WHERE source_record_id=NEW.source_record_id
          AND source_fingerprint=NEW.source_fingerprint
          AND worker_id=NEW.worker_id
          AND source_revision=NEW.source_revision
    )
BEGIN
    SELECT RAISE(ABORT, 'M5 source capture must append exact authentic source');
END;
CREATE TRIGGER m5_internal_cost_source_captures_no_update
BEFORE UPDATE ON m5_internal_cost_source_captures BEGIN
    SELECT RAISE(ABORT, 'M5 source capture is immutable');
END;
CREATE TRIGGER m5_internal_cost_source_captures_no_delete
BEFORE DELETE ON m5_internal_cost_source_captures BEGIN
    SELECT RAISE(ABORT, 'M5 source capture is append-only');
END;
CREATE TRIGGER m5_internal_cost_source_captures_no_replace
BEFORE INSERT ON m5_internal_cost_source_captures
WHEN EXISTS (
    SELECT 1 FROM m5_internal_cost_source_captures
    WHERE capture_id=NEW.capture_id
       OR capture_fingerprint=NEW.capture_fingerprint
       OR ledger_generation=NEW.ledger_generation
       OR source_record_id=NEW.source_record_id
) BEGIN
    SELECT RAISE(ABORT, 'M5 source capture cannot be replaced');
END;

CREATE TABLE m5_internal_cost_support_cuts (
    support_id TEXT NOT NULL PRIMARY KEY CHECK (
        support_id IS 'm5-internal-cost-support-' || support_fingerprint
    ),
    support_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(support_fingerprint)=64
        AND support_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='m5-internal-cost-support-v1'
    ),
    evaluation_input_id TEXT NOT NULL
        REFERENCES m3_evaluation_inputs(evaluation_input_id),
    evaluation_input_fingerprint TEXT NOT NULL CHECK (
        length(evaluation_input_fingerprint)=64
        AND evaluation_input_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    feasibility_support_snapshot_id TEXT NOT NULL
        REFERENCES m3_feasibility_support_snapshots(support_snapshot_id),
    feasibility_support_snapshot_fingerprint TEXT NOT NULL CHECK (
        length(feasibility_support_snapshot_fingerprint)=64
        AND feasibility_support_snapshot_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    company_plan_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL CHECK (
        typeof(base_plan_revision)='integer' AND base_plan_revision>=0
    ),
    base_plan_revision_id TEXT NOT NULL
        REFERENCES m3_plan_revisions(revision_id),
    base_plan_revision_fingerprint TEXT NOT NULL CHECK (
        length(base_plan_revision_fingerprint)=64
        AND base_plan_revision_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    m3_result_id TEXT NOT NULL CHECK (
        m3_result_id IS 'm3-feasibility-result-' || m3_result_fingerprint
    ),
    m3_result_fingerprint TEXT NOT NULL CHECK (
        length(m3_result_fingerprint)=64
        AND m3_result_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    rule_id TEXT NOT NULL REFERENCES m5_internal_cost_rules(rule_id),
    rule_revision INTEGER NOT NULL CHECK (
        typeof(rule_revision)='integer' AND rule_revision=0
    ),
    rule_fingerprint TEXT NOT NULL CHECK (
        length(rule_fingerprint)=64
        AND rule_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND rule_id IS 'm5-internal-cost-rule-' || rule_fingerprint
    ),
    source_cut_generation INTEGER NOT NULL CHECK (
        typeof(source_cut_generation)='integer'
        AND source_cut_generation>=0
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS support_fingerprint
        AND json_extract(canonical_semantic_json, '$.support_id') IS NULL
        AND json_extract(canonical_semantic_json, '$.evaluation_input_id')
            IS evaluation_input_id
        AND json_extract(canonical_semantic_json, '$.evaluation_input_fingerprint')
            IS evaluation_input_fingerprint
        AND json_extract(canonical_semantic_json, '$.feasibility_support_snapshot_id')
            IS feasibility_support_snapshot_id
        AND json_extract(canonical_semantic_json, '$.feasibility_support_snapshot_fingerprint')
            IS feasibility_support_snapshot_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision')
            IS base_plan_revision
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_id')
            IS base_plan_revision_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_fingerprint')
            IS base_plan_revision_fingerprint
        AND json_extract(canonical_semantic_json, '$.m3_result_id') IS m3_result_id
        AND json_extract(canonical_semantic_json, '$.m3_result_fingerprint')
            IS m3_result_fingerprint
        AND json_extract(canonical_semantic_json, '$.rule_id') IS rule_id
        AND json_extract(canonical_semantic_json, '$.rule_revision')
            IS rule_revision
        AND json_extract(canonical_semantic_json, '$.rule_fingerprint')
            IS rule_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_cut_generation')
            IS source_cut_generation
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.configuration_id')
            IS 'gen2-internal-labor-cost-rates-v1'
        AND json_extract(canonical_semantic_json, '$.source_classification')
            IS 'GEN2_SYNTHETIC_COMPANY_CONFIGURATION'
        AND json_extract(canonical_semantic_json, '$.access_classification')
            IS 'INTERNAL_ONLY'
    ),
    FOREIGN KEY(company_plan_id, base_plan_revision)
        REFERENCES m3_plan_revisions(company_plan_id, revision)
) WITHOUT ROWID;

CREATE TRIGGER m5_internal_cost_support_cuts_exact_bindings
BEFORE INSERT ON m5_internal_cost_support_cuts
WHEN NOT EXISTS (
        SELECT 1 FROM m3_evaluation_inputs
        WHERE evaluation_input_id=NEW.evaluation_input_id
          AND evaluation_input_fingerprint=NEW.evaluation_input_fingerprint
          AND company_plan_id=NEW.company_plan_id
          AND base_plan_revision=NEW.base_plan_revision
          AND base_plan_revision_id=NEW.base_plan_revision_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM m3_feasibility_support_snapshots
        WHERE support_snapshot_id=NEW.feasibility_support_snapshot_id
          AND support_fingerprint=NEW.feasibility_support_snapshot_fingerprint
          AND evaluation_input_id=NEW.evaluation_input_id
          AND evaluation_input_fingerprint=NEW.evaluation_input_fingerprint
          AND company_plan_id=NEW.company_plan_id
          AND base_plan_revision=NEW.base_plan_revision
          AND base_plan_revision_id=NEW.base_plan_revision_id
          AND base_plan_revision_fingerprint=NEW.base_plan_revision_fingerprint
    )
    OR NOT EXISTS (
        SELECT 1 FROM m3_plan_revisions
        WHERE company_plan_id=NEW.company_plan_id
          AND revision=NEW.base_plan_revision
          AND revision_id=NEW.base_plan_revision_id
          AND content_sha256=NEW.base_plan_revision_fingerprint
    )
    OR NOT EXISTS (
        SELECT 1 FROM m5_internal_cost_rules
        WHERE rule_id=NEW.rule_id
          AND rule_revision=NEW.rule_revision
          AND rule_fingerprint=NEW.rule_fingerprint
    )
    OR NEW.source_cut_generation <> COALESCE((
        SELECT max(ledger_generation)
        FROM m5_internal_cost_source_captures
    ), 0)
BEGIN
    SELECT RAISE(ABORT, 'M5 support must bind exact M3 evidence, rule and authentic source generation');
END;

CREATE TRIGGER m5_internal_cost_support_cuts_no_update
BEFORE UPDATE ON m5_internal_cost_support_cuts BEGIN
    SELECT RAISE(ABORT, 'M5 internal cost support is immutable');
END;
CREATE TRIGGER m5_internal_cost_support_cuts_no_delete
BEFORE DELETE ON m5_internal_cost_support_cuts BEGIN
    SELECT RAISE(ABORT, 'M5 internal cost support is append-only');
END;
CREATE TRIGGER m5_internal_cost_support_cuts_no_replace
BEFORE INSERT ON m5_internal_cost_support_cuts
WHEN EXISTS (
    SELECT 1 FROM m5_internal_cost_support_cuts
    WHERE support_id=NEW.support_id OR support_fingerprint=NEW.support_fingerprint
) BEGIN
    SELECT RAISE(ABORT, 'M5 internal cost support cannot be replaced');
END;

CREATE TABLE m5_internal_cost_support_candidates (
    support_id TEXT NOT NULL
        REFERENCES m5_internal_cost_support_cuts(support_id),
    candidate_position INTEGER NOT NULL CHECK (
        typeof(candidate_position)='integer' AND candidate_position>=0
    ),
    candidate_id TEXT NOT NULL CHECK (
        candidate_id IS 'm3-candidate-' || candidate_fingerprint
    ),
    candidate_fingerprint TEXT NOT NULL CHECK (
        length(candidate_fingerprint)=64
        AND candidate_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    PRIMARY KEY(support_id, candidate_position),
    UNIQUE(support_id, candidate_id)
) WITHOUT ROWID;

CREATE TRIGGER m5_internal_cost_support_candidates_no_update
BEFORE UPDATE ON m5_internal_cost_support_candidates BEGIN
    SELECT RAISE(ABORT, 'M5 support candidate binding is immutable');
END;
CREATE TRIGGER m5_internal_cost_support_candidates_no_delete
BEFORE DELETE ON m5_internal_cost_support_candidates BEGIN
    SELECT RAISE(ABORT, 'M5 support candidate binding is append-only');
END;
CREATE TRIGGER m5_internal_cost_support_candidates_no_replace
BEFORE INSERT ON m5_internal_cost_support_candidates
WHEN EXISTS (
    SELECT 1 FROM m5_internal_cost_support_candidates
    WHERE support_id=NEW.support_id
      AND (candidate_position=NEW.candidate_position
           OR candidate_id=NEW.candidate_id)
) BEGIN
    SELECT RAISE(ABORT, 'M5 support candidate binding cannot be replaced');
END;

CREATE TABLE m5_internal_cost_support_subjects (
    support_id TEXT NOT NULL
        REFERENCES m5_internal_cost_support_cuts(support_id),
    subject_id TEXT NOT NULL CHECK (
        subject_id IS 'm5-internal-labor-cost-subject-' || subject_fingerprint
    ),
    subject_fingerprint TEXT NOT NULL CHECK (
        length(subject_fingerprint)=64
        AND subject_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    candidate_position INTEGER NOT NULL CHECK (
        typeof(candidate_position)='integer' AND candidate_position>=0
    ),
    candidate_id TEXT NOT NULL,
    candidate_fingerprint TEXT NOT NULL CHECK (
        length(candidate_fingerprint)=64
        AND candidate_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND candidate_id IS 'm3-candidate-' || candidate_fingerprint
    ),
    subject_scope TEXT NOT NULL CHECK (
        subject_scope IN ('BASELINE','CANDIDATE')
    ),
    commitment_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    interval_start TEXT NOT NULL,
    interval_end TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (
        schema_version='m5-internal-labor-cost-subject-v1'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS subject_fingerprint
        AND json_extract(canonical_semantic_json, '$.candidate_position')
            IS candidate_position
        AND json_extract(canonical_semantic_json, '$.candidate_id') IS candidate_id
        AND json_extract(canonical_semantic_json, '$.candidate_fingerprint')
            IS candidate_fingerprint
        AND json_extract(canonical_semantic_json, '$.scope') IS subject_scope
        AND json_extract(canonical_semantic_json, '$.commitment_id')
            IS commitment_id
        AND json_extract(canonical_semantic_json, '$.worker_id') IS worker_id
        AND json_extract(canonical_semantic_json, '$.interval_start')
            IS interval_start
        AND json_extract(canonical_semantic_json, '$.interval_end') IS interval_end
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
    ),
    PRIMARY KEY(support_id, subject_id),
    UNIQUE(
        support_id, candidate_position, subject_scope, commitment_id,
        worker_id, interval_start, interval_end
    ),
    FOREIGN KEY(support_id, candidate_position)
        REFERENCES m5_internal_cost_support_candidates(
            support_id, candidate_position
        )
) WITHOUT ROWID;

CREATE TRIGGER m5_internal_cost_support_subjects_exact_candidate
BEFORE INSERT ON m5_internal_cost_support_subjects
WHEN NOT EXISTS (
    SELECT 1 FROM m5_internal_cost_support_candidates
    WHERE support_id=NEW.support_id
      AND candidate_position=NEW.candidate_position
      AND candidate_id=NEW.candidate_id
      AND candidate_fingerprint=NEW.candidate_fingerprint
) BEGIN
    SELECT RAISE(ABORT, 'M5 subject must bind exact ordered candidate');
END;
CREATE TRIGGER m5_internal_cost_support_subjects_no_update
BEFORE UPDATE ON m5_internal_cost_support_subjects BEGIN
    SELECT RAISE(ABORT, 'M5 support subject is immutable');
END;
CREATE TRIGGER m5_internal_cost_support_subjects_no_delete
BEFORE DELETE ON m5_internal_cost_support_subjects BEGIN
    SELECT RAISE(ABORT, 'M5 support subject is append-only');
END;
CREATE TRIGGER m5_internal_cost_support_subjects_no_replace
BEFORE INSERT ON m5_internal_cost_support_subjects
WHEN EXISTS (
    SELECT 1 FROM m5_internal_cost_support_subjects
    WHERE support_id=NEW.support_id AND subject_id=NEW.subject_id
) BEGIN
    SELECT RAISE(ABORT, 'M5 support subject cannot be replaced');
END;

CREATE TABLE m5_internal_cost_support_selections (
    support_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    selection_id TEXT NOT NULL CHECK (
        selection_id IS
            'm5-internal-labor-rate-selection-' || selection_fingerprint
    ),
    selection_fingerprint TEXT NOT NULL CHECK (
        length(selection_fingerprint)=64
        AND selection_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    selection_status TEXT NOT NULL CHECK (
        selection_status IN ('SELECTED','NO_SOURCE')
    ),
    source_record_id TEXT
        REFERENCES m5_internal_labor_rate_sources(source_record_id),
    source_fingerprint TEXT,
    source_revision INTEGER,
    source_capture_id TEXT
        REFERENCES m5_internal_cost_source_captures(capture_id),
    source_capture_fingerprint TEXT,
    source_capture_generation INTEGER,
    no_source_reason TEXT CHECK (
        no_source_reason IS NULL OR no_source_reason IN (
            'NO_RATE_SOURCE',
            'RATE_INTERVAL_NOT_COVERED',
            'AMBIGUOUS_RATE_SOURCE',
            'MULTI_REVISION_COVERAGE_UNSUPPORTED'
        )
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='m5-internal-labor-rate-selection-v1'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS selection_fingerprint
        AND json_extract(canonical_semantic_json, '$.subject_id') IS subject_id
        AND json_extract(canonical_semantic_json, '$.status') IS selection_status
        AND json_extract(canonical_semantic_json, '$.source_record_id')
            IS source_record_id
        AND json_extract(canonical_semantic_json, '$.source_fingerprint')
            IS source_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_revision')
            IS source_revision
        AND json_extract(canonical_semantic_json, '$.source_capture_id')
            IS source_capture_id
        AND json_extract(canonical_semantic_json, '$.source_capture_fingerprint')
            IS source_capture_fingerprint
        AND json_extract(canonical_semantic_json, '$.source_capture_generation')
            IS source_capture_generation
        AND json_extract(canonical_semantic_json, '$.no_source_reason')
            IS no_source_reason
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
    ),
    PRIMARY KEY(support_id, subject_id),
    UNIQUE(support_id, selection_id),
    FOREIGN KEY(support_id, subject_id)
        REFERENCES m5_internal_cost_support_subjects(support_id, subject_id),
    CHECK (
        (selection_status='SELECTED'
         AND source_record_id IS NOT NULL
         AND source_fingerprint IS NOT NULL
         AND source_revision IS NOT NULL
         AND source_capture_id IS NOT NULL
         AND source_capture_fingerprint IS NOT NULL
         AND source_capture_generation IS NOT NULL
         AND no_source_reason IS NULL)
        OR
        (selection_status='NO_SOURCE'
         AND source_record_id IS NULL
         AND source_fingerprint IS NULL
         AND source_revision IS NULL
         AND source_capture_id IS NULL
         AND source_capture_fingerprint IS NULL
         AND source_capture_generation IS NULL
         AND no_source_reason IS NOT NULL)
    )
) WITHOUT ROWID;

CREATE TRIGGER m5_internal_cost_support_selections_exact_source
BEFORE INSERT ON m5_internal_cost_support_selections
WHEN (NEW.selection_status='SELECTED' AND NOT EXISTS (
        SELECT 1
        FROM m5_internal_labor_rate_sources AS source
        JOIN m5_internal_cost_source_captures AS capture
          ON capture.source_record_id=source.source_record_id
         AND capture.source_fingerprint=source.source_fingerprint
        JOIN m5_internal_cost_support_cuts AS support
          ON support.support_id=NEW.support_id
        WHERE source.source_record_id=NEW.source_record_id
          AND source.source_fingerprint=NEW.source_fingerprint
          AND source.source_revision=NEW.source_revision
          AND capture.capture_id=NEW.source_capture_id
          AND capture.capture_fingerprint=NEW.source_capture_fingerprint
          AND capture.ledger_generation=NEW.source_capture_generation
          AND capture.ledger_generation<=support.source_cut_generation
    ))
BEGIN
    SELECT RAISE(ABORT, 'M5 selected rate must bind exact captured source at cut');
END;
CREATE TRIGGER m5_internal_cost_support_selections_no_update
BEFORE UPDATE ON m5_internal_cost_support_selections BEGIN
    SELECT RAISE(ABORT, 'M5 support selection is immutable');
END;
CREATE TRIGGER m5_internal_cost_support_selections_no_delete
BEFORE DELETE ON m5_internal_cost_support_selections BEGIN
    SELECT RAISE(ABORT, 'M5 support selection is append-only');
END;
CREATE TRIGGER m5_internal_cost_support_selections_no_replace
BEFORE INSERT ON m5_internal_cost_support_selections
WHEN EXISTS (
    SELECT 1 FROM m5_internal_cost_support_selections
    WHERE support_id=NEW.support_id
      AND (subject_id=NEW.subject_id OR selection_id=NEW.selection_id)
) BEGIN
    SELECT RAISE(ABORT, 'M5 support selection cannot be replaced');
END;
