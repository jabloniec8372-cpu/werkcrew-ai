-- M3-E0 authoritative policy, OWNER approval, and worker consent evidence.
-- No candidate evaluation, ACT/ASK/BLOCK result, ranking, APPLY, or HUMAN_TASK.

CREATE TABLE m3e0_company_policy_profiles (
    profile_id TEXT NOT NULL PRIMARY KEY CHECK (
        profile_id IS 'm3e0-company-policy-profile-' || profile_fingerprint
    ),
    profile_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(profile_fingerprint)=64
        AND profile_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='company-policy-profile-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='owner-company-policy-profile-v1'
    ),
    governance_reference TEXT NOT NULL CHECK (
        governance_reference=
            'docs/decisions/0013-owner-company-policy-profile-v1-freeze.md'
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
    company_plan_provenance_reference TEXT NOT NULL CHECK (
        length(trim(company_plan_provenance_reference))>0
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS profile_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.governance_reference')
            IS governance_reference
        AND json_extract(canonical_semantic_json, '$.authority_root_id')
            IS authority_root_id
        AND json_extract(
            canonical_semantic_json,
            '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_id') IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(
            canonical_semantic_json,
            '$.company_plan_provenance_reference'
        ) IS company_plan_provenance_reference
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_assign.authority'
        ) IS 'CONSTRAINED_AUTO'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_window_01.movement_outside_window.m3c_verdict'
        ) IS 'REJECTED'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_window_01.intra_window_slot_shift.separate_authority_gate'
        ) IS 0
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_cost.max_additional_internal_labor_cost'
        ) IS '50.00'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_cost.currency'
        ) IS 'EUR'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_cost.boundary'
        ) IS 'INCLUSIVE'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_overtime.authority'
        ) IS 'ASK_ALWAYS'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_vehicle.authority'
        ) IS 'OWNER_APPROVAL_AND_WORKER_CONSENT'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_tag.authority'
        ) IS 'ASK_ALWAYS'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_tag.policy_relation'
        ) IS 'SOFT_EXCEPTION'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_horizon.authority_horizon'
        ) IS 'INCIDENT_DAY_ONLY'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_search.exhausted_without_feasible.preserved_outcome'
        ) IS 'SEARCH_ENVELOPE_EXHAUSTED'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_search.exhausted_without_feasible.disposition'
        ) IS 'ABSTAIN'
        AND json_extract(
            canonical_semantic_json,
            '$.policy_payload.p_search.exhausted_without_feasible.reason'
        ) IS 'ENVELOPE_EXHAUSTED'
    ),
    UNIQUE(authority_root_id, schema_version, rule_version)
) WITHOUT ROWID;

CREATE TRIGGER m3e0_company_policy_profiles_root_binding
BEFORE INSERT ON m3e0_company_policy_profiles
WHEN NOT EXISTS (
    SELECT 1 FROM auth0_company_authority_roots AS root
    WHERE root.authority_root_id=NEW.authority_root_id
      AND root.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND root.company_id=NEW.company_id
      AND root.company_plan_id=NEW.company_plan_id
      AND root.company_plan_provenance_reference=
          NEW.company_plan_provenance_reference
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 profile must bind exact AUTH-0 root');
END;

CREATE TRIGGER m3e0_company_policy_profiles_no_update
BEFORE UPDATE ON m3e0_company_policy_profiles BEGIN
    SELECT RAISE(ABORT, 'M3-E0 company policy profile is immutable');
END;
CREATE TRIGGER m3e0_company_policy_profiles_no_delete
BEFORE DELETE ON m3e0_company_policy_profiles BEGIN
    SELECT RAISE(ABORT, 'M3-E0 company policy profile is append-only');
END;
CREATE TRIGGER m3e0_company_policy_profiles_no_replace
BEFORE INSERT ON m3e0_company_policy_profiles
WHEN EXISTS (
    SELECT 1 FROM m3e0_company_policy_profiles
    WHERE profile_id=NEW.profile_id
       OR profile_fingerprint=NEW.profile_fingerprint
       OR (
          authority_root_id=NEW.authority_root_id
          AND schema_version=NEW.schema_version
          AND rule_version=NEW.rule_version
       )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 company policy profile cannot be replaced');
END;

CREATE TABLE m3e0_policy_issuances (
    issuance_id TEXT NOT NULL PRIMARY KEY CHECK (
        issuance_id IS 'm3e0-policy-issuance-' || issuance_fingerprint
    ),
    issuance_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(issuance_fingerprint)=64
        AND issuance_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='policy-issuance-evidence-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='m3e0-authoritative-policy-evidence-v1'
    ),
    profile_id TEXT NOT NULL REFERENCES m3e0_company_policy_profiles(profile_id),
    profile_fingerprint TEXT NOT NULL CHECK (
        length(profile_fingerprint)=64
        AND profile_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND profile_id IS 'm3e0-company-policy-profile-' || profile_fingerprint
    ),
    authority_root_id TEXT NOT NULL
        REFERENCES auth0_company_authority_roots(authority_root_id),
    authority_root_fingerprint TEXT NOT NULL CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    owner_principal_id TEXT NOT NULL
        REFERENCES auth0_trusted_principals(principal_id),
    owner_principal_fingerprint TEXT NOT NULL CHECK (
        length(owner_principal_fingerprint)=64
        AND owner_principal_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND owner_principal_id IS
            'auth0-trusted-principal-' || owner_principal_fingerprint
    ),
    company_id TEXT NOT NULL CHECK (length(trim(company_id))>0),
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    company_plan_provenance_reference TEXT NOT NULL CHECK (
        length(trim(company_plan_provenance_reference))>0
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS issuance_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.profile_id') IS profile_id
        AND json_extract(canonical_semantic_json, '$.profile_fingerprint')
            IS profile_fingerprint
        AND json_extract(canonical_semantic_json, '$.authority_root_id')
            IS authority_root_id
        AND json_extract(
            canonical_semantic_json,
            '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.owner_principal_id')
            IS owner_principal_id
        AND json_extract(
            canonical_semantic_json,
            '$.owner_principal_fingerprint'
        ) IS owner_principal_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_id') IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(
            canonical_semantic_json,
            '$.company_plan_provenance_reference'
        ) IS company_plan_provenance_reference
    ),
    UNIQUE(profile_id)
) WITHOUT ROWID;

