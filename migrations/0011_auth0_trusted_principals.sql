-- AUTH-0 deployment-provisioned company authority and trusted principals.
-- No request authentication, policy, consent, approval, decision, or execution.

CREATE TABLE auth0_company_authority_roots (
    authority_root_id TEXT NOT NULL PRIMARY KEY CHECK (
        authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    authority_root_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='company-authority-root-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='auth0-deployment-bootstrap-v1'
    ),
    company_id TEXT NOT NULL CHECK (length(trim(company_id))>0),
    company_plan_id TEXT NOT NULL UNIQUE
        REFERENCES m3_company_plans(company_plan_id),
    company_plan_provenance_reference TEXT NOT NULL CHECK (
        length(trim(company_plan_provenance_reference))>0
    ),
    owner_principal_subject_id TEXT NOT NULL CHECK (
        length(trim(owner_principal_subject_id))>0
    ),
    provisioning_source TEXT NOT NULL CHECK (
        provisioning_source='DEPLOYMENT_BOOTSTRAP'
    ),
    provisioning_reference TEXT NOT NULL CHECK (
        length(trim(provisioning_reference))>0
    ),
    worker_registry_schema_version TEXT NOT NULL CHECK (
        worker_registry_schema_version='m2-worker-registry-v1'
    ),
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
            IS worker_registry_schema_version
        AND json_extract(
            canonical_worker_registry_json,
            '$.payload.registry_revision'
        ) IS worker_registry_revision
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS authority_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.company_id')
            IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(
            canonical_semantic_json,
            '$.company_plan_provenance_reference'
        ) IS company_plan_provenance_reference
        AND json_extract(
            canonical_semantic_json,
            '$.owner_principal_subject_id'
        ) IS owner_principal_subject_id
        AND json_extract(canonical_semantic_json, '$.provisioning_source')
            IS provisioning_source
        AND json_extract(canonical_semantic_json, '$.provisioning_reference')
            IS provisioning_reference
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_schema_version'
        ) IS worker_registry_schema_version
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_revision'
        ) IS worker_registry_revision
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_fingerprint'
        ) IS worker_registry_fingerprint
        AND json_extract(
            canonical_semantic_json,
            '$.canonical_worker_registry_json'
        ) IS canonical_worker_registry_json
    )
) WITHOUT ROWID;

CREATE UNIQUE INDEX ux_auth0_company_authority_company_plan
ON auth0_company_authority_roots(company_id, company_plan_id);

CREATE TRIGGER auth0_company_authority_roots_plan_binding
BEFORE INSERT ON auth0_company_authority_roots
WHEN NOT EXISTS (
    SELECT 1 FROM m3_company_plans
    WHERE company_plan_id=NEW.company_plan_id
      AND provenance_reference=NEW.company_plan_provenance_reference
)
OR NOT EXISTS (
    SELECT 1 FROM m2_worker_registry
    WHERE registry_key='GLOBAL'
      AND root_schema_version=NEW.worker_registry_schema_version
      AND registry_revision=NEW.worker_registry_revision
      AND content_sha256=NEW.worker_registry_fingerprint
      AND canonical_root_json=NEW.canonical_worker_registry_json
)
BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 root must bind exact CompanyPlan and worker registry');
END;

CREATE TRIGGER auth0_company_authority_roots_no_update
BEFORE UPDATE ON auth0_company_authority_roots BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 company authority root is immutable');
END;
CREATE TRIGGER auth0_company_authority_roots_no_delete
BEFORE DELETE ON auth0_company_authority_roots BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 company authority root is append-only');
END;
CREATE TRIGGER auth0_company_authority_roots_no_replace
BEFORE INSERT ON auth0_company_authority_roots
WHEN EXISTS (
    SELECT 1 FROM auth0_company_authority_roots
    WHERE authority_root_id=NEW.authority_root_id
       OR authority_root_fingerprint=NEW.authority_root_fingerprint
       OR company_plan_id=NEW.company_plan_id
)
BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 company authority root cannot be replaced');
END;

