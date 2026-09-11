from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

import werkcrew_ai.planning.bounded_feasibility as bounded
from werkcrew_ai.catalog.m8_demo import M8_CONFIGURATION
from werkcrew_ai.field.models import AssignmentKind, TaskStatus
from werkcrew_ai.field.serialization import sha256_text
from werkcrew_ai.planning.bounded_feasibility import (
    MAX_EVALUATED_CANDIDATES,
    MAX_MODIFIED_COMMITMENTS,
    MAX_SEED_PAIR_PROBES,
    CandidateVerdict,
    M3BoundedFeasibilityBindingError,
    M3BoundedFeasibilityService,
    M3BoundedFeasibilityValidationError,
    ReasonCode,
    SearchOutcome,
    generate_bounded_repair_candidates,
    serialize_bounded_feasibility_result,
)
from werkcrew_ai.planning.evaluation_input import (
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
    PlanDayAssociationEvidence,
    serialize_evaluation_input,
)
from werkcrew_ai.planning.feasibility_support import (
    AvailabilityEvidence,
    AvailabilityKnowledge,
    AvailabilityWindowEvidence,
    ConstraintKnowledge,
    FeasibilitySupportSnapshot,
    PlanScheduleSource,
    ReadinessEvidence,
    ReadinessState,
    RouteEvidence,
    RouteKnowledge,
    ScheduledPlacementEvidence,
    TaskConstraintSource,
    VehicleTechnicalEvidence,
    WorkerSkillLevelEvidence,
    WorkerTechnicalEvidence,
    m8_feasibility_configuration_fingerprint,
    serialize_feasibility_support,
)
from werkcrew_ai.planning.m3_unavailability_impact import M3ImpactOutcome


D = date(2026, 9, 14)
UTC = timezone.utc
DAY_START = datetime(2026, 9, 14, 6, tzinfo=UTC)
H = sha256_text("fixture")
PETER = "peter-berger"
ANNA = "anna-fischer"
STEFAN = "stefan-mueller"
THOMAS = "thomas-becker"
ANDREAS = "andreas-hoffmann"
CAR = "company-octavia"


def _commitment(
    letter: str,
    *,
    worker: str,
    source_assignment: bool = True,
) -> CurrentCommitmentEvidence:
    return CurrentCommitmentEvidence(
        commitment_id=f"commitment-{letter}",
        job_id="job-hero",
        task_id=f"task-{letter}",
        task_definition_version=f"task-{letter}-v1",
        source_handoff_id="handoff-hero",
        source_revision=1,
        source_handoff_sha256=H,
        business_date=D,
        planned_worker_ids=(worker,),
        source_m2_assignment_ids=(f"assignment-{letter}",) if source_assignment else (),
    )


def _evaluation(
    *,
    commitments: tuple[CurrentCommitmentEvidence, ...] | None = None,
    direct_letters: tuple[str, ...] = ("A",),
    dependencies: tuple[tuple[str, str], ...] = (),
    dependency_letters: tuple[str, ...] = (),
    crew_letters: tuple[str, ...] = (),
    unknown_kind_letters: tuple[str, ...] = (),
    done_letters: tuple[str, ...] = (),
) -> M3EvaluationInput:
    commitments = commitments or (_commitment("A", worker=PETER),)
    active = tuple(
        item
        for item in commitments
        if item.commitment_id.removeprefix("commitment-") not in done_letters
    )
    assignments = []
    for item in commitments:
        letter = item.commitment_id.removeprefix("commitment-")
        if letter in unknown_kind_letters or not item.source_m2_assignment_ids:
            continue
        assignments.append(
            HistoricalAssignmentEvidence(
                assignment_id=f"assignment-{letter}",
                job_id=item.job_id,
                task_id=item.task_id,
                task_definition_version=item.task_definition_version,
                assignment_kind=(
                    AssignmentKind.CREW
                    if letter in crew_letters
                    else AssignmentKind.SINGLE
                ),
                member_worker_ids=(
                    (PETER, ANNA) if letter in crew_letters else (PETER,)
                ),
                plan_day_ids=("plan-day-peter",),
                lead_worker_id=PETER if letter in crew_letters else None,
                released_worker_ids=(),
                supersedes_assignment_id=None,
                task_status=TaskStatus.OPEN,
                task_source_handoff_id=item.source_handoff_id,
                task_source_revision=item.source_revision,
                supersedes_task_id=None,
                job_execution_revision=3,
                job_root_sha256=H,
            )
        )
    historical = HistoricalSourceScope(
        request_fingerprint=H,
        input_namespace="FIELD_EVENT",
        event_id="unavailable-event",
        server_event_id="server-event",
        input_payload_sha256=H,
        reduction_proof_sha256=H,
        effect_ordinal=0,
        effect_sha256=H,
        historical_plan_result_sha256=H,
        assignments=tuple(assignments),
        job_roots=(
            (HistoricalJobRootEvidence(job_id="job-hero", revision=3, content_sha256=H),)
            if assignments
            else ()
        ),
    )
    active_ids = {item.commitment_id for item in active}
    direct = tuple(
        ImpactCommitmentReference(
            commitment_id=f"commitment-{letter}",
            job_id="job-hero",
            task_id=f"task-{letter}",
        )
        for letter in direct_letters
        if f"commitment-{letter}" in active_ids
    )
    edge_evidence = tuple(
        DependencyEvidence(
            predecessor_task_id=f"task-{before}",
            successor_task_id=f"task-{after}",
            relation="FINISH_BEFORE_START",
            provenance_reference=f"dependency-{before}-{after}",
        )
        for before, after in dependencies
    )
    dependency_refs = tuple(
        DependencyImpactReference(
            commitment_id=f"commitment-{letter}",
            job_id="job-hero",
            task_id=f"task-{letter}",
            originating_direct_task_ids=("task-A",),
        )
        for letter in dependency_letters
    )
    task_preconditions = tuple(
        CurrentM2TaskPrecondition(
            job_id=item.job_id,
            job_execution_revision=3,
            job_root_sha256=H,
            task_id=item.task_id,
            task_definition_version=item.task_definition_version,
            task_route_sha256=H,
            source_handoff_id=item.source_handoff_id,
            source_revision=item.source_revision,
            task_status=(
                TaskStatus.DONE
                if item.commitment_id.removeprefix("commitment-") in done_letters
                else TaskStatus.OPEN
            ),
        )
        for item in commitments
    )
    assignment_preconditions = tuple(
        CurrentM2AssignmentPrecondition(
            assignment_id=assignment_id,
            job_id=item.job_id,
            task_id=item.task_id,
            assignment_route_sha256=H,
            plan_day_ids=("plan-day-peter",),
        )
        for item in commitments
        for assignment_id in item.source_m2_assignment_ids
    )
    return M3EvaluationInput(
        historical_source_scope=historical,
        company_plan_id="company-plan",
        base_plan_revision=0,
        base_plan_revision_id="m3-plan-revision-" + H,
        base_plan_revision_fingerprint=H,
        current_planning_scope_fingerprint=H,
        unavailable_worker_id=PETER,
        business_date=D,
        plan_day_id="plan-day-peter",
        plan_day_associations=(
            PlanDayAssociationEvidence(plan_day_id="plan-day-andreas", worker_id=ANDREAS, business_date=D),
            PlanDayAssociationEvidence(plan_day_id="plan-day-anna", worker_id=ANNA, business_date=D),
            PlanDayAssociationEvidence(plan_day_id="plan-day-peter", worker_id=PETER, business_date=D),
            PlanDayAssociationEvidence(plan_day_id="plan-day-stefan", worker_id=STEFAN, business_date=D),
            PlanDayAssociationEvidence(plan_day_id="plan-day-thomas", worker_id=THOMAS, business_date=D),
        ),
        current_active_commitments=active,
        current_m2_task_preconditions=task_preconditions,
        current_m2_assignment_preconditions=assignment_preconditions,
        direct_current_impact=DirectCurrentImpactScope(
            m3a_evaluation_id="m3-impact-" + H,
            m3a_evaluation_fingerprint=H,
            outcome=(M3ImpactOutcome.REPLAN_REQUIRED if direct else M3ImpactOutcome.NO_REPLAN_REQUIRED),
            commitments=direct,
        ),
        dependency_impact=DependencyImpactScope(
            edges=edge_evidence,
            commitments=dependency_refs,
        ),
        candidate_induced_impact=CandidateInducedImpactScope(),
    )


