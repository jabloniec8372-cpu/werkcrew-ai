from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from werkcrew_ai.catalog.m8_demo import M8_CONFIGURATION
from werkcrew_ai.field.serialization import sha256_text
from werkcrew_ai.planning.feasibility_support import (
    AvailabilityEvidence,
    AvailabilityKnowledge,
    AvailabilityWindowEvidence,
    ConstraintKnowledge,
    M3FeasibilitySupportValidationError,
    ReadinessEvidence,
    ReadinessState,
    TaskConstraintSource,
    TaskReadinessSource,
    WorkerAvailabilitySource,
    restore_source,
    source_semantic_json,
)


NOW = datetime(2026, 9, 10, 8, tzinfo=timezone.utc)


def constraint(**changes) -> TaskConstraintSource:
    values = dict(
        job_id="job-1",
        task_id="task-1",
        task_definition_version="task-1-v1",
        source_handoff_id="handoff-1",
        source_revision_number=1,
        m8_sku="painting.strip",
        duration_seconds=3600,
        location_reference="job-1-location",
        location_fingerprint=sha256_text("job-1-location-v1"),
        customer_window_state=ConstraintKnowledge.KNOWN,
        customer_window_start=NOW,
        customer_window_end=NOW + timedelta(hours=4),
        hard_deadline_state=ConstraintKnowledge.KNOWN,
        hard_deadline=NOW + timedelta(hours=6),
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference="trusted-task-planning-source-v1",
    )
    values.update(changes)
    return TaskConstraintSource(**values)


def test_canonical_task_binding_derives_exact_m8_skill_and_vehicle_requirement() -> None:
    value = constraint()
    profile = M8_CONFIGURATION.profile("painting.strip")

    assert value.required_skill_key == profile.required_skill_key == "painting.strip"
    assert value.minimum_skill_level == profile.min_skill_level
    assert value.required_vehicle_class == profile.vehicle_class.value == "LIGHT"
    assert value.m8_service_catalog_version == M8_CONFIGURATION.service_catalog_version
    assert value.m8_planning_profile_version == M8_CONFIGURATION.sku_planning_profile_version

    with pytest.raises(TypeError, match="required_skill_key"):
        TaskConstraintSource(
            **{
                field: getattr(value, field)
                for field in (
                    "job_id",
                    "task_id",
                    "task_definition_version",
                    "source_handoff_id",
                    "source_revision_number",
                    "m8_sku",
                    "duration_seconds",
                    "location_reference",
                    "location_fingerprint",
                    "customer_window_state",
                    "customer_window_start",
                    "customer_window_end",
                    "hard_deadline_state",
                    "hard_deadline",
                    "source_revision",
                    "previous_source_record_id",
                    "provenance_reference",
                )
            },
            required_skill_key="caller-forgery",
        )


def test_unknown_m8_mapping_fails_closed() -> None:
    with pytest.raises(M3FeasibilitySupportValidationError, match="UNKNOWN_M8_SKU"):
        constraint(m8_sku="invented.skill")


def test_missing_availability_is_unknown_and_empty_known_windows_mean_unavailable() -> None:
    missing = AvailabilityEvidence(
        subject_id="worker-1",
        knowledge=AvailabilityKnowledge.UNKNOWN,
        coverage_start=None,
        coverage_end=None,
        available_windows=(),
        source_record_id=None,
        source_fingerprint=None,
        source_revision=None,
    )
    source = WorkerAvailabilitySource(
        worker_id="worker-1",
        coverage_start=NOW,
        coverage_end=NOW + timedelta(hours=8),
        available_windows=(),
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference="trusted-roster-v1",
    )
    known_unavailable = AvailabilityEvidence(
        subject_id=source.worker_id,
        knowledge=AvailabilityKnowledge.KNOWN,
        coverage_start=source.coverage_start,
        coverage_end=source.coverage_end,
        available_windows=source.available_windows,
        source_record_id=source.source_record_id,
        source_fingerprint=source.source_fingerprint,
        source_revision=source.source_revision,
    )

    assert missing.knowledge is AvailabilityKnowledge.UNKNOWN
    assert known_unavailable.knowledge is AvailabilityKnowledge.KNOWN
    assert missing != known_unavailable


def test_availability_windows_have_canonical_instant_and_union_representation() -> None:
    source = WorkerAvailabilitySource(
        worker_id="worker-1",
        coverage_start=NOW,
        coverage_end=NOW + timedelta(hours=8),
        available_windows=(
            AvailabilityWindowEvidence(
                start_at=NOW + timedelta(hours=2),
                end_at=NOW + timedelta(hours=4),
            ),
            AvailabilityWindowEvidence(
                start_at=NOW,
                end_at=NOW + timedelta(hours=2),
            ),
        ),
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference="trusted-roster-v1",
    )

    assert source.available_windows == (
        AvailabilityWindowEvidence(start_at=NOW, end_at=NOW + timedelta(hours=4)),
    )


def test_expected_and_unknown_readiness_never_become_ready_by_time_or_empty_blockers() -> None:
    expected = TaskReadinessSource(
        job_id="job-1",
        task_id="task-1",
        task_definition_version="task-1-v1",
        status=ReadinessState.EXPECTED,
        expected_at=NOW - timedelta(days=1),
        blocker_references=(),
        source_revision=0,
        previous_source_record_id=None,
        provenance_reference="trusted-material-source-v1",
    )
    unknown = ReadinessEvidence(
        job_id="job-1",
        task_id="task-1",
        task_definition_version="task-1-v1",
        status=ReadinessState.UNKNOWN,
        expected_at=None,
        blocker_references=(),
        source_record_id=None,
        source_fingerprint=None,
        source_revision=None,
    )

    assert expected.status is ReadinessState.EXPECTED
    assert unknown.status is ReadinessState.UNKNOWN
    assert expected.status is not ReadinessState.READY
    assert unknown.status is not ReadinessState.READY


def test_identical_source_semantics_reproduce_identity_and_material_change_does_not() -> None:
    first = constraint()
    same = constraint()
    changed = replace(first, duration_seconds=7200)

    assert same.source_record_id == first.source_record_id
    assert same.source_fingerprint == first.source_fingerprint
    assert changed.source_record_id != first.source_record_id
    assert restore_source(
        source_semantic_json(first), first.source_fingerprint, first.source_record_id
    ) == first