CREATE TRIGGER m3e0_policy_issuances_authority_binding
BEFORE INSERT ON m3e0_policy_issuances
WHEN NOT EXISTS (
    SELECT 1
    FROM m3e0_company_policy_profiles AS profile
    JOIN auth0_company_authority_roots AS root
      ON root.authority_root_id=profile.authority_root_id
    JOIN auth0_trusted_principals AS owner
      ON owner.authority_root_id=root.authority_root_id
    WHERE profile.profile_id=NEW.profile_id
      AND profile.profile_fingerprint=NEW.profile_fingerprint
      AND profile.authority_root_id=NEW.authority_root_id
      AND profile.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND profile.company_id=NEW.company_id
      AND profile.company_plan_id=NEW.company_plan_id
      AND profile.company_plan_provenance_reference=
          NEW.company_plan_provenance_reference
      AND owner.principal_id=NEW.owner_principal_id
      AND owner.principal_fingerprint=NEW.owner_principal_fingerprint
      AND owner.principal_type='OWNER'
      AND owner.company_id=NEW.company_id
      AND owner.company_plan_id=NEW.company_plan_id
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 issuance requires exact AUTH-0 OWNER');
END;

CREATE TRIGGER m3e0_policy_issuances_no_update
BEFORE UPDATE ON m3e0_policy_issuances BEGIN
    SELECT RAISE(ABORT, 'M3-E0 policy issuance is immutable');
END;
CREATE TRIGGER m3e0_policy_issuances_no_delete
BEFORE DELETE ON m3e0_policy_issuances BEGIN
    SELECT RAISE(ABORT, 'M3-E0 policy issuance is append-only');
END;
CREATE TRIGGER m3e0_policy_issuances_no_replace
BEFORE INSERT ON m3e0_policy_issuances
WHEN EXISTS (
    SELECT 1 FROM m3e0_policy_issuances
    WHERE issuance_id=NEW.issuance_id
       OR issuance_fingerprint=NEW.issuance_fingerprint
       OR profile_id=NEW.profile_id
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 policy issuance cannot be replaced');
END;

CREATE TABLE m3e0_human_action_capture_roots (
    capture_root_id TEXT NOT NULL PRIMARY KEY CHECK (
        capture_root_id IS
            'm3e0-human-action-root-' || capture_root_fingerprint
    ),
    capture_root_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(capture_root_fingerprint)=64
        AND capture_root_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='trusted-human-action-capture-root-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='m3e0-deployment-human-action-capture-v1'
    ),
    authority_root_id TEXT NOT NULL UNIQUE
        REFERENCES auth0_company_authority_roots(authority_root_id),
    authority_root_fingerprint TEXT NOT NULL CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    company_id TEXT NOT NULL CHECK (length(trim(company_id))>0),
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    company_plan_provenance_reference TEXT NOT NULL CHECK (
        length(trim(company_plan_provenance_reference))>0
    ),
    verification_key_hex TEXT NOT NULL CHECK (
        length(verification_key_hex)=64
        AND verification_key_hex NOT GLOB '*[^0-9a-f]*'
    ),
    capture_key_id TEXT NOT NULL CHECK (
        length(capture_key_id)=length('m3e0-human-action-key-')+64
        AND capture_key_id GLOB 'm3e0-human-action-key-*'
        AND substr(capture_key_id, length('m3e0-human-action-key-')+1)
            NOT GLOB '*[^0-9a-f]*'
    ),
    provisioning_source TEXT NOT NULL CHECK (
        provisioning_source='DEPLOYMENT_BOOTSTRAP'
    ),
    provisioning_reference TEXT NOT NULL CHECK (
        length(trim(provisioning_reference))>0
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS capture_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.authority_root_id')
            IS authority_root_id
        AND json_extract(
            canonical_semantic_json, '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_id') IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(
            canonical_semantic_json, '$.company_plan_provenance_reference'
        ) IS company_plan_provenance_reference
        AND json_extract(canonical_semantic_json, '$.verification_key_hex')
            IS verification_key_hex
        AND json_extract(canonical_semantic_json, '$.provisioning_source')
            IS provisioning_source
        AND json_extract(canonical_semantic_json, '$.provisioning_reference')
            IS provisioning_reference
    )
) WITHOUT ROWID;

CREATE TRIGGER m3e0_human_action_capture_roots_authority_binding
BEFORE INSERT ON m3e0_human_action_capture_roots
WHEN NOT EXISTS (
    SELECT 1 FROM auth0_company_authority_roots AS root
    WHERE root.authority_root_id=NEW.authority_root_id
      AND root.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND root.company_id=NEW.company_id
      AND root.company_plan_id=NEW.company_plan_id
      AND root.company_plan_provenance_reference=
          NEW.company_plan_provenance_reference
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 capture root must bind exact AUTH-0 root');
END;

CREATE TRIGGER m3e0_human_action_capture_roots_no_update
BEFORE UPDATE ON m3e0_human_action_capture_roots BEGIN
    SELECT RAISE(ABORT, 'M3-E0 human-action capture root is immutable');
END;
CREATE TRIGGER m3e0_human_action_capture_roots_no_delete
BEFORE DELETE ON m3e0_human_action_capture_roots BEGIN
    SELECT RAISE(ABORT, 'M3-E0 human-action capture root is append-only');
END;
CREATE TRIGGER m3e0_human_action_capture_roots_no_replace
BEFORE INSERT ON m3e0_human_action_capture_roots
WHEN EXISTS (
    SELECT 1 FROM m3e0_human_action_capture_roots
    WHERE capture_root_id=NEW.capture_root_id
       OR capture_root_fingerprint=NEW.capture_root_fingerprint
       OR authority_root_id=NEW.authority_root_id
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 human-action capture root cannot be replaced');
END;

CREATE TABLE m3e0_human_action_capture_consumptions (
    consumption_id TEXT NOT NULL PRIMARY KEY CHECK (
        consumption_id IS
            'm3e0-human-action-consumption-' || consumption_fingerprint
    ),
    consumption_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(consumption_fingerprint)=64
        AND consumption_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='human-action-capture-consumption-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='m3e0-deployment-human-action-capture-v1'
    ),
    capture_key_id TEXT NOT NULL CHECK (
        length(capture_key_id)=length('m3e0-human-action-key-')+64
        AND capture_key_id GLOB 'm3e0-human-action-key-*'
        AND substr(capture_key_id, length('m3e0-human-action-key-')+1)
            NOT GLOB '*[^0-9a-f]*'
    ),
    capture_reference TEXT NOT NULL CHECK (
        length(trim(capture_reference))>0
    ),
    authority_root_id TEXT NOT NULL
        REFERENCES auth0_company_authority_roots(authority_root_id),
    authority_root_fingerprint TEXT NOT NULL CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    action_kind TEXT NOT NULL CHECK (
        action_kind IN ('OWNER_APPROVAL','WORKER_CONSENT')
    ),
    action_value TEXT NOT NULL CHECK (
        (action_kind='OWNER_APPROVAL' AND action_value='APPROVED')
        OR (
            action_kind='WORKER_CONSENT'
            AND action_value IN (
                'UNKNOWN','REQUESTED','FREELY_GIVEN','DECLINED','PRESSURED',
                'ABSENT','REVOKED'
            )
        )
    ),
    principal_id TEXT NOT NULL
        REFERENCES auth0_trusted_principals(principal_id),
    principal_fingerprint TEXT NOT NULL CHECK (
        length(principal_fingerprint)=64
        AND principal_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND principal_id IS
            'auth0-trusted-principal-' || principal_fingerprint
    ),
    worker_id TEXT REFERENCES m2_worker_identities(worker_id),
    scope_id TEXT NOT NULL CHECK (
        scope_id IS 'm3e0-authority-scope-' || scope_fingerprint
    ),
    scope_fingerprint TEXT NOT NULL CHECK (
        length(scope_fingerprint)=64
        AND scope_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    evidence_record_id TEXT NOT NULL UNIQUE CHECK (
        (action_kind='OWNER_APPROVAL' AND evidence_record_id IS
            'm3e0-owner-approval-' || evidence_record_fingerprint)
        OR (action_kind='WORKER_CONSENT' AND evidence_record_id IS
            'm3e0-worker-consent-' || evidence_record_fingerprint)
    ),
    evidence_record_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(evidence_record_fingerprint)=64
        AND evidence_record_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    human_action_provenance_id TEXT NOT NULL UNIQUE CHECK (
        human_action_provenance_id IS
            'm3e0-human-action-' || human_action_provenance_fingerprint
    ),
    human_action_provenance_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(human_action_provenance_fingerprint)=64
        AND human_action_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    human_action_occurred_at TEXT NOT NULL CHECK (
        length(human_action_occurred_at)>1
        AND substr(human_action_occurred_at, -1)='Z'
    ),
    canonical_human_action_provenance_json TEXT NOT NULL CHECK (
        json_valid(canonical_human_action_provenance_json)=1
        AND werkcrew_canonical_json(canonical_human_action_provenance_json)
            IS canonical_human_action_provenance_json
        AND werkcrew_sha256(canonical_human_action_provenance_json)
            IS human_action_provenance_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.action_kind'
        ) IS action_kind
        AND json_extract(
            canonical_human_action_provenance_json, '$.action_value'
        ) IS action_value
        AND json_extract(
            canonical_human_action_provenance_json, '$.authority_root_id'
        ) IS authority_root_id
        AND json_extract(
            canonical_human_action_provenance_json,
            '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.principal_id'
        ) IS principal_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.principal_fingerprint'
        ) IS principal_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.scope_id'
        ) IS scope_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.scope_fingerprint'
        ) IS scope_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.capture_key_id'
        ) IS capture_key_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.capture_reference'
        ) IS capture_reference
        AND json_extract(
            canonical_human_action_provenance_json, '$.occurred_at'
        ) IS human_action_occurred_at
        AND json_extract(
            canonical_human_action_provenance_json, '$.schema_version'
        ) IS 'trusted-human-action-provenance-v1'
        AND json_extract(
            canonical_human_action_provenance_json, '$.rule_version'
        ) IS rule_version
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS consumption_fingerprint
        AND json_extract(canonical_semantic_json, '$.schema_version')
            IS schema_version
        AND json_extract(canonical_semantic_json, '$.rule_version')
            IS rule_version
        AND json_extract(canonical_semantic_json, '$.capture_key_id')
            IS capture_key_id
        AND json_extract(canonical_semantic_json, '$.capture_reference')
            IS capture_reference
        AND json_extract(canonical_semantic_json, '$.authority_root_id')
            IS authority_root_id
        AND json_extract(
            canonical_semantic_json, '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(canonical_semantic_json, '$.action_kind')
            IS action_kind
        AND json_extract(canonical_semantic_json, '$.action_value')
            IS action_value
        AND json_extract(canonical_semantic_json, '$.principal_id')
            IS principal_id
        AND json_extract(canonical_semantic_json, '$.principal_fingerprint')
            IS principal_fingerprint
        AND json_extract(canonical_semantic_json, '$.worker_id') IS worker_id
        AND json_extract(canonical_semantic_json, '$.scope_id') IS scope_id
        AND json_extract(canonical_semantic_json, '$.scope_fingerprint')
            IS scope_fingerprint
        AND json_extract(canonical_semantic_json, '$.evidence_record_id')
            IS evidence_record_id
        AND json_extract(
            canonical_semantic_json, '$.evidence_record_fingerprint'
        ) IS evidence_record_fingerprint
        AND json_extract(
            canonical_semantic_json, '$.human_action_provenance_id'
        ) IS human_action_provenance_id
        AND json_extract(
            canonical_semantic_json,
            '$.human_action_provenance_fingerprint'
        ) IS human_action_provenance_fingerprint
        AND json_extract(canonical_semantic_json, '$.occurred_at')
            IS human_action_occurred_at
        AND werkcrew_canonical_json(json_extract(
            canonical_semantic_json, '$.human_action_provenance'
        )) IS canonical_human_action_provenance_json
    ),
    UNIQUE(capture_key_id, capture_reference)
) WITHOUT ROWID;

