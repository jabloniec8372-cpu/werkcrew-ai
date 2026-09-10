-- Immutable M3-B fresh EvaluationInput evidence. No plan mutation or M2 write.
CREATE TABLE m3_evaluation_inputs (
    evaluation_input_id TEXT NOT NULL PRIMARY KEY,
    evaluation_input_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(evaluation_input_fingerprint) = 64
        AND evaluation_input_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND evaluation_input_id IS 'm3-evaluation-input-' || evaluation_input_fingerprint
    ),
    schema_version TEXT NOT NULL CHECK (schema_version = 'm3-evaluation-input-v1'),
    rule_version TEXT NOT NULL CHECK (rule_version = 'm3-unavailability-impact-analysis-v1'),
    source_request_fingerprint TEXT NOT NULL CHECK (
        length(source_request_fingerprint) = 64
        AND source_request_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    source_event_id TEXT NOT NULL CHECK (length(trim(source_event_id)) > 0),
    company_plan_id TEXT NOT NULL,
    base_plan_revision INTEGER NOT NULL CHECK (
        typeof(base_plan_revision) = 'integer' AND base_plan_revision >= 0
    ),
    base_plan_revision_id TEXT NOT NULL REFERENCES m3_plan_revisions(revision_id),
    current_scope_fingerprint TEXT NOT NULL CHECK (
        length(current_scope_fingerprint) = 64
        AND current_scope_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json) = 1
        AND werkcrew_canonical_json(canonical_semantic_json) IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json) IS evaluation_input_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version') IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version') IS rule_version
        AND json_extract(canonical_semantic_json, '$.historical_source_scope.request_fingerprint')
            IS source_request_fingerprint
        AND json_extract(canonical_semantic_json, '$.historical_source_scope.event_id')
            IS source_event_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id') IS company_plan_id
        AND json_extract(canonical_semantic_json, '$.base_plan_revision') IS base_plan_revision
        AND json_extract(canonical_semantic_json, '$.base_plan_revision_id') IS base_plan_revision_id
        AND json_extract(canonical_semantic_json, '$.current_planning_scope_fingerprint')
            IS current_scope_fingerprint
    ),
    FOREIGN KEY (company_plan_id, base_plan_revision)
        REFERENCES m3_plan_revisions(company_plan_id, revision)
);

CREATE TRIGGER m3_evaluation_inputs_no_update
BEFORE UPDATE ON m3_evaluation_inputs
BEGIN
    SELECT RAISE(ABORT, 'M3 EvaluationInput evidence is immutable');
END;

CREATE TRIGGER m3_evaluation_inputs_exact_revision
BEFORE INSERT ON m3_evaluation_inputs
WHEN NOT EXISTS (
    SELECT 1
    FROM m3_plan_revisions
    WHERE company_plan_id = NEW.company_plan_id
      AND revision = NEW.base_plan_revision
      AND revision_id = NEW.base_plan_revision_id
      AND content_sha256 = json_extract(
          NEW.canonical_semantic_json, '$.base_plan_revision_fingerprint'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'M3 EvaluationInput must bind one exact PlanRevision');
END;

CREATE TRIGGER m3_evaluation_inputs_no_delete
BEFORE DELETE ON m3_evaluation_inputs
BEGIN
    SELECT RAISE(ABORT, 'M3 EvaluationInput evidence is append-only');
END;

CREATE TRIGGER m3_evaluation_inputs_no_replace
BEFORE INSERT ON m3_evaluation_inputs
WHEN EXISTS (
    SELECT 1 FROM m3_evaluation_inputs
    WHERE evaluation_input_id = NEW.evaluation_input_id
       OR evaluation_input_fingerprint = NEW.evaluation_input_fingerprint
)
BEGIN
    SELECT RAISE(ABORT, 'M3 EvaluationInput evidence cannot be replaced');
END;
