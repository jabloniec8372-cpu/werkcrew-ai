from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date
from itertools import permutations

import pytest

import werkcrew_ai.planning.m3_unavailability_impact as impact
from tests.unit.planning.test_m2_unavailable_bridge import _build, _reconstruction
from werkcrew_ai.field.models import TaskStatus
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.planning.m2_bridge import (
    M3FeasibilityEvaluationRequest,
    OperationalContextStatus,
    _request_fingerprint_fields,
)
from werkcrew_ai.planning.m3_unavailability_impact import (
    AUTHORITY_SCHEMA_VERSION,
    SCOPE_SCHEMA_VERSION,
    M3CurrentCommitment,
    M3CurrentPlanningScope,
    M3ImpactAssociationError,
    M3ImpactBoundaryError,
    M3ImpactFingerprintError,
    M3ImpactOutcome,
    M3ImpactValidationError,
    M3PlanningScopeAuthority,
    evaluate_unavailability_impact,
    serialize_current_planning_scope,
    serialize_unavailability_impact_result,
)


def request_fixture(*, empty=False, reference=None):
    historical = _reconstruction(roots=() if empty else None)
    historical = replace(historical, plan_day=replace(
        historical.plan_day, confirmed_plan_reference=reference,
    ))
    return _build(historical)[1]


def commitment_fixture(**changes):
    return M3CurrentCommitment(**{
        "commitment_id": "commitment-1", "job_id": "job-1", "task_id": "current-task",
        "assigned_worker_ids": ("worker-1", "worker-2"),
        "source_m2_assignment_ids": ("assignment-job-1",), **changes,
    })


def scope_fixture(request, *, commitments=None, **changes):
    return M3CurrentPlanningScope(**{
        "schema_version": SCOPE_SCHEMA_VERSION,
        "company_plan_id": "company-plan", "company_plan_revision": 7,
        "plan_day_id": "plan-1", "unavailable_worker_id": "worker-1",
        "business_date": date(2026, 9, 6),
        "source_request_fingerprint": request.request_fingerprint,
        "coverage_complete": True,
        "commitments": (commitment_fixture(),) if commitments is None else commitments,
        **changes,
    })


def trusted_fixture_authority(scope):
    # Test fixture stands in for an independent trusted producer. Never expose
    # this convenience as a production auto-authorize function for submissions.
    return M3PlanningScopeAuthority(
        schema_version=AUTHORITY_SCHEMA_VERSION,
        company_plan_id=scope.company_plan_id,
        company_plan_revision=scope.company_plan_revision,
        plan_day_id=scope.plan_day_id, unavailable_worker_id=scope.unavailable_worker_id,
        business_date=scope.business_date,
        source_request_fingerprint=scope.source_request_fingerprint,
        expected_snapshot_fingerprint=scope.snapshot_fingerprint,
    )


def evaluate(request, scope):
    return evaluate_unavailability_impact(request, scope, authority=trusted_fixture_authority(scope))


def resign_request(request, **changes):
    values = {item.name: getattr(request, item.name) for item in fields(request)}
    values.update(changes)
    values["request_fingerprint"] = _request_fingerprint_fields(
        values["schema_version"], values["evaluation_kind"], values["snapshot"],
        values["requires_fresh_evaluation"], values["authoritative_plan_mutation"],
    )
    return M3FeasibilityEvaluationRequest(**values)


def test_identical_replay_and_exact_content_identity():
    request = request_fixture()
    scope = scope_fixture(request)
    before_request, before_scope = replace(request), serialize_current_planning_scope(scope)
    results = [evaluate(request, scope) for _ in range(20)]
    assert all(result == results[0] for result in results)
    assert request == before_request
    assert serialize_current_planning_scope(scope) == before_scope
    result = results[0]
    assert result.outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert result.schema_version == "m3-unavailability-impact-result-v1"
    assert result.rule_version == "m3-unavailability-impact-v1"
    import json
    document = json.loads(serialize_unavailability_impact_result(result))
    fingerprint = document.pop("evaluation_fingerprint")
    assert document.pop("evaluation_id") == "m3-impact-" + fingerprint
    assert sha256_text(canonical_json(document)) == fingerprint


