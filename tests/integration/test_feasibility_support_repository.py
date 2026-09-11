from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from tests.integration.test_current_plan_bootstrap import (
    m2_rows,
    next_revision,
    setup,
)
from tests.integration.test_evaluation_input_repository import m3_plan_rows
from tests.integration.test_m3_unavailability_impact_integration import NOW
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.field.serialization import (
    canonical_json,
    serialize_worker_registry,
    sha256_text,
)
import werkcrew_ai.planning.feasibility_support as feasibility_contract
from werkcrew_ai.planning.feasibility_repository import (
    M3FeasibilitySupportRepository,
)
from werkcrew_ai.planning.bounded_feasibility import (
    M3BoundedFeasibilityService,
    serialize_bounded_feasibility_result,
)
from werkcrew_ai.planning.feasibility_support import (
    AvailabilityKnowledge,
    AvailabilityWindowEvidence,
    ConstraintKnowledge,
    FeasibilitySourceKind,
    M3FeasibilitySupportConflictError,
    M3FeasibilitySupportStorageError,
    PlanScheduleSource,
    ReadinessState,
    RouteKnowledge,
    RouteSource,
    ScheduledPlacementEvidence,
    SourceSelection,
    SourceSelectionCut,
    TaskConstraintSource,
    TaskReadinessSource,
    VehicleAvailabilitySource,
    WorkerAvailabilitySource,
    WorkerRegistryProvenance,
    deserialize_feasibility_support,
    restore_feasibility_support,
    serialize_feasibility_support,
    snapshot_semantic_json,
    source_cut_semantic_json,
    source_semantic_json,
)


def task_source(m2, task_number: int, **changes) -> TaskConstraintSource:
    definition = m2.get_job_execution_root(f"job-{task_number}").tasks[0].definition
    values = dict(
        job_id=definition.job_id,
        task_id=definition.task_id,
        task_definition_version=definition.definition_version,
        source_handoff_id=definition.source_handoff_id,
        source_revision_number=definition.source_revision,
        m8_sku="painting.wash" if task_number == 1 else "painting.strip",
        duration_seconds=3600,
        location_reference=f"job-{task_number}-location",
        location_fingerprint=sha256_text(f"job-{task_number}-location-v1"),
        customer_window_state=(
            ConstraintKnowledge.KNOWN
            if task_number == 1
            else ConstraintKnowledge.UNKNOWN
        ),
        customer_window_start=NOW + timedelta(hours=1) if task_number == 1 else None,
        customer_window_end=NOW + timedelta(hours=5) if task_number == 1 else None,
        hard_deadline_state=(
            ConstraintKnowledge.KNOWN
            if task_number == 1
            else ConstraintKnowledge.ABSENT
        ),
        hard_deadline=NOW + timedelta(hours=7) if task_number == 1 else None,
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference=f"trusted-task-{task_number}-constraints-v1",
    )
    values.update(changes)
    return TaskConstraintSource(**values)


def schedule_source(revision, **changes) -> PlanScheduleSource:
    placements = []
    for index, commitment in enumerate(revision.commitments):
        start = NOW + timedelta(hours=1 + index * 2)
        placements.append(
            ScheduledPlacementEvidence(
                commitment_id=commitment.commitment_id,
                job_id=commitment.job_id,
                task_id=commitment.task_id,
                task_definition_version=commitment.task_definition_version,
                business_date=commitment.business_date,
                worker_ids=commitment.planned_worker_ids,
                vehicle_id=(
                    "company-caddy-maxi" if commitment.task_id == "task-2" else None
                ),
                planned_start=start,
                planned_end=start + timedelta(hours=1),
            )
        )
    values = dict(
        company_plan_id=revision.company_plan_id,
        base_plan_revision=revision.revision,
        base_plan_revision_id=revision.revision_id,
        base_plan_revision_fingerprint=revision.fingerprint,
        business_timezone="Europe/Berlin",
        placements=tuple(placements),
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference="trusted-company-schedule-v1",
    )
    values.update(changes)
    return PlanScheduleSource(**values)


def prerequisites(path: Path):
    b0, company, revision, request, m2 = setup(path)
    b0.import_revision(company, revision)
    repository = M3FeasibilitySupportRepository(path)
    evaluation = repository.record_evaluation_input(request)
    constraints = tuple(
        repository.record_task_constraint(task_source(m2, number))
        for number in (1, 2)
    )
    schedule = repository.record_plan_schedule(schedule_source(revision))
    return repository, evaluation, revision, m2, constraints, schedule


def test_m3c_service_reloads_exact_persisted_cut_and_is_restart_read_only(tmp_path):
    path = tmp_path / "m3c-read-only.sqlite3"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    before_m2 = m2_rows(path)
    before_m3 = m3_plan_rows(path)
    with sqlite3.connect(path) as connection:
        before_evidence = tuple(
            connection.execute(
                f"SELECT * FROM {table} ORDER BY 1"
            ).fetchall()
            for table in (
                "m3_evaluation_inputs",
                "m3_feasibility_source_records",
                "m3_feasibility_support_snapshots",
            )
        )

    first = M3BoundedFeasibilityService(repository).evaluate(
        evaluation.evaluation_input_id,
        support.support_snapshot_id,
    )
    restarted = M3FeasibilitySupportRepository(path)
    replay = M3BoundedFeasibilityService(restarted).evaluate(
        evaluation.evaluation_input_id,
        support.support_snapshot_id,
    )

    assert serialize_bounded_feasibility_result(first) == serialize_bounded_feasibility_result(replay)
    assert first.source_evaluation_input_id == evaluation.evaluation_input_id
    assert first.source_support_snapshot_id == support.support_snapshot_id
    assert m2_rows(path) == before_m2
    assert m3_plan_rows(path) == before_m3
    with sqlite3.connect(path) as connection:
        after_evidence = tuple(
            connection.execute(
                f"SELECT * FROM {table} ORDER BY 1"
            ).fetchall()
            for table in (
                "m3_evaluation_inputs",
                "m3_feasibility_source_records",
                "m3_feasibility_support_snapshots",
            )
        )
    assert after_evidence == before_evidence


def advance_worker_registry(m2, worker_ids: tuple[str, ...]):
    current = m2.get_worker_registry()
    updated = WorkerIdentityRegistry(
        worker_ids=worker_ids,
        registry_revision=current.registry_revision + 1,
    )
    raw = serialize_worker_registry(updated)
    with m2.transaction() as connection:
        existing = {
            row["worker_id"]
            for row in connection.execute(
                "SELECT worker_id FROM m2_worker_identities"
            ).fetchall()
        }
        connection.executemany(
            "INSERT INTO m2_worker_identities(worker_id) VALUES(?)",
            ((worker_id,) for worker_id in set(worker_ids) - existing),
        )
        connection.execute(
            """
            UPDATE m2_worker_registry
            SET registry_revision=?, canonical_root_json=?, content_sha256=?
            WHERE registry_key='GLOBAL'
            """,
            (updated.registry_revision, raw, sha256_text(raw)),
        )
        connection.executemany(
            "DELETE FROM m2_worker_identities WHERE worker_id=?",
            ((worker_id,) for worker_id in existing - set(worker_ids)),
        )
    return updated


