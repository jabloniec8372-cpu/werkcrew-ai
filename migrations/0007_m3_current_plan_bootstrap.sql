-- Planning intent only. No M2 execution root, assignment route or outbox is written.
CREATE TABLE m3_company_plans (
    company_plan_id TEXT NOT NULL PRIMARY KEY CHECK (length(trim(company_plan_id)) > 0),
    provenance_reference TEXT NOT NULL CHECK (length(trim(provenance_reference)) > 0)
);

CREATE TABLE m3_plan_day_associations (
    plan_day_id TEXT NOT NULL PRIMARY KEY REFERENCES m2_plan_day_roots(plan_day_id),
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    worker_id TEXT NOT NULL REFERENCES m2_worker_identities(worker_id),
    business_date TEXT NOT NULL,
    UNIQUE(worker_id, business_date)
);

CREATE TABLE m3_plan_revisions (
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0),
    revision_id TEXT NOT NULL UNIQUE,
    previous_revision_id TEXT REFERENCES m3_plan_revisions(revision_id),
    content_sha256 TEXT NOT NULL CHECK (
        length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'
        AND revision_id IS 'm3-plan-revision-' || content_sha256
    ),
    canonical_content_json TEXT NOT NULL CHECK (
        json_valid(canonical_content_json) = 1
        AND werkcrew_canonical_json(canonical_content_json) IS canonical_content_json
        AND werkcrew_sha256(canonical_content_json) IS content_sha256
        AND json_extract(canonical_content_json, '$.schema_version') IS 'm3-plan-revision-v1'
        AND json_extract(canonical_content_json, '$.company_plan_id') IS company_plan_id
        AND json_extract(canonical_content_json, '$.revision') IS revision
        AND json_extract(canonical_content_json, '$.previous_revision_id') IS previous_revision_id
    ),
    PRIMARY KEY(company_plan_id, revision),
    CHECK ((revision = 0 AND previous_revision_id IS NULL) OR (revision > 0 AND previous_revision_id IS NOT NULL))
);

CREATE TRIGGER m3_company_plans_no_update BEFORE UPDATE ON m3_company_plans
BEGIN SELECT RAISE(ABORT, 'CompanyPlan identity is immutable'); END;
CREATE TRIGGER m3_company_plans_no_delete BEFORE DELETE ON m3_company_plans
BEGIN SELECT RAISE(ABORT, 'CompanyPlan identity is immutable'); END;
CREATE TRIGGER m3_company_plans_no_replace BEFORE INSERT ON m3_company_plans
WHEN EXISTS(SELECT 1 FROM m3_company_plans WHERE company_plan_id = NEW.company_plan_id)
BEGIN SELECT RAISE(ABORT, 'CompanyPlan identity already exists'); END;

CREATE TRIGGER m3_plan_day_associations_no_update BEFORE UPDATE ON m3_plan_day_associations
BEGIN SELECT RAISE(ABORT, 'M3 plan-day association is immutable'); END;
CREATE TRIGGER m3_plan_day_associations_no_delete BEFORE DELETE ON m3_plan_day_associations
BEGIN SELECT RAISE(ABORT, 'M3 plan-day association is immutable'); END;
CREATE TRIGGER m3_plan_day_associations_no_replace BEFORE INSERT ON m3_plan_day_associations
WHEN EXISTS(SELECT 1 FROM m3_plan_day_associations
    WHERE plan_day_id = NEW.plan_day_id OR (worker_id = NEW.worker_id AND business_date = NEW.business_date))
BEGIN SELECT RAISE(ABORT, 'M3 worker-day association already exists'); END;
CREATE TRIGGER m3_plan_day_associations_binding BEFORE INSERT ON m3_plan_day_associations
WHEN NOT EXISTS(SELECT 1 FROM m2_plan_day_roots
    WHERE plan_day_id = NEW.plan_day_id AND worker_id = NEW.worker_id AND business_date = NEW.business_date)
BEGIN SELECT RAISE(ABORT, 'M3 worker-day association must match M2 identity'); END;

CREATE TRIGGER m3_plan_revisions_no_update BEFORE UPDATE ON m3_plan_revisions
BEGIN SELECT RAISE(ABORT, 'PlanRevision history is immutable'); END;
CREATE TRIGGER m3_plan_revisions_no_delete BEFORE DELETE ON m3_plan_revisions
BEGIN SELECT RAISE(ABORT, 'PlanRevision history is immutable'); END;
CREATE TRIGGER m3_plan_revisions_append BEFORE INSERT ON m3_plan_revisions
WHEN NEW.revision <> COALESCE((SELECT max(revision) + 1 FROM m3_plan_revisions WHERE company_plan_id = NEW.company_plan_id), 0)
    OR (NEW.revision > 0 AND NOT EXISTS(SELECT 1 FROM m3_plan_revisions
        WHERE company_plan_id = NEW.company_plan_id AND revision = NEW.revision - 1 AND revision_id = NEW.previous_revision_id))
    OR EXISTS(SELECT 1 FROM m3_plan_revisions WHERE revision_id = NEW.revision_id)
BEGIN SELECT RAISE(ABORT, 'PlanRevision must append to its exact current parent'); END;