def _skill_level(worker_id: str, sku: str) -> int | None:
    try:
        worker = next(item for item in M8_CONFIGURATION.crew if item.worker_id == worker_id)
    except StopIteration:
        return None
    return next(item.level for item in worker.skills if item.sku == sku)


def _support(
    evaluation: M3EvaluationInput,
    *,
    workers: tuple[str, ...] = (STEFAN,),
    sku_by_letter: dict[str, str] | None = None,
    starts: dict[str, datetime] | None = None,
    locations: dict[str, str] | None = None,
    worker_knowledge: dict[str, AvailabilityKnowledge] | None = None,
    worker_windows: dict[str, tuple[AvailabilityWindowEvidence, ...]] | None = None,
    readiness: dict[str, ReadinessState] | None = None,
    window: dict[str, tuple[ConstraintKnowledge, datetime | None, datetime | None]] | None = None,
    deadline: dict[str, tuple[ConstraintKnowledge, datetime | None]] | None = None,
    vehicle_knowledge: AvailabilityKnowledge = AvailabilityKnowledge.KNOWN,
    vehicle_windows: tuple[AvailabilityWindowEvidence, ...] | None = None,
    route_knowledge: RouteKnowledge = RouteKnowledge.KNOWN,
    route_seconds: int = 0,
    route_buffer_seconds: int = 0,
) -> FeasibilitySupportSnapshot:
    sku_by_letter = sku_by_letter or {}
    starts = starts or {}
    locations = locations or {}
    worker_knowledge = worker_knowledge or {}
    worker_windows = worker_windows or {}
    readiness = readiness or {}
    window = window or {}
    deadline = deadline or {}
    constraints = []
    placements = []
    readiness_evidence = []
    for index, commitment in enumerate(evaluation.current_active_commitments):
        letter = commitment.commitment_id.removeprefix("commitment-")
        sku = sku_by_letter.get(letter, "painting.wash")
        location = locations.get(letter, "same-site")
        window_state, window_start, window_end = window.get(
            letter, (ConstraintKnowledge.ABSENT, None, None)
        )
        deadline_state, deadline_at = deadline.get(
            letter, (ConstraintKnowledge.ABSENT, None)
        )
        constraint = TaskConstraintSource(
            job_id=commitment.job_id,
            task_id=commitment.task_id,
            task_definition_version=commitment.task_definition_version,
            source_handoff_id=commitment.source_handoff_id,
            source_revision_number=commitment.source_revision,
            m8_sku=sku,
            duration_seconds=3600,
            location_reference=location,
            location_fingerprint=sha256_text(location),
            customer_window_state=window_state,
            customer_window_start=window_start,
            customer_window_end=window_end,
            hard_deadline_state=deadline_state,
            hard_deadline=deadline_at,
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference=f"constraint-{letter}",
        )
        constraints.append(constraint)
        start = starts.get(letter, DAY_START + timedelta(hours=2 + index * 2))
        placements.append(
            ScheduledPlacementEvidence(
                commitment_id=commitment.commitment_id,
                job_id=commitment.job_id,
                task_id=commitment.task_id,
                task_definition_version=commitment.task_definition_version,
                business_date=start.astimezone(bounded.ZoneInfo("Europe/Berlin")).date(),
                worker_ids=commitment.planned_worker_ids,
                vehicle_id=(CAR if constraint.required_vehicle_class != "NONE" else None),
                planned_start=start,
                planned_end=start + timedelta(hours=1),
            )
        )
        state = readiness.get(letter, ReadinessState.READY)
        readiness_evidence.append(
            ReadinessEvidence(
                job_id=commitment.job_id,
                task_id=commitment.task_id,
                task_definition_version=commitment.task_definition_version,
                status=state,
                expected_at=(DAY_START + timedelta(hours=1) if state is ReadinessState.EXPECTED else None),
                blocker_references=(),
                source_record_id=(None if state is ReadinessState.UNKNOWN else f"readiness-{letter}"),
                source_fingerprint=(None if state is ReadinessState.UNKNOWN else H),
                source_revision=(None if state is ReadinessState.UNKNOWN else 0),
            )
        )
    schedule = PlanScheduleSource(
        company_plan_id=evaluation.company_plan_id,
        base_plan_revision=evaluation.base_plan_revision,
        base_plan_revision_id=evaluation.base_plan_revision_id,
        base_plan_revision_fingerprint=evaluation.base_plan_revision_fingerprint,
        business_timezone="Europe/Berlin",
        placements=tuple(placements),
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference="schedule",
    )
    required_skills = tuple(sorted({item.required_skill_key for item in constraints}))
    technical_workers = tuple(
        WorkerTechnicalEvidence(
            worker_id=worker_id,
            m8_worker_known=_skill_level(worker_id, required_skills[0]) is not None,
            skill_matrix_version=M8_CONFIGURATION.skill_matrix_version,
            license_b=(None if _skill_level(worker_id, required_skills[0]) is None else True),
            skill_levels=tuple(
                WorkerSkillLevelEvidence(
                    required_skill_key=skill,
                    level=_skill_level(worker_id, skill),
                )
                for skill in required_skills
            ),
        )
        for worker_id in workers
    )
    coverage_start = DAY_START - timedelta(hours=6)
    coverage_end = DAY_START + timedelta(days=3, hours=12)
    default_window = (
        AvailabilityWindowEvidence(start_at=coverage_start, end_at=coverage_end),
    )
    worker_availability = tuple(
        AvailabilityEvidence(
            subject_id=worker_id,
            knowledge=worker_knowledge.get(worker_id, AvailabilityKnowledge.KNOWN),
            coverage_start=(None if worker_knowledge.get(worker_id) is AvailabilityKnowledge.UNKNOWN else coverage_start),
            coverage_end=(None if worker_knowledge.get(worker_id) is AvailabilityKnowledge.UNKNOWN else coverage_end),
            available_windows=(
                ()
                if worker_knowledge.get(worker_id) is AvailabilityKnowledge.UNKNOWN
                else worker_windows.get(worker_id, default_window)
            ),
            source_record_id=(None if worker_knowledge.get(worker_id) is AvailabilityKnowledge.UNKNOWN else f"availability-{worker_id}"),
            source_fingerprint=(None if worker_knowledge.get(worker_id) is AvailabilityKnowledge.UNKNOWN else H),
            source_revision=(None if worker_knowledge.get(worker_id) is AvailabilityKnowledge.UNKNOWN else 0),
        )
        for worker_id in workers
    )
    vehicle = VehicleTechnicalEvidence(
        vehicle_id=CAR,
        kind="COMPANY",
        capabilities=("LIGHT",),
        assigned_worker_id=None,
    )
    vehicle_available = default_window if vehicle_windows is None else vehicle_windows
    vehicle_availability = AvailabilityEvidence(
        subject_id=CAR,
        knowledge=vehicle_knowledge,
        coverage_start=None if vehicle_knowledge is AvailabilityKnowledge.UNKNOWN else coverage_start,
        coverage_end=None if vehicle_knowledge is AvailabilityKnowledge.UNKNOWN else coverage_end,
        available_windows=() if vehicle_knowledge is AvailabilityKnowledge.UNKNOWN else vehicle_available,
        source_record_id=None if vehicle_knowledge is AvailabilityKnowledge.UNKNOWN else "vehicle-availability",
        source_fingerprint=None if vehicle_knowledge is AvailabilityKnowledge.UNKNOWN else H,
        source_revision=None if vehicle_knowledge is AvailabilityKnowledge.UNKNOWN else 0,
    )
    endpoints = tuple(sorted({(item.location_reference, item.location_fingerprint) for item in constraints}))
    routes = tuple(
        RouteEvidence(
            origin_reference=origin[0],
            origin_fingerprint=origin[1],
            destination_reference=destination[0],
            destination_fingerprint=destination[1],
            transport_mode="CAR",
            knowledge=route_knowledge,
            departure_time_basis=None if route_knowledge is RouteKnowledge.UNKNOWN else "PLANNED_DEPARTURE",
            distance_meters=None if route_knowledge is RouteKnowledge.UNKNOWN else 1000,
            travel_duration_seconds=None if route_knowledge is RouteKnowledge.UNKNOWN else route_seconds,
            buffer_seconds=None if route_knowledge is RouteKnowledge.UNKNOWN else route_buffer_seconds,
            buffer_rule_version=None if route_knowledge is RouteKnowledge.UNKNOWN else "buffer-v1",
            route_snapshot_version=None if route_knowledge is RouteKnowledge.UNKNOWN else "route-v1",
            provider=None if route_knowledge is RouteKnowledge.UNKNOWN else "offline-fixture",
            source_record_id=None if route_knowledge is RouteKnowledge.UNKNOWN else f"route-{origin[0]}-{destination[0]}",
            source_fingerprint=None if route_knowledge is RouteKnowledge.UNKNOWN else H,
            source_revision=None if route_knowledge is RouteKnowledge.UNKNOWN else 0,
        )
        for origin in endpoints
        for destination in endpoints
        if origin != destination
    )
    return FeasibilitySupportSnapshot(
        evaluation_input_id=evaluation.evaluation_input_id,
        evaluation_input_fingerprint=evaluation.evaluation_input_fingerprint,
        company_plan_id=evaluation.company_plan_id,
        base_plan_revision=evaluation.base_plan_revision,
        base_plan_revision_id=evaluation.base_plan_revision_id,
        base_plan_revision_fingerprint=evaluation.base_plan_revision_fingerprint,
        source_cut_id="m3-feasibility-source-cut-" + H,
        source_cut_generation=0,
        source_cut_fingerprint=H,
        m8_service_catalog_version=M8_CONFIGURATION.service_catalog_version,
        m8_planning_profile_version=M8_CONFIGURATION.sku_planning_profile_version,
        m8_skill_matrix_version=M8_CONFIGURATION.skill_matrix_version,
        m8_vehicle_policy_version=M8_CONFIGURATION.vehicle_policy_version,
        m8_configuration_fingerprint=m8_feasibility_configuration_fingerprint(),
        worker_registry_provenance_id="m3-feasibility-worker-registry-" + H,
        worker_registry_provenance_fingerprint=H,
        worker_registry_capture_id="m3-feasibility-worker-registry-capture-" + H,
        worker_registry_capture_fingerprint=H,
        worker_registry_capture_generation=0,
        worker_registry_revision=1,
        worker_registry_fingerprint=H,
        task_constraints=tuple(constraints),
        schedule=schedule,
        worker_technical_evidence=technical_workers,
        worker_availability=worker_availability,
        readiness=tuple(readiness_evidence),
        vehicle_technical_evidence=(vehicle,),
        vehicle_availability=(vehicle_availability,),
        routes=routes,
    )