def insert_refingerprinted_support(
    repository: M3FeasibilitySupportRepository,
    trusted,
    mutate,
    *,
    bind_corrupt_sources: bool = False,
    source_cut: SourceSelectionCut | None = None,
    bypass_exact_capture: bool = False,
) -> str:
    document = json.loads(snapshot_semantic_json(trusted))
    mutate(document)
    if source_cut is not None:
        document["source_cut_id"] = source_cut.source_cut_id
        document["source_cut_generation"] = source_cut.source_cut_generation
        document["source_cut_fingerprint"] = source_cut.source_cut_fingerprint
        document["worker_registry_provenance_id"] = (
            source_cut.worker_registry_provenance_id
        )
        document["worker_registry_provenance_fingerprint"] = (
            source_cut.worker_registry_provenance_fingerprint
        )
        document["worker_registry_capture_id"] = (
            source_cut.worker_registry_capture_id
        )
        document["worker_registry_capture_fingerprint"] = (
            source_cut.worker_registry_capture_fingerprint
        )
        document["worker_registry_capture_generation"] = (
            source_cut.worker_registry_capture_generation
        )
    raw = canonical_json(document)
    fingerprint = sha256_text(raw)
    snapshot_id = "m3-feasibility-support-" + fingerprint
    corrupt = restore_feasibility_support(raw, fingerprint, snapshot_id)
    with repository.transaction() as connection:
        if source_cut is not None:
            if bypass_exact_capture:
                connection.execute(
                    "DROP TRIGGER m3_feasibility_source_cuts_exact_capture"
                )
            connection.execute(
                """
                INSERT INTO m3_feasibility_source_selection_cuts(
                    source_cut_id, source_cut_generation, source_cut_fingerprint,
                    schema_version, evaluation_input_id,
                    evaluation_input_fingerprint, company_plan_id,
                    base_plan_revision, base_plan_revision_id,
                    base_plan_revision_fingerprint,
                    worker_registry_provenance_id,
                    worker_registry_provenance_fingerprint,
                    worker_registry_capture_id,
                    worker_registry_capture_fingerprint,
                    worker_registry_capture_generation,
                    canonical_semantic_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source_cut.source_cut_id,
                    source_cut.source_cut_generation,
                    source_cut.source_cut_fingerprint,
                    source_cut.schema_version,
                    source_cut.evaluation_input_id,
                    source_cut.evaluation_input_fingerprint,
                    source_cut.company_plan_id,
                    source_cut.base_plan_revision,
                    source_cut.base_plan_revision_id,
                    source_cut.base_plan_revision_fingerprint,
                    source_cut.worker_registry_provenance_id,
                    source_cut.worker_registry_provenance_fingerprint,
                    source_cut.worker_registry_capture_id,
                    source_cut.worker_registry_capture_fingerprint,
                    source_cut.worker_registry_capture_generation,
                    source_cut_semantic_json(source_cut),
                ),
            )
        connection.execute(
            """
            INSERT INTO m3_feasibility_support_snapshots(
                support_snapshot_id, support_fingerprint, schema_version,
                rule_version, evaluation_input_id, evaluation_input_fingerprint,
                company_plan_id, base_plan_revision, base_plan_revision_id,
                base_plan_revision_fingerprint, source_cut_id,
                source_cut_generation, source_cut_fingerprint,
                worker_registry_provenance_id,
                worker_registry_provenance_fingerprint,
                worker_registry_capture_id,
                worker_registry_capture_fingerprint,
                worker_registry_capture_generation,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_id,
                fingerprint,
                corrupt.schema_version,
                corrupt.rule_version,
                corrupt.evaluation_input_id,
                corrupt.evaluation_input_fingerprint,
                corrupt.company_plan_id,
                corrupt.base_plan_revision,
                corrupt.base_plan_revision_id,
                corrupt.base_plan_revision_fingerprint,
                corrupt.source_cut_id,
                corrupt.source_cut_generation,
                corrupt.source_cut_fingerprint,
                corrupt.worker_registry_provenance_id,
                corrupt.worker_registry_provenance_fingerprint,
                corrupt.worker_registry_capture_id,
                corrupt.worker_registry_capture_fingerprint,
                corrupt.worker_registry_capture_generation,
                raw,
            ),
        )
        connection.executemany(
            """
            INSERT INTO m3_feasibility_support_source_bindings(
                support_snapshot_id, evidence_kind,
                subject_fingerprint, source_record_id
            ) VALUES(?,?,?,?)
            """,
            (
                (snapshot_id, *binding)
                for binding in repository._support_source_bindings(
                    corrupt if bind_corrupt_sources else trusted
                )
            ),
        )
    return snapshot_id


def source_cut_for(repository, support) -> SourceSelectionCut:
    with repository._read_snapshot() as connection:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_source_selection_cuts
            WHERE source_cut_id=?
            """,
            (support.source_cut_id,),
        ).fetchone()
        return repository._source_cut_from_row(row)


def worker_registry_provenance_for(repository, support):
    with repository._read_snapshot() as connection:
        return repository._worker_registry_provenance_by_id(
            connection, support.worker_registry_provenance_id
        )


def worker_registry_capture_for(repository, support):
    with repository._read_snapshot() as connection:
        return repository._worker_registry_capture_by_id(
            connection, support.worker_registry_capture_id
        )


def persist_worker_registry_provenance_only(
    repository: M3FeasibilitySupportRepository,
    registry: WorkerIdentityRegistry,
    *,
    bypass_current_authority: bool = False,
) -> WorkerRegistryProvenance:
    raw_registry = serialize_worker_registry(registry)
    value = WorkerRegistryProvenance(
        worker_registry_revision=registry.registry_revision,
        worker_registry_fingerprint=sha256_text(raw_registry),
        canonical_worker_registry_json=raw_registry,
    )
    with repository.transaction() as connection:
        if bypass_current_authority:
            connection.execute(
                "DROP TRIGGER "
                "m3_feasibility_worker_registry_provenance_current_authority"
            )
        connection.execute(
            """
            INSERT INTO m3_feasibility_worker_registry_provenance(
                worker_registry_provenance_id,
                worker_registry_provenance_fingerprint,
                schema_version, worker_registry_schema_version,
                worker_registry_revision, worker_registry_fingerprint,
                canonical_worker_registry_json, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                value.worker_registry_provenance_id,
                value.worker_registry_provenance_fingerprint,
                value.schema_version,
                value.worker_registry_schema_version,
                value.worker_registry_revision,
                value.worker_registry_fingerprint,
                value.canonical_worker_registry_json,
                feasibility_contract.worker_registry_provenance_semantic_json(value),
            ),
        )
    return value


def cut_with_selection(
    cut: SourceSelectionCut,
    kind,
    subject_fingerprint: str,
    source=None,
    *,
    omit: bool = False,
) -> SourceSelectionCut:
    selections = [
        item
        for item in cut.selections
        if (item.source_kind, item.subject_fingerprint)
        != (kind, subject_fingerprint)
    ]
    if not omit:
        selections.append(
            SourceSelection(
                source_kind=kind,
                subject_fingerprint=subject_fingerprint,
                source_record_id=None if source is None else source.source_record_id,
                source_revision=None if source is None else source.source_revision,
                source_fingerprint=None if source is None else source.source_fingerprint,
            )
        )
    return replace(cut, selections=tuple(selections))


def refingerprint_embedded_source(document: dict) -> None:
    semantic = canonical_json(
        {
            key: value
            for key, value in document.items()
            if key not in ("source_fingerprint", "source_record_id")
        }
    )
    fingerprint = sha256_text(semantic)
    document["source_fingerprint"] = fingerprint
    document["source_record_id"] = "m3-feasibility-source-" + fingerprint