CREATE TRIGGER m3e0_human_action_capture_consumptions_authority_binding
BEFORE INSERT ON m3e0_human_action_capture_consumptions
WHEN NOT EXISTS (
    SELECT 1
    FROM auth0_company_authority_roots AS root
    JOIN m3e0_human_action_capture_roots AS capture
      ON capture.authority_root_id=root.authority_root_id
    JOIN auth0_trusted_principals AS principal
      ON principal.authority_root_id=root.authority_root_id
    WHERE root.authority_root_id=NEW.authority_root_id
      AND root.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND capture.capture_key_id=NEW.capture_key_id
      AND capture.capture_root_id=json_extract(
          NEW.canonical_human_action_provenance_json, '$.capture_root_id'
      )
      AND capture.capture_root_fingerprint=json_extract(
          NEW.canonical_human_action_provenance_json,
          '$.capture_root_fingerprint'
      )
      AND principal.principal_id=NEW.principal_id
      AND principal.principal_fingerprint=NEW.principal_fingerprint
      AND (
          (NEW.action_kind='OWNER_APPROVAL'
           AND principal.principal_type='OWNER')
          OR
          (NEW.action_kind='WORKER_CONSENT'
           AND principal.principal_type='WORKER'
           AND principal.worker_id=NEW.worker_id)
      )
      AND (
          NEW.worker_id IS NULL
          OR EXISTS (
              SELECT 1 FROM json_each(
                  root.canonical_worker_registry_json,
                  '$.payload.worker_ids'
              ) AS worker
              WHERE worker.value=NEW.worker_id
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 capture consumption binding is invalid');
END;

CREATE TRIGGER m3e0_human_action_capture_consumptions_no_update
BEFORE UPDATE ON m3e0_human_action_capture_consumptions BEGIN
    SELECT RAISE(ABORT, 'M3-E0 capture consumption is immutable');
END;
CREATE TRIGGER m3e0_human_action_capture_consumptions_no_delete
BEFORE DELETE ON m3e0_human_action_capture_consumptions BEGIN
    SELECT RAISE(ABORT, 'M3-E0 capture consumption is append-only');
END;
CREATE TRIGGER m3e0_human_action_capture_consumptions_no_replace
BEFORE INSERT ON m3e0_human_action_capture_consumptions
WHEN EXISTS (
    SELECT 1 FROM m3e0_human_action_capture_consumptions
    WHERE consumption_id=NEW.consumption_id
       OR consumption_fingerprint=NEW.consumption_fingerprint
       OR evidence_record_id=NEW.evidence_record_id
       OR evidence_record_fingerprint=NEW.evidence_record_fingerprint
       OR human_action_provenance_id=NEW.human_action_provenance_id
       OR human_action_provenance_fingerprint=
          NEW.human_action_provenance_fingerprint
       OR (
          capture_key_id=NEW.capture_key_id
          AND capture_reference=NEW.capture_reference
       )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 capture consumption cannot be replaced');
END;

CREATE TABLE m3e0_owner_approvals (
    approval_id TEXT NOT NULL PRIMARY KEY CHECK (
        approval_id IS 'm3e0-owner-approval-' || approval_fingerprint
    ),
    approval_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(approval_fingerprint)=64
        AND approval_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='owner-approval-evidence-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='m3e0-authoritative-policy-evidence-v1'
    ),
    authority_root_id TEXT NOT NULL
        REFERENCES auth0_company_authority_roots(authority_root_id),
    authority_root_fingerprint TEXT NOT NULL CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    owner_principal_id TEXT NOT NULL
        REFERENCES auth0_trusted_principals(principal_id),
    owner_principal_fingerprint TEXT NOT NULL CHECK (
        length(owner_principal_fingerprint)=64
        AND owner_principal_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND owner_principal_id IS
            'auth0-trusted-principal-' || owner_principal_fingerprint
    ),
    company_id TEXT NOT NULL CHECK (length(trim(company_id))>0),
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    company_plan_provenance_reference TEXT NOT NULL CHECK (
        length(trim(company_plan_provenance_reference))>0
    ),
    scope_id TEXT NOT NULL CHECK (
        scope_id IS 'm3e0-authority-scope-' || scope_fingerprint
    ),
    scope_fingerprint TEXT NOT NULL CHECK (
        length(scope_fingerprint)=64
        AND scope_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_scope_json TEXT NOT NULL CHECK (
        json_valid(canonical_scope_json)=1
        AND werkcrew_canonical_json(canonical_scope_json) IS canonical_scope_json
        AND werkcrew_sha256(canonical_scope_json) IS scope_fingerprint
    ),
    human_action_provenance_id TEXT NOT NULL UNIQUE CHECK (
        human_action_provenance_id IS
            'm3e0-human-action-' || human_action_provenance_fingerprint
    ),
    human_action_provenance_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(human_action_provenance_fingerprint)=64
        AND human_action_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    human_action_capture_key_id TEXT NOT NULL CHECK (
        length(human_action_capture_key_id)=86
        AND human_action_capture_key_id GLOB 'm3e0-human-action-key-*'
    ),
    human_action_capture_reference TEXT NOT NULL CHECK (
        length(trim(human_action_capture_reference))>0
    ),
    human_action_occurred_at TEXT NOT NULL CHECK (
        length(human_action_occurred_at)>1
        AND substr(human_action_occurred_at, -1)='Z'
    ),
    human_action_lineage_sequence INTEGER NOT NULL CHECK (
        typeof(human_action_lineage_sequence)='integer'
        AND human_action_lineage_sequence=1
    ),
    previous_human_action_id TEXT CHECK (previous_human_action_id IS NULL),
    previous_human_action_fingerprint TEXT CHECK (
        previous_human_action_fingerprint IS NULL
    ),
    canonical_human_action_provenance_json TEXT NOT NULL CHECK (
        json_valid(canonical_human_action_provenance_json)=1
        AND werkcrew_canonical_json(canonical_human_action_provenance_json)
            IS canonical_human_action_provenance_json
        AND werkcrew_sha256(canonical_human_action_provenance_json)
            IS human_action_provenance_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.action_kind'
        ) IS 'OWNER_APPROVAL'
        AND json_extract(
            canonical_human_action_provenance_json, '$.action_value'
        ) IS 'APPROVED'
        AND json_extract(
            canonical_human_action_provenance_json, '$.authority_root_id'
        ) IS authority_root_id
        AND json_extract(
            canonical_human_action_provenance_json,
            '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.principal_id'
        ) IS owner_principal_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.principal_fingerprint'
        ) IS owner_principal_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.scope_id'
        ) IS scope_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.scope_fingerprint'
        ) IS scope_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.capture_key_id'
        ) IS human_action_capture_key_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.capture_reference'
        ) IS human_action_capture_reference
        AND json_extract(
            canonical_human_action_provenance_json, '$.occurred_at'
        ) IS human_action_occurred_at
        AND json_extract(
            canonical_human_action_provenance_json, '$.lineage_sequence'
        ) IS human_action_lineage_sequence
        AND json_extract(
            canonical_human_action_provenance_json, '$.previous_action_id'
        ) IS previous_human_action_id
        AND json_extract(
            canonical_human_action_provenance_json,
            '$.previous_action_fingerprint'
        ) IS previous_human_action_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.schema_version'
        ) IS 'trusted-human-action-provenance-v1'
        AND json_extract(
            canonical_human_action_provenance_json, '$.rule_version'
        ) IS 'm3e0-deployment-human-action-capture-v1'
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS approval_fingerprint
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
        AND json_extract(canonical_semantic_json, '$.owner_principal_id')
            IS owner_principal_id
        AND json_extract(
            canonical_semantic_json,
            '$.owner_principal_fingerprint'
        ) IS owner_principal_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_id') IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(
            canonical_semantic_json,
            '$.company_plan_provenance_reference'
        ) IS company_plan_provenance_reference
        AND json_extract(canonical_semantic_json, '$.scope_id') IS scope_id
        AND json_extract(canonical_semantic_json, '$.scope_fingerprint')
            IS scope_fingerprint
        AND werkcrew_canonical_json(
            json_extract(canonical_semantic_json, '$.scope')
        ) IS canonical_scope_json
        AND json_extract(
            canonical_semantic_json, '$.human_action_provenance_id'
        ) IS human_action_provenance_id
        AND json_extract(
            canonical_semantic_json, '$.human_action_provenance_fingerprint'
        ) IS human_action_provenance_fingerprint
        AND werkcrew_canonical_json(json_extract(
            canonical_semantic_json, '$.human_action_provenance'
        )) IS canonical_human_action_provenance_json
    ),
    FOREIGN KEY (
        human_action_capture_key_id,
        human_action_capture_reference
    ) REFERENCES m3e0_human_action_capture_consumptions(
        capture_key_id,
        capture_reference
    ),
    UNIQUE(authority_root_id, scope_id)
) WITHOUT ROWID;

