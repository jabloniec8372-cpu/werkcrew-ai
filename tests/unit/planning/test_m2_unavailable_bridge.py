from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum

import pytest

from werkcrew_ai.field.models import (
    AssignmentKind,
    AssignmentState,
    CompletionType,
    EffectType,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2JobExecutionRoot,
    PlanDayRoot,
    PlanDayStatus,
    SystemEffect,
    TaskDefinition,
    TaskState,
    TaskStatus,
    UnavailableReason,
)
from werkcrew_ai.field.repository import (
    FIELD_EVENT,
    DurableOutboxEffect,
    HistoricalPlanDayTransition,
    UnavailableHistoricalReconstruction,
    VerifiedActivationLineage,
)
from werkcrew_ai.field.serialization import serialize_job_execution_root, sha256_text
from werkcrew_ai.planning.m2_bridge import (
    M2EffectSourceIdentity,
    M2M3BridgeSourceError,
    M2UnavailableToM3Bridge,
    OperationalContextStatus,
    _request_fingerprint,
)


NOW = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
EVENT_ID = "018f6f3e-7c45-7ac8-b0b6-6f1f93194c21"
SERVER_EVENT_ID = "6a166438-239d-4b0a-8277-d613db328547"


def _job(
    job_id: str = "job-1",
    revision: int = 4,
    status: TaskStatus = TaskStatus.BLOCKED,
) -> M2JobExecutionRoot:
    task = TaskState(
        TaskDefinition(
            task_id=f"task-{job_id}",
            definition_version=f"task-{job_id}-v1",
            job_id=job_id,
            source_handoff_id=f"handoff-{job_id}",
            source_revision=3,
            business_meaning="historical work",
            completion_type=CompletionType.TASK,
            assignment_kind=AssignmentKind.CREW,
        ),
        status=status,
    )
    assignment = AssignmentState(
        assignment_id=f"assignment-{job_id}",
        job_id=job_id,
        task_id=task.definition.task_id,
        task_definition_version=task.definition.definition_version,
        kind=AssignmentKind.CREW,
        member_worker_ids=("worker-1", "worker-2"),
        plan_day_ids=("plan-1",),
        lead_worker_id="worker-1",
        released_worker_ids=("worker-2",),
    )
    return M2JobExecutionRoot(job_id, (task,), (assignment,), revision)


def _reconstruction(
    *,
    event_type: FieldEventType = FieldEventType.UNAVAILABLE_TODAY_REPORTED,
    roots: tuple[M2JobExecutionRoot, ...] | None = None,
    plan_revision: int = 2,
    effect_type: EffectType = EffectType.UNAVAILABLE_TODAY_RECORDED,
    effect_sha256: str = "e" * 64,
    activation_effect_sha256: str = "a" * 64,
) -> UnavailableHistoricalReconstruction:
    roots = (_job(),) if roots is None else roots
    event = FieldEventInput(
        FieldEventEnvelope(
            event_id=EVENT_ID,
            schema_version=1,
            event_type=event_type,
            actor_id="worker-1",
            occurred_at=NOW,
            plan_day_id="plan-1",
            reason_class=(
                "SICK"
                if event_type is FieldEventType.UNAVAILABLE_TODAY_REPORTED
                else None
            ),
        ),
        SERVER_EVENT_ID,
    )
    effect = SystemEffect(
        effect_type,
        EVENT_ID,
        plan_day_id="plan-1",
        actor_id="worker-1",
        details=(("reason", "SICK"),),
    )
    transition = HistoricalPlanDayTransition(
        FIELD_EVENT,
        "activation-event",
        "b" * 64,
        "activation-server",
        "c" * 64,
        0,
        "d" * 64,
        1,
        "f" * 64,
    )
    return UnavailableHistoricalReconstruction(
        explicit_input=event,
        input_schema_version="m2-field-event-envelope-v1",
        input_payload_sha256="1" * 64,
        reduction_proof_schema_version="m2-reduction-input-proof-v2",
        reduction_proof_sha256="2" * 64,
        plan_day_preimage_sha256="3" * 64,
        plan_day_result_sha256="4" * 64,
        unavailable_effect=DurableOutboxEffect(
            FIELD_EVENT,
            EVENT_ID,
            0,
            effect,
            effect_sha256,
            "RECORDED",
            NOW,
            None,
        ),
        activation_lineage=VerifiedActivationLineage(
            "activation-event",
            "activation-server",
            "b" * 64,
            "c" * 64,
            0,
            activation_effect_sha256,
            1,
            "f" * 64,
            (transition,),
        ),
        plan_day=PlanDayRoot(
            "plan-1",
            "worker-1",
            date(2026, 9, 6),
            NOW,
            PlanDayStatus.ACTIVE,
            worker_available=False,
            confirmed_plan_reference="confirmed-plan-v1",
            plan_day_revision=plan_revision,
        ),
        job_execution_roots=roots,
        job_root_preimage_hashes=tuple(
            (
                root.job_id,
                root.job_execution_revision,
                sha256_text(serialize_job_execution_root(root)),
            )
            for root in roots
        ),
    )


