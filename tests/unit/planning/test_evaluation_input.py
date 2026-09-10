from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from werkcrew_ai.field.models import AssignmentKind, TaskStatus
from werkcrew_ai.planning.evaluation_input import (
    CANDIDATE_ABSENCE,
    CandidateInducedImpactScope,
    CurrentCommitmentEvidence,
    CurrentM2AssignmentPrecondition,
    CurrentM2TaskPrecondition,
    DependencyEvidence,
    DependencyImpactReference,
    DependencyImpactScope,
    DirectCurrentImpactScope,
    HistoricalAssignmentEvidence,
    HistoricalJobRootEvidence,
    HistoricalSourceScope,
    ImpactCommitmentReference,
    M3EvaluationInput,
    M3EvaluationInputStorageError,
    M3EvaluationInputValidationError,
    PlanDayAssociationEvidence,
    deserialize_evaluation_input,
    semantic_evaluation_input_json,
    serialize_evaluation_input,
)
from werkcrew_ai.planning.m3_unavailability_impact import M3ImpactOutcome


DAY = date(2026, 9, 10)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def commitment(number, workers=("worker-1",), lineage=()):
    return CurrentCommitmentEvidence(
        commitment_id=f"commitment-{number}", job_id=f"job-{number}", task_id=f"task-{number}",
        task_definition_version=f"task-{number}-v1", source_handoff_id=f"handoff-{number}",
        source_revision=1, source_handoff_sha256=DIGEST_A, business_date=DAY,
        planned_worker_ids=workers, source_m2_assignment_ids=lineage,
    )


def task(number, status=TaskStatus.OPEN):
    return CurrentM2TaskPrecondition(
        job_id=f"job-{number}", job_execution_revision=2, job_root_sha256=DIGEST_A,
        task_id=f"task-{number}", task_definition_version=f"task-{number}-v1",
        task_route_sha256=DIGEST_B, source_handoff_id=f"handoff-{number}",
        source_revision=1, task_status=status,
    )


def historical():
    return HistoricalSourceScope(
        request_fingerprint=DIGEST_A, input_namespace="FIELD_EVENT", event_id="unavailable-1",
        server_event_id="server-unavailable-1", input_payload_sha256=DIGEST_B,
        reduction_proof_sha256=DIGEST_A, effect_ordinal=0, effect_sha256=DIGEST_B,
        historical_plan_result_sha256=DIGEST_A,
        assignments=(HistoricalAssignmentEvidence(
            assignment_id="assignment-1", job_id="job-1", task_id="task-1",
            task_definition_version="task-1-v1", assignment_kind=AssignmentKind.SINGLE,
            member_worker_ids=("worker-1",), plan_day_ids=("plan-day",), lead_worker_id=None,
            released_worker_ids=(), supersedes_assignment_id=None, task_status=TaskStatus.OPEN,
            task_source_handoff_id="handoff-1", task_source_revision=1,
            supersedes_task_id=None, job_execution_revision=0, job_root_sha256=DIGEST_A,
        ),),
        job_roots=(HistoricalJobRootEvidence(job_id="job-1", revision=0, content_sha256=DIGEST_A),),
    )


def evaluation_input(**changes):
    values = dict(
        historical_source_scope=historical(), company_plan_id="company-plan",
        base_plan_revision=3, base_plan_revision_id="m3-plan-revision-" + DIGEST_A,
        base_plan_revision_fingerprint=DIGEST_A, current_planning_scope_fingerprint=DIGEST_B,
        unavailable_worker_id="worker-1", business_date=DAY, plan_day_id="plan-day",
        plan_day_associations=(PlanDayAssociationEvidence(
            plan_day_id="plan-day", worker_id="worker-1", business_date=DAY),),
        current_active_commitments=(commitment(1, lineage=("assignment-1",)), commitment(2)),
        current_m2_task_preconditions=(task(1), task(2)),
        current_m2_assignment_preconditions=(CurrentM2AssignmentPrecondition(
            assignment_id="assignment-1", job_id="job-1", task_id="task-1",
            assignment_route_sha256=DIGEST_B, plan_day_ids=("plan-day",)),),
        direct_current_impact=DirectCurrentImpactScope(
            m3a_evaluation_id="m3-impact-" + DIGEST_A,
            m3a_evaluation_fingerprint=DIGEST_A, outcome=M3ImpactOutcome.REPLAN_REQUIRED,
            commitments=(ImpactCommitmentReference(
                commitment_id="commitment-1", job_id="job-1", task_id="task-1"),)),
        dependency_impact=DependencyImpactScope(
            edges=(DependencyEvidence(predecessor_task_id="task-1", successor_task_id="task-2",
                                      relation="FINISH_BEFORE_START", provenance_reference="explicit-edge"),),
            commitments=(DependencyImpactReference(commitment_id="commitment-2", job_id="job-2",
                task_id="task-2", originating_direct_task_ids=("task-1",)),)),
        candidate_induced_impact=CandidateInducedImpactScope(),
    )
    values.update(changes)
    return M3EvaluationInput(**values)