def _codes(candidate) -> set[ReasonCode]:
    return {item.reason_code for item in candidate.rejection_trace}


def test_exact_skill_is_exact_and_hero_stefan_level_two_is_rejected() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        workers=(ANNA, STEFAN),
        sku_by_letter={"A": "waterproof.bath"},
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    stefan = next(
        item
        for item in result.candidates
        if item.proposed_worker_placements[0].worker_id == STEFAN
    )
    mismatch = next(
        item
        for item in stefan.rejection_trace
        if item.reason_code is ReasonCode.EXACT_SKILL_MISMATCH
    )
    assert mismatch.required_value == "waterproof.bath:3"
    assert mismatch.actual_value == "waterproof.bath:2"
    assert stefan.verdict is CandidateVerdict.REJECTED
    assert any(
        item.verdict is CandidateVerdict.FEASIBLE
        and item.proposed_worker_placements[0].worker_id == ANNA
        for item in result.candidates
    )


def test_unavailable_replacement_worker_is_rejected() -> None:
    evaluation = _evaluation()
    support = _support(evaluation, worker_windows={STEFAN: ()})

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.outcome is SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED
    assert all(ReasonCode.WORKER_UNAVAILABLE in _codes(item) for item in result.candidates)


def test_missing_worker_availability_is_unknown_not_available() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        worker_knowledge={STEFAN: AvailabilityKnowledge.UNKNOWN},
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.outcome is SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE
    assert ReasonCode.AVAILABILITY_UNKNOWN in _codes(result.candidates[0])