CREATE TABLE auth0_trusted_principals (
    principal_id TEXT NOT NULL PRIMARY KEY CHECK (
        principal_id IS 'auth0-trusted-principal-' || principal_fingerprint
    ),
    principal_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(principal_fingerprint)=64
        AND principal_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='trusted-principal-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='auth0-deployment-bootstrap-v1'
    ),
    authority_root_id TEXT NOT NULL
        REFERENCES auth0_company_authority_roots(authority_root_id),
    authority_root_fingerprint TEXT NOT NULL CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    company_id TEXT NOT NULL CHECK (length(trim(company_id))>0),
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    principal_subject_id TEXT NOT NULL CHECK (
        length(trim(principal_subject_id))>0
    ),
    principal_type TEXT NOT NULL CHECK (
        principal_type IN ('OWNER','WORKER')
    ),
    worker_id TEXT REFERENCES m2_worker_identities(worker_id),
    worker_registry_revision INTEGER NOT NULL CHECK (
        typeof(worker_registry_revision)='integer'
        AND worker_registry_revision>=0
    ),
    worker_registry_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_fingerprint)=64
        AND worker_registry_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS principal_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.authority_root_id')
            IS authority_root_id
        AND json_extract(
            canonical_semantic_json,
            '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_id')
            IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(canonical_semantic_json, '$.principal_subject_id')
            IS principal_subject_id
        AND json_extract(canonical_semantic_json, '$.principal_type')
            IS principal_type
        AND json_extract(canonical_semantic_json, '$.worker_id')
            IS worker_id
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_revision'
        ) IS worker_registry_revision
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_fingerprint'
        ) IS worker_registry_fingerprint
    ),
    CHECK (
        (principal_type='OWNER' AND worker_id IS NULL)
        OR (principal_type='WORKER' AND worker_id IS NOT NULL)
    ),
    UNIQUE(authority_root_id, principal_subject_id)
) WITHOUT ROWID;

CREATE UNIQUE INDEX ux_auth0_one_owner_per_root
ON auth0_trusted_principals(authority_root_id)
WHERE principal_type='OWNER';

CREATE UNIQUE INDEX ux_auth0_one_principal_per_worker
ON auth0_trusted_principals(authority_root_id, worker_id)
WHERE principal_type='WORKER';

CREATE TRIGGER auth0_trusted_principals_root_binding
BEFORE INSERT ON auth0_trusted_principals
WHEN NOT EXISTS (
    SELECT 1
    FROM auth0_company_authority_roots AS root
    WHERE root.authority_root_id=NEW.authority_root_id
      AND root.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND root.company_id=NEW.company_id
      AND root.company_plan_id=NEW.company_plan_id
      AND root.worker_registry_revision=NEW.worker_registry_revision
      AND root.worker_registry_fingerprint=NEW.worker_registry_fingerprint
      AND (
          (
              NEW.principal_type='OWNER'
              AND NEW.worker_id IS NULL
              AND NEW.principal_subject_id=root.owner_principal_subject_id
          )
          OR (
              NEW.principal_type='WORKER'
              AND NEW.worker_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM json_each(
                      root.canonical_worker_registry_json,
                      '$.payload.worker_ids'
                  ) AS worker
                  WHERE worker.value=NEW.worker_id
              )
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 principal must bind exact root and worker registry');
END;

CREATE TRIGGER auth0_trusted_principals_no_update
BEFORE UPDATE ON auth0_trusted_principals BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 trusted principal is immutable');
END;
CREATE TRIGGER auth0_trusted_principals_no_delete
BEFORE DELETE ON auth0_trusted_principals BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 trusted principal is append-only');
END;
CREATE TRIGGER auth0_trusted_principals_no_replace
BEFORE INSERT ON auth0_trusted_principals
WHEN EXISTS (
    SELECT 1 FROM auth0_trusted_principals
    WHERE principal_id=NEW.principal_id
       OR principal_fingerprint=NEW.principal_fingerprint
       OR (
           authority_root_id=NEW.authority_root_id
           AND principal_subject_id=NEW.principal_subject_id
       )
       OR (
           NEW.principal_type='WORKER'
           AND authority_root_id=NEW.authority_root_id
           AND worker_id=NEW.worker_id
       )
)
BEGIN
    SELECT RAISE(ABORT, 'AUTH-0 trusted principal cannot be replaced');
END;