CREATE TRIGGER m3e0_owner_approvals_authority_binding
BEFORE INSERT ON m3e0_owner_approvals
WHEN NOT EXISTS (
    SELECT 1
    FROM auth0_company_authority_roots AS root
    JOIN m3e0_human_action_capture_roots AS capture
      ON capture.authority_root_id=root.authority_root_id
    JOIN m3e0_human_action_capture_consumptions AS consumption
      ON consumption.capture_key_id=NEW.human_action_capture_key_id
     AND consumption.capture_reference=NEW.human_action_capture_reference
    JOIN auth0_trusted_principals AS owner
      ON owner.authority_root_id=root.authority_root_id
    WHERE root.authority_root_id=NEW.authority_root_id
      AND root.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND root.company_id=NEW.company_id
      AND root.company_plan_id=NEW.company_plan_id
      AND root.company_plan_provenance_reference=
          NEW.company_plan_provenance_reference
      AND owner.principal_id=NEW.owner_principal_id
      AND owner.principal_fingerprint=NEW.owner_principal_fingerprint
      AND owner.principal_type='OWNER'
      AND owner.company_id=NEW.company_id
      AND owner.company_plan_id=NEW.company_plan_id
      AND capture.capture_root_id=json_extract(
          NEW.canonical_human_action_provenance_json, '$.capture_root_id'
      )
      AND capture.capture_root_fingerprint=json_extract(
          NEW.canonical_human_action_provenance_json,
          '$.capture_root_fingerprint'
      )
      AND capture.capture_key_id=NEW.human_action_capture_key_id
      AND consumption.authority_root_id=NEW.authority_root_id
      AND consumption.authority_root_fingerprint=
          NEW.authority_root_fingerprint
      AND consumption.action_kind='OWNER_APPROVAL'
      AND consumption.action_value='APPROVED'
      AND consumption.principal_id=NEW.owner_principal_id
      AND consumption.principal_fingerprint=NEW.owner_principal_fingerprint
      AND consumption.worker_id IS
          json_extract(NEW.canonical_scope_json, '$.worker_id')
      AND consumption.scope_id=NEW.scope_id
      AND consumption.scope_fingerprint=NEW.scope_fingerprint
      AND consumption.evidence_record_id=NEW.approval_id
      AND consumption.evidence_record_fingerprint=NEW.approval_fingerprint
      AND consumption.human_action_provenance_id=
          NEW.human_action_provenance_id
      AND consumption.human_action_provenance_fingerprint=
          NEW.human_action_provenance_fingerprint
      AND consumption.human_action_occurred_at=NEW.human_action_occurred_at
      AND consumption.canonical_human_action_provenance_json=
          NEW.canonical_human_action_provenance_json
      AND (
          json_extract(NEW.canonical_scope_json, '$.worker_id') IS NULL
          OR EXISTS (
              SELECT 1 FROM json_each(
                  root.canonical_worker_registry_json,
                  '$.payload.worker_ids'
              ) AS worker
              WHERE worker.value=
                  json_extract(NEW.canonical_scope_json, '$.worker_id')
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 approval requires exact AUTH-0 OWNER and scope');
END;

CREATE TRIGGER m3e0_owner_approvals_no_update
BEFORE UPDATE ON m3e0_owner_approvals BEGIN
    SELECT RAISE(ABORT, 'M3-E0 OWNER approval is immutable');
END;
CREATE TRIGGER m3e0_owner_approvals_no_delete
BEFORE DELETE ON m3e0_owner_approvals BEGIN
    SELECT RAISE(ABORT, 'M3-E0 OWNER approval is append-only');
END;
CREATE TRIGGER m3e0_owner_approvals_no_replace
BEFORE INSERT ON m3e0_owner_approvals
WHEN EXISTS (
    SELECT 1 FROM m3e0_owner_approvals
    WHERE approval_id=NEW.approval_id
       OR approval_fingerprint=NEW.approval_fingerprint
       OR (
          authority_root_id=NEW.authority_root_id
          AND scope_id=NEW.scope_id
       )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 OWNER approval cannot be replaced');
END;

CREATE TABLE m3e0_worker_consents (
    consent_id TEXT NOT NULL PRIMARY KEY CHECK (
        consent_id IS 'm3e0-worker-consent-' || consent_fingerprint
    ),
    consent_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(consent_fingerprint)=64
        AND consent_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    schema_version TEXT NOT NULL CHECK (
        schema_version='worker-consent-evidence-v1'
    ),
    rule_version TEXT NOT NULL CHECK (
        rule_version='m3e0-authoritative-policy-evidence-v1'
    ),
    authority_root_id TEXT NOT NULL
        REFERENCES auth0_company_authority_roots(authority_root_id),
    authority_root_fingerprint TEXT NOT NULL CHECK (
        length(authority_root_fingerprint)=64
        AND authority_root_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND authority_root_id IS
            'auth0-company-authority-root-' || authority_root_fingerprint
    ),
    worker_principal_id TEXT NOT NULL
        REFERENCES auth0_trusted_principals(principal_id),
    worker_principal_fingerprint TEXT NOT NULL CHECK (
        length(worker_principal_fingerprint)=64
        AND worker_principal_fingerprint NOT GLOB '*[^0-9a-f]*'
        AND worker_principal_id IS
            'auth0-trusted-principal-' || worker_principal_fingerprint
    ),
    company_id TEXT NOT NULL CHECK (length(trim(company_id))>0),
    company_plan_id TEXT NOT NULL REFERENCES m3_company_plans(company_plan_id),
    company_plan_provenance_reference TEXT NOT NULL CHECK (
        length(trim(company_plan_provenance_reference))>0
    ),
    worker_id TEXT NOT NULL REFERENCES m2_worker_identities(worker_id),
    worker_registry_revision INTEGER NOT NULL CHECK (
        typeof(worker_registry_revision)='integer'
        AND worker_registry_revision>=0
    ),
    worker_registry_fingerprint TEXT NOT NULL CHECK (
        length(worker_registry_fingerprint)=64
        AND worker_registry_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    scope_id TEXT NOT NULL CHECK (
        scope_id IS 'm3e0-authority-scope-' || scope_fingerprint
    ),
    scope_fingerprint TEXT NOT NULL CHECK (
        length(scope_fingerprint)=64
        AND scope_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_scope_json TEXT NOT NULL CHECK (
        json_valid(canonical_scope_json)=1
        AND werkcrew_canonical_json(canonical_scope_json) IS canonical_scope_json
        AND werkcrew_sha256(canonical_scope_json) IS scope_fingerprint
        AND json_extract(canonical_scope_json, '$.worker_id') IS worker_id
    ),
    consent_status TEXT NOT NULL CHECK (
        consent_status IN (
            'UNKNOWN','REQUESTED','FREELY_GIVEN','DECLINED','PRESSURED',
            'ABSENT','REVOKED'
        )
    ),
    human_action_provenance_id TEXT NOT NULL UNIQUE CHECK (
        human_action_provenance_id IS
            'm3e0-human-action-' || human_action_provenance_fingerprint
    ),
    human_action_provenance_fingerprint TEXT NOT NULL UNIQUE CHECK (
        length(human_action_provenance_fingerprint)=64
        AND human_action_provenance_fingerprint NOT GLOB '*[^0-9a-f]*'
    ),
    human_action_capture_key_id TEXT NOT NULL CHECK (
        length(human_action_capture_key_id)=86
        AND human_action_capture_key_id GLOB 'm3e0-human-action-key-*'
    ),
    human_action_capture_reference TEXT NOT NULL CHECK (
        length(trim(human_action_capture_reference))>0
    ),
    human_action_occurred_at TEXT NOT NULL CHECK (
        length(human_action_occurred_at)>1
        AND substr(human_action_occurred_at, -1)='Z'
    ),
    human_action_lineage_sequence INTEGER NOT NULL CHECK (
        typeof(human_action_lineage_sequence)='integer'
        AND human_action_lineage_sequence>=1
    ),
    previous_human_action_id TEXT REFERENCES
        m3e0_worker_consents(human_action_provenance_id),
    previous_human_action_fingerprint TEXT CHECK (
        previous_human_action_fingerprint IS NULL
        OR (
            length(previous_human_action_fingerprint)=64
            AND previous_human_action_fingerprint NOT GLOB '*[^0-9a-f]*'
            AND previous_human_action_id IS
                'm3e0-human-action-' || previous_human_action_fingerprint
        )
    ),
    canonical_human_action_provenance_json TEXT NOT NULL CHECK (
        json_valid(canonical_human_action_provenance_json)=1
        AND werkcrew_canonical_json(canonical_human_action_provenance_json)
            IS canonical_human_action_provenance_json
        AND werkcrew_sha256(canonical_human_action_provenance_json)
            IS human_action_provenance_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.action_kind'
        ) IS 'WORKER_CONSENT'
        AND json_extract(
            canonical_human_action_provenance_json, '$.action_value'
        ) IS consent_status
        AND json_extract(
            canonical_human_action_provenance_json, '$.authority_root_id'
        ) IS authority_root_id
        AND json_extract(
            canonical_human_action_provenance_json,
            '$.authority_root_fingerprint'
        ) IS authority_root_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.principal_id'
        ) IS worker_principal_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.principal_fingerprint'
        ) IS worker_principal_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.scope_id'
        ) IS scope_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.scope_fingerprint'
        ) IS scope_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.capture_key_id'
        ) IS human_action_capture_key_id
        AND json_extract(
            canonical_human_action_provenance_json, '$.capture_reference'
        ) IS human_action_capture_reference
        AND json_extract(
            canonical_human_action_provenance_json, '$.occurred_at'
        ) IS human_action_occurred_at
        AND json_extract(
            canonical_human_action_provenance_json, '$.lineage_sequence'
        ) IS human_action_lineage_sequence
        AND json_extract(
            canonical_human_action_provenance_json, '$.previous_action_id'
        ) IS previous_human_action_id
        AND json_extract(
            canonical_human_action_provenance_json,
            '$.previous_action_fingerprint'
        ) IS previous_human_action_fingerprint
        AND json_extract(
            canonical_human_action_provenance_json, '$.schema_version'
        ) IS 'trusted-human-action-provenance-v1'
        AND json_extract(
            canonical_human_action_provenance_json, '$.rule_version'
        ) IS 'm3e0-deployment-human-action-capture-v1'
        AND (
            (human_action_lineage_sequence=1
             AND previous_human_action_id IS NULL
             AND previous_human_action_fingerprint IS NULL)
            OR
            (human_action_lineage_sequence>1
             AND previous_human_action_id IS NOT NULL
             AND previous_human_action_fingerprint IS NOT NULL)
        )
    ),
    canonical_semantic_json TEXT NOT NULL CHECK (
        json_valid(canonical_semantic_json)=1
        AND werkcrew_canonical_json(canonical_semantic_json)
            IS canonical_semantic_json
        AND werkcrew_sha256(canonical_semantic_json)
            IS consent_fingerprint
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
        AND json_extract(canonical_semantic_json, '$.worker_principal_id')
            IS worker_principal_id
        AND json_extract(
            canonical_semantic_json,
            '$.worker_principal_fingerprint'
        ) IS worker_principal_fingerprint
        AND json_extract(canonical_semantic_json, '$.company_id') IS company_id
        AND json_extract(canonical_semantic_json, '$.company_plan_id')
            IS company_plan_id
        AND json_extract(
            canonical_semantic_json,
            '$.company_plan_provenance_reference'
        ) IS company_plan_provenance_reference
        AND json_extract(canonical_semantic_json, '$.worker_id') IS worker_id
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_revision'
        ) IS worker_registry_revision
        AND json_extract(
            canonical_semantic_json,
            '$.worker_registry_fingerprint'
        ) IS worker_registry_fingerprint
        AND json_extract(canonical_semantic_json, '$.scope_id') IS scope_id
        AND json_extract(canonical_semantic_json, '$.scope_fingerprint')
            IS scope_fingerprint
        AND werkcrew_canonical_json(
            json_extract(canonical_semantic_json, '$.scope')
        ) IS canonical_scope_json
        AND json_extract(canonical_semantic_json, '$.consent_status')
            IS consent_status
        AND json_extract(
            canonical_semantic_json, '$.human_action_provenance_id'
        ) IS human_action_provenance_id
        AND json_extract(
            canonical_semantic_json, '$.human_action_provenance_fingerprint'
        ) IS human_action_provenance_fingerprint
        AND werkcrew_canonical_json(json_extract(
            canonical_semantic_json, '$.human_action_provenance'
        )) IS canonical_human_action_provenance_json
    ),
    FOREIGN KEY (
        human_action_capture_key_id,
        human_action_capture_reference
    ) REFERENCES m3e0_human_action_capture_consumptions(
        capture_key_id,
        capture_reference
    ),
    UNIQUE(
        authority_root_id,
        worker_principal_id,
        worker_id,
        scope_id,
        human_action_lineage_sequence
    )
) WITHOUT ROWID;

