"""Read-only historical M2 unavailable fact to M3 request boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from werkcrew_ai.field.models import (
    AssignmentKind,
    EffectType,
    FieldEventType,
    PlanDayStatus,
    TaskStatus,
    UnavailableReason,
)
from werkcrew_ai.field.repository import FIELD_EVENT, M2DurableRepository
from werkcrew_ai.field.serialization import canonical_json, sha256_text


REQUEST_SCHEMA_VERSION = "m2-m3-feasibility-evaluation-request-v2"


class OperationalContextStatus(StrEnum):
    EVALUATION_REQUIRED = "EVALUATION_REQUIRED"
    NO_LINKED_ASSIGNMENTS = "NO_LINKED_ASSIGNMENTS"


class M2M3BridgeSourceError(RuntimeError):
    """The selected durable source is not an exact unavailable-today fact."""


@dataclass(frozen=True, slots=True)
class M2EffectSourceIdentity:
    input_namespace: str
    input_id: str
    effect_ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.input_namespace, str) or not self.input_namespace.strip():
            raise ValueError("input_namespace must be nonblank")
        if not isinstance(self.input_id, str) or not self.input_id.strip():
            raise ValueError("input_id must be nonblank")
        if (
            isinstance(self.effect_ordinal, bool)
            or not isinstance(self.effect_ordinal, int)
            or self.effect_ordinal < 0
        ):
            raise ValueError("effect_ordinal must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class UnavailableEffectSource:
    input_namespace: str
    event_id: str
    server_event_id: str
    input_schema_version: str
    input_payload_sha256: str
    reduction_proof_schema_version: str
    reduction_proof_sha256: str
    effect_ordinal: int
    effect_type: str
    effect_sha256: str


@dataclass(frozen=True, slots=True)
class ActivationTransitionContext:
    input_namespace: str
    input_id: str
    input_payload_sha256: str
    server_event_id: str
    receipt_sha256: str
    precondition_revision: int
    precondition_sha256: str
    resulting_revision: int
    resulting_sha256: str


@dataclass(frozen=True, slots=True)
class ActivationLineageContext:
    activation_event_id: str
    activation_server_event_id: str
    activation_input_payload_sha256: str
    activation_receipt_sha256: str
    activation_effect_ordinal: int
    activation_effect_sha256: str
    resulting_plan_day_revision: int
    resulting_plan_day_sha256: str
    transitions: tuple[ActivationTransitionContext, ...]

    def __post_init__(self) -> None:
        transitions = tuple(self.transitions)
        if not all(isinstance(item, ActivationTransitionContext) for item in transitions):
            raise TypeError("activation transitions must be immutable DTOs")
        object.__setattr__(self, "transitions", transitions)


@dataclass(frozen=True, slots=True)
class PlanDayImpactContext:
    plan_day_id: str
    worker_id: str
    business_date: date
    start_at: datetime
    status: PlanDayStatus
    worker_available: bool
    day_close_reported: bool
    start_unknown_escalated: bool
    confirmed_plan_reference: str | None
    plan_day_revision: int
    preimage_sha256: str
    result_sha256: str


@dataclass(frozen=True, slots=True)
class AssignmentImpactContext:
    assignment_id: str
    job_id: str
    job_execution_revision: int
    job_root_sha256: str
    task_id: str
    task_definition_version: str
    assignment_kind: AssignmentKind
    member_worker_ids: tuple[str, ...]
    plan_day_ids: tuple[str, ...]
    lead_worker_id: str | None
    released_worker_ids: tuple[str, ...]
    supersedes_assignment_id: str | None
    task_status: TaskStatus
    task_source_handoff_id: str
    task_source_revision: int
    supersedes_task_id: str | None

    def __post_init__(self) -> None:
        for name in ("member_worker_ids", "plan_day_ids", "released_worker_ids"):
            values = tuple(getattr(self, name))
            if not all(isinstance(item, str) and item.strip() for item in values):
                raise TypeError(f"{name} must contain immutable string identities")
            if len(values) != len(set(values)):
                raise ValueError(f"{name} identities must be unique")
            object.__setattr__(self, name, tuple(sorted(values)))


@dataclass(frozen=True, slots=True)
class OperationalImpactSnapshot:
    source: UnavailableEffectSource
    activation_lineage: ActivationLineageContext
    reason: UnavailableReason
    plan_day: PlanDayImpactContext
    assignments: tuple[AssignmentImpactContext, ...]
    job_root_preimages: tuple[tuple[str, int, str], ...]
    context_status: OperationalContextStatus

    def __post_init__(self) -> None:
        values = tuple(self.assignments)
        if not all(isinstance(item, AssignmentImpactContext) for item in values):
            raise TypeError("assignments must contain immutable impact contexts")
        assignments = tuple(
            sorted(
                values,
                key=lambda item: (item.job_id, item.task_id, item.assignment_id),
            )
        )
        roots = tuple(sorted(tuple(tuple(item) for item in self.job_root_preimages)))
        if not all(
            len(item) == 3
            and isinstance(item[0], str)
            and isinstance(item[1], int)
            and not isinstance(item[1], bool)
            and isinstance(item[2], str)
            for item in roots
        ):
            raise TypeError("job_root_preimages must contain immutable triples")
        object.__setattr__(self, "assignments", assignments)
        object.__setattr__(self, "job_root_preimages", roots)


@dataclass(frozen=True, slots=True)
class M3FeasibilityEvaluationRequest:
    schema_version: str
    evaluation_kind: str
    snapshot: OperationalImpactSnapshot
    requires_fresh_evaluation: bool
    authoritative_plan_mutation: bool
    request_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, OperationalImpactSnapshot):
            raise TypeError("snapshot must be an immutable operational snapshot")
        if self.request_fingerprint != _request_fingerprint_fields(
            self.schema_version,
            self.evaluation_kind,
            self.snapshot,
            self.requires_fresh_evaluation,
            self.authoritative_plan_mutation,
        ):
            raise ValueError("request_fingerprint does not cover this request")


def _transition_document(item: ActivationTransitionContext) -> dict[str, object]:
    return {
        "input_id": item.input_id,
        "input_namespace": item.input_namespace,
        "input_payload_sha256": item.input_payload_sha256,
        "precondition_revision": item.precondition_revision,
        "precondition_sha256": item.precondition_sha256,
        "receipt_sha256": item.receipt_sha256,
        "resulting_revision": item.resulting_revision,
        "resulting_sha256": item.resulting_sha256,
        "server_event_id": item.server_event_id,
    }


def _snapshot_document(snapshot: OperationalImpactSnapshot) -> dict[str, object]:
    return {
        "activation_lineage": {
            "activation_effect_ordinal": snapshot.activation_lineage.activation_effect_ordinal,
            "activation_effect_sha256": snapshot.activation_lineage.activation_effect_sha256,
            "activation_event_id": snapshot.activation_lineage.activation_event_id,
            "activation_input_payload_sha256": snapshot.activation_lineage.activation_input_payload_sha256,
            "activation_receipt_sha256": snapshot.activation_lineage.activation_receipt_sha256,
            "activation_server_event_id": snapshot.activation_lineage.activation_server_event_id,
            "resulting_plan_day_revision": snapshot.activation_lineage.resulting_plan_day_revision,
            "resulting_plan_day_sha256": snapshot.activation_lineage.resulting_plan_day_sha256,
            "transitions": [
                _transition_document(item)
                for item in snapshot.activation_lineage.transitions
            ],
        },
        "assignments": [
            {
                "assignment_id": item.assignment_id,
                "assignment_kind": item.assignment_kind.value,
                "job_execution_revision": item.job_execution_revision,
                "job_id": item.job_id,
                "job_root_sha256": item.job_root_sha256,
                "lead_worker_id": item.lead_worker_id,
                "member_worker_ids": list(item.member_worker_ids),
                "plan_day_ids": list(item.plan_day_ids),
                "released_worker_ids": list(item.released_worker_ids),
                "supersedes_assignment_id": item.supersedes_assignment_id,
                "supersedes_task_id": item.supersedes_task_id,
                "task_definition_version": item.task_definition_version,
                "task_id": item.task_id,
                "task_source_handoff_id": item.task_source_handoff_id,
                "task_source_revision": item.task_source_revision,
                "task_status": item.task_status.value,
            }
            for item in snapshot.assignments
        ],
        "context_status": snapshot.context_status.value,
        "job_root_preimages": [list(item) for item in snapshot.job_root_preimages],
        "plan_day": {
            "business_date": snapshot.plan_day.business_date.isoformat(),
            "confirmed_plan_reference": snapshot.plan_day.confirmed_plan_reference,
            "day_close_reported": snapshot.plan_day.day_close_reported,
            "plan_day_id": snapshot.plan_day.plan_day_id,
            "plan_day_revision": snapshot.plan_day.plan_day_revision,
            "preimage_sha256": snapshot.plan_day.preimage_sha256,
            "result_sha256": snapshot.plan_day.result_sha256,
            "start_at": snapshot.plan_day.start_at.isoformat(),
            "start_unknown_escalated": snapshot.plan_day.start_unknown_escalated,
            "status": snapshot.plan_day.status.value,
            "worker_available": snapshot.plan_day.worker_available,
            "worker_id": snapshot.plan_day.worker_id,
        },
        "reason": snapshot.reason.value,
        "source": {
            "effect_ordinal": snapshot.source.effect_ordinal,
            "effect_sha256": snapshot.source.effect_sha256,
            "effect_type": snapshot.source.effect_type,
            "event_id": snapshot.source.event_id,
            "input_namespace": snapshot.source.input_namespace,
            "input_payload_sha256": snapshot.source.input_payload_sha256,
            "input_schema_version": snapshot.source.input_schema_version,
            "reduction_proof_schema_version": snapshot.source.reduction_proof_schema_version,
            "reduction_proof_sha256": snapshot.source.reduction_proof_sha256,
            "server_event_id": snapshot.source.server_event_id,
        },
    }


def _request_fingerprint_fields(
    schema_version: str,
    evaluation_kind: str,
    snapshot: OperationalImpactSnapshot,
    requires_fresh_evaluation: bool,
    authoritative_plan_mutation: bool,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "document_type": "M3FeasibilityEvaluationRequest",
                "payload": {
                    "authoritative_plan_mutation": authoritative_plan_mutation,
                    "evaluation_kind": evaluation_kind,
                    "requires_fresh_evaluation": requires_fresh_evaluation,
                    "snapshot": _snapshot_document(snapshot),
                },
                "schema_version": schema_version,
            }
        )
    )


def _request_fingerprint(snapshot: OperationalImpactSnapshot) -> str:
    return _request_fingerprint_fields(
        REQUEST_SCHEMA_VERSION,
        "FRESH_FEASIBILITY",
        snapshot,
        True,
        False,
    )


class M2UnavailableToM3Bridge:
    """Build a deterministic request; never calculate or apply a replan."""

    def __init__(self, repository: M2DurableRepository) -> None:
        self.repository = repository

    def build_request(
        self,
        source_identity: M2EffectSourceIdentity,
    ) -> M3FeasibilityEvaluationRequest:
        if source_identity.input_namespace != FIELD_EVENT:
            raise M2M3BridgeSourceError("M3 unavailable bridge requires FIELD_EVENT")

        reconstructed = self.repository.reconstruct_unavailable_historical_context(
            source_identity.input_namespace,
            source_identity.input_id,
            source_identity.effect_ordinal,
        )
        event = reconstructed.explicit_input.event
        effect = reconstructed.unavailable_effect.effect
        try:
            expected_reason = UnavailableReason(event.reason_class)
        except (TypeError, ValueError) as error:
            raise M2M3BridgeSourceError(
                "unavailable source has no canonical reason"
            ) from error
        if (
            event.event_type is not FieldEventType.UNAVAILABLE_TODAY_REPORTED
            or effect.effect_type is not EffectType.UNAVAILABLE_TODAY_RECORDED
            or effect.source_id != event.event_id
            or effect.actor_id != event.actor_id
            or effect.plan_day_id != event.plan_day_id
            or effect.job_id is not None
            or effect.task_id is not None
            or effect.assignment_id is not None
            or effect.stage_id is not None
            or effect.directive_id is not None
            or effect.details != (("reason", expected_reason.value),)
        ):
            raise M2M3BridgeSourceError(
                "durable effect is not the exact unavailable-today result"
            )

        plan = reconstructed.plan_day
        if (
            event.plan_day_id is None
            or plan.plan_day_id != event.plan_day_id
            or plan.worker_id != event.actor_id
            or plan.worker_available
        ):
            raise M2M3BridgeSourceError(
                "historical plan-day result does not prove the unavailable fact"
            )
        root_hashes = {
            (job_id, revision): digest
            for job_id, revision, digest in reconstructed.job_root_preimage_hashes
        }
        assignments: list[AssignmentImpactContext] = []
        linked_roots: set[tuple[str, int, str]] = set()
        for root in reconstructed.job_execution_roots:
            digest = root_hashes.get((root.job_id, root.job_execution_revision))
            if digest is None:
                raise M2M3BridgeSourceError(
                    "historical job root lacks its preimage hash"
                )
            for assignment in root.assignments:
                if plan.plan_day_id not in assignment.plan_day_ids:
                    continue
                task = root.task(assignment.task_id)
                if task is None:
                    raise M2M3BridgeSourceError(
                        "historical linked assignment lacks its task"
                    )
                linked_roots.add((root.job_id, root.job_execution_revision, digest))
                assignments.append(
                    AssignmentImpactContext(
                        assignment_id=assignment.assignment_id,
                        job_id=assignment.job_id,
                        job_execution_revision=root.job_execution_revision,
                        job_root_sha256=digest,
                        task_id=assignment.task_id,
                        task_definition_version=assignment.task_definition_version,
                        assignment_kind=assignment.kind,
                        member_worker_ids=assignment.member_worker_ids,
                        plan_day_ids=assignment.plan_day_ids,
                        lead_worker_id=assignment.lead_worker_id,
                        released_worker_ids=assignment.released_worker_ids,
                        supersedes_assignment_id=assignment.supersedes_assignment_id,
                        task_status=task.status,
                        task_source_handoff_id=task.definition.source_handoff_id,
                        task_source_revision=task.definition.source_revision,
                        supersedes_task_id=task.definition.supersedes_task_id,
                    )
                )
        if linked_roots != set(reconstructed.job_root_preimage_hashes):
            raise M2M3BridgeSourceError(
                "certified historical proof contains an unlinked job root"
            )

        lineage = reconstructed.activation_lineage
        snapshot = OperationalImpactSnapshot(
            source=UnavailableEffectSource(
                input_namespace=source_identity.input_namespace,
                event_id=event.event_id,
                server_event_id=reconstructed.explicit_input.server_event_id,
                input_schema_version=reconstructed.input_schema_version,
                input_payload_sha256=reconstructed.input_payload_sha256,
                reduction_proof_schema_version=reconstructed.reduction_proof_schema_version,
                reduction_proof_sha256=reconstructed.reduction_proof_sha256,
                effect_ordinal=reconstructed.unavailable_effect.effect_ordinal,
                effect_type=effect.effect_type.value,
                effect_sha256=reconstructed.unavailable_effect.effect_sha256,
            ),
            activation_lineage=ActivationLineageContext(
                activation_event_id=lineage.activation_event_id,
                activation_server_event_id=lineage.activation_server_event_id,
                activation_input_payload_sha256=lineage.activation_input_payload_sha256,
                activation_receipt_sha256=lineage.activation_receipt_sha256,
                activation_effect_ordinal=lineage.activation_effect_ordinal,
                activation_effect_sha256=lineage.activation_effect_sha256,
                resulting_plan_day_revision=lineage.resulting_plan_day_revision,
                resulting_plan_day_sha256=lineage.resulting_plan_day_sha256,
                transitions=tuple(
                    ActivationTransitionContext(
                        input_namespace=item.input_namespace,
                        input_id=item.input_id,
                        input_payload_sha256=item.input_payload_sha256,
                        server_event_id=item.server_event_id,
                        receipt_sha256=item.receipt_sha256,
                        precondition_revision=item.precondition_revision,
                        precondition_sha256=item.precondition_sha256,
                        resulting_revision=item.resulting_revision,
                        resulting_sha256=item.resulting_sha256,
                    )
                    for item in lineage.transitions
                ),
            ),
            reason=expected_reason,
            plan_day=PlanDayImpactContext(
                plan_day_id=plan.plan_day_id,
                worker_id=plan.worker_id,
                business_date=plan.business_date,
                start_at=plan.start_at,
                status=plan.status,
                worker_available=plan.worker_available,
                day_close_reported=plan.day_close_reported,
                start_unknown_escalated=plan.start_unknown_escalated,
                confirmed_plan_reference=plan.confirmed_plan_reference,
                plan_day_revision=plan.plan_day_revision,
                preimage_sha256=reconstructed.plan_day_preimage_sha256,
                result_sha256=reconstructed.plan_day_result_sha256,
            ),
            assignments=tuple(assignments),
            job_root_preimages=tuple(linked_roots),
            context_status=(
                OperationalContextStatus.EVALUATION_REQUIRED
                if assignments
                else OperationalContextStatus.NO_LINKED_ASSIGNMENTS
            ),
        )
        return M3FeasibilityEvaluationRequest(
            schema_version=REQUEST_SCHEMA_VERSION,
            evaluation_kind="FRESH_FEASIBILITY",
            snapshot=snapshot,
            requires_fresh_evaluation=True,
            authoritative_plan_mutation=False,
            request_fingerprint=_request_fingerprint(snapshot),
        )


__all__ = [
    "ActivationLineageContext",
    "ActivationTransitionContext",
    "AssignmentImpactContext",
    "M2EffectSourceIdentity",
    "M2M3BridgeSourceError",
    "M2UnavailableToM3Bridge",
    "M3FeasibilityEvaluationRequest",
    "OperationalContextStatus",
    "OperationalImpactSnapshot",
    "PlanDayImpactContext",
    "REQUEST_SCHEMA_VERSION",
    "UnavailableEffectSource",
]