def test_overlap_sees_commitment_outside_modified_set_and_induces_impact() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=STEFAN),
    )
    evaluation = _evaluation(commitments=commitments)
    support = _support(
        evaluation,
        starts={"A": DAY_START + timedelta(hours=2), "B": DAY_START + timedelta(hours=2)},
    )

    result = generate_bounded_repair_candidates(evaluation, support)
    first = result.candidates[0]

    assert first.modified_commitment_ids == ("commitment-A",)
    assert ReasonCode.OVERLAP_CONFLICT in _codes(first)
    assert "commitment-B" in {
        item.commitment_id for item in first.impact.candidate_induced_impact
    }


def test_dependency_direction_is_validated_exactly() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=ANNA),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"),),
        dependency_letters=("B",),
    )
    support = _support(
        evaluation,
        starts={"A": DAY_START + timedelta(hours=2), "B": DAY_START + timedelta(hours=2, minutes=30)},
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert ReasonCode.DEPENDENCY_VIOLATION in _codes(result.candidates[0])
    assert result.candidates[0].affected_dependencies[0].predecessor_task_id == "task-A"
    assert result.candidates[0].affected_dependencies[0].successor_task_id == "task-B"


def test_transitive_dependency_impact_a_b_c_is_preserved() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=ANNA),
        _commitment("C", worker=ANNA),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C")),
        dependency_letters=("B", "C"),
    )
    support = _support(
        evaluation,
        starts={
            "A": DAY_START + timedelta(hours=2),
            "B": DAY_START + timedelta(hours=3),
            "C": DAY_START + timedelta(hours=4),
        },
    )

    candidate = generate_bounded_repair_candidates(evaluation, support).candidates[0]

    assert tuple(
        (item.predecessor_task_id, item.successor_task_id)
        for item in candidate.affected_dependencies
    ) == (("task-A", "task-B"), ("task-B", "task-C"))
    assert {item.commitment_id for item in candidate.impact.candidate_induced_impact} >= {
        "commitment-B",
        "commitment-C",
    }


def test_hero_anna_induces_b_and_bounded_move_can_repair_a_and_b_with_c_residual() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=ANNA),
        _commitment("C", worker=STEFAN),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C")),
        dependency_letters=("B", "C"),
    )
    support = _support(
        evaluation,
        workers=(ANNA, STEFAN),
        sku_by_letter={"A": "waterproof.bath", "B": "waterproof.bath"},
        starts={
            "A": DAY_START + timedelta(hours=2),
            "B": DAY_START + timedelta(hours=2),
            "C": DAY_START + timedelta(hours=5),
        },
    )

    result = generate_bounded_repair_candidates(evaluation, support)
    anna_a = next(
        item
        for item in result.candidates
        if item.modified_commitment_ids == ("commitment-A",)
        and item.proposed_worker_placements[0].worker_id == ANNA
    )
    repaired = next(
        item
        for item in result.candidates
        if item.modified_commitment_ids == ("commitment-A", "commitment-B")
        and item.verdict is CandidateVerdict.FEASIBLE
    )

    assert "commitment-B" in {
        item.commitment_id for item in anna_a.impact.candidate_induced_impact
    }
    assert {item.commitment_id for item in repaired.impact.restored_impact} == {
        "commitment-A",
        "commitment-B",
    }
    assert {item.commitment_id for item in repaired.impact.residual_unresolved_impact} == {
        "commitment-C"
    }
    assert not hasattr(repaired, "authority_decision")


def test_done_execution_reality_is_never_proposed_for_repair() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("C", worker=ANNA),
        _commitment("D", worker=PETER),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "D"), ("D", "C")),
        done_letters=("D",),
    )
    support = _support(evaluation)

    result = generate_bounded_repair_candidates(evaluation, support)

    assert all("commitment-D" not in item.modified_commitment_ids for item in result.candidates)
    assert all(not item.affected_dependencies for item in result.candidates)
    assert all(
        "commitment-C"
        not in {impact.commitment_id for impact in item.impact.candidate_induced_impact}
        for item in result.candidates
    )
    assert all(
        precondition.task_status is TaskStatus.DONE
        for precondition in evaluation.current_m2_task_preconditions
        if precondition.task_id == "task-D"
    )


@pytest.mark.parametrize(
    ("state", "reason"),
    (
        (ReadinessState.EXPECTED, ReasonCode.READINESS_NOT_READY),
        (ReadinessState.UNKNOWN, ReasonCode.READINESS_UNKNOWN),
    ),
)
def test_only_ready_passes_readiness_gate(state, reason) -> None:
    evaluation = _evaluation()
    support = _support(evaluation, readiness={"A": state})

    result = generate_bounded_repair_candidates(evaluation, support)

    assert reason in _codes(result.candidates[0])
    assert not any(item.verdict is CandidateVerdict.FEASIBLE for item in result.candidates)