def test_all_input_orderings_preserve_scope_result_and_authority_pin():
    request = request_fixture()
    commitments = [
        commitment_fixture(commitment_id="z", job_id="job-z", task_id="task-a", source_m2_assignment_ids=("new-b", "new-a")),
        commitment_fixture(commitment_id="b", task_id="task-b"),
        commitment_fixture(commitment_id="a", task_id="task-b"),
    ]
    baseline = scope_fixture(request, commitments=commitments)
    authority = trusted_fixture_authority(baseline)
    expected = evaluate_unavailability_impact(request, baseline, authority=authority)
    for ordering in permutations(commitments):
        reordered = [replace(item,
            assigned_worker_ids=list(reversed(item.assigned_worker_ids)),
            source_m2_assignment_ids=list(reversed(item.source_m2_assignment_ids)),
        ) for item in ordering]
        scope = scope_fixture(request, commitments=reordered)
        assert scope == baseline
        assert evaluate_unavailability_impact(request, scope, authority=authority) == expected
    assert [item.commitment_id for item in expected.affected_commitments] == ["a", "b", "z"]
    assert expected.reason_codes == ("M2_ASSIGNMENT_LINEAGE_MISSING", "UNAVAILABLE_WORKER_STILL_ASSIGNED")
    assert expected.affected_commitments[0].matched_historical_assignment_ids == ("assignment-job-1",)


def test_empty_historical_and_complete_empty_current_scope():
    request = request_fixture(empty=True)
    result = evaluate(request, scope_fixture(request, commitments=()))
    assert result.outcome is M3ImpactOutcome.NO_LINKED_COMMITMENTS
    assert result.affected_commitments == ()


@pytest.mark.parametrize("lineage", [(), ("unknown-assignment",)])
def test_historical_empty_does_not_hide_current_worker_day_commitment(lineage):
    request = request_fixture(empty=True)
    result = evaluate(request, scope_fixture(request, commitments=(
        commitment_fixture(source_m2_assignment_ids=lineage),
    )))
    assert result.outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert "M2_ASSIGNMENT_LINEAGE_MISSING" in result.reason_codes


@pytest.mark.parametrize("commitments", [(), (commitment_fixture(assigned_worker_ids=("replacement",)),)])
def test_history_is_not_a_live_commitment_and_replacement_needs_no_replan(commitments):
    request = request_fixture()
    result = evaluate(request, scope_fixture(request, commitments=commitments))
    assert result.outcome is M3ImpactOutcome.NO_REPLAN_REQUIRED
    assert result.affected_commitments == ()


@pytest.mark.parametrize("status", list(TaskStatus))
def test_historical_status_and_release_do_not_override_current_m3_liveness(status):
    request = request_fixture()
    historical = replace(request.snapshot.assignments[0], task_status=status,
                         released_worker_ids=("worker-1",), supersedes_assignment_id="older")
    request = resign_request(request, snapshot=replace(request.snapshot, assignments=(historical,)))
    assert evaluate(request, scope_fixture(request)).outcome is M3ImpactOutcome.REPLAN_REQUIRED
    assert evaluate(request, scope_fixture(request, commitments=())).outcome is M3ImpactOutcome.NO_REPLAN_REQUIRED


@pytest.mark.parametrize("empty", [False, True])
def test_incomplete_coverage_has_precedence_and_retains_only_observed_impacts(empty):
    request = request_fixture(empty=empty)
    scope = scope_fixture(request, commitments=() if empty else None, coverage_complete=False)
    result = evaluate(request, scope)
    assert result.outcome is M3ImpactOutcome.INSUFFICIENT_INFORMATION
    assert "CURRENT_SCOPE_INCOMPLETE" in result.reason_codes
    assert len(result.affected_commitments) == (0 if empty else 1)