def fully_sourced_snapshot(path: Path):
    repository, evaluation, revision, _, constraints, _ = prerequisites(path)
    end = NOW + timedelta(hours=10)
    repository.record_worker_availability(
        WorkerAvailabilitySource(
            worker_id="replacement",
            coverage_start=NOW,
            coverage_end=end,
            available_windows=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-empty-worker-availability",
        )
    )
    repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.EXPECTED,
            expected_at=NOW + timedelta(hours=2),
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-expected-readiness",
        )
    )
    repository.record_vehicle_availability(
        VehicleAvailabilitySource(
            vehicle_id="company-caddy-maxi",
            coverage_start=NOW,
            coverage_end=end,
            available_windows=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-empty-vehicle-availability",
        )
    )
    repository.record_route(
        RouteSource(
            origin_reference=constraints[0].location_reference,
            origin_fingerprint=constraints[0].location_fingerprint,
            destination_reference=constraints[1].location_reference,
            destination_fingerprint=constraints[1].location_fingerprint,
            transport_mode="CAR",
            departure_time_basis="2026-09-06-business-day",
            distance_meters=12345,
            travel_duration_seconds=987,
            buffer_seconds=600,
            buffer_rule_version="m3-travel-buffer-v1",
            route_snapshot_version="fixture-route-set-v3",
            provider="trusted-offline-route-snapshot",
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="route-fixture-record-12",
        )
    )
    return repository, evaluation, revision, repository.record_feasibility_support(
        evaluation.evaluation_input_id
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "readiness",
        "worker_availability",
        "vehicle_availability",
        "route",
        "worker_m8",
        "task_duration",
        "customer_window",
        "hard_deadline",
        "empty_evidence",
    ],
)
def test_refingerprinted_support_values_are_rejected_against_authoritative_sources(
    tmp_path: Path, corruption: str
) -> None:
    repository, _, _, trusted = fully_sourced_snapshot(
        tmp_path / f"content-{corruption}.db"
    )

    def mutate(document):
        if corruption == "readiness":
            evidence = next(
                item for item in document["readiness"] if item["task_id"] == "task-1"
            )
            evidence["status"] = "READY"
            evidence["expected_at"] = None
        elif corruption == "worker_availability":
            evidence = next(
                item
                for item in document["worker_availability"]
                if item["subject_id"] == "replacement"
            )
            evidence["available_windows"] = [
                {
                    "start_at": evidence["coverage_start"],
                    "end_at": evidence["coverage_end"],
                }
            ]
        elif corruption == "vehicle_availability":
            evidence = next(
                item
                for item in document["vehicle_availability"]
                if item["subject_id"] == "company-caddy-maxi"
            )
            evidence["available_windows"] = [
                {
                    "start_at": evidence["coverage_start"],
                    "end_at": evidence["coverage_end"],
                }
            ]
        elif corruption == "route":
            evidence = next(
                item for item in document["routes"] if item["knowledge"] == "KNOWN"
            )
            evidence["travel_duration_seconds"] = 0
            evidence["buffer_seconds"] = 0
        elif corruption == "worker_m8":
            evidence = next(
                item
                for item in document["worker_technical_evidence"]
                if item["worker_id"] == "replacement"
            )
            evidence["m8_worker_known"] = True
            evidence["license_b"] = True
            for skill in evidence["skill_levels"]:
                skill["level"] = 3
        elif corruption in ("task_duration", "customer_window", "hard_deadline"):
            constraint = document["task_constraints"][0]
            if corruption == "task_duration":
                constraint["duration_seconds"] += 60
            elif corruption == "customer_window":
                constraint["customer_window_end"] = (
                    NOW + timedelta(hours=6)
                ).isoformat()
            else:
                constraint["hard_deadline"] = (
                    NOW + timedelta(hours=8)
                ).isoformat()
            refingerprint_embedded_source(constraint)
        else:
            for name in (
                "task_constraints",
                "worker_technical_evidence",
                "worker_availability",
                "readiness",
                "vehicle_technical_evidence",
                "vehicle_availability",
                "routes",
            ):
                document[name] = []

    corrupt_id = insert_refingerprinted_support(repository, trusted, mutate)
    with pytest.raises(M3FeasibilitySupportStorageError):
        repository.get_feasibility_support(corrupt_id)


def test_refingerprinted_nested_schedule_from_another_revision_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-revision-schedule.db"
    b0, company, zero, request, m2 = setup(path)
    b0.import_revision(company, zero)
    repository = M3FeasibilitySupportRepository(path)
    evaluation = repository.record_evaluation_input(request)
    for number in (1, 2):
        repository.record_task_constraint(task_source(m2, number))
    repository.record_plan_schedule(schedule_source(zero))
    trusted = repository.record_feasibility_support(evaluation.evaluation_input_id)

    one = next_revision(zero)
    b0.import_revision(company, one)
    other_schedule = repository.record_plan_schedule(schedule_source(one))

    def mutate(document):
        schedule_document = json.loads(source_semantic_json(other_schedule))
        schedule_document["source_fingerprint"] = other_schedule.source_fingerprint
        schedule_document["source_record_id"] = other_schedule.source_record_id
        document["schedule"] = schedule_document

    corrupt_id = insert_refingerprinted_support(
        repository, trusted, mutate, bind_corrupt_sources=True
    )
    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="SUPPORT_SOURCE_CUT_SELECTION_MISMATCH|SUPPORT_SCHEDULE_REVISION_MISMATCH",
    ):
        repository.get_feasibility_support(corrupt_id)


def test_source_cut_rejects_known_readiness_forged_to_unknown(tmp_path: Path) -> None:
    repository, _, _, trusted = fully_sourced_snapshot(
        tmp_path / "known-forged-unknown.db"
    )
    subject = next(
        item.subject_fingerprint
        for item in source_cut_for(repository, trusted).selections
        if item.source_kind is FeasibilitySourceKind.TASK_READINESS
        and item.source_record_id is not None
    )
    forged_cut = cut_with_selection(
        source_cut_for(repository, trusted),
        FeasibilitySourceKind.TASK_READINESS,
        subject,
    )

    def mutate(document):
        evidence = next(
            item for item in document["readiness"] if item["task_id"] == "task-1"
        )
        evidence.update(
            status="UNKNOWN",
            expected_at=None,
            blocker_references=[],
            source_record_id=None,
            source_fingerprint=None,
            source_revision=None,
        )

    forged_id = insert_refingerprinted_support(
        repository,
        trusted,
        mutate,
        bind_corrupt_sources=True,
        source_cut=forged_cut,
    )
    with pytest.raises(M3FeasibilitySupportStorageError, match="SOURCE_CUT_HEAD_MISMATCH"):
        repository.get_feasibility_support(forged_id)


def test_source_cut_rejects_valid_older_head_revision(tmp_path: Path) -> None:
    path = tmp_path / "head-downgrade.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    revision_zero = repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.EXPECTED,
            expected_at=NOW + timedelta(hours=2),
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="readiness-r0",
        )
    )
    revision_one = repository.record_task_readiness(
        replace(
            revision_zero,
            status=ReadinessState.READY,
            expected_at=None,
            source_revision=1,
            previous_source_record_id=revision_zero.source_record_id,
            provenance_reference="readiness-r1",
        )
    )
    trusted = repository.record_feasibility_support(evaluation.evaluation_input_id)
    forged_cut = cut_with_selection(
        source_cut_for(repository, trusted),
        FeasibilitySourceKind.TASK_READINESS,
        revision_zero.subject_fingerprint,
        revision_zero,
    )

    def mutate(document):
        evidence = next(
            item for item in document["readiness"] if item["task_id"] == "task-1"
        )
        evidence.update(
            status=revision_zero.status.value,
            expected_at=revision_zero.expected_at.isoformat(),
            blocker_references=list(revision_zero.blocker_references),
            source_record_id=revision_zero.source_record_id,
            source_fingerprint=revision_zero.source_fingerprint,
            source_revision=revision_zero.source_revision,
        )

    forged_id = insert_refingerprinted_support(
        repository,
        trusted,
        mutate,
        bind_corrupt_sources=True,
        source_cut=forged_cut,
    )
    assert revision_one.source_revision == 1
    with pytest.raises(M3FeasibilitySupportStorageError, match="SOURCE_CUT_HEAD_MISMATCH"):
        repository.get_feasibility_support(forged_id)


def test_later_source_revision_does_not_invalidate_historical_cut(tmp_path: Path) -> None:
    path = tmp_path / "later-head.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    revision_zero = repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.EXPECTED,
            expected_at=NOW + timedelta(hours=2),
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="readiness-history-r0",
        )
    )
    revision_one = repository.record_task_readiness(
        replace(
            revision_zero,
            status=ReadinessState.READY,
            expected_at=None,
            source_revision=1,
            previous_source_record_id=revision_zero.source_record_id,
            provenance_reference="readiness-history-r1",
        )
    )
    historical = repository.record_feasibility_support(evaluation.evaluation_input_id)
    serialized = serialize_feasibility_support(historical)
    repository.record_task_readiness(
        replace(
            revision_one,
            status=ReadinessState.BLOCKED,
            blocker_references=("materials",),
            source_revision=2,
            previous_source_record_id=revision_one.source_record_id,
            provenance_reference="readiness-history-r2",
        )
    )

    assert serialize_feasibility_support(
        repository.get_feasibility_support(historical.support_snapshot_id)
    ) == serialized