def test_required_vehicle_unavailable_is_rejected() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        workers=(ANNA,),
        sku_by_letter={"A": "waterproof.bath"},
        vehicle_windows=(),
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert ReasonCode.VEHICLE_UNAVAILABLE in _codes(result.candidates[0])


def test_missing_required_vehicle_availability_is_unknown() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        workers=(ANNA,),
        sku_by_letter={"A": "waterproof.bath"},
        vehicle_knowledge=AvailabilityKnowledge.UNKNOWN,
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.outcome is SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE
    assert ReasonCode.VEHICLE_EVIDENCE_UNKNOWN in _codes(result.candidates[0])


@pytest.mark.parametrize(
    ("knowledge", "reason"),
    (
        (RouteKnowledge.KNOWN, ReasonCode.ROUTE_BUFFER_VIOLATION),
        (RouteKnowledge.UNKNOWN, ReasonCode.ROUTE_EVIDENCE_UNKNOWN),
    ),
)
def test_route_and_buffer_use_only_captured_evidence(knowledge, reason) -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=STEFAN),
    )
    evaluation = _evaluation(commitments=commitments)
    support = _support(
        evaluation,
        starts={"A": DAY_START + timedelta(hours=2), "B": DAY_START + timedelta(hours=4)},
        locations={"A": "site-a", "B": "site-b"},
        route_knowledge=knowledge,
        route_seconds=3600,
        route_buffer_seconds=1800,
    )

    candidate = generate_bounded_repair_candidates(evaluation, support).candidates[0]

    assert reason in _codes(candidate)


def test_hard_customer_window_violation_is_rejected() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        window={
            "A": (
                ConstraintKnowledge.KNOWN,
                DAY_START + timedelta(hours=5),
                DAY_START + timedelta(hours=7),
            )
        },
    )

    first = generate_bounded_repair_candidates(evaluation, support).candidates[0]

    assert ReasonCode.CUSTOMER_WINDOW_VIOLATION in _codes(first)


def test_hard_deadline_violation_is_rejected() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        deadline={
            "A": (ConstraintKnowledge.KNOWN, DAY_START + timedelta(hours=2, minutes=30))
        },
    )

    first = generate_bounded_repair_candidates(evaluation, support).candidates[0]

    assert ReasonCode.DEADLINE_VIOLATION in _codes(first)


def test_unknown_customer_window_and_deadline_remain_insufficient() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        window={"A": (ConstraintKnowledge.UNKNOWN, None, None)},
        deadline={"A": (ConstraintKnowledge.UNKNOWN, None)},
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.outcome is SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE
    assert {
        ReasonCode.CUSTOMER_WINDOW_EVIDENCE_UNKNOWN,
        ReasonCode.DEADLINE_EVIDENCE_UNKNOWN,
    }.issubset(_codes(result.candidates[0]))