@pytest.mark.parametrize("reference", [None, "opaque-worker-plan", "company-plan", "opaque\nM2 reference"])
def test_reference_never_supplies_authority_but_does_not_prevent_valid_association(reference):
    request = request_fixture(reference=reference)
    scope = scope_fixture(request)
    with pytest.raises(M3ImpactAssociationError, match="M3_ASSOCIATION_REQUIRED"):
        evaluate_unavailability_impact(request, scope)
    assert evaluate(request, scope).outcome is M3ImpactOutcome.REPLAN_REQUIRED


@pytest.mark.parametrize("field_name,value", [
    ("company_plan_id", "wrong-company-plan"), ("company_plan_revision", 8),
    ("plan_day_id", "wrong-day"), ("unavailable_worker_id", "wrong-worker"),
    ("business_date", date(2026, 9, 7)), ("source_request_fingerprint", "f" * 64),
])
def test_resigned_scope_cannot_replace_independently_trusted_association(field_name, value):
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    wrong_scope = replace(scope, **{field_name: value})
    with pytest.raises(M3ImpactAssociationError) as failure:
        evaluate_unavailability_impact(request, wrong_scope, authority=authority)
    assert failure.value.code == "M3_ASSOCIATION_MISMATCH"
    assert failure.value.field_name == field_name


@pytest.mark.parametrize("field_name,value", [
    ("plan_day_id", "another-plan-day"), ("unavailable_worker_id", "another-worker"),
    ("business_date", date(2026, 9, 7)), ("source_request_fingerprint", "a" * 64),
])
def test_valid_authority_for_another_m2_request_is_rejected(field_name, value):
    request = request_fixture()
    scope = scope_fixture(request, **{field_name: value})
    with pytest.raises(M3ImpactAssociationError, match="M2_M3_ASSOCIATION_MISMATCH"):
        evaluate(request, scope)


def test_rehashed_commitment_or_completeness_tampering_is_not_authority():
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    for changed in (replace(scope, commitments=()), replace(scope, coverage_complete=False)):
        with pytest.raises(M3ImpactFingerprintError, match="UNAUTHORIZED_SCOPE_FINGERPRINT"):
            evaluate_unavailability_impact(request, changed, authority=authority)


def test_runtime_revalidates_request_and_scope_fingerprints():
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    object.__setattr__(scope, "snapshot_fingerprint", "0" * 64)
    with pytest.raises(M3ImpactFingerprintError, match="SCOPE_FINGERPRINT_MISMATCH"):
        evaluate_unavailability_impact(request, scope, authority=authority)
    scope = scope_fixture(request)
    object.__setattr__(request, "request_fingerprint", "0" * 64)
    with pytest.raises(M3ImpactFingerprintError, match="REQUEST_FINGERPRINT_MISMATCH"):
        evaluate_unavailability_impact(request, scope, authority=authority)


@pytest.mark.parametrize("changes", [
    {"schema_version": "wrong"}, {"evaluation_kind": "REPLAN"},
    {"requires_fresh_evaluation": False}, {"authoritative_plan_mutation": True},
    {"requires_fresh_evaluation": 1}, {"authoritative_plan_mutation": 0},
])
def test_valid_hash_does_not_make_invalid_source_semantics_valid(changes):
    request = resign_request(request_fixture(), **changes)
    with pytest.raises(M3ImpactValidationError):
        evaluate(request, scope_fixture(request))


@pytest.mark.parametrize("change", ["available", "context", "effect", "proof", "roots", "duplicate"])
def test_incoherent_source_snapshot_is_a_boundary_failure(change):
    request = request_fixture()
    snapshot = request.snapshot
    if change == "available":
        snapshot = replace(snapshot, plan_day=replace(snapshot.plan_day, worker_available=True))
    elif change == "context":
        snapshot = replace(snapshot, context_status=OperationalContextStatus.NO_LINKED_ASSIGNMENTS)
    elif change == "effect":
        snapshot = replace(snapshot, source=replace(snapshot.source, effect_type="PLAN_DAY_ACTIVATED"))
    elif change == "proof":
        snapshot = replace(snapshot, source=replace(snapshot.source, reduction_proof_schema_version="m2-reduction-input-proof-v1"))
    elif change == "roots":
        snapshot = replace(snapshot, job_root_preimages=())
    else:
        snapshot = replace(snapshot, assignments=(*snapshot.assignments, *snapshot.assignments))
    request = resign_request(request, snapshot=snapshot)
    with pytest.raises(M3ImpactValidationError):
        evaluate(request, scope_fixture(request))