def test_canonical_round_trip_and_stable_identity():
    value = evaluation_input()
    raw = serialize_evaluation_input(value)
    assert deserialize_evaluation_input(raw) == value
    assert value.evaluation_input_id == "m3-evaluation-input-" + value.evaluation_input_fingerprint
    assert semantic_evaluation_input_json(value) != raw
    assert serialize_evaluation_input(deserialize_evaluation_input(raw)) == raw


def test_unordered_semantic_collections_are_canonical():
    original = evaluation_input()
    reordered = replace(original,
        current_active_commitments=list(reversed(original.current_active_commitments)),
        current_m2_task_preconditions=list(reversed(original.current_m2_task_preconditions)),
        plan_day_associations=list(original.plan_day_associations),
        dependency_impact=replace(original.dependency_impact,
            commitments=list(original.dependency_impact.commitments), edges=list(original.dependency_impact.edges)))
    assert reordered == original
    assert serialize_evaluation_input(reordered) == serialize_evaluation_input(original)


@pytest.mark.parametrize("change", [
    {"base_plan_revision": 4},
    {"base_plan_revision_fingerprint": DIGEST_B,
     "base_plan_revision_id": "m3-plan-revision-" + DIGEST_B},
    {"current_active_commitments": (
        commitment(1, lineage=("assignment-1",)), commitment(2, workers=())),
    },
    {"current_m2_task_preconditions": (task(1), task(2, TaskStatus.WAITING))},
    {"dependency_impact": DependencyImpactScope(edges=(), commitments=())},
])
def test_relevant_semantic_change_changes_identity(change):
    assert evaluation_input(**change).evaluation_input_id != evaluation_input().evaluation_input_id


def test_candidate_induced_absence_is_explicit_and_fixed():
    scope = evaluation_input().candidate_induced_impact
    assert scope.status == CANDIDATE_ABSENCE
    assert scope.candidate_fingerprint is None and scope.commitments == ()
    with pytest.raises(M3EvaluationInputValidationError):
        CandidateInducedImpactScope(status="CANDIDATE_PRESENT")
    with pytest.raises(M3EvaluationInputValidationError):
        CandidateInducedImpactScope(commitments=(ImpactCommitmentReference(
            commitment_id="c", job_id="j", task_id="t"),))


def test_done_task_cannot_be_present_in_current_active_commitments():
    with pytest.raises(M3EvaluationInputValidationError, match="active_done_task"):
        evaluation_input(current_m2_task_preconditions=(task(1, TaskStatus.DONE), task(2)))


def test_direct_and_dependency_scopes_must_be_disjoint_and_current():
    direct = evaluation_input().direct_current_impact
    with pytest.raises(M3EvaluationInputValidationError, match="impact_scope_overlap"):
        evaluation_input(dependency_impact=DependencyImpactScope(edges=(), commitments=(
            DependencyImpactReference(commitment_id="commitment-1", job_id="job-1", task_id="task-1",
                                      originating_direct_task_ids=("task-1",)),)))
    with pytest.raises(M3EvaluationInputValidationError, match="impact_commitments"):
        evaluation_input(direct_current_impact=replace(direct, commitments=(
            ImpactCommitmentReference(commitment_id="missing", job_id="job-x", task_id="task-x"),)))