def test_no_source_at_cut_remains_unknown_after_later_source(tmp_path: Path) -> None:
    path = tmp_path / "true-no-source.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    historical = repository.record_feasibility_support(evaluation.evaluation_input_id)
    assert next(
        item for item in historical.readiness if item.task_id == "task-1"
    ).status is ReadinessState.UNKNOWN
    repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.READY,
            expected_at=None,
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="created-after-no-source-cut",
        )
    )

    restored = M3FeasibilitySupportRepository(path).get_feasibility_support(
        historical.support_snapshot_id
    )
    assert restored == historical


def test_source_created_after_cut_cannot_be_forged_into_old_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "source-after-cut.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    historical = repository.record_feasibility_support(evaluation.evaluation_input_id)
    later = repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.READY,
            expected_at=None,
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="later-readiness",
        )
    )

    def mutate(document):
        evidence = next(
            item for item in document["readiness"] if item["task_id"] == "task-1"
        )
        evidence.update(
            status=later.status.value,
            expected_at=None,
            blocker_references=[],
            source_record_id=later.source_record_id,
            source_fingerprint=later.source_fingerprint,
            source_revision=later.source_revision,
        )

    forged_id = insert_refingerprinted_support(
        repository,
        historical,
        mutate,
        bind_corrupt_sources=True,
    )
    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="SUPPORT_SOURCE_CUT_SELECTION_MISMATCH",
    ):
        repository.get_feasibility_support(forged_id)


def test_source_cut_rejects_required_subject_omission(tmp_path: Path) -> None:
    path = tmp_path / "required-subject-omission.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    trusted = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut = source_cut_for(repository, trusted)
    omitted = next(
        item
        for item in cut.selections
        if item.source_kind is FeasibilitySourceKind.TASK_READINESS
    )
    forged_cut = cut_with_selection(
        cut,
        omitted.source_kind,
        omitted.subject_fingerprint,
        omit=True,
    )
    forged_id = insert_refingerprinted_support(
        repository,
        trusted,
        lambda document: None,
        source_cut=forged_cut,
    )
    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="SOURCE_CUT_SUBJECT_UNIVERSE_MISMATCH",
    ):
        repository.get_feasibility_support(forged_id)


def test_valid_exact_cut_with_selected_and_no_source_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exact-cut-restart.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.EXPECTED,
            expected_at=NOW + timedelta(hours=2),
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="selected-at-exact-cut",
        )
    )
    value = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut = source_cut_for(repository, value)

    assert any(item.source_record_id is None for item in cut.selections)
    assert any(item.source_record_id is not None for item in cut.selections)
    assert M3FeasibilitySupportRepository(path).get_feasibility_support(
        value.support_snapshot_id
    ) == value


def test_source_append_and_support_cut_are_transactionally_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cut-concurrency.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    original_build = repository._build_support
    build_entered = threading.Event()
    release_build = threading.Event()
    append_attempted = threading.Event()
    append_finished = threading.Event()
    results = {}
    failures = []

    def paused_build(
        connection,
        exact_evaluation,
        worker_registry_provenance,
        worker_registry_capture,
    ):
        build_entered.set()
        assert release_build.wait(10)
        return original_build(
            connection,
            exact_evaluation,
            worker_registry_provenance,
            worker_registry_capture,
        )

    monkeypatch.setattr(repository, "_build_support", paused_build)

    def capture_support():
        try:
            results["support"] = repository.record_feasibility_support(
                evaluation.evaluation_input_id
            )
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    def append_source():
        append_attempted.set()
        try:
            M3FeasibilitySupportRepository(path).record_task_readiness(
                TaskReadinessSource(
                    job_id="job-1",
                    task_id="task-1",
                    task_definition_version="task-1-v1",
                    status=ReadinessState.READY,
                    expected_at=None,
                    blocker_references=(),
                    source_revision=0,
                    previous_source_record_id=None,
                    provenance_reference="concurrent-readiness",
                )
            )
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)
        finally:
            append_finished.set()

    support_thread = threading.Thread(target=capture_support)
    support_thread.start()
    assert build_entered.wait(10)
    source_thread = threading.Thread(target=append_source)
    source_thread.start()
    assert append_attempted.wait(10)
    assert not append_finished.wait(0.25)
    release_build.set()
    support_thread.join(10)
    source_thread.join(10)

    assert not support_thread.is_alive()
    assert not source_thread.is_alive()
    assert failures == []
    historical = results["support"]
    assert next(
        item for item in historical.readiness if item.task_id == "task-1"
    ).status is ReadinessState.UNKNOWN
    assert repository.get_feasibility_support(historical.support_snapshot_id) == historical
    current = repository.record_feasibility_support(evaluation.evaluation_input_id)
    assert next(
        item for item in current.readiness if item.task_id == "task-1"
    ).status is ReadinessState.READY


def test_same_membership_new_registry_revision_preserves_old_support(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-same-membership.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    registry_a = m2.get_worker_registry()
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    serialized_a = serialize_feasibility_support(support_a)

    updated = advance_worker_registry(m2, registry_a.worker_ids)
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut_a = source_cut_for(repository, support_a)
    cut_b = source_cut_for(repository, support_b)
    provenance_a = worker_registry_provenance_for(repository, support_a)
    provenance_b = worker_registry_provenance_for(repository, support_b)
    capture_a = worker_registry_capture_for(repository, support_a)
    capture_b = worker_registry_capture_for(repository, support_b)

    assert updated.registry_revision == 1
    assert support_a.worker_registry_revision == 0
    assert support_b.worker_registry_revision == 1
    assert support_a.worker_registry_provenance_id != (
        support_b.worker_registry_provenance_id
    )
    assert support_a.source_cut_id != support_b.source_cut_id
    assert support_a.support_snapshot_id != support_b.support_snapshot_id
    assert capture_a.ledger_generation == support_a.worker_registry_capture_generation
    assert capture_b.ledger_generation == support_b.worker_registry_capture_generation
    assert capture_a.ledger_generation < capture_b.ledger_generation
    with repository._read_snapshot() as connection:
        ledger_generations = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT ledger_generation
                FROM (
                    SELECT ledger_generation FROM m3_feasibility_source_records
                    UNION ALL
                    SELECT ledger_generation
                    FROM m3_feasibility_worker_registry_captures
                )
                ORDER BY ledger_generation
                """
            ).fetchall()
        )
    assert ledger_generations == tuple(range(1, len(ledger_generations) + 1))
    assert cut_a.worker_registry_provenance_id == (
        support_a.worker_registry_provenance_id
    )
    assert cut_b.worker_registry_provenance_id == (
        support_b.worker_registry_provenance_id
    )
    assert provenance_a.canonical_worker_registry_json == serialize_worker_registry(
        registry_a
    )
    assert provenance_b.canonical_worker_registry_json == serialize_worker_registry(
        updated
    )
    assert serialize_feasibility_support(
        repository.get_feasibility_support(support_a.support_snapshot_id)
    ) == serialized_a
    assert repository.get_feasibility_support(support_b.support_snapshot_id) == support_b


def test_membership_change_uses_each_captured_worker_universe(tmp_path: Path) -> None:
    path = tmp_path / "registry-membership-change.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    workers_a = tuple(item.worker_id for item in support_a.worker_technical_evidence)

    updated = advance_worker_registry(m2, (*workers_a, "marek"))
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)

    assert tuple(item.worker_id for item in support_a.worker_technical_evidence) == (
        workers_a
    )
    assert tuple(item.subject_id for item in support_a.worker_availability) == workers_a
    assert feasibility_contract.worker_subject_fingerprint("marek") not in {
        item.subject_fingerprint
        for item in source_cut_for(repository, support_a).selections
        if item.source_kind is FeasibilitySourceKind.WORKER_AVAILABILITY
    }
    assert tuple(item.worker_id for item in support_b.worker_technical_evidence) == (
        updated.worker_ids
    )
    assert tuple(item.subject_id for item in support_b.worker_availability) == (
        updated.worker_ids
    )
    assert any(
        item.source_kind is FeasibilitySourceKind.WORKER_AVAILABILITY
        and item.subject_fingerprint
        == feasibility_contract.worker_subject_fingerprint("marek")
        for item in source_cut_for(repository, support_b).selections
    )
    assert repository.get_feasibility_support(support_a.support_snapshot_id) == support_a
    assert repository.get_feasibility_support(support_b.support_snapshot_id) == support_b


def test_historical_read_never_consults_current_worker_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "registry-current-not-read.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    expected = serialize_feasibility_support(support_a)
    advance_worker_registry(m2, (*m2.get_worker_registry().worker_ids, "marek"))

    def current_registry_must_not_be_read(*_args, **_kwargs):
        raise AssertionError("historical support read consulted current M2 registry")

    monkeypatch.setattr(
        M2DurableRepository,
        "_worker_registry_from_connection",
        current_registry_must_not_be_read,
    )
    restored = repository.get_feasibility_support(support_a.support_snapshot_id)

    assert serialize_feasibility_support(restored) == expected
    assert "marek" not in {item.worker_id for item in restored.worker_technical_evidence}


def test_historical_worker_registry_provenance_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-restart.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    expected = serialize_feasibility_support(support_a)
    advance_worker_registry(m2, (*m2.get_worker_registry().worker_ids, "marek"))

    restarted = M3FeasibilitySupportRepository(path)
    restored = restarted.get_feasibility_support(support_a.support_snapshot_id)

    assert serialize_feasibility_support(restored) == expected
    assert worker_registry_provenance_for(
        restarted, restored
    ).worker_registry_revision == 0
    assert worker_registry_capture_for(
        restarted, restored
    ).ledger_generation == support_a.worker_registry_capture_generation


@pytest.mark.parametrize("corruption", ["content", "revision", "fingerprint"])
def test_tampered_worker_registry_provenance_fails_closed(
    tmp_path: Path, corruption: str
) -> None:
    path = tmp_path / f"registry-provenance-{corruption}.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    provenance = worker_registry_provenance_for(repository, support)
    inner = json.loads(provenance.canonical_worker_registry_json)
    if corruption == "content":
        inner["payload"]["worker_ids"].append("forged-worker")
    elif corruption == "revision":
        inner["payload"]["registry_revision"] += 1
    tampered_inner = canonical_json(inner)
    outer = json.loads(
        feasibility_contract.worker_registry_provenance_semantic_json(provenance)
    )
    outer["canonical_worker_registry_json"] = tampered_inner
    if corruption == "revision":
        outer["worker_registry_fingerprint"] = sha256_text(tampered_inner)
    elif corruption == "fingerprint":
        outer["worker_registry_fingerprint"] = "0" * 64
    tampered_outer = canonical_json(outer)

    with repository.transaction() as connection:
        connection.execute(
            "DROP TRIGGER m3_feasibility_worker_registry_provenance_no_update"
        )
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE m3_feasibility_worker_registry_provenance
            SET canonical_worker_registry_json=?, canonical_semantic_json=?,
                worker_registry_fingerprint=?,
                worker_registry_provenance_fingerprint=?
            WHERE worker_registry_provenance_id=?
            """,
            (
                tampered_inner,
                tampered_outer,
                outer["worker_registry_fingerprint"],
                sha256_text(tampered_outer),
                provenance.worker_registry_provenance_id,
            ),
        )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="INVALID_WORKER_REGISTRY_PROVENANCE",
    ):
        repository.get_feasibility_support(support.support_snapshot_id)