@pytest.mark.parametrize("value", ["", " ", " padded", "bad\nidentity", 1, None])
def test_malformed_ids_are_typed_failures(value):
    with pytest.raises(M3ImpactValidationError):
        commitment_fixture(commitment_id=value)


@pytest.mark.parametrize("changes", [
    {"assigned_worker_ids": ("same", "same")},
    {"source_m2_assignment_ids": ("same", "same")},
    {"assigned_worker_ids": "worker-1"},
])
def test_duplicate_or_malformed_nested_ids_are_typed_failures(changes):
    with pytest.raises(M3ImpactValidationError):
        commitment_fixture(**changes)


def test_duplicate_commitment_identity_is_rejected_even_across_jobs():
    request = request_fixture()
    with pytest.raises(M3ImpactValidationError):
        scope_fixture(request, commitments=(commitment_fixture(), commitment_fixture(job_id="other")))


@pytest.mark.parametrize("changes", [
    {"company_plan_revision": -1}, {"company_plan_revision": True},
    {"coverage_complete": 1}, {"business_date": "2026-09-06"},
    {"schema_version": "wrong"}, {"source_request_fingerprint": "bad"},
])
def test_malformed_scope_is_not_insufficient_information(changes):
    with pytest.raises(M3ImpactValidationError):
        scope_fixture(request_fixture(), **changes)


@pytest.mark.parametrize("changes,code", [
    ({"job_id": "another-job"}, "CROSS_JOB_ASSIGNMENT_LINEAGE"),
    ({"assigned_worker_ids": ("other",), "source_m2_assignment_ids": ()}, "COMMITMENT_OUTSIDE_SCOPE"),
])
def test_incoherent_commitment_fails_even_with_incomplete_coverage(changes, code):
    request = request_fixture()
    scope = scope_fixture(request, commitments=(commitment_fixture(**changes),), coverage_complete=False)
    with pytest.raises(M3ImpactAssociationError, match=code):
        evaluate(request, scope)


def test_deep_immutability_and_defensive_tuple_normalization():
    request = request_fixture()
    workers, lineage = ["worker-2", "worker-1"], ["assignment-job-1"]
    commitment = commitment_fixture(assigned_worker_ids=workers, source_m2_assignment_ids=lineage)
    commitments = [commitment]
    scope = scope_fixture(request, commitments=commitments)
    authority = trusted_fixture_authority(scope)
    result = evaluate_unavailability_impact(request, scope, authority=authority)
    workers.append("not-in-scope")
    lineage.clear()
    commitments.clear()
    assert scope.commitments == (commitment,)
    assert commitment.assigned_worker_ids == ("worker-1", "worker-2")
    assert commitment.source_m2_assignment_ids == ("assignment-job-1",)
    for value, name in ((scope, "commitments"), (authority, "company_plan_id"),
                        (commitment, "assigned_worker_ids"), (result, "outcome"),
                        (result.affected_commitments[0], "task_id")):
        with pytest.raises(FrozenInstanceError):
            setattr(value, name, None)


def test_semantic_changes_produce_new_identity():
    request = request_fixture()
    scope = scope_fixture(request)
    baseline = evaluate(request, scope)
    for changed in (
        replace(scope, company_plan_revision=8),
        replace(scope, company_plan_id="another-authorized-plan"),
        replace(scope, coverage_complete=False),
        replace(scope, commitments=(replace(scope.commitments[0], task_id="new-task"),)),
    ):
        result = evaluate(request, changed)
        assert result.evaluation_id != baseline.evaluation_id
        assert result.evaluation_fingerprint != baseline.evaluation_fingerprint
    other_request = request_fixture(reference="later-historical-reference")
    result = evaluate(other_request, scope_fixture(other_request))
    assert result.outcome == baseline.outcome
    assert result.evaluation_fingerprint != baseline.evaluation_fingerprint