@pytest.mark.parametrize("change,error", [
    ({"direct_current_impact": DirectCurrentImpactScope(
        m3a_evaluation_id="m3-impact-" + DIGEST_A,
        m3a_evaluation_fingerprint=DIGEST_A,
        outcome=M3ImpactOutcome.NO_REPLAN_REQUIRED,
        commitments=(ImpactCommitmentReference(
            commitment_id="commitment-1", job_id="job-1", task_id="task-1"),),
    )}, "direct_impact_outcome"),
    ({"direct_current_impact": DirectCurrentImpactScope(
        m3a_evaluation_id="wrong-id", m3a_evaluation_fingerprint=DIGEST_A,
        outcome=M3ImpactOutcome.REPLAN_REQUIRED,
        commitments=(ImpactCommitmentReference(
            commitment_id="commitment-1", job_id="job-1", task_id="task-1"),),
    )}, "m3a_evaluation_identity"),
    ({"dependency_impact": DependencyImpactScope(
        edges=(), commitments=(DependencyImpactReference(
            commitment_id="commitment-2", job_id="job-2", task_id="task-2",
            originating_direct_task_ids=("task-1",)),),
    )}, "dependency_impact_path"),
])
def test_impact_evidence_must_be_internally_coherent(change, error):
    with pytest.raises(M3EvaluationInputValidationError, match=error):
        evaluation_input(**change)


def test_current_task_assignment_worker_and_direct_bindings_are_enforced():
    with pytest.raises(M3EvaluationInputValidationError, match="current_task_binding"):
        evaluation_input(current_m2_task_preconditions=(
            replace(task(1), source_revision=2), task(2)))
    with pytest.raises(M3EvaluationInputValidationError, match="current_assignment_binding"):
        evaluation_input(current_m2_assignment_preconditions=(
            CurrentM2AssignmentPrecondition(
                assignment_id="assignment-1", job_id="job-2", task_id="task-2",
                assignment_route_sha256=DIGEST_B, plan_day_ids=("plan-day",)),))
    with pytest.raises(M3EvaluationInputValidationError, match="current_commitment_worker_day"):
        evaluation_input(current_active_commitments=(
            commitment(1, lineage=("assignment-1",)),
            replace(commitment(2), business_date=date(2026, 9, 11)),
        ))
    with pytest.raises(M3EvaluationInputValidationError, match="direct_worker_binding"):
        evaluation_input(
            plan_day_associations=(
                PlanDayAssociationEvidence(
                    plan_day_id="plan-day", worker_id="worker-1", business_date=DAY),
                PlanDayAssociationEvidence(
                    plan_day_id="replacement-day", worker_id="worker-2", business_date=DAY),
            ),
            current_active_commitments=(
                commitment(1, workers=("worker-2",), lineage=("assignment-1",)), commitment(2)),
        )


def test_revision_id_must_be_derived_from_revision_fingerprint():
    with pytest.raises(M3EvaluationInputValidationError, match="base_plan_revision_identity"):
        evaluation_input(base_plan_revision_id="m3-plan-revision-" + DIGEST_B)


@pytest.mark.parametrize("field_name,value", [
    ("company_plan_id", "\ud800"), ("base_plan_revision", True),
    ("current_planning_scope_fingerprint", "bad"), ("plan_day_associations", {}),
    ("current_active_commitments", None),
])
def test_malformed_input_is_typed(field_name, value):
    with pytest.raises(M3EvaluationInputValidationError):
        evaluation_input(**{field_name: value})


@pytest.mark.parametrize("target,field_name,value", [
    ("input", "current_active_commitments", []),
    ("input", "evaluation_input_fingerprint", DIGEST_B),
    ("commitment", "planned_worker_ids", ["worker-1"]),
    ("historical", "assignments", []),
    ("direct", "commitments", []),
    ("dependency", "edges", []),
    ("candidate", "status", "CANDIDATE_PRESENT"),
])
def test_reflective_mutation_is_rejected_before_serialization(target, field_name, value):
    item = evaluation_input()
    selected = {
        "input": item, "commitment": item.current_active_commitments[0],
        "historical": item.historical_source_scope, "direct": item.direct_current_impact,
        "dependency": item.dependency_impact, "candidate": item.candidate_induced_impact,
    }[target]
    object.__setattr__(selected, field_name, value)
    with pytest.raises(M3EvaluationInputValidationError):
        serialize_evaluation_input(item)
    assert getattr(selected, field_name) is value


@pytest.mark.parametrize("raw", ["{}", "null", "[]", "{", '{"schema_version":"wrong"}'])
def test_invalid_stored_document_fails_typed(raw):
    with pytest.raises(M3EvaluationInputStorageError):
        deserialize_evaluation_input(raw)


def test_dtos_are_frozen():
    value = evaluation_input()
    with pytest.raises(FrozenInstanceError):
        value.company_plan_id = "other"