def test_every_generated_interval_stays_inside_berlin_d_through_d_plus_two() -> None:
    evaluation = _evaluation()
    support = _support(
        evaluation,
        worker_windows={
            STEFAN: (
                AvailabilityWindowEvidence(
                    start_at=DAY_START + timedelta(days=2),
                    end_at=DAY_START + timedelta(days=3),
                ),
            )
        },
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert all(
        D <= placement.business_date <= D + timedelta(days=2)
        for candidate in result.candidates
        for placement in candidate.proposed_worker_placements
    )


def test_crew_loss_is_explicitly_unsupported_and_not_treated_as_single() -> None:
    commitment = replace(
        _commitment("A", worker=PETER), planned_worker_ids=(ANNA, PETER)
    )
    evaluation = _evaluation(commitments=(commitment,), crew_letters=("A",))
    support = _support(evaluation)

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.outcome is SearchOutcome.CREW_REPAIR_UNSUPPORTED
    assert result.evaluated_candidate_count == 0
    assert result.unsupported_commitment_ids == ("commitment-A",)
    assert result.search_trace[0].reason_code is ReasonCode.CREW_REPAIR_UNSUPPORTED


def test_unknown_single_kind_fails_as_insufficient_instead_of_inference() -> None:
    evaluation = _evaluation(unknown_kind_letters=("A",))
    support = _support(evaluation)

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.outcome is SearchOutcome.INSUFFICIENT_CRITICAL_EVIDENCE
    assert result.evaluated_candidate_count == 0
    assert result.insufficient_evidence_commitment_ids == ("commitment-A",)


def test_conflict_expansion_never_modifies_more_than_three_commitments() -> None:
    commitments = tuple(
        _commitment(letter, worker=PETER if letter == "A" else STEFAN)
        for letter in "ABCD"
    )
    evaluation = _evaluation(commitments=commitments)
    support = _support(
        evaluation,
        starts={letter: DAY_START + timedelta(hours=2) for letter in "ABCD"},
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert all(len(item.modified_commitment_ids) <= MAX_MODIFIED_COMMITMENTS for item in result.candidates)
    assert any(
        item.reason_code is ReasonCode.SEARCH_ENVELOPE_LIMIT
        and "more than 3" in item.detail
        for item in result.search_trace
    )


def _many_workers_support(evaluation: M3EvaluationInput) -> FeasibilitySupportSnapshot:
    worker_ids = tuple(f"worker-{index:02d}" for index in range(20))
    return _support(evaluation, workers=worker_ids)


def test_more_than_twelve_possibilities_is_bounded_deterministically() -> None:
    evaluation = _evaluation()
    support = _many_workers_support(evaluation)

    first = generate_bounded_repair_candidates(evaluation, support)
    second = generate_bounded_repair_candidates(evaluation, support)

    assert first.evaluated_candidate_count == MAX_EVALUATED_CANDIDATES
    assert first.search_envelope_exhausted is True
    assert first.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED
    assert tuple(item.candidate_id for item in first.candidates) == tuple(
        item.candidate_id for item in second.candidates
    )
    assert serialize_bounded_feasibility_result(first) == serialize_bounded_feasibility_result(second)


def test_search_guard_is_before_validation_and_not_after_large_expansion(monkeypatch) -> None:
    evaluation = _evaluation()
    support = _many_workers_support(evaluation)
    calls = 0
    original = bounded._evaluate_draft

    def counted(context, draft):
        nonlocal calls
        calls += 1
        return original(context, draft)

    monkeypatch.setattr(bounded, "_evaluate_draft", counted)

    result = generate_bounded_repair_candidates(evaluation, support)

    assert calls == MAX_EVALUATED_CANDIDATES == result.evaluated_candidate_count


def _large_pair_search(
    commitment_count: int,
    worker_count: int,
) -> tuple[M3EvaluationInput, FeasibilitySupportSnapshot]:
    letters = ("A",) + tuple(
        f"X{index:03d}" for index in range(1, commitment_count)
    )
    commitments = tuple(_commitment(letter, worker=PETER) for letter in letters)
    evaluation = _evaluation(
        commitments=commitments,
        direct_letters=letters,
        dependencies=tuple(zip(letters, letters[1:])),
    )
    starts = {
        letter: DAY_START + timedelta(minutes=index * 5)
        for index, letter in enumerate(letters)
    }
    workers = tuple(f"worker-{index:03d}" for index in range(worker_count))
    return evaluation, _support(
        evaluation,
        workers=workers,
        starts=starts,
    )


def test_twenty_by_twenty_search_streams_pairs_before_both_hard_guards(
    monkeypatch,
) -> None:
    evaluation, support = _large_pair_search(20, 20)
    anchor_calls: list[tuple[str, str]] = []
    evaluation_calls = 0
    original_anchor = bounded._anchor_starts
    original_evaluate = bounded._evaluate_draft

    def counted_anchor(context, commitment_id, worker_id):
        anchor_calls.append((commitment_id, worker_id))
        return original_anchor(context, commitment_id, worker_id)

    def counted_evaluate(context, draft):
        nonlocal evaluation_calls
        evaluation_calls += 1
        return original_evaluate(context, draft)

    monkeypatch.setattr(bounded, "_anchor_starts", counted_anchor)
    monkeypatch.setattr(bounded, "_evaluate_draft", counted_evaluate)

    first = generate_bounded_repair_candidates(evaluation, support)
    first_probe_order = tuple(anchor_calls)
    anchor_calls.clear()
    second = generate_bounded_repair_candidates(evaluation, support)

    assert evaluation_calls == 2 * MAX_EVALUATED_CANDIDATES
    assert first.evaluated_candidate_count == MAX_EVALUATED_CANDIDATES
    assert len(first_probe_order) <= MAX_SEED_PAIR_PROBES
    assert len(first_probe_order) < 20 * 20
    assert tuple(anchor_calls) == first_probe_order
    assert tuple(item.candidate_id for item in first.candidates) == tuple(
        item.candidate_id for item in second.candidates
    )
    assert serialize_bounded_feasibility_result(first) == serialize_bounded_feasibility_result(second)
    assert first.search_envelope_exhausted is True
    assert first.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED


def test_large_empty_pair_search_stops_at_exact_probe_budget_with_no_tail_scan(
    monkeypatch,
) -> None:
    evaluation, support = _large_pair_search(100, 100)
    anchor_calls: list[tuple[str, str]] = []

    def no_anchors(context, commitment_id, worker_id):
        anchor_calls.append((commitment_id, worker_id))
        return ()

    monkeypatch.setattr(bounded, "_anchor_starts", no_anchors)

    first = generate_bounded_repair_candidates(evaluation, support)
    first_probe_order = tuple(anchor_calls)
    anchor_calls.clear()
    second = generate_bounded_repair_candidates(evaluation, support)

    assert len(first_probe_order) == MAX_SEED_PAIR_PROBES
    assert first_probe_order == tuple(
        ("commitment-A", f"worker-{index:03d}")
        for index in range(MAX_SEED_PAIR_PROBES)
    )
    assert tuple(anchor_calls) == first_probe_order
    assert first.evaluated_candidate_count == 0
    assert first.search_envelope_exhausted is True
    assert first.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED
    assert serialize_bounded_feasibility_result(first) == serialize_bounded_feasibility_result(second)
    assert any(
        item.reason_code is ReasonCode.SEARCH_ENVELOPE_LIMIT
        and f"{MAX_SEED_PAIR_PROBES}-pair budget" in item.detail
        for item in first.search_trace
    )


def test_dependency_repair_a_b_is_generated_and_feasible_with_residual_c() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=THOMAS),
        _commitment("C", worker=THOMAS),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C")),
        dependency_letters=("B", "C"),
    )
    support = _support(
        evaluation,
        workers=(STEFAN, THOMAS),
        starts={
            "A": DAY_START + timedelta(hours=4, minutes=30),
            "B": DAY_START + timedelta(hours=3),
            "C": DAY_START + timedelta(hours=8),
        },
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert result.candidates[0].modified_commitment_ids == ("commitment-A",)
    assert ReasonCode.DEPENDENCY_VIOLATION in _codes(result.candidates[0])
    repaired = result.candidates[1]
    assert repaired.modified_commitment_ids == ("commitment-A", "commitment-B")
    assert repaired.verdict is CandidateVerdict.FEASIBLE
    placements = {item.commitment_id: item for item in repaired.proposed_worker_placements}
    assert placements["commitment-A"].proposed_start == DAY_START + timedelta(hours=4, minutes=30)
    assert placements["commitment-A"].proposed_end == DAY_START + timedelta(hours=5, minutes=30)
    assert placements["commitment-B"].proposed_start == placements["commitment-A"].proposed_end
    assert placements["commitment-B"].proposed_end == DAY_START + timedelta(hours=6, minutes=30)
    assert placements["commitment-B"].worker_id == THOMAS
    assert "commitment-B" in {
        item.commitment_id for item in repaired.impact.candidate_induced_impact
    }
    assert {item.commitment_id for item in repaired.impact.restored_impact} == {
        "commitment-A",
        "commitment-B",
    }
    assert {item.commitment_id for item in repaired.impact.residual_unresolved_impact} == {
        "commitment-C"
    }


def test_transitive_dependency_repair_a_b_c_reaches_legal_three_change_candidate() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=THOMAS),
        _commitment("C", worker=THOMAS),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C")),
        dependency_letters=("B", "C"),
    )
    support = _support(
        evaluation,
        workers=(STEFAN, THOMAS),
        starts={
            "A": DAY_START + timedelta(hours=4, minutes=30),
            "B": DAY_START + timedelta(hours=3),
            "C": DAY_START + timedelta(hours=4),
        },
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert tuple(item.modified_commitment_ids for item in result.candidates[:3]) == (
        ("commitment-A",),
        ("commitment-A", "commitment-B"),
        ("commitment-A", "commitment-B", "commitment-C"),
    )
    repaired = result.candidates[2]
    assert repaired.verdict is CandidateVerdict.FEASIBLE
    placements = {item.commitment_id: item for item in repaired.proposed_worker_placements}
    assert placements["commitment-B"].proposed_start == placements["commitment-A"].proposed_end
    assert placements["commitment-C"].proposed_start == placements["commitment-B"].proposed_end


def _four_change_dependency_result():
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=THOMAS),
        _commitment("C", worker=STEFAN),
        _commitment("D", worker=ANNA),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C"), ("C", "D")),
        dependency_letters=("B", "C", "D"),
    )
    a_start = DAY_START + timedelta(hours=4, minutes=30)
    support = _support(
        evaluation,
        workers=(ANDREAS, ANNA, STEFAN, THOMAS),
        sku_by_letter={
            "A": "drywall.partition",
            "B": "painting.wash",
            "C": "painting.wash",
            "D": "waterproof.bath",
        },
        starts={
            "A": a_start,
            "B": DAY_START + timedelta(hours=3),
            "C": DAY_START + timedelta(hours=4),
            "D": DAY_START + timedelta(hours=5),
        },
        window={
            "A": (ConstraintKnowledge.KNOWN, a_start, a_start + timedelta(hours=1))
        },
    )
    return generate_bounded_repair_candidates(evaluation, support)