def test_no_direct_io_service_or_orchestration_dependencies():
    tree = ast.parse(inspect.getsource(impact))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {
        "__future__", "dataclasses", "datetime", "enum",
        "werkcrew_ai.field.models", "werkcrew_ai.field.serialization",
        "werkcrew_ai.planning.m2_bridge",
    }
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    forbidden = {"open", "hash", "eval", "exec", "connect", "execute", "getenv", "now", "today", "uuid4"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            assert name not in forbidden


def test_evaluation_does_not_invoke_io(monkeypatch):
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    import builtins
    import os
    import socket
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError("Evaluator attempted an external side effect")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        assert evaluate_unavailability_impact(request, scope, authority=authority).outcome is M3ImpactOutcome.REPLAN_REQUIRED


@pytest.mark.parametrize("target,field_name,value", [
    ("scope", "commitments", None),
    ("scope", "commitments", ({"commitment_id": "fake"},)),
    ("scope", "coverage_complete", "true"),
    ("authority", "expected_snapshot_fingerprint", None),
    ("authority", "company_plan_revision", True),
    ("commitment", "assigned_worker_ids", ("worker-1", "worker-1")),
])
def test_tampered_dto_shapes_always_fail_with_typed_errors(target, field_name, value):
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    target_value = {"scope": scope, "authority": authority, "commitment": scope.commitments[0]}[target]
    object.__setattr__(target_value, field_name, value)
    with pytest.raises(M3ImpactBoundaryError):
        evaluate_unavailability_impact(request, scope, authority=authority)


def test_scope_fingerprint_covers_every_public_semantic_field():
    request = request_fixture()
    scope = scope_fixture(request)
    changes = {
        "company_plan_id": "changed-plan", "company_plan_revision": 99,
        "plan_day_id": "changed-day", "unavailable_worker_id": "changed-worker",
        "business_date": date(2026, 9, 7), "source_request_fingerprint": "9" * 64,
        "coverage_complete": False, "commitments": (),
    }
    assert set(changes) == {item.name for item in fields(scope)} - {"schema_version", "snapshot_fingerprint"}
    for name, value in changes.items():
        assert replace(scope, **{name: value}).snapshot_fingerprint != scope.snapshot_fingerprint
    import json
    document = json.loads(serialize_current_planning_scope(scope))
    digest = document.pop("snapshot_fingerprint")
    assert sha256_text(canonical_json(document)) == digest
    document["schema_version"] = "future-schema"
    assert sha256_text(canonical_json(document)) != digest
    for name, value in {
        "commitment_id": "changed-id", "job_id": "changed-job", "task_id": "changed-task",
        "assigned_worker_ids": ("other",), "source_m2_assignment_ids": ("other",),
    }.items():
        changed = replace(scope.commitments[0], **{name: value})
        assert replace(scope, commitments=(changed,)).snapshot_fingerprint != scope.snapshot_fingerprint


def test_errors_have_stable_code_and_field_without_echoing_untrusted_identity():
    request = request_fixture()
    scope = scope_fixture(request)
    changed = replace(scope, company_plan_id="untrusted-company")
    errors = []
    for _ in range(3):
        with pytest.raises(M3ImpactBoundaryError) as failure:
            evaluate_unavailability_impact(request, changed, authority=trusted_fixture_authority(scope))
        errors.append(str(failure.value))
        assert failure.value.code == "M3_ASSOCIATION_MISMATCH"
        assert failure.value.field_name == "company_plan_id"
    assert errors == ["M3_ASSOCIATION_MISMATCH: company_plan_id"] * 3


@pytest.mark.parametrize("value", ["\ud800", "\udfff", "prefix\ud800suffix", "\ud800\udc00"])
def test_surrogate_company_plan_identity_is_a_typed_boundary_failure(value):
    with pytest.raises(M3ImpactValidationError) as failure:
        scope_fixture(request_fixture(), company_plan_id=value)
    assert str(failure.value) == "INVALID_VALUE: company_plan_id"


@pytest.mark.parametrize("field_name", [
    "commitment_id", "job_id", "task_id", "assigned_worker_ids", "source_m2_assignment_ids",
])
def test_surrogate_commitment_text_is_rejected_before_canonicalization(field_name):
    value = ("\ud800",) if field_name.endswith("_ids") else "\ud800"
    with pytest.raises(M3ImpactValidationError) as failure:
        commitment_fixture(**{field_name: value})
    assert failure.value.field_name == field_name


@pytest.mark.parametrize("target,field_name", [
    ("scope", "company_plan_id"), ("scope", "plan_day_id"),
    ("scope", "unavailable_worker_id"), ("authority", "company_plan_id"),
    ("plan", "worker_id"), ("plan", "plan_day_id"),
    ("plan", "confirmed_plan_reference"), ("source", "event_id"),
    ("source", "server_event_id"), ("lineage", "activation_event_id"),
    ("transition", "input_id"), ("assignment", "task_source_handoff_id"),
])
def test_reflected_surrogate_text_is_rejected_at_evaluation_boundary(target, field_name):
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    values = {
        "scope": scope, "authority": authority, "plan": request.snapshot.plan_day,
        "source": request.snapshot.source, "lineage": request.snapshot.activation_lineage,
        "transition": request.snapshot.activation_lineage.transitions[0],
        "assignment": request.snapshot.assignments[0],
    }
    object.__setattr__(values[target], field_name, "untrusted\ud800text")
    with pytest.raises(M3ImpactValidationError) as failure:
        evaluate_unavailability_impact(request, scope, authority=authority)
    assert "untrusted" not in str(failure.value)


@pytest.mark.parametrize("field_name,value", [
    ("commitment_id", {"untrusted": "id"}), ("job_id", []), ("task_id", None),
    ("assigned_worker_ids", ({"untrusted": "worker"},)),
    ("assigned_worker_ids", ["worker-1", "worker-2"]),
    ("source_m2_assignment_ids", [["untrusted"]]),
    ("source_m2_assignment_ids", None),
])
def test_nested_scope_content_is_checked_before_ordering_or_serialization(field_name, value):
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    object.__setattr__(scope.commitments[0], field_name, value)
    for consume in (
        lambda: replace(scope),
        lambda: serialize_current_planning_scope(scope),
        lambda: evaluate_unavailability_impact(request, scope, authority=authority),
    ):
        with pytest.raises(M3ImpactValidationError):
            consume()
        assert getattr(scope.commitments[0], field_name) is value


@pytest.mark.parametrize("field_name", [
    "assigned_worker_ids", "source_m2_assignment_ids", "matched_historical_assignment_ids",
])
def test_result_serializer_rejects_equal_list_substitution_without_normalizing(field_name):
    request = request_fixture()
    result = evaluate(request, scope_fixture(request))
    nested = result.affected_commitments[0]
    value = list(getattr(nested, field_name))
    fingerprint = result.evaluation_fingerprint
    object.__setattr__(nested, field_name, value)
    with pytest.raises(M3ImpactValidationError):
        serialize_unavailability_impact_result(result)
    assert getattr(nested, field_name) is value
    assert result.evaluation_fingerprint == fingerprint


@pytest.mark.parametrize("field_name,value", [
    ("commitment_id", {}), ("job_id", []), ("task_id", None),
    ("commitment_id", "\ud800"), ("job_id", "\ud800"), ("task_id", "\ud800"),
    ("assigned_worker_ids", None), ("assigned_worker_ids", ({"worker": 1},)),
    ("assigned_worker_ids", ("worker-1", "worker-1")),
    ("assigned_worker_ids", ("worker-2", "worker-1")),
    ("source_m2_assignment_ids", ("\ud800",)),
    ("source_m2_assignment_ids", ("assignment-job-1", "assignment-job-1")),
    ("matched_historical_assignment_ids", ("not-in-source-lineage",)),
    ("matched_historical_assignment_ids", ("assignment-job-1", "assignment-job-1")),
    ("matched_historical_assignment_ids", (True,)),
    ("matched_historical_assignment_ids", ("\ud800",)),
])
def test_result_nested_semantics_are_recursively_revalidated(field_name, value):
    request = request_fixture()
    result = evaluate(request, scope_fixture(request))
    object.__setattr__(result.affected_commitments[0], field_name, value)
    # Both direct reconstruction and public serialization consume nested DTOs.
    for consume in (lambda: replace(result), lambda: serialize_unavailability_impact_result(result)):
        with pytest.raises(M3ImpactValidationError):
            consume()


@pytest.mark.parametrize("field_name,mutation", [
    ("affected_commitments", lambda result: list(result.affected_commitments)),
    ("affected_commitments", lambda result: ({"commitment_id": "fake"},)),
    ("affected_commitments", lambda result: (commitment_fixture(),)),
    ("affected_commitments", lambda result: (list(result.affected_commitments),)),
    ("affected_commitments", lambda result: None),
    ("reason_codes", lambda result: list(result.reason_codes)),
    ("reason_codes", lambda result: ({"reason": "fake"},)),
    ("reason_codes", lambda result: ("\ud800",)),
    ("reason_codes", lambda result: (*result.reason_codes, *result.reason_codes)),
    ("reason_codes", lambda result: None),
    ("company_plan_id", lambda result: "\ud800"),
    ("schema_version", lambda result: []),
    ("rule_version", lambda result: {}),
    ("evaluation_id", lambda result: "\ud800"),
    ("evaluation_fingerprint", lambda result: []),
])
def test_result_collection_and_document_shapes_fail_with_typed_errors(field_name, mutation):
    request = request_fixture()
    result = evaluate(request, scope_fixture(request))
    value = mutation(result)
    object.__setattr__(result, field_name, value)
    with pytest.raises(M3ImpactValidationError):
        serialize_unavailability_impact_result(result)
    assert getattr(result, field_name) is value


def test_valid_serialization_retains_pre_hardening_content_fingerprints():
    request = request_fixture()
    scope = scope_fixture(request)
    result = evaluate(request, scope)
    assert scope.snapshot_fingerprint == "5c0433f6b03dcc289d801dc62b5125009c23f4d587125b77d2dcb5e20265b3fb"
    assert result.evaluation_fingerprint == "3a9997ebf41fdc88f06eaec3458487b0cdb56dde9da72f9bdd8687d3ff2b303c"
    assert result.evaluation_id == "m3-impact-" + result.evaluation_fingerprint
    scope_document = serialize_current_planning_scope(scope)
    result_document = serialize_unavailability_impact_result(result)
    for _ in range(3):
        assert serialize_current_planning_scope(scope) == scope_document
        assert serialize_unavailability_impact_result(evaluate(request, scope)) == result_document


@pytest.mark.parametrize("target,field_name", [
    ("scope", "commitments"), ("commitment", "task_id"),
    ("authority", "expected_snapshot_fingerprint"), ("request", "snapshot"),
    ("result", "reason_codes"), ("affected", "assigned_worker_ids"),
])
def test_incomplete_reflected_dtos_are_typed_boundary_failures(target, field_name):
    request = request_fixture()
    scope = scope_fixture(request)
    authority = trusted_fixture_authority(scope)
    result = evaluate_unavailability_impact(request, scope, authority=authority)
    values = {
        "request": request, "scope": scope, "authority": authority,
        "commitment": scope.commitments[0], "result": result,
        "affected": result.affected_commitments[0],
    }
    object.__delattr__(values[target], field_name)
    with pytest.raises(M3ImpactValidationError):
        if target in ("result", "affected"):
            serialize_unavailability_impact_result(result)
        else:
            evaluate_unavailability_impact(request, scope, authority=authority)