def test_cut_worker_registry_provenance_substitution_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-cut-substitution.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    advance_worker_registry(m2, m2.get_worker_registry().worker_ids)
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)

    with repository.transaction() as connection:
        connection.execute("DROP TRIGGER m3_feasibility_source_cuts_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            """
            UPDATE m3_feasibility_source_selection_cuts
            SET worker_registry_provenance_id=?,
                worker_registry_provenance_fingerprint=?
            WHERE source_cut_id=?
            """,
            (
                support_b.worker_registry_provenance_id,
                support_b.worker_registry_provenance_fingerprint,
                support_a.source_cut_id,
            ),
        )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="SOURCE_CUT_METADATA_MISMATCH",
    ):
        repository.get_feasibility_support(support_a.support_snapshot_id)


def test_fully_refingerprinted_later_registry_capture_cannot_be_backdated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-capture-backdate.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut_a = source_cut_for(repository, support_a)

    repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.READY,
            expected_at=None,
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="advance-ledger-before-registry-revision-1",
        )
    )
    advance_worker_registry(m2, m2.get_worker_registry().worker_ids)
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut_b = source_cut_for(repository, support_b)
    capture_b = worker_registry_capture_for(repository, support_b)
    assert capture_b.ledger_generation > cut_a.source_cut_generation

    forged_cut = replace(
        cut_b,
        source_cut_generation=cut_a.source_cut_generation,
    )
    forged_id = insert_refingerprinted_support(
        repository,
        support_b,
        lambda _document: None,
        bind_corrupt_sources=True,
        source_cut=forged_cut,
        bypass_exact_capture=True,
    )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_CAPTURE_AFTER_SOURCE_CUT",
    ):
        repository.get_feasibility_support(forged_id)
    assert repository.get_feasibility_support(support_a.support_snapshot_id) == support_a


def test_membership_changed_later_capture_cannot_redefine_old_worker_universe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-membership-capture-backdate.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut_a = source_cut_for(repository, support_a)
    worker_ids_a = tuple(item.worker_id for item in support_a.worker_technical_evidence)

    advance_worker_registry(m2, (*worker_ids_a, "marek"))
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut_b = source_cut_for(repository, support_b)
    assert support_b.worker_registry_capture_generation > cut_a.source_cut_generation

    forged_cut = replace(
        cut_b,
        source_cut_generation=cut_a.source_cut_generation,
    )
    forged_id = insert_refingerprinted_support(
        repository,
        support_b,
        lambda _document: None,
        bind_corrupt_sources=True,
        source_cut=forged_cut,
        bypass_exact_capture=True,
    )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_CAPTURE_AFTER_SOURCE_CUT",
    ):
        repository.get_feasibility_support(forged_id)
    restored_a = repository.get_feasibility_support(support_a.support_snapshot_id)
    assert tuple(item.worker_id for item in restored_a.worker_technical_evidence) == (
        worker_ids_a
    )
    assert "marek" not in {item.subject_id for item in restored_a.worker_availability}


def test_invented_registry_revision_without_authentic_capture_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-invented-revision.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut = source_cut_for(repository, support)
    invented = persist_worker_registry_provenance_only(
        repository,
        WorkerIdentityRegistry(
            worker_ids=m2.get_worker_registry().worker_ids,
            registry_revision=99,
        ),
        bypass_current_authority=True,
    )
    forged_cut = replace(
        cut,
        worker_registry_provenance_id=invented.worker_registry_provenance_id,
        worker_registry_provenance_fingerprint=(
            invented.worker_registry_provenance_fingerprint
        ),
    )

    def bind_invented_registry(document):
        document["worker_registry_revision"] = invented.worker_registry_revision
        document["worker_registry_fingerprint"] = invented.worker_registry_fingerprint

    forged_id = insert_refingerprinted_support(
        repository,
        support,
        bind_invented_registry,
        source_cut=forged_cut,
        bypass_exact_capture=True,
    )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_CAPTURE_BINDING_MISMATCH",
    ):
        repository.get_feasibility_support(forged_id)


def test_valid_provenance_row_without_ledger_capture_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry-provenance-without-capture.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut = source_cut_for(repository, support)
    registry_b = advance_worker_registry(m2, m2.get_worker_registry().worker_ids)
    provenance_b = persist_worker_registry_provenance_only(repository, registry_b)
    forged_cut = replace(
        cut,
        worker_registry_provenance_id=provenance_b.worker_registry_provenance_id,
        worker_registry_provenance_fingerprint=(
            provenance_b.worker_registry_provenance_fingerprint
        ),
    )

    def bind_uncaptured_registry(document):
        document["worker_registry_revision"] = provenance_b.worker_registry_revision
        document["worker_registry_fingerprint"] = (
            provenance_b.worker_registry_fingerprint
        )

    forged_id = insert_refingerprinted_support(
        repository,
        support,
        bind_uncaptured_registry,
        source_cut=forged_cut,
        bypass_exact_capture=True,
    )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_CAPTURE_BINDING_MISMATCH",
    ):
        repository.get_feasibility_support(forged_id)


def test_wrong_registry_capture_generation_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "registry-wrong-capture-generation.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut = source_cut_for(repository, support)
    assert cut.worker_registry_capture_generation > 1
    forged_cut = replace(
        cut,
        worker_registry_capture_generation=(
            cut.worker_registry_capture_generation - 1
        ),
    )
    forged_id = insert_refingerprinted_support(
        repository,
        support,
        lambda _document: None,
        source_cut=forged_cut,
        bypass_exact_capture=True,
    )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_CAPTURE_BINDING_MISMATCH",
    ):
        repository.get_feasibility_support(forged_id)


def test_capture_from_another_provenance_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "registry-wrong-authentic-capture.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    advance_worker_registry(m2, m2.get_worker_registry().worker_ids)
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)
    cut_b = source_cut_for(repository, support_b)
    forged_cut = replace(
        cut_b,
        worker_registry_provenance_id=support_a.worker_registry_provenance_id,
        worker_registry_provenance_fingerprint=(
            support_a.worker_registry_provenance_fingerprint
        ),
    )
    forged_id = insert_refingerprinted_support(
        repository,
        support_a,
        lambda _document: None,
        source_cut=forged_cut,
        bypass_exact_capture=True,
    )

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_CAPTURE_BINDING_MISMATCH",
    ):
        repository.get_feasibility_support(forged_id)


def test_missing_worker_registry_provenance_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "registry-provenance-missing.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    connection = repository._connect()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "DROP TRIGGER m3_feasibility_worker_registry_provenance_no_delete"
        )
        connection.execute(
            """
            DELETE FROM m3_feasibility_worker_registry_provenance
            WHERE worker_registry_provenance_id=?
            """,
            (support.worker_registry_provenance_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        M3FeasibilitySupportStorageError,
        match="WORKER_REGISTRY_PROVENANCE_NOT_FOUND",
    ):
        repository.get_feasibility_support(support.support_snapshot_id)


def test_registry_update_and_support_capture_are_transactionally_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "registry-capture-concurrency.db"
    repository, evaluation, _, m2, _, _ = prerequisites(path)
    original_build = repository._build_support
    build_entered = threading.Event()
    release_build = threading.Event()
    update_attempted = threading.Event()
    update_finished = threading.Event()
    results = {}
    failures = []

    def paused_build(
        connection,
        exact_evaluation,
        worker_registry_provenance,
        worker_registry_capture,
    ):
        build_entered.set()
        assert release_build.wait(10)
        return original_build(
            connection,
            exact_evaluation,
            worker_registry_provenance,
            worker_registry_capture,
        )

    monkeypatch.setattr(repository, "_build_support", paused_build)

    def capture_support():
        try:
            results["support"] = repository.record_feasibility_support(
                evaluation.evaluation_input_id
            )
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)

    def update_registry():
        update_attempted.set()
        try:
            current_ids = m2.get_worker_registry().worker_ids
            results["registry"] = advance_worker_registry(
                m2, (*current_ids, "marek")
            )
        except BaseException as error:  # pragma: no cover - surfaced below
            failures.append(error)
        finally:
            update_finished.set()

    support_thread = threading.Thread(target=capture_support)
    support_thread.start()
    assert build_entered.wait(10)
    registry_thread = threading.Thread(target=update_registry)
    registry_thread.start()
    assert update_attempted.wait(10)
    assert not update_finished.wait(0.25)
    release_build.set()
    support_thread.join(10)
    registry_thread.join(10)

    assert not support_thread.is_alive()
    assert not registry_thread.is_alive()
    assert failures == []
    support_a = results["support"]
    assert support_a.worker_registry_revision == 0
    assert "marek" not in {
        item.worker_id for item in support_a.worker_technical_evidence
    }
    assert results["registry"].registry_revision == 1
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)
    assert support_b.worker_registry_revision == 1
    assert "marek" in {item.worker_id for item in support_b.worker_technical_evidence}


def test_historical_m8_configuration_survives_current_configuration_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "historical-m8.db"
    repository, evaluation, _, _, sources_a, _ = prerequisites(path)
    support_a = repository.record_feasibility_support(evaluation.evaluation_input_id)
    serialized_a = serialize_feasibility_support(support_a)

    configuration_b = replace(
        feasibility_contract.M8_CONFIGURATION,
        sku_planning_profile_version="m8-sku-planning-v2-test",
    )
    monkeypatch.setattr(feasibility_contract, "M8_CONFIGURATION", configuration_b)

    assert serialize_feasibility_support(
        repository.get_feasibility_support(support_a.support_snapshot_id)
    ) == serialized_a

    sources_b = tuple(
        repository.record_task_constraint(
            replace(
                source,
                source_revision=source.source_revision + 1,
                previous_source_record_id=source.source_record_id,
                provenance_reference=source.provenance_reference + "-config-b",
            )
        )
        for source in sources_a
    )
    support_b = repository.record_feasibility_support(evaluation.evaluation_input_id)

    assert all(
        source.m8_planning_profile_version == "m8-sku-planning-v2-test"
        for source in sources_b
    )
    assert support_b.m8_planning_profile_version == "m8-sku-planning-v2-test"
    assert support_b.support_snapshot_id != support_a.support_snapshot_id
    assert repository.get_feasibility_support(support_a.support_snapshot_id) == support_a
    assert repository.get_feasibility_support(support_b.support_snapshot_id) == support_b


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize(
    "table",
    [
        "m3_feasibility_worker_registry_provenance",
        "m3_feasibility_worker_registry_captures",
        "m3_feasibility_source_records",
        "m3_feasibility_source_selection_cuts",
        "m3_feasibility_support_snapshots",
    ],
)
def test_without_rowid_closes_physical_replace_channel(
    tmp_path: Path, recursive: int, table: str
) -> None:
    path = tmp_path / f"without-rowid-{table}-{recursive}.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    original = repository.record_feasibility_support(evaluation.evaluation_input_id)

    if table == "m3_feasibility_worker_registry_provenance":
        with repository._read_snapshot() as connection:
            candidate = repository._worker_registry_provenance_by_id(
                connection, original.worker_registry_provenance_id
            )
        columns = (
            "worker_registry_provenance_id,"
            "worker_registry_provenance_fingerprint,schema_version,"
            "worker_registry_schema_version,worker_registry_revision,"
            "worker_registry_fingerprint,canonical_worker_registry_json,"
            "canonical_semantic_json"
        )
        values = (
            candidate.worker_registry_provenance_id,
            candidate.worker_registry_provenance_fingerprint,
            candidate.schema_version,
            candidate.worker_registry_schema_version,
            candidate.worker_registry_revision,
            candidate.worker_registry_fingerprint,
            candidate.canonical_worker_registry_json,
            feasibility_contract.worker_registry_provenance_semantic_json(
                candidate
            ),
        )
    elif table == "m3_feasibility_worker_registry_captures":
        with repository._read_snapshot() as connection:
            candidate = repository._worker_registry_capture_by_id(
                connection, original.worker_registry_capture_id
            )
        columns = (
            "worker_registry_capture_id,worker_registry_capture_fingerprint,"
            "ledger_generation,capture_kind,schema_version,"
            "worker_registry_provenance_id,"
            "worker_registry_provenance_fingerprint,worker_registry_revision,"
            "worker_registry_fingerprint,canonical_semantic_json"
        )
        values = (
            candidate.worker_registry_capture_id,
            candidate.worker_registry_capture_fingerprint,
            candidate.ledger_generation,
            candidate.capture_kind,
            candidate.schema_version,
            candidate.worker_registry_provenance_id,
            candidate.worker_registry_provenance_fingerprint,
            candidate.worker_registry_revision,
            candidate.worker_registry_fingerprint,
            feasibility_contract.worker_registry_capture_semantic_json(candidate),
        )
    elif table == "m3_feasibility_source_records":
        candidate = TaskReadinessSource(
            job_id="job-2",
            task_id="task-2",
            task_definition_version="task-2-v1",
            status=ReadinessState.READY,
            expected_at=None,
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="valid-logically-new-source",
        )
        columns = (
            "source_record_id,source_kind,subject_fingerprint,source_revision,"
            "previous_source_record_id,ledger_generation,schema_version,source_fingerprint,"
            "canonical_semantic_json"
        )
        with repository._read_snapshot() as connection:
            ledger_generation = repository._source_cut_generation(connection) + 1
        values = (
            candidate.source_record_id,
            candidate.source_kind.value,
            candidate.subject_fingerprint,
            candidate.source_revision,
            candidate.previous_source_record_id,
            ledger_generation,
            candidate.schema_version,
            candidate.source_fingerprint,
            source_semantic_json(candidate),
        )
    elif table == "m3_feasibility_support_snapshots":
        repository.record_worker_availability(
            WorkerAvailabilitySource(
                worker_id="replacement",
                coverage_start=NOW,
                coverage_end=NOW + timedelta(hours=10),
                available_windows=(),
                source_revision=0,
                previous_source_record_id=None,
                provenance_reference="valid-logically-new-support-source",
            )
        )
        with repository.transaction() as connection:
            evaluation_row = connection.execute(
                "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
                (evaluation.evaluation_input_id,),
            ).fetchone()
            worker_registry_provenance, worker_registry_capture = (
                repository._capture_current_worker_registry(connection)
            )
            candidate = repository._build_support(
                connection,
                repository._from_row(connection, evaluation_row),
                worker_registry_provenance,
                worker_registry_capture,
            )
        columns = (
            "support_snapshot_id,support_fingerprint,schema_version,rule_version,"
            "evaluation_input_id,evaluation_input_fingerprint,company_plan_id,"
            "base_plan_revision,base_plan_revision_id,"
            "base_plan_revision_fingerprint,source_cut_id,"
            "source_cut_generation,source_cut_fingerprint,"
            "worker_registry_provenance_id,"
            "worker_registry_provenance_fingerprint,"
            "worker_registry_capture_id,worker_registry_capture_fingerprint,"
            "worker_registry_capture_generation,canonical_semantic_json"
        )
        values = (
            candidate.support_snapshot_id,
            candidate.support_fingerprint,
            candidate.schema_version,
            candidate.rule_version,
            candidate.evaluation_input_id,
            candidate.evaluation_input_fingerprint,
            candidate.company_plan_id,
            candidate.base_plan_revision,
            candidate.base_plan_revision_id,
            candidate.base_plan_revision_fingerprint,
            candidate.source_cut_id,
            candidate.source_cut_generation,
            candidate.source_cut_fingerprint,
            candidate.worker_registry_provenance_id,
            candidate.worker_registry_provenance_fingerprint,
            candidate.worker_registry_capture_id,
            candidate.worker_registry_capture_fingerprint,
            candidate.worker_registry_capture_generation,
            snapshot_semantic_json(candidate),
        )
    else:
        candidate = source_cut_for(repository, original)
        columns = (
            "source_cut_id,source_cut_generation,source_cut_fingerprint,"
            "schema_version,evaluation_input_id,evaluation_input_fingerprint,"
            "company_plan_id,base_plan_revision,base_plan_revision_id,"
            "base_plan_revision_fingerprint,worker_registry_provenance_id,"
            "worker_registry_provenance_fingerprint,worker_registry_capture_id,"
            "worker_registry_capture_fingerprint,"
            "worker_registry_capture_generation,canonical_semantic_json"
        )
        values = (
            candidate.source_cut_id,
            candidate.source_cut_generation,
            candidate.source_cut_fingerprint,
            candidate.schema_version,
            candidate.evaluation_input_id,
            candidate.evaluation_input_fingerprint,
            candidate.company_plan_id,
            candidate.base_plan_revision,
            candidate.base_plan_revision_id,
            candidate.base_plan_revision_fingerprint,
            candidate.worker_registry_provenance_id,
            candidate.worker_registry_provenance_fingerprint,
            candidate.worker_registry_capture_id,
            candidate.worker_registry_capture_fingerprint,
            candidate.worker_registry_capture_generation,
            source_cut_semantic_json(candidate),
        )

    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA recursive_triggers={recursive}")
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "WITHOUT ROWID" in schema
        with pytest.raises(sqlite3.OperationalError, match="rowid"):
            connection.execute(f"SELECT rowid FROM {table}").fetchone()
        with pytest.raises(sqlite3.OperationalError, match="rowid"):
            connection.execute(
                f"INSERT OR REPLACE INTO {table}(rowid,{columns}) "
                f"VALUES(1,{','.join('?' for _ in values)})",
                values,
            )

    if table == "m3_feasibility_source_records":
        assert repository.record_task_readiness(candidate) == candidate
    elif table == "m3_feasibility_support_snapshots":
        later = repository.record_feasibility_support(evaluation.evaluation_input_id)
        assert later == candidate
    assert repository.get_feasibility_support(original.support_snapshot_id) == original


def test_authoritative_snapshot_captures_exact_bindings_intervals_conflicts_and_unknowns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "support.db"
    repository, evaluation, revision, _, constraints, schedule = prerequisites(path)

    snapshot = repository.record_feasibility_support(evaluation.evaluation_input_id)

    assert (
        snapshot.evaluation_input_id,
        snapshot.evaluation_input_fingerprint,
    ) == (
        evaluation.evaluation_input_id,
        evaluation.evaluation_input_fingerprint,
    )
    assert (
        snapshot.company_plan_id,
        snapshot.base_plan_revision,
        snapshot.base_plan_revision_id,
        snapshot.base_plan_revision_fingerprint,
    ) == (
        revision.company_plan_id,
        revision.revision,
        revision.revision_id,
        revision.fingerprint,
    )
    assert snapshot.task_constraints == constraints
    assert snapshot.task_constraints[0].required_skill_key == "painting.wash"
    assert snapshot.task_constraints[1].required_vehicle_class == "LIGHT"
    assert snapshot.schedule == schedule
    assert tuple(item.commitment_id for item in snapshot.schedule.placements) == (
        "c1",
        "c2",
    )
    assert all(
        item.planned_end - item.planned_start == timedelta(hours=1)
        for item in snapshot.schedule.placements
    )
    assert snapshot.schedule.business_timezone == "Europe/Berlin"
    assert all(
        item.knowledge is AvailabilityKnowledge.UNKNOWN
        for item in snapshot.worker_availability
    )
    assert all(item.status is ReadinessState.UNKNOWN for item in snapshot.readiness)
    assert next(
        item
        for item in snapshot.vehicle_availability
        if item.subject_id == "company-caddy-maxi"
    ).knowledge is AvailabilityKnowledge.UNKNOWN
    assert len(snapshot.routes) == 2
    assert all(item.knowledge is RouteKnowledge.UNKNOWN for item in snapshot.routes)
    first = next(item for item in snapshot.task_constraints if item.task_id == "task-1")
    assert first.customer_window_state is ConstraintKnowledge.KNOWN
    assert first.hard_deadline_state is ConstraintKnowledge.KNOWN
    assert first.hard_deadline == NOW + timedelta(hours=7)


def test_missing_or_wrong_task_mapping_and_incomplete_schedule_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "closed.db"
    b0, company, revision, request, m2 = setup(path)
    b0.import_revision(company, revision)
    repository = M3FeasibilitySupportRepository(path)
    evaluation = repository.record_evaluation_input(request)
    first = repository.record_task_constraint(task_source(m2, 1))
    repository.record_plan_schedule(schedule_source(revision))

    with pytest.raises(M3FeasibilitySupportConflictError, match="TASK_M8_BINDING_MISSING"):
        repository.record_feasibility_support(evaluation.evaluation_input_id)

    with pytest.raises(
        M3FeasibilitySupportConflictError, match="CANONICAL_TASK_BINDING_MISMATCH"
    ):
        repository.record_task_constraint(
            task_source(m2, 2, task_definition_version="wrong-version")
        )

    with pytest.raises(M3FeasibilitySupportConflictError, match="TASK_M8_BINDING_CHANGED"):
        repository.record_task_constraint(
            replace(
                first,
                m8_sku="painting.coat1",
                source_revision=1,
                previous_source_record_id=first.source_record_id,
            )
        )

    other_path = tmp_path / "incomplete.db"
    b0, company, revision, _, _ = setup(other_path)
    b0.import_revision(company, revision)
    with pytest.raises(M3FeasibilitySupportConflictError, match="INCOMPLETE_PLAN_SCHEDULE"):
        M3FeasibilitySupportRepository(other_path).record_plan_schedule(
            schedule_source(
                revision,
                placements=schedule_source(revision).placements[:1],
            )
        )


def test_versioned_availability_readiness_vehicle_and_route_are_content_bound(
    tmp_path: Path,
) -> None:
    path = tmp_path / "versioned.db"
    repository, evaluation, _, _, constraints, _ = prerequisites(path)
    coverage_end = NOW + timedelta(hours=10)
    worker = repository.record_worker_availability(
        WorkerAvailabilitySource(
            worker_id="replacement",
            coverage_start=NOW,
            coverage_end=coverage_end,
            available_windows=(
                AvailabilityWindowEvidence(
                    start_at=NOW, end_at=coverage_end
                ),
            ),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-roster-replacement-v1",
        )
    )
    readiness = repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.EXPECTED,
            expected_at=NOW - timedelta(hours=1),
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-material-status-v1",
        )
    )
    vehicle = repository.record_vehicle_availability(
        VehicleAvailabilitySource(
            vehicle_id="company-caddy-maxi",
            coverage_start=NOW,
            coverage_end=coverage_end,
            available_windows=(
                AvailabilityWindowEvidence(
                    start_at=NOW, end_at=coverage_end
                ),
            ),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-fleet-roster-v1",
        )
    )
    route = repository.record_route(
        RouteSource(
            origin_reference=constraints[0].location_reference,
            origin_fingerprint=constraints[0].location_fingerprint,
            destination_reference=constraints[1].location_reference,
            destination_fingerprint=constraints[1].location_fingerprint,
            transport_mode="CAR",
            departure_time_basis="2026-09-06-business-day",
            distance_meters=12345,
            travel_duration_seconds=987,
            buffer_seconds=600,
            buffer_rule_version="m3-travel-buffer-v1",
            route_snapshot_version="fixture-route-set-v3",
            provider="trusted-offline-route-snapshot",
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="route-fixture-record-12",
        )
    )

    snapshot = repository.record_feasibility_support(evaluation.evaluation_input_id)
    captured_worker = next(
        item for item in snapshot.worker_availability if item.subject_id == "replacement"
    )
    captured_vehicle = next(
        item
        for item in snapshot.vehicle_availability
        if item.subject_id == "company-caddy-maxi"
    )
    captured_route = next(
        item
        for item in snapshot.routes
        if item.origin_reference == constraints[0].location_reference
    )

    assert captured_worker.source_fingerprint == worker.source_fingerprint
    assert captured_worker.knowledge is AvailabilityKnowledge.KNOWN
    assert snapshot.readiness[0].status is ReadinessState.EXPECTED
    assert snapshot.readiness[0].source_fingerprint == readiness.source_fingerprint
    assert captured_vehicle.source_fingerprint == vehicle.source_fingerprint
    assert captured_route.knowledge is RouteKnowledge.KNOWN
    assert (
        captured_route.travel_duration_seconds,
        captured_route.distance_meters,
        captured_route.buffer_seconds,
        captured_route.buffer_rule_version,
        captured_route.route_snapshot_version,
        captured_route.source_fingerprint,
    ) == (987, 12345, 600, "m3-travel-buffer-v1", "fixture-route-set-v3", route.source_fingerprint)
    assert next(
        item
        for item in snapshot.routes
        if item.origin_reference == constraints[1].location_reference
    ).knowledge is RouteKnowledge.UNKNOWN


def test_changed_authoritative_source_changes_snapshot_and_identical_evidence_replays(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    first = repository.record_feasibility_support(evaluation.evaluation_input_id)
    assert repository.record_feasibility_support(evaluation.evaluation_input_id) == first

    availability = repository.record_worker_availability(
        WorkerAvailabilitySource(
            worker_id="replacement",
            coverage_start=NOW,
            coverage_end=NOW + timedelta(hours=10),
            available_windows=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="trusted-roster-v1",
        )
    )
    second = repository.record_feasibility_support(evaluation.evaluation_input_id)

    assert second.support_snapshot_id != first.support_snapshot_id
    assert second.support_fingerprint != first.support_fingerprint
    assert repository.record_worker_availability(availability) == availability
    assert repository.record_feasibility_support(evaluation.evaluation_input_id) == second
    assert repository.get_feasibility_support(first.support_snapshot_id) == first


def test_forged_caller_snapshot_cannot_become_authority_and_no_candidate_api_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "forgery.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    trusted = repository.record_feasibility_support(evaluation.evaluation_input_id)
    forged = replace(trusted, routes=())

    with pytest.raises(TypeError):
        repository.record_feasibility_support(
            evaluation.evaluation_input_id, snapshot=forged
        )
    assert repository.get_feasibility_support(trusted.support_snapshot_id) == trusted
    assert not hasattr(repository, "generate_candidates")
    assert not hasattr(repository, "evaluate_feasibility")


def test_restart_reconstructs_interval_and_snapshot_without_m2_or_plan_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    m2_before = m2_rows(path)
    plan_before = m3_plan_rows(path)
    value = repository.record_feasibility_support(evaluation.evaluation_input_id)
    expected = serialize_feasibility_support(value)
    assert deserialize_feasibility_support(expected) == value

    restarted = M3FeasibilitySupportRepository(path)
    assert serialize_feasibility_support(
        restarted.get_feasibility_support(value.support_snapshot_id)
    ) == expected
    assert m2_rows(path) == m2_before
    assert m3_plan_rows(path) == plan_before

    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(root / "tests/support/feasibility_support_process_probe.py"),
            str(path),
            value.support_snapshot_id,
        ],
        cwd=root,
        env=dict(
            os.environ,
            PYTHONHASHSEED="73",
            PYTHONPATH=str(root / "src"),
            PYTHONDONTWRITEBYTECODE="1",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(completed.stdout) == {
        "serialized": expected,
        "support_snapshot_id": value.support_snapshot_id,
    }
    assert m2_rows(path) == m2_before
    assert m3_plan_rows(path) == plan_before


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE m3_feasibility_source_records SET source_revision=source_revision",
        "DELETE FROM m3_feasibility_source_records",
        "INSERT OR REPLACE INTO m3_feasibility_source_records SELECT * FROM m3_feasibility_source_records",
        "UPDATE m3_feasibility_worker_registry_provenance SET worker_registry_revision=worker_registry_revision",
        "DELETE FROM m3_feasibility_worker_registry_provenance",
        "INSERT OR REPLACE INTO m3_feasibility_worker_registry_provenance SELECT * FROM m3_feasibility_worker_registry_provenance",
        "UPDATE m3_feasibility_worker_registry_captures SET ledger_generation=ledger_generation",
        "DELETE FROM m3_feasibility_worker_registry_captures",
        "INSERT OR REPLACE INTO m3_feasibility_worker_registry_captures SELECT * FROM m3_feasibility_worker_registry_captures",
        "UPDATE m3_feasibility_source_selection_cuts SET source_cut_generation=source_cut_generation",
        "DELETE FROM m3_feasibility_source_selection_cuts",
        "INSERT OR REPLACE INTO m3_feasibility_source_selection_cuts SELECT * FROM m3_feasibility_source_selection_cuts",
        "UPDATE m3_feasibility_support_snapshots SET support_fingerprint=support_fingerprint",
        "DELETE FROM m3_feasibility_support_snapshots",
        "INSERT OR REPLACE INTO m3_feasibility_support_snapshots SELECT * FROM m3_feasibility_support_snapshots",
    ],
)
def test_persisted_sources_and_snapshots_are_immutable(
    tmp_path: Path, recursive: int, operation: str
) -> None:
    path = tmp_path / f"guards-{recursive}.db"
    repository, evaluation, _, _, _, _ = prerequisites(path)
    value = repository.record_feasibility_support(evaluation.evaluation_input_id)

    with repository.transaction() as connection:
        connection.execute(f"PRAGMA recursive_triggers={recursive}")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(operation)
    assert repository.get_feasibility_support(value.support_snapshot_id) == value
