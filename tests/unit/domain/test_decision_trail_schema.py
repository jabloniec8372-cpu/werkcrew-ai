"""Executable contract for the public Decision Trail JSON Schema."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


SCHEMA_PATH = Path(__file__).parents[3] / "schemas" / "decision-trail.schema.json"


def _record() -> dict:
    return {
        "decision_id": "decision-dom-1017",
        "decision_object_id": "move-team-b-to-dom",
        "decision_scope": "DOM protection",
        "timestamp": "2026-09-02T10:17:00+02:00",
        "trigger_event": "rain-risk-review",
        "versions": {
            "constitution": "1.1",
            "matrix": "1.1",
            "policy": None,
            "engine": "decision-semantics-v1.1",
        },
        "facts_snapshot_hash": "sha256:0123456789abcdef",
        "facts": [
            {
                "fact_id": "dom-rain-risk",
                "fact_scope": "DOM",
                "value": "protection needed",
                "quality": "SUFFICIENT",
                "sources": ["site-report"],
                "observed_at": "2026-09-02T10:15:00+02:00",
                "materiality": "MATERIAL",
                "dependent_actions": ["move-team-b-to-dom"],
            }
        ],
        "axis_assessments": [
            {
                "axis": "B",
                "applicability": "APPLICABLE",
                "assessment_status": "ASSESSED",
                "state": "FORCED",
                "fact_ids": ["dom-rain-risk"],
            }
        ],
        "promise_assessment": {
            "technical_feasibility": "FEASIBLE",
            "policy_relation": "SOFT_EXCEPTION",
            "override_status": "APPROVED",
            "critical_data_quality": "SUFFICIENT",
            "safety_status": "CLEAR",
            "permission_status": "VERIFIED",
            "consent_status": "NOT_REQUIRED",
            "execution_authorization": "ALLOWED",
            "derived_state": "FORCED",
            "named_conditions": [],
        },
        "health_recovery": None,
        "evaluated_value_level": None,
        "authority": {
            "required_authority": "OWNER",
            "actor_role": "OWNER",
            "authority_basis": "soft-exception-v1",
        },
        "consent": {"required": False, "status": "NOT_REQUIRED", "scope": None},
        "objections": [],
        "execution": {
            "disposition": "EXECUTE_WITH_RECORDED_RISK",
            "allowed_actions": ["move-team-b-to-dom"],
            "blocked_actions": [],
            "reason_codes": ["OWNER_APPROVED_SOFT_EXCEPTION"],
        },
        "override": {
            "status": "APPROVED",
            "scope": "move-team-b-to-dom",
            "owner_statement": "Approved for DOM protection only",
            "expires_at": "2026-09-02T18:00:00+02:00",
        },
        "communication": {
            "draft_text": {
                "record_type": "DRAFT_TEXT",
                "text_hash": "sha256:draft0123456789",
                "created_at": "2026-09-02T10:17:00+02:00",
                "approved_by_system": False,
            },
            "approved_text": None,
            "sent_message": None,
            "voice_validation": "REJECTED",
            "claims": [],
            "actual_claims": [
                {
                    "record_type": "ACTUAL_CLAIM",
                    "claim_id": "owner-loft-promise",
                    "speaker_role": "OWNER",
                    "speech_act": "PROMISE",
                    "text_hash": "sha256:actual01234567",
                    "recorded_at": "2026-09-02T10:18:00+02:00",
                    "approved_by_system": False,
                    "contradiction_fact_ids": ["piotr-unavailable"],
                }
            ],
        },
        "outcome_event_ids": [],
    }


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_schema_is_valid_draft_2020_12_and_accepts_complete_record() -> None:
    _validator().validate(_record())


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("technical_feasibility", "UNKNOWN"),
        ("policy_relation", "HARD_BLOCK"),
        ("critical_data_quality", "STALE"),
        ("safety_status", "HARD_BLOCK"),
        ("permission_status", "MISSING"),
        ("consent_status", "ABSENT"),
    ],
)
def test_schema_rejects_forced_when_a_required_gate_fails(
    field: str,
    invalid_value: str,
) -> None:
    record = _record()
    record["promise_assessment"][field] = invalid_value

    with pytest.raises(ValidationError):
        _validator().validate(record)


def test_schema_never_allows_actual_claim_to_look_system_approved() -> None:
    record = deepcopy(_record())
    record["communication"]["actual_claims"][0]["approved_by_system"] = True

    with pytest.raises(ValidationError):
        _validator().validate(record)