class RecordingRepository:
    def __init__(self, value: UnavailableHistoricalReconstruction) -> None:
        self.value = value
        self.calls: list[tuple[object, ...]] = []

    def reconstruct_unavailable_historical_context(
        self, namespace: str, input_id: str, ordinal: int
    ) -> UnavailableHistoricalReconstruction:
        self.calls.append((namespace, input_id, ordinal))
        return self.value


def _build(value: UnavailableHistoricalReconstruction | None = None):
    repository = RecordingRepository(value or _reconstruction())
    request = M2UnavailableToM3Bridge(repository).build_request(  # type: ignore[arg-type]
        M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
    )
    return repository, request


def test_bridge_uses_one_repository_reconstruction_and_covers_lineage() -> None:
    repository, request = _build()

    assert repository.calls == [(FIELD_EVENT, EVENT_ID, 0)]
    assert request.schema_version == "m2-m3-feasibility-evaluation-request-v2"
    assert request.snapshot.source.reduction_proof_schema_version.endswith("v2")
    assert request.snapshot.activation_lineage.activation_event_id == "activation-event"
    assert request.snapshot.plan_day.plan_day_revision == 2
    assert request.snapshot.context_status is OperationalContextStatus.EVALUATION_REQUIRED
    assert request.snapshot.job_root_preimages[0][:2] == ("job-1", 4)
    assert len(request.request_fingerprint) == 64


def test_nested_collections_are_canonical_tuples_and_frozen() -> None:
    _repository, request = _build()

    assert isinstance(request.snapshot.assignments, tuple)
    assert isinstance(request.snapshot.job_root_preimages, tuple)
    assert isinstance(request.snapshot.assignments[0].member_worker_ids, tuple)
    assert isinstance(request.snapshot.assignments[0].plan_day_ids, tuple)
    assert isinstance(request.snapshot.assignments[0].released_worker_ids, tuple)
    assert isinstance(request.snapshot.activation_lineage.transitions, tuple)
    with pytest.raises((AttributeError, FrozenInstanceError)):
        request.snapshot.assignments += ()  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        request.snapshot.assignments[0].member_worker_ids.append("worker-3")  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        request.snapshot.job_root_preimages.append(("job-x", 0, "x" * 64))  # type: ignore[attr-defined]
    with pytest.raises((AttributeError, TypeError)):
        request.snapshot.activation_lineage.transitions.append(object())  # type: ignore[attr-defined]