CREATE TRIGGER m3e0_worker_consents_authority_binding
BEFORE INSERT ON m3e0_worker_consents
WHEN NOT EXISTS (
    SELECT 1
    FROM auth0_company_authority_roots AS root
    JOIN m3e0_human_action_capture_roots AS capture
      ON capture.authority_root_id=root.authority_root_id
    JOIN m3e0_human_action_capture_consumptions AS consumption
      ON consumption.capture_key_id=NEW.human_action_capture_key_id
     AND consumption.capture_reference=NEW.human_action_capture_reference
    JOIN auth0_trusted_principals AS worker
      ON worker.authority_root_id=root.authority_root_id
    WHERE root.authority_root_id=NEW.authority_root_id
      AND root.authority_root_fingerprint=NEW.authority_root_fingerprint
      AND root.company_id=NEW.company_id
      AND root.company_plan_id=NEW.company_plan_id
      AND root.company_plan_provenance_reference=
          NEW.company_plan_provenance_reference
      AND root.worker_registry_revision=NEW.worker_registry_revision
      AND root.worker_registry_fingerprint=NEW.worker_registry_fingerprint
      AND worker.principal_id=NEW.worker_principal_id
      AND worker.principal_fingerprint=NEW.worker_principal_fingerprint
      AND worker.principal_type='WORKER'
      AND worker.worker_id=NEW.worker_id
      AND worker.company_id=NEW.company_id
      AND worker.company_plan_id=NEW.company_plan_id
      AND worker.worker_registry_revision=NEW.worker_registry_revision
      AND worker.worker_registry_fingerprint=NEW.worker_registry_fingerprint
      AND capture.capture_root_id=json_extract(
          NEW.canonical_human_action_provenance_json, '$.capture_root_id'
      )
      AND capture.capture_root_fingerprint=json_extract(
          NEW.canonical_human_action_provenance_json,
          '$.capture_root_fingerprint'
      )
      AND capture.capture_key_id=NEW.human_action_capture_key_id
      AND consumption.authority_root_id=NEW.authority_root_id
      AND consumption.authority_root_fingerprint=
          NEW.authority_root_fingerprint
      AND consumption.action_kind='WORKER_CONSENT'
      AND consumption.action_value=NEW.consent_status
      AND consumption.principal_id=NEW.worker_principal_id
      AND consumption.principal_fingerprint=NEW.worker_principal_fingerprint
      AND consumption.worker_id=NEW.worker_id
      AND consumption.scope_id=NEW.scope_id
      AND consumption.scope_fingerprint=NEW.scope_fingerprint
      AND consumption.evidence_record_id=NEW.consent_id
      AND consumption.evidence_record_fingerprint=NEW.consent_fingerprint
      AND consumption.human_action_provenance_id=
          NEW.human_action_provenance_id
      AND consumption.human_action_provenance_fingerprint=
          NEW.human_action_provenance_fingerprint
      AND consumption.human_action_occurred_at=NEW.human_action_occurred_at
      AND consumption.canonical_human_action_provenance_json=
          NEW.canonical_human_action_provenance_json
      AND json_extract(NEW.canonical_scope_json, '$.worker_id')=NEW.worker_id
      AND EXISTS (
          SELECT 1 FROM json_each(
              root.canonical_worker_registry_json,
              '$.payload.worker_ids'
          ) AS registered
          WHERE registered.value=NEW.worker_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 consent requires exact AUTH-0 WORKER and scope');
END;

CREATE TRIGGER m3e0_worker_consents_chronology
BEFORE INSERT ON m3e0_worker_consents
WHEN (
    NEW.human_action_lineage_sequence=1
    AND EXISTS (
        SELECT 1 FROM m3e0_worker_consents AS existing
        WHERE existing.authority_root_id=NEW.authority_root_id
          AND existing.worker_principal_id=NEW.worker_principal_id
          AND existing.worker_id=NEW.worker_id
          AND existing.scope_id=NEW.scope_id
    )
) OR (
    NEW.human_action_lineage_sequence>1
    AND NOT EXISTS (
        SELECT 1 FROM m3e0_worker_consents AS previous
        WHERE previous.authority_root_id=NEW.authority_root_id
          AND previous.worker_principal_id=NEW.worker_principal_id
          AND previous.worker_id=NEW.worker_id
          AND previous.scope_id=NEW.scope_id
          AND previous.human_action_lineage_sequence=
              NEW.human_action_lineage_sequence-1
          AND previous.human_action_provenance_id=
              NEW.previous_human_action_id
          AND previous.human_action_provenance_fingerprint=
              NEW.previous_human_action_fingerprint
          AND previous.human_action_capture_key_id=
              NEW.human_action_capture_key_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 worker consent chronology is invalid');
END;

CREATE TRIGGER m3e0_worker_consents_no_update
BEFORE UPDATE ON m3e0_worker_consents BEGIN
    SELECT RAISE(ABORT, 'M3-E0 worker consent is immutable');
END;
CREATE TRIGGER m3e0_worker_consents_no_delete
BEFORE DELETE ON m3e0_worker_consents BEGIN
    SELECT RAISE(ABORT, 'M3-E0 worker consent is append-only');
END;
CREATE TRIGGER m3e0_worker_consents_no_replace
BEFORE INSERT ON m3e0_worker_consents
WHEN EXISTS (
    SELECT 1 FROM m3e0_worker_consents
    WHERE consent_id=NEW.consent_id
       OR consent_fingerprint=NEW.consent_fingerprint
       OR human_action_provenance_id=NEW.human_action_provenance_id
       OR human_action_provenance_fingerprint=
          NEW.human_action_provenance_fingerprint
       OR (
          human_action_capture_key_id=NEW.human_action_capture_key_id
          AND human_action_capture_reference=
              NEW.human_action_capture_reference
       )
)
BEGIN
    SELECT RAISE(ABORT, 'M3-E0 worker consent cannot be replaced');
END;