def test_dependency_fourth_change_marks_top_level_search_exhausted() -> None:
    result = _four_change_dependency_result()

    assert all(
        len(item.modified_commitment_ids) <= MAX_MODIFIED_COMMITMENTS
        for item in result.candidates
    )
    assert any(
        item.modified_commitment_ids
        == ("commitment-A", "commitment-B", "commitment-C")
        and ReasonCode.DEPENDENCY_VIOLATION in _codes(item)
        for item in result.candidates
    )
    assert any(
        item.reason_code is ReasonCode.SEARCH_ENVELOPE_LIMIT
        and item.commitment_ids
        == ("commitment-A", "commitment-B", "commitment-C", "commitment-D")
        and "more than 3" in item.detail
        for item in result.search_trace
    )
    assert result.search_envelope_exhausted is True
    assert result.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED


def test_result_model_rejects_bw_false_exhaustion_reconstruction() -> None:
    result = _four_change_dependency_result()

    with pytest.raises(M3BoundedFeasibilityValidationError) as captured:
        replace(
            result,
            outcome=SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED,
            search_envelope_exhausted=False,
        )

    assert captured.value.field_name == "search_envelope_exhausted"


def test_result_model_rejects_all_rejected_when_exhaustion_is_true() -> None:
    result = _four_change_dependency_result()

    with pytest.raises(M3BoundedFeasibilityValidationError) as captured:
        replace(
            result,
            outcome=SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED,
            search_envelope_exhausted=True,
        )

    assert captured.value.field_name == "search_outcome"


def test_result_model_rejects_exhausted_outcome_with_false_flag() -> None:
    result = _four_change_dependency_result()

    with pytest.raises(M3BoundedFeasibilityValidationError) as captured:
        replace(result, search_envelope_exhausted=False)

    assert captured.value.field_name == "search_envelope_exhausted"


def test_result_model_accepts_canonical_exhausted_result() -> None:
    result = _four_change_dependency_result()

    reconstructed = replace(result)

    assert reconstructed == result
    assert reconstructed.result_id == result.result_id
    assert reconstructed.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED
    assert reconstructed.search_envelope_exhausted is True


def _feasible_truncated_result():
    evaluation = _evaluation()
    support = _support(
        evaluation,
        workers=(ANNA,) + tuple(f"worker-{index:02d}" for index in range(20)),
        sku_by_letter={"A": "waterproof.bath"},
    )
    return generate_bounded_repair_candidates(evaluation, support)


def test_result_model_accepts_feasible_result_with_truthful_truncation() -> None:
    result = _feasible_truncated_result()

    reconstructed = replace(result)

    assert reconstructed == result
    assert reconstructed.outcome is SearchOutcome.FEASIBLE_CANDIDATES_FOUND
    assert reconstructed.search_envelope_exhausted is True
    assert any(
        item.reason_code is ReasonCode.SEARCH_ENVELOPE_LIMIT
        for item in reconstructed.search_trace
    )


@pytest.mark.parametrize(
    "wrong_outcome",
    (
        SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED,
        SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED,
    ),
)
def test_result_model_rejects_wrong_outcome_when_feasible_candidate_exists(
    wrong_outcome,
) -> None:
    result = _feasible_truncated_result()

    with pytest.raises(M3BoundedFeasibilityValidationError) as captured:
        replace(result, outcome=wrong_outcome)

    assert captured.value.field_name == "search_outcome"


def test_result_model_accepts_conclusive_all_rejected_without_truncation() -> None:
    evaluation = _evaluation()
    result = generate_bounded_repair_candidates(
        evaluation,
        _support(evaluation, worker_windows={STEFAN: ()}),
    )

    reconstructed = replace(result)

    assert reconstructed == result
    assert reconstructed.outcome is SearchOutcome.ALL_EVALUATED_CANDIDATES_REJECTED
    assert reconstructed.search_envelope_exhausted is False
    assert all(
        item.reason_code is not ReasonCode.SEARCH_ENVELOPE_LIMIT
        for item in reconstructed.search_trace
    )


def test_result_model_rejects_exhaustion_flag_without_structured_trace_evidence() -> None:
    evaluation = _evaluation()
    result = generate_bounded_repair_candidates(
        evaluation,
        _support(evaluation, worker_windows={STEFAN: ()}),
    )

    with pytest.raises(M3BoundedFeasibilityValidationError) as captured:
        replace(
            result,
            outcome=SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED,
            search_envelope_exhausted=True,
        )

    assert captured.value.field_name == "search_envelope_exhausted"


def test_dependency_expansion_consumes_same_twelve_candidate_budget(monkeypatch) -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=THOMAS),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"),),
        dependency_letters=("B",),
    )
    synthetic_workers = tuple(f"worker-{index:02d}" for index in range(20))
    support = _support(
        evaluation,
        workers=(STEFAN, THOMAS) + synthetic_workers,
        starts={
            "A": DAY_START + timedelta(hours=4, minutes=30),
            "B": DAY_START + timedelta(hours=3),
        },
    )
    evaluation_calls = 0
    original = bounded._evaluate_draft

    def counted(context, draft):
        nonlocal evaluation_calls
        evaluation_calls += 1
        return original(context, draft)

    monkeypatch.setattr(bounded, "_evaluate_draft", counted)

    result = generate_bounded_repair_candidates(evaluation, support)

    assert evaluation_calls == MAX_EVALUATED_CANDIDATES
    assert result.evaluated_candidate_count == MAX_EVALUATED_CANDIDATES
    assert any(len(item.modified_commitment_ids) == 2 for item in result.candidates)
    assert result.search_envelope_exhausted is True
    assert result.outcome is SearchOutcome.FEASIBLE_CANDIDATES_FOUND