def test_semantic_order_is_stable_and_semantic_changes_change_fingerprint() -> None:
    job_a = _job("job-a", 2)
    job_z = _job("job-z", 7)
    _repository, first = _build(_reconstruction(roots=(job_z, job_a)))
    _repository, reordered = _build(_reconstruction(roots=(job_a, job_z)))
    _repository, revised = _build(_reconstruction(plan_revision=3))
    _repository, effect_changed = _build(
        _reconstruction(effect_sha256="9" * 64)
    )
    _repository, activation_changed = _build(
        _reconstruction(activation_effect_sha256="8" * 64)
    )
    _repository, task_changed = _build(
        _reconstruction(roots=(_job(status=TaskStatus.WAITING),))
    )

    assert first.request_fingerprint == reordered.request_fingerprint
    assert first.snapshot.assignments == reordered.snapshot.assignments
    assert revised.request_fingerprint != first.request_fingerprint
    assert effect_changed.request_fingerprint != first.request_fingerprint
    assert activation_changed.request_fingerprint != first.request_fingerprint
    assert task_changed.request_fingerprint != first.request_fingerprint
    with pytest.raises(ValueError, match="fingerprint"):
        replace(first, evaluation_kind="DIFFERENT")


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, datetime):
        return value + timedelta(seconds=1)
    if isinstance(value, date):
        return value + timedelta(days=1)
    if isinstance(value, Enum):
        return next(item for item in type(value) if item is not value)
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return value + "-changed"
    if value is None:
        return "changed"
    if isinstance(value, tuple):
        return (*value, "changed")
    raise AssertionError(f"no semantic mutation for {type(value)!r}")


def test_fingerprint_is_sensitive_to_every_public_semantic_field() -> None:
    _repository, request = _build()
    snapshot = request.snapshot
    baseline = request.request_fingerprint
    mutations = []

    for field in fields(snapshot.source):
        mutations.append(
            replace(snapshot, source=replace(
                snapshot.source,
                **{field.name: _changed(getattr(snapshot.source, field.name))},
            ))
        )
    for field in fields(snapshot.plan_day):
        mutations.append(
            replace(snapshot, plan_day=replace(
                snapshot.plan_day,
                **{field.name: _changed(getattr(snapshot.plan_day, field.name))},
            ))
        )
    for field in fields(snapshot.activation_lineage):
        value = getattr(snapshot.activation_lineage, field.name)
        if field.name == "transitions":
            value = (
                replace(value[0], resulting_sha256="7" * 64),
            )
        else:
            value = _changed(value)
        mutations.append(
            replace(
                snapshot,
                activation_lineage=replace(
                    snapshot.activation_lineage, **{field.name: value}
                ),
            )
        )
    assignment = snapshot.assignments[0]
    for field in fields(assignment):
        mutations.append(
            replace(
                snapshot,
                assignments=(
                    replace(
                        assignment,
                        **{field.name: _changed(getattr(assignment, field.name))},
                    ),
                ),
            )
        )
    mutations.extend(
        (
            replace(snapshot, reason=UnavailableReason.OTHER),
            replace(
                snapshot,
                job_root_preimages=(("job-1", 4, "9" * 64),),
            ),
            replace(
                snapshot,
                context_status=OperationalContextStatus.NO_LINKED_ASSIGNMENTS,
            ),
        )
    )
    assert all(_request_fingerprint(item) != baseline for item in mutations)


def test_certified_empty_scope_is_explicit_not_a_feasibility_verdict() -> None:
    _repository, request = _build(_reconstruction(roots=()))

    assert request.snapshot.context_status is OperationalContextStatus.NO_LINKED_ASSIGNMENTS
    assert request.snapshot.assignments == ()
    assert request.snapshot.job_root_preimages == ()
    assert request.requires_fresh_evaluation is True
    assert request.authoritative_plan_mutation is False


def test_rejects_non_field_and_mismatched_effect() -> None:
    repository = RecordingRepository(_reconstruction())
    bridge = M2UnavailableToM3Bridge(repository)  # type: ignore[arg-type]
    with pytest.raises(M2M3BridgeSourceError, match="FIELD_EVENT"):
        bridge.build_request(M2EffectSourceIdentity("SYSTEM_SIGNAL", EVENT_ID, 0))
    assert repository.calls == []

    bad = RecordingRepository(
        _reconstruction(effect_type=EffectType.PLAN_DAY_ACTIVATED)
    )
    with pytest.raises(M2M3BridgeSourceError, match="exact unavailable"):
        M2UnavailableToM3Bridge(bad).build_request(  # type: ignore[arg-type]
            M2EffectSourceIdentity(FIELD_EVENT, EVENT_ID, 0)
        )