def test_overlap_then_dependency_uses_one_three_change_path() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=STEFAN),
        _commitment("C", worker=THOMAS),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C")),
        dependency_letters=("B", "C"),
    )
    support = _support(
        evaluation,
        workers=(STEFAN, THOMAS),
        starts={
            "A": DAY_START + timedelta(hours=4),
            "B": DAY_START + timedelta(hours=4),
            "C": DAY_START + timedelta(hours=5),
        },
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert ReasonCode.OVERLAP_CONFLICT in _codes(result.candidates[0])
    assert ReasonCode.DEPENDENCY_VIOLATION in _codes(result.candidates[1])
    assert result.candidates[2].modified_commitment_ids == (
        "commitment-A",
        "commitment-B",
        "commitment-C",
    )
    assert result.candidates[2].verdict is CandidateVerdict.FEASIBLE


def test_dependency_then_overlap_uses_one_three_change_path() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=THOMAS),
        _commitment("C", worker=THOMAS),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"),),
        dependency_letters=("B",),
    )
    support = _support(
        evaluation,
        workers=(STEFAN, THOMAS),
        starts={
            "A": DAY_START + timedelta(hours=4, minutes=30),
            "B": DAY_START + timedelta(hours=3),
            "C": DAY_START + timedelta(hours=6),
        },
    )

    result = generate_bounded_repair_candidates(evaluation, support)

    assert ReasonCode.DEPENDENCY_VIOLATION in _codes(result.candidates[0])
    assert ReasonCode.OVERLAP_CONFLICT in _codes(result.candidates[1])
    assert result.candidates[2].modified_commitment_ids == (
        "commitment-A",
        "commitment-B",
        "commitment-C",
    )
    assert result.candidates[2].verdict is CandidateVerdict.FEASIBLE


def test_changed_evaluation_input_changes_candidate_identity() -> None:
    evaluation = _evaluation()
    support = _support(evaluation)
    changed = replace(evaluation, current_planning_scope_fingerprint=sha256_text("changed-scope"))
    changed_support = replace(
        support,
        evaluation_input_id=changed.evaluation_input_id,
        evaluation_input_fingerprint=changed.evaluation_input_fingerprint,
    )

    first = generate_bounded_repair_candidates(evaluation, support)
    second = generate_bounded_repair_candidates(changed, changed_support)

    assert first.candidates[0].candidate_id != second.candidates[0].candidate_id


def test_changed_support_snapshot_changes_candidate_identity() -> None:
    evaluation = _evaluation()
    support = _support(evaluation)
    availability = support.worker_availability[0]
    changed_availability = replace(
        availability,
        source_fingerprint=sha256_text("changed-availability"),
    )
    changed_support = replace(support, worker_availability=(changed_availability,))

    first = generate_bounded_repair_candidates(evaluation, support)
    second = generate_bounded_repair_candidates(evaluation, changed_support)

    assert first.candidates[0].candidate_id != second.candidates[0].candidate_id


def test_partial_repair_keeps_transitive_residual_impact_explicit() -> None:
    commitments = (
        _commitment("A", worker=PETER),
        _commitment("B", worker=ANNA),
        _commitment("C", worker=ANNA),
    )
    evaluation = _evaluation(
        commitments=commitments,
        dependencies=(("A", "B"), ("B", "C")),
        dependency_letters=("B", "C"),
    )
    support = _support(
        evaluation,
        starts={
            "A": DAY_START + timedelta(hours=2),
            "B": DAY_START + timedelta(hours=3),
            "C": DAY_START + timedelta(hours=4),
        },
    )

    feasible = next(
        item
        for item in generate_bounded_repair_candidates(evaluation, support).candidates
        if item.verdict is CandidateVerdict.FEASIBLE
    )

    assert {item.commitment_id for item in feasible.impact.restored_impact} == {"commitment-A"}
    assert {item.commitment_id for item in feasible.impact.residual_unresolved_impact} == {
        "commitment-B",
        "commitment-C",
    }


def test_bounded_exhaustion_never_claims_global_impossibility() -> None:
    evaluation = _evaluation()
    result = generate_bounded_repair_candidates(
        evaluation, _many_workers_support(evaluation)
    )

    assert result.outcome is SearchOutcome.SEARCH_ENVELOPE_EXHAUSTED
    assert result.global_solution_status == "NOT_EVALUATED_GLOBALLY"


def test_mismatched_support_fails_closed_before_candidate_generation() -> None:
    evaluation = _evaluation()
    other = replace(evaluation, current_planning_scope_fingerprint=sha256_text("other"))
    support = _support(other)

    with pytest.raises(M3BoundedFeasibilityBindingError) as captured:
        generate_bounded_repair_candidates(evaluation, support)

    assert captured.value.code == "SUPPORT_EVALUATION_INPUT_MISMATCH"


def test_read_only_persisted_adapter_and_pure_search_have_no_side_effects() -> None:
    evaluation = _evaluation()
    support = _support(evaluation)

    class Reader:
        def __init__(self):
            self.calls = []

        def get_evaluation_input(self, evaluation_input_id):
            self.calls.append(("evaluation", evaluation_input_id))
            return evaluation

        def get_feasibility_support(self, support_snapshot_id):
            self.calls.append(("support", support_snapshot_id))
            return support

    reader = Reader()
    before_evaluation = serialize_evaluation_input(evaluation)
    before_support = serialize_feasibility_support(support)

    result = M3BoundedFeasibilityService(reader).evaluate(
        evaluation.evaluation_input_id,
        support.support_snapshot_id,
    )

    assert reader.calls == [
        ("evaluation", evaluation.evaluation_input_id),
        ("support", support.support_snapshot_id),
    ]
    assert serialize_evaluation_input(evaluation) == before_evaluation
    assert serialize_feasibility_support(support) == before_support
    assert result.base_plan_revision == evaluation.base_plan_revision
    assert not hasattr(result, "price")
    assert not hasattr(result, "authority_decision")
    assert not hasattr(result, "apply")
    assert not hasattr(result, "publication")
