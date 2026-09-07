"""Crash-safe SQLite boundary around the pure deterministic M2 reducer."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping

from werkcrew_ai.field.models import (
    DirectiveClass,
    DirectiveRoot,
    EffectType,
    ExplicitInput,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2JobExecutionRoot,
    M2ReductionScope,
    PlanDayRoot,
    PolicyTimeContext,
    ProcessedEventLedger,
    ProcessedEventReceipt,
    Reduction,
    ReductionOutcome,
    RootDelta,
    RootKind,
    SystemEffect,
    SystemSignal,
    SystemSignalType,
    WorkerIdentityRegistry,
)
from werkcrew_ai.field.reducer import reduce
from werkcrew_ai.field.serialization import (
    ImmutablePrecondition,
    PreconditionVector,
    ResultingRevision,
    ResultingRevisionVector,
    RootAccess,
    RootPrecondition,
    RoutingVector,
    SerializationError,
    canonical_json,
    deserialize_directive_root,
    deserialize_effect,
    deserialize_effects,
    deserialize_field_event_envelope,
    deserialize_job_execution_root,
    deserialize_plan_day_root,
    deserialize_policy_time_context,
    deserialize_precondition_vector,
    deserialize_processed_event_receipt,
    deserialize_resulting_revision_vector,
    deserialize_routing_vector,
    deserialize_string_tuple,
    deserialize_system_signal,
    deserialize_worker_registry,
    serialize_directive_root,
    serialize_effect,
    serialize_effects,
    serialize_field_event_envelope,
    serialize_job_execution_root,
    serialize_plan_day_root,
    serialize_policy_time_context,
    serialize_precondition_vector,
    serialize_processed_event_receipt,
    serialize_resulting_revision_vector,
    serialize_routing_vector,
    serialize_string_tuple,
    serialize_system_signal,
    serialize_worker_registry,
    sha256_text,
    verify_canonical_document,
)
from werkcrew_ai.intake.publication import M1BoundaryPublicationRepository
from werkcrew_ai.persistence import SqlitePersistence


FIELD_EVENT = "FIELD_EVENT"
SYSTEM_SIGNAL = "SYSTEM_SIGNAL"
class M2PersistenceError(RuntimeError):
    """Base error for durable M2 invariants."""


class M2NotFoundError(M2PersistenceError):
    """A canonical stable reference cannot be resolved."""


class M2ConflictError(M2PersistenceError):
    """An immutable identity was presented with different content."""


class M2StorageIntegrityError(M2PersistenceError):
    """Stored canonical data or one of its projections is malformed."""


class M2StaleRevisionError(M2PersistenceError):
    """A numeric/hash precondition no longer matches durable state."""


class M2InputPendingError(M2PersistenceError):
    """An input depends on an earlier server-event claim still being processed."""


class M2InsufficientHistoricalEvidenceError(M2StorageIntegrityError):
    """A completed input predates the historical proof required by a consumer."""


@dataclass(frozen=True, slots=True)
class IngressAcceptance:
    input_namespace: str
    input_id: str
    processing_status: str
    inserted: bool
    server_claim_owner_input_id: str | None = None


@dataclass(frozen=True, slots=True)
class DurableReductionResult:
    input_namespace: str
    input_id: str
    outcome: ReductionOutcome
    root_deltas: tuple[RootDelta, ...]
    appended_receipt: ProcessedEventReceipt | None
    emitted_effects: tuple[SystemEffect, ...]
    response_effects: tuple[SystemEffect, ...]
    server_event_id: str | None
    missing_requirements: tuple[str, ...]
    reason_codes: tuple[str, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class DurableOutboxEffect:
    input_namespace: str
    input_id: str
    effect_ordinal: int
    effect: SystemEffect
    effect_sha256: str
    dispatch_status: str
    recorded_at: datetime
    dispatched_at: datetime | None


@dataclass(frozen=True, slots=True)
class HistoricalPlanDayTransition:
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
class VerifiedActivationLineage:
    activation_event_id: str
    activation_server_event_id: str
    activation_input_payload_sha256: str
    activation_receipt_sha256: str
    activation_effect_ordinal: int
    activation_effect_sha256: str
    resulting_plan_day_revision: int
    resulting_plan_day_sha256: str
    transitions: tuple[HistoricalPlanDayTransition, ...]

    def __post_init__(self) -> None:
        transitions = tuple(self.transitions)
        if not all(
            isinstance(item, HistoricalPlanDayTransition) for item in transitions
        ):
            raise TypeError("activation transitions must be immutable DTOs")
        object.__setattr__(self, "transitions", transitions)


@dataclass(frozen=True, slots=True)
class UnavailableHistoricalReconstruction:
    explicit_input: FieldEventInput
    input_schema_version: str
    input_payload_sha256: str
    reduction_proof_schema_version: str
    reduction_proof_sha256: str
    plan_day_preimage_sha256: str
    plan_day_result_sha256: str
    unavailable_effect: DurableOutboxEffect
    activation_lineage: VerifiedActivationLineage
    plan_day: PlanDayRoot
    job_execution_roots: tuple[M2JobExecutionRoot, ...]
    job_root_preimage_hashes: tuple[tuple[str, int, str], ...]

    def __post_init__(self) -> None:
        roots = tuple(self.job_execution_roots)
        hashes = tuple(tuple(item) for item in self.job_root_preimage_hashes)
        if not all(isinstance(item, M2JobExecutionRoot) for item in roots):
            raise TypeError("historical job roots must be immutable M2 roots")
        if not all(
            len(item) == 3
            and isinstance(item[0], str)
            and isinstance(item[1], int)
            and not isinstance(item[1], bool)
            and isinstance(item[2], str)
            for item in hashes
        ):
            raise TypeError("historical job-root hashes are malformed")
        object.__setattr__(self, "job_execution_roots", roots)
        object.__setattr__(self, "job_root_preimage_hashes", tuple(sorted(hashes)))


@dataclass(frozen=True, slots=True)
class _Hydration:
    scope: M2ReductionScope
    routing: RoutingVector
    preconditions: PreconditionVector
    complete_plan_day_assignment_scope_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ReductionInputProof:
    schema_version: str
    worker_registry: WorkerIdentityRegistry
    job_execution_roots: tuple[M2JobExecutionRoot, ...]
    plan_day_roots: tuple[PlanDayRoot, ...]
    directive_roots: tuple[DirectiveRoot, ...]
    processed_events: ProcessedEventLedger
    evidence_identities: tuple["_EvidenceIdentityProof", ...] = ()
    evidence_usages: tuple["_EvidenceUsageProof", ...] = ()
    complete_plan_day_assignment_scope_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class _EvidenceIdentityProof:
    evidence_id: str
    evidence_kind: str
    content_reference: str
    owner_job_id: str | None
    owner_task_id: str | None
    owner_assignment_id: str | None
    owner_stage_id: str | None
    canonical_evidence_json: str
    content_sha256: str


@dataclass(frozen=True, slots=True, order=True)
class _EvidenceUsageProof:
    input_namespace: str
    input_id: str
    evidence_id: str
    job_id: str | None
    task_id: str | None
    assignment_id: str | None
    stage_id: str | None
    scope_json: str
    scope_sha256: str


FaultInjector = Callable[[str], None]


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _task_route_payload(task) -> dict[str, object]:
    definition = task.definition
    return {
        "assignment_kind": definition.assignment_kind.value,
        "business_meaning": definition.business_meaning,
        "completion_type": definition.completion_type.value,
        "definition_version": definition.definition_version,
        "job_id": definition.job_id,
        "required_evidence": [item.value for item in definition.required_evidence],
        "required_postconditions": list(definition.required_postconditions),
        "requires_quantity": definition.requires_quantity,
        "source_handoff_id": definition.source_handoff_id,
        "source_revision": definition.source_revision,
        "stage_ids": list(definition.stage_ids),
        "supersedes_task_id": definition.supersedes_task_id,
        "task_id": definition.task_id,
    }


def _assignment_route_payload(assignment) -> dict[str, object]:
    return {
        "assignment_id": assignment.assignment_id,
        "assignment_kind": assignment.kind.value,
        "job_id": assignment.job_id,
        "lead_worker_id": assignment.lead_worker_id,
        "member_worker_ids": list(assignment.member_worker_ids),
        "plan_day_ids": list(assignment.plan_day_ids),
        "supersedes_assignment_id": assignment.supersedes_assignment_id,
        "task_definition_version": assignment.task_definition_version,
        "task_id": assignment.task_id,
    }


def _evidence_payload(item) -> dict[str, str]:
    return {
        "content_reference": item.content_reference,
        "evidence_id": item.evidence_id,
        "kind": item.kind.value,
    }


def _evidence_scope_payload(event: FieldEventEnvelope) -> dict[str, str | None]:
    return {
        "assignment_id": event.assignment_id,
        "job_id": event.job_id,
        "stage_id": event.stage_id,
        "task_id": event.task_id,
    }


def _evidence_identity_document(
    item,
    scope: tuple[str | None, str | None, str | None, str | None],
) -> str:
    return canonical_json(
        {
            "evidence": _evidence_payload(item),
            "owner_scope": {
                "assignment_id": scope[2],
                "job_id": scope[0],
                "stage_id": scope[3],
                "task_id": scope[1],
            },
        }
    )


def _directive_stream(root: DirectiveRoot) -> tuple[str, str]:
    if root.plan_day_id is not None:
        return "PLAN_DAY", root.plan_day_id
    if root.assignment_id is not None:
        return "ASSIGNMENT", root.assignment_id
    if root.task_id is not None:
        return "TASK", root.task_id
    if root.job_id is not None:
        return "JOB", root.job_id
    return "WORKER", root.worker_id


def _topological(items: Iterable[object], identity, predecessor) -> tuple[object, ...]:
    remaining = {identity(item): item for item in items}
    ordered: list[object] = []
    emitted: set[str] = set()
    while remaining:
        ready = sorted(
            (
                item
                for item in remaining.values()
                if predecessor(item) is None or predecessor(item) in emitted
            ),
            key=identity,
        )
        if not ready:
            raise M2StorageIntegrityError("supersession graph is cyclic or incomplete")
        for item in ready:
            item_id = identity(item)
            ordered.append(item)
            emitted.add(item_id)
            del remaining[item_id]
    return tuple(ordered)


class M2DurableRepository(SqlitePersistence):
    """Own durable M2 roots while keeping ``reduce`` pure and deterministic."""

    def __init__(
        self,
        database_path,
        *,
        migrations_directory=None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        keyword = {} if migrations_directory is None else {
            "migrations_directory": migrations_directory
        }
        super().__init__(database_path, **keyword)
        self._fault_injector = fault_injector

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    # -- Explicit caller-provided root creation; never business materialization. --

    def create_worker_registry(
        self,
        registry: WorkerIdentityRegistry,
    ) -> WorkerIdentityRegistry:
        if registry.registry_revision != 0:
            raise ValueError("initial worker registry revision must be zero")
        raw = serialize_worker_registry(registry)
        digest = sha256_text(raw)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM m2_worker_registry WHERE registry_key = 'GLOBAL'"
            ).fetchone()
            if existing is not None:
                stored = self._worker_registry_from_connection(connection)
                if stored != registry or existing["canonical_root_json"] != raw:
                    raise M2ConflictError("worker registry already exists with other content")
                return stored
            try:
                connection.execute(
                    """
                    INSERT INTO m2_worker_registry(
                        registry_key, root_schema_version, registry_revision,
                        canonical_root_json, content_sha256
                    ) VALUES('GLOBAL', 'm2-worker-registry-v1', ?, ?, ?)
                    """,
                    (registry.registry_revision, raw, digest),
                )
                connection.executemany(
                    "INSERT INTO m2_worker_identities(worker_id) VALUES(?)",
                    ((worker_id,) for worker_id in registry.worker_ids),
                )
            except sqlite3.IntegrityError as error:
                raise M2ConflictError("worker registry identity conflict") from error
        return registry

    def create_plan_day_root(self, root: PlanDayRoot) -> PlanDayRoot:
        if root.plan_day_revision != 0:
            raise ValueError("initial plan day revision must be zero")
        raw = serialize_plan_day_root(root)
        digest = sha256_text(raw)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM m2_plan_day_roots WHERE plan_day_id = ?",
                (root.plan_day_id,),
            ).fetchone()
            if existing is not None:
                stored = self._plan_day_from_row(existing)
                if stored != root or existing["canonical_root_json"] != raw:
                    raise M2ConflictError("plan day root identity conflict")
                return stored
            try:
                connection.execute(
                    """
                    INSERT INTO m2_plan_day_roots(
                        plan_day_id, root_schema_version, worker_id, business_date,
                        status, plan_day_revision, canonical_root_json, content_sha256
                    ) VALUES(?, 'm2-plan-day-root-v1', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        root.plan_day_id,
                        root.worker_id,
                        root.business_date.isoformat(),
                        root.status.value,
                        root.plan_day_revision,
                        raw,
                        digest,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise M2ConflictError("plan day root references invalid authority") from error
        return root

    def create_job_execution_root(
        self,
        root: M2JobExecutionRoot,
    ) -> M2JobExecutionRoot:
        if root.job_execution_revision != 0:
            raise ValueError("initial job execution revision must be zero")
        raw = serialize_job_execution_root(root)
        digest = sha256_text(raw)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM m2_job_execution_roots WHERE job_id = ?",
                (root.job_id,),
            ).fetchone()
            if existing is not None:
                stored = self._job_root_from_connection(connection, existing)
                if stored != root or existing["canonical_root_json"] != raw:
                    raise M2ConflictError("job execution root identity conflict")
                return stored
            try:
                connection.execute(
                    """
                    INSERT INTO m2_job_execution_roots(
                        job_id, root_schema_version, job_execution_revision,
                        canonical_root_json, content_sha256
                    ) VALUES(?, 'm2-job-execution-root-v1', ?, ?, ?)
                    """,
                    (root.job_id, root.job_execution_revision, raw, digest),
                )
                self._insert_job_projections(connection, root)
                self._insert_initial_root_evidence(connection, root)
            except sqlite3.IntegrityError as error:
                raise M2ConflictError(
                    "job execution root has invalid or occupied stable references"
                ) from error
        return root

    def create_directive_root(self, root: DirectiveRoot) -> DirectiveRoot:
        if root.directive_revision != 0:
            raise ValueError("initial directive revision must be zero")
        raw = serialize_directive_root(root)
        digest = sha256_text(raw)
        stream_kind, stream_id = _directive_stream(root)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM m2_directive_roots WHERE directive_id = ?",
                (root.directive_id,),
            ).fetchone()
            if existing is not None:
                stored = self._directive_from_row(existing)
                if stored != root or existing["canonical_root_json"] != raw:
                    raise M2ConflictError("directive root identity conflict")
                return stored
            self._validate_new_directive(connection, root, stream_kind, stream_id)
            definition = root.definition
            try:
                connection.execute(
                    """
                    INSERT INTO m2_directive_roots(
                        directive_id, root_schema_version, worker_id, job_id,
                        task_id, assignment_id, plan_day_id, stream_kind, stream_id,
                        issuance_sequence, supersedes_directive_id, directive_type,
                        directive_class, issued_at, escalation_due_at,
                        proposed_plan_reference, delivery_evidence,
                        acknowledged_event_id, exception_event_id, e1_escalated,
                        stop_in_force, directive_revision, canonical_root_json,
                        content_sha256
                    ) VALUES(
                        ?, 'm2-directive-root-v1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        root.directive_id,
                        root.worker_id,
                        root.job_id,
                        root.task_id,
                        root.assignment_id,
                        root.plan_day_id,
                        stream_kind,
                        stream_id,
                        root.issuance_sequence,
                        root.supersedes_directive_id,
                        definition.directive_type.value,
                        definition.directive_class.value,
                        definition.issued_at.isoformat(),
                        definition.escalation_due_at.isoformat()
                        if definition.escalation_due_at is not None
                        else None,
                        definition.proposed_plan_reference,
                        root.delivery_evidence.value,
                        root.acknowledged_event_id,
                        root.exception_event_id,
                        int(root.e1_escalated),
                        int(root.stop_in_force),
                        root.directive_revision,
                        raw,
                        digest,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise M2ConflictError(
                    "directive scope, ordering, or stable identity conflicts"
                ) from error
        return root

    # -- Strict root reads. --

    def get_worker_registry(self) -> WorkerIdentityRegistry:
        with self._connect() as connection:
            return self._worker_registry_from_connection(connection)

    def get_job_execution_root(self, job_id: str) -> M2JobExecutionRoot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM m2_job_execution_roots WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise M2NotFoundError(f"unknown job execution root: {job_id}")
            return self._job_root_from_connection(connection, row)

    def get_plan_day_root(self, plan_day_id: str) -> PlanDayRoot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM m2_plan_day_roots WHERE plan_day_id = ?",
                (plan_day_id,),
            ).fetchone()
            if row is None:
                raise M2NotFoundError(f"unknown plan day root: {plan_day_id}")
            return self._plan_day_from_row(row)

    def get_directive_root(self, directive_id: str) -> DirectiveRoot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM m2_directive_roots WHERE directive_id = ?",
                (directive_id,),
            ).fetchone()
            if row is None:
                raise M2NotFoundError(f"unknown directive root: {directive_id}")
            return self._directive_from_row(row)

    def load_processed_event_ledger(self) -> ProcessedEventLedger:
        with self._connect() as connection:
            receipts = self._load_receipts(connection, event_ids=None)
        return ProcessedEventLedger(receipts)

    def reconstruct_unavailable_historical_context(
        self,
        input_namespace: str,
        input_id: str,
        effect_ordinal: int,
    ) -> UnavailableHistoricalReconstruction:
        """Verify and reconstruct one unavailable event from one read snapshot."""

        if input_namespace != FIELD_EVENT:
            raise M2StorageIntegrityError(
                "unavailable historical reconstruction requires FIELD_EVENT"
            )
        if (
            isinstance(effect_ordinal, bool)
            or not isinstance(effect_ordinal, int)
            or effect_ordinal < 0
        ):
            raise ValueError("effect_ordinal must be a non-negative integer")

        connection = self._connect_read_only()
        try:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM m2_input_inbox WHERE input_namespace=? AND input_id=?",
                (input_namespace, input_id),
            ).fetchone()
            if row is None:
                raise M2NotFoundError(
                    f"unknown inbox input: {input_namespace}/{input_id}"
                )
            self._verify_inbox_input(row)
            if row["processing_status"] != "COMPLETED":
                raise M2InputPendingError(
                    f"inbox input is not completed: {input_namespace}/{input_id}"
                )
            completed = self._completed_result_from_row(
                connection,
                row,
                replayed=True,
                validate_current_roots=False,
            )
            explicit_input = self._explicit_input_from_row(row)
            if (
                not isinstance(explicit_input, FieldEventInput)
                or explicit_input.event.event_type
                is not FieldEventType.UNAVAILABLE_TODAY_REPORTED
                or completed.outcome is not ReductionOutcome.APPLIED
            ):
                raise M2StorageIntegrityError(
                    "source is not an applied UNAVAILABLE_TODAY_REPORTED input"
                )
            event = explicit_input.event
            if event.plan_day_id is None:
                raise M2StorageIntegrityError(
                    "unavailable input lacks its plan-day identity"
                )

            routing = deserialize_routing_vector(row["routing_json"])
            proof = self._deserialize_reduction_input_proof(
                row["reduction_input_proof_json"],
                row["reduction_input_proof_sha256"],
            )
            if (
                proof.schema_version != "m2-reduction-input-proof-v2"
                or proof.complete_plan_day_assignment_scope_ids
                != (event.plan_day_id,)
            ):
                raise M2InsufficientHistoricalEvidenceError(
                    "completed unavailable input lacks exhaustive v2 historical "
                    "operational evidence"
                )
            scope = self._completed_scope_from_proof(connection, routing, proof)
            self._validate_reduction_proof_version(explicit_input, scope, proof)
            preimage = scope.plan_day(event.plan_day_id)
            if preimage is None:
                raise M2StorageIntegrityError(
                    "v2 unavailable proof lacks its historical plan-day preimage"
                )
            context = deserialize_policy_time_context(row["policy_context_json"])
            reduction = self._normalize_reduction_for_durability(
                scope,
                explicit_input,
                reduce(scope, explicit_input, context),
            )
            historical_plan = reduction.state.plan_day(event.plan_day_id)
            if historical_plan is None:
                raise M2StorageIntegrityError(
                    "unavailable reduction did not produce its historical plan day"
                )
            resulting = deserialize_resulting_revision_vector(
                row["resulting_revision_vector_json"]
            )
            plan_result = next(
                (
                    item
                    for item in resulting.roots
                    if item.root_kind == RootKind.PLAN_DAY.value
                    and item.root_id == event.plan_day_id
                ),
                None,
            )
            preconditions = deserialize_precondition_vector(
                row["precondition_vector_json"]
            )
            plan_precondition = next(
                (
                    item
                    for item in preconditions.roots
                    if item.root_kind == RootKind.PLAN_DAY.value
                    and item.root_id == event.plan_day_id
                ),
                None,
            )
            if plan_precondition is None:
                raise M2StorageIntegrityError(
                    "unavailable proof lacks its plan-day precondition"
                )
            effect = self._durable_effect_from_connection(
                connection, row, effect_ordinal
            )
            activation = self._activation_lineage_from_connection(
                connection,
                event.plan_day_id,
                plan_precondition.expected_revision,
                plan_precondition.expected_content_sha256,
            )
            connection.rollback()
            return UnavailableHistoricalReconstruction(
                explicit_input=explicit_input,
                input_schema_version=row["input_schema_version"],
                input_payload_sha256=row["payload_sha256"],
                reduction_proof_schema_version=proof.schema_version,
                reduction_proof_sha256=row["reduction_input_proof_sha256"],
                plan_day_preimage_sha256=plan_precondition.expected_content_sha256,
                plan_day_result_sha256=(
                    plan_result.resulting_content_sha256
                    if plan_result is not None
                    else sha256_text(serialize_plan_day_root(historical_plan))
                ),
                unavailable_effect=effect,
                activation_lineage=activation,
                plan_day=historical_plan,
                job_execution_roots=proof.job_execution_roots,
                job_root_preimage_hashes=tuple(
                    (
                        root.job_id,
                        root.job_execution_revision,
                        sha256_text(serialize_job_execution_root(root)),
                    )
                    for root in proof.job_execution_roots
                ),
            )
        except M2PersistenceError:
            raise
        except (sqlite3.Error, SerializationError, ValueError, TypeError) as error:
            raise M2StorageIntegrityError(
                "read-only historical reconstruction failed"
            ) from error
        finally:
            connection.close()

    def _connect_read_only(self) -> sqlite3.Connection:
        path = Path(self.database_path)
        if not path.is_file():
            raise M2NotFoundError("durable database does not exist")
        try:
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
                timeout=10,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA recursive_triggers = ON")
            return connection
        except sqlite3.Error as error:
            raise M2StorageIntegrityError(
                "durable database cannot be opened read-only"
            ) from error

    def _durable_effect_from_connection(
        self,
        connection: sqlite3.Connection,
        inbox: sqlite3.Row,
        effect_ordinal: int,
    ) -> DurableOutboxEffect:
        row = connection.execute(
            """
            SELECT * FROM m2_effect_outbox
            WHERE input_namespace=? AND input_id=? AND effect_ordinal=?
            """,
            (inbox["input_namespace"], inbox["input_id"], effect_ordinal),
        ).fetchone()
        if row is None:
            raise M2NotFoundError(
                "unknown durable outbox effect: "
                f"{inbox['input_namespace']}/{inbox['input_id']}/{effect_ordinal}"
            )
        try:
            verify_canonical_document(
                row["canonical_effect_json"], row["effect_sha256"]
            )
            effect = deserialize_effect(row["canonical_effect_json"])
            recorded_at = datetime.fromisoformat(row["recorded_at"])
            _require_aware(recorded_at, "stored outbox recorded_at")
            dispatched_at = (
                datetime.fromisoformat(row["dispatched_at"])
                if row["dispatched_at"] is not None
                else None
            )
            if dispatched_at is not None:
                _require_aware(dispatched_at, "stored outbox dispatched_at")
            completed_at = datetime.fromisoformat(inbox["completed_at"])
            _require_aware(completed_at, "stored inbox completed_at")
        except (SerializationError, ValueError, TypeError) as error:
            raise M2StorageIntegrityError(
                "stored durable outbox effect is invalid"
            ) from error
        if (
            (row["input_namespace"], row["input_id"], row["effect_ordinal"])
            != (
                inbox["input_namespace"],
                inbox["input_id"],
                effect_ordinal,
            )
            or row["effect_type"] != effect.effect_type.value
            or recorded_at != completed_at
            or row["dispatch_status"]
            not in {"RECORDED", "DISPATCHING", "DISPATCHED", "FAILED"}
            or (row["dispatch_status"] == "DISPATCHED")
            != (dispatched_at is not None)
        ):
            raise M2StorageIntegrityError(
                "stored durable outbox effect projection mismatch"
            )
        return DurableOutboxEffect(
            input_namespace=row["input_namespace"],
            input_id=row["input_id"],
            effect_ordinal=row["effect_ordinal"],
            effect=effect,
            effect_sha256=row["effect_sha256"],
            dispatch_status=row["dispatch_status"],
            recorded_at=recorded_at,
            dispatched_at=dispatched_at,
        )

    def _activation_lineage_from_connection(
        self,
        connection: sqlite3.Connection,
        plan_day_id: str,
        target_revision: int,
        target_sha256: str,
    ) -> VerifiedActivationLineage:
        rows = connection.execute(
            """
            SELECT * FROM m2_input_inbox
            WHERE input_namespace='FIELD_EVENT'
              AND processing_status='COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM json_each(
                      json_extract(resulting_revision_vector_json, '$.payload.roots')
                  ) AS result_root
                  WHERE json_extract(result_root.value, '$.root_kind')='PLAN_DAY'
                    AND json_extract(result_root.value, '$.root_id')=?
              )
            ORDER BY input_id
            """,
            (plan_day_id,),
        ).fetchall()
        candidates: dict[
            tuple[int, str],
            list[tuple[sqlite3.Row, RootPrecondition, ResultingRevision]],
        ] = {}
        for row in rows:
            verify_canonical_document(
                row["precondition_vector_json"],
                row["precondition_vector_sha256"],
            )
            verify_canonical_document(
                row["resulting_revision_vector_json"],
                row["resulting_revision_vector_sha256"],
            )
            preconditions = deserialize_precondition_vector(
                row["precondition_vector_json"]
            )
            results = deserialize_resulting_revision_vector(
                row["resulting_revision_vector_json"]
            )
            precondition = next(
                (
                    item
                    for item in preconditions.roots
                    if item.root_kind == RootKind.PLAN_DAY.value
                    and item.root_id == plan_day_id
                ),
                None,
            )
            result = next(
                (
                    item
                    for item in results.roots
                    if item.root_kind == RootKind.PLAN_DAY.value
                    and item.root_id == plan_day_id
                ),
                None,
            )
            if precondition is None or result is None:
                raise M2StorageIntegrityError(
                    "plan-day history contains an incomplete transition"
                )
            candidates.setdefault(
                (result.resulting_revision, result.resulting_content_sha256), []
            ).append((row, precondition, result))

        reverse_transitions: list[HistoricalPlanDayTransition] = []
        revision = target_revision
        digest = target_sha256
        while revision > 0:
            matches = candidates.get((revision, digest), [])
            if len(matches) != 1:
                raise M2StorageIntegrityError(
                    "historical plan-day activation lineage is missing or ambiguous"
                )
            row, precondition, result = matches[0]
            if (
                precondition.access is not RootAccess.MUTATE
                or result.resulting_revision != precondition.expected_revision + 1
            ):
                raise M2StorageIntegrityError(
                    "historical plan-day activation lineage is corrupt"
                )
            self._completed_result_from_row(
                connection,
                row,
                replayed=True,
                validate_current_roots=False,
            )
            explicit = self._explicit_input_from_row(row)
            if not isinstance(explicit, FieldEventInput):
                raise M2StorageIntegrityError(
                    "plan-day lineage transition is not a field event"
                )
            if row["appended_receipt_sha256"] is None:
                raise M2StorageIntegrityError(
                    "plan-day lineage transition lacks its receipt integrity"
                )
            transition = HistoricalPlanDayTransition(
                input_namespace=row["input_namespace"],
                input_id=row["input_id"],
                input_payload_sha256=row["payload_sha256"],
                server_event_id=row["presented_server_event_id"],
                receipt_sha256=row["appended_receipt_sha256"],
                precondition_revision=precondition.expected_revision,
                precondition_sha256=precondition.expected_content_sha256,
                resulting_revision=result.resulting_revision,
                resulting_sha256=result.resulting_content_sha256,
            )
            reverse_transitions.append(transition)
            if explicit.event.event_type is FieldEventType.DAY_PLAN_ACTIVATED:
                activation_effect = self._durable_effect_from_connection(
                    connection, row, 0
                )
                value = activation_effect.effect
                if (
                    value.effect_type is not EffectType.PLAN_DAY_ACTIVATED
                    or value.source_id != explicit.event.event_id
                    or value.plan_day_id != plan_day_id
                    or value.actor_id != explicit.event.actor_id
                    or value.job_id is not None
                    or value.task_id is not None
                    or value.assignment_id is not None
                    or value.stage_id is not None
                    or value.directive_id is not None
                    or value.details
                ):
                    raise M2StorageIntegrityError(
                        "historical activation effect is mismatched"
                    )
                return VerifiedActivationLineage(
                    activation_event_id=explicit.event.event_id,
                    activation_server_event_id=row["presented_server_event_id"],
                    activation_input_payload_sha256=row["payload_sha256"],
                    activation_receipt_sha256=row["appended_receipt_sha256"],
                    activation_effect_ordinal=activation_effect.effect_ordinal,
                    activation_effect_sha256=activation_effect.effect_sha256,
                    resulting_plan_day_revision=result.resulting_revision,
                    resulting_plan_day_sha256=result.resulting_content_sha256,
                    transitions=tuple(reversed(reverse_transitions)),
                )
            revision = precondition.expected_revision
            digest = precondition.expected_content_sha256
        raise M2StorageIntegrityError(
            "historical plan-day has no qualifying activation lineage"
        )

    # -- TX1 durable acceptance. --

    def accept_input(
        self,
        explicit_input: ExplicitInput,
        policy_time_context: PolicyTimeContext,
        *,
        received_at: datetime,
    ) -> IngressAcceptance | DurableReductionResult:
        _require_aware(received_at, "received_at")
        namespace, input_id, input_raw, schema_version, presented = self._input_document(
            explicit_input
        )
        input_hash = sha256_text(input_raw)
        context_raw = serialize_policy_time_context(policy_time_context)
        context_hash = sha256_text(context_raw)
        refs = self._raw_references(explicit_input)
        conflict: tuple[str, str | None] | None = None
        acceptance: IngressAcceptance | None = None
        replay: DurableReductionResult | None = None

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM m2_input_inbox WHERE input_namespace = ? AND input_id = ?",
                (namespace, input_id),
            ).fetchone()
            if existing is not None:
                self._verify_inbox_input(existing)
                if existing["canonical_input_json"] != input_raw:
                    self._record_input_conflict(
                        connection,
                        namespace=namespace,
                        input_id=input_id,
                        conflict_kind="INPUT_ID_PAYLOAD_MISMATCH",
                        attempted_input_json=input_raw,
                        attempted_payload_sha256=input_hash,
                        owner_input_id=input_id,
                        recorded_at=received_at,
                    )
                    conflict = ("INPUT_ID_PAYLOAD_MISMATCH", input_id)
                elif existing["processing_status"] == "COMPLETED":
                    replay = self._completed_result_from_row(
                        connection, existing, replayed=True
                    )
                else:
                    acceptance = IngressAcceptance(
                        namespace,
                        input_id,
                        "RECEIVED",
                        False,
                        existing["server_claim_owner_input_id"],
                    )
            else:
                claim_owner: str | None = None
                claimed = presented
                if presented is not None:
                    owner = connection.execute(
                        """
                        SELECT input_id FROM m2_input_inbox
                        WHERE claimed_server_event_id = ?
                        """,
                        (presented,),
                    ).fetchone()
                    if owner is not None:
                        claim_owner = owner["input_id"]
                        claimed = None
                connection.execute(
                    """
                    INSERT INTO m2_input_inbox(
                        input_namespace, input_id, input_schema_version,
                        canonical_input_json, payload_sha256,
                        presented_server_event_id, claimed_server_event_id,
                        server_claim_owner_input_id, raw_job_id, raw_task_id,
                        raw_assignment_id, raw_plan_day_id, raw_directive_id,
                        raw_against_event_id, policy_context_json,
                        policy_context_sha256, rule_version, first_received_at,
                        processing_status
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RECEIVED')
                    """,
                    (
                        namespace,
                        input_id,
                        schema_version,
                        input_raw,
                        input_hash,
                        presented,
                        claimed,
                        claim_owner,
                        refs["job_id"],
                        refs["task_id"],
                        refs["assignment_id"],
                        refs["plan_day_id"],
                        refs["directive_id"],
                        refs["against_event_id"],
                        context_raw,
                        context_hash,
                        policy_time_context.rule_version,
                        received_at.isoformat(),
                    ),
                )
                if claim_owner is not None:
                    self._record_input_conflict(
                        connection,
                        namespace=namespace,
                        input_id=input_id,
                        conflict_kind="SERVER_EVENT_ID_COLLISION",
                        attempted_input_json=input_raw,
                        attempted_payload_sha256=input_hash,
                        owner_input_id=claim_owner,
                        recorded_at=received_at,
                    )
                acceptance = IngressAcceptance(
                    namespace, input_id, "RECEIVED", True, claim_owner
                )
                self._fault("after_ingress_insert")

        if conflict is not None:
            raise M2ConflictError(
                f"durable input conflict {conflict[0]} for {namespace}/{input_id}"
            )
        if replay is not None:
            return replay
        assert acceptance is not None
        self._fault("after_ingress_commit")
        return acceptance

    @staticmethod
    def _record_input_conflict(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        input_id: str,
        conflict_kind: str,
        attempted_input_json: str,
        attempted_payload_sha256: str,
        owner_input_id: str,
        recorded_at: datetime,
    ) -> None:
        existing = connection.execute(
            """
            SELECT 1 FROM m2_input_conflicts
            WHERE input_namespace=? AND input_id=? AND conflict_kind=?
              AND attempted_payload_sha256=? AND owner_input_id=?
            """,
            (
                namespace,
                input_id,
                conflict_kind,
                attempted_payload_sha256,
                owner_input_id,
            ),
        ).fetchone()
        if existing is not None:
            return
        connection.execute(
            """
            INSERT INTO m2_input_conflicts(
                input_namespace, input_id, conflict_kind,
                attempted_input_json, attempted_payload_sha256,
                owner_input_id, recorded_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                namespace,
                input_id,
                conflict_kind,
                attempted_input_json,
                attempted_payload_sha256,
                owner_input_id,
                recorded_at.isoformat(),
            ),
        )

    # -- TX2 routing, reduction, CAS, receipt, evidence, and outbox. --

    def process_input(
        self,
        input_namespace: str,
        input_id: str,
        *,
        completed_at: datetime,
    ) -> DurableReductionResult:
        _require_aware(completed_at, "completed_at")
        result: DurableReductionResult | None = None
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM m2_input_inbox WHERE input_namespace = ? AND input_id = ?",
                (input_namespace, input_id),
            ).fetchone()
            if row is None:
                raise M2NotFoundError(f"unknown inbox input: {input_namespace}/{input_id}")
            self._verify_inbox_input(row)
            if row["processing_status"] == "COMPLETED":
                return self._completed_result_from_row(
                    connection, row, replayed=True
                )
            if row["processing_status"] != "RECEIVED":
                raise M2StorageIntegrityError("unsupported inbox processing status")

            explicit_input = self._explicit_input_from_row(row)
            context = deserialize_policy_time_context(row["policy_context_json"])
            if row["server_claim_owner_input_id"] is not None:
                reduction = Reduction(
                    state=M2ReductionScope(
                        publications=(),
                        worker_registry=self._worker_registry_from_connection(connection),
                    ),
                    outcome=ReductionOutcome.REJECTED,
                    reason_codes=("SERVER_EVENT_ID_CONFLICT",),
                )
                hydration = _Hydration(
                    reduction.state,
                    RoutingVector(),
                    PreconditionVector(
                        roots=(self._registry_precondition(connection),)
                    ),
                )
            else:
                hydration = self._hydrate(connection, explicit_input)
                self._fault("during_hydration")
                self._fault("before_reduce")
                reduction = reduce(hydration.scope, explicit_input, context)
                reduction = self._normalize_reduction_for_durability(
                    hydration.scope,
                    explicit_input,
                    reduction,
                )
                self._fault("after_reduce")

            self._validate_reduction_result(
                hydration.scope,
                reduction,
                hydration.preconditions,
            )
            preconditions = hydration.preconditions
            self._fault("before_precondition_recheck")
            self._recheck_preconditions(connection, preconditions)
            resulting = self._apply_deltas(connection, reduction.root_deltas)
            if reduction.appended_receipt is not None:
                evidence_scope_is_canonical = (
                    self._evidence_projection_scope_is_canonical(
                        hydration.scope, reduction.appended_receipt.event
                    )
                )
                if evidence_scope_is_canonical:
                    self._persist_evidence(connection, reduction.appended_receipt)
                elif reduction.outcome is not ReductionOutcome.REJECTED:
                    raise M2StorageIntegrityError(
                        "reducer accepted evidence without canonical ownership scope"
                    )
            self._insert_outbox(
                connection,
                input_namespace,
                input_id,
                reduction.emitted_effects,
                completed_at,
            )
            self._fault("after_outbox_insert")
            self._complete_inbox(
                connection,
                row,
                reduction,
                hydration.scope,
                hydration.routing,
                preconditions,
                resulting,
                completed_at,
                complete_plan_day_assignment_scope_ids=(
                    hydration.complete_plan_day_assignment_scope_ids
                ),
            )
            self._fault("before_processing_commit")
            result = DurableReductionResult(
                input_namespace=input_namespace,
                input_id=input_id,
                outcome=reduction.outcome,
                root_deltas=reduction.root_deltas,
                appended_receipt=reduction.appended_receipt,
                emitted_effects=reduction.emitted_effects,
                response_effects=reduction.response_effects,
                server_event_id=reduction.server_event_id,
                missing_requirements=reduction.missing_requirements,
                reason_codes=reduction.reason_codes,
                replayed=False,
            )
        assert result is not None
        return result

    def execute(
        self,
        explicit_input: ExplicitInput,
        policy_time_context: PolicyTimeContext,
        *,
        received_at: datetime,
        completed_at: datetime | None = None,
    ) -> DurableReductionResult:
        accepted = self.accept_input(
            explicit_input, policy_time_context, received_at=received_at
        )
        if isinstance(accepted, DurableReductionResult):
            return accepted
        return self.process_input(
            accepted.input_namespace,
            accepted.input_id,
            completed_at=completed_at or received_at,
        )

    # -- Creation projections. --

    def _insert_job_projections(
        self,
        connection: sqlite3.Connection,
        root: M2JobExecutionRoot,
    ) -> None:
        tasks = _topological(
            root.tasks,
            lambda item: item.definition.task_id,
            lambda item: item.definition.supersedes_task_id,
        )
        for task in tasks:
            definition = task.definition
            route = canonical_json(_task_route_payload(task))
            connection.execute(
                """
                INSERT INTO m2_task_routes(
                    task_id, job_id, source_revision, source_handoff_id,
                    definition_version, supersedes_task_id, route_json, route_sha256
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition.task_id,
                    definition.job_id,
                    definition.source_revision,
                    definition.source_handoff_id,
                    definition.definition_version,
                    definition.supersedes_task_id,
                    route,
                    sha256_text(route),
                ),
            )
        assignments = _topological(
            root.assignments,
            lambda item: item.assignment_id,
            lambda item: item.supersedes_assignment_id,
        )
        for assignment in assignments:
            route = canonical_json(_assignment_route_payload(assignment))
            connection.execute(
                """
                INSERT INTO m2_assignment_routes(
                    assignment_id, job_id, task_id, definition_version,
                    assignment_kind, lead_worker_id, supersedes_assignment_id,
                    route_json, route_sha256
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment.assignment_id,
                    assignment.job_id,
                    assignment.task_id,
                    assignment.task_definition_version,
                    assignment.kind.value,
                    assignment.lead_worker_id,
                    assignment.supersedes_assignment_id,
                    route,
                    sha256_text(route),
                ),
            )
            connection.executemany(
                """
                INSERT INTO m2_assignment_members(assignment_id, worker_id)
                VALUES(?, ?)
                """,
                (
                    (assignment.assignment_id, worker_id)
                    for worker_id in assignment.member_worker_ids
                ),
            )
            connection.executemany(
                """
                INSERT INTO m2_assignment_plan_days(assignment_id, plan_day_id)
                VALUES(?, ?)
                """,
                (
                    (assignment.assignment_id, plan_day_id)
                    for plan_day_id in assignment.plan_day_ids
                ),
            )

    def _insert_initial_root_evidence(
        self,
        connection: sqlite3.Connection,
        root: M2JobExecutionRoot,
    ) -> None:
        by_id: dict[str, tuple[object, tuple[str, str, str | None, None]]] = {}
        for task in root.tasks:
            assignments = tuple(
                item for item in root.assignments if item.task_id == task.definition.task_id
            )
            assignment_id = assignments[0].assignment_id if len(assignments) == 1 else None
            scope = (root.job_id, task.definition.task_id, assignment_id, None)
            for item in task.evidence:
                existing = by_id.get(item.evidence_id)
                if existing is not None and (existing[0] != item or existing[1] != scope):
                    raise M2ConflictError(
                        "initial root contains conflicting evidence identity or scope"
                    )
                by_id[item.evidence_id] = (item, scope)
            for observation in task.observations:
                for item in observation.evidence:
                    existing = by_id.get(item.evidence_id)
                    if existing is not None and (
                        existing[0] != item or existing[1] != scope
                    ):
                        raise M2ConflictError(
                            "initial root contains conflicting evidence identity or scope"
                        )
                    by_id[item.evidence_id] = (item, scope)
        for evidence_id in sorted(by_id):
            item, scope = by_id[evidence_id]
            raw = _evidence_identity_document(item, scope)
            digest = sha256_text(raw)
            self._validate_prior_completed_evidence_authority(
                connection, evidence_id
            )
            existing = connection.execute(
                "SELECT canonical_evidence_json, content_sha256, owner_job_id, "
                "owner_task_id, owner_assignment_id, owner_stage_id "
                "FROM m2_evidence_identities WHERE evidence_id=?",
                (evidence_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] != raw
                    or existing[1] != digest
                    or tuple(existing[2:]) != scope
                ):
                    raise M2ConflictError(
                        "initial root reuses conflicting evidence identity or scope"
                    )
                continue
            connection.execute(
                """
                INSERT INTO m2_evidence_identities(
                    evidence_id, evidence_kind, content_reference,
                    owner_job_id, owner_task_id, owner_assignment_id, owner_stage_id,
                    canonical_evidence_json, content_sha256
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    item.kind.value,
                    item.content_reference,
                    *scope,
                    raw,
                    digest,
                ),
            )

    # -- Strict stored-data decoders. --

    def _worker_registry_from_connection(
        self, connection: sqlite3.Connection
    ) -> WorkerIdentityRegistry:
        row = connection.execute(
            "SELECT * FROM m2_worker_registry WHERE registry_key = 'GLOBAL'"
        ).fetchone()
        if row is None:
            raise M2NotFoundError("canonical worker registry is not initialized")
        try:
            verify_canonical_document(row["canonical_root_json"], row["content_sha256"])
            result = deserialize_worker_registry(row["canonical_root_json"])
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError("stored worker registry is invalid") from error
        identities = tuple(
            item["worker_id"]
            for item in connection.execute(
                "SELECT worker_id FROM m2_worker_identities ORDER BY worker_id"
            ).fetchall()
        )
        if (
            row["registry_key"] != "GLOBAL"
            or result.registry_revision != row["registry_revision"]
            or result.worker_ids != identities
        ):
            raise M2StorageIntegrityError("worker registry projection mismatch")
        return result

    def _job_root_from_connection(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> M2JobExecutionRoot:
        try:
            verify_canonical_document(row["canonical_root_json"], row["content_sha256"])
            root = deserialize_job_execution_root(row["canonical_root_json"])
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError("stored job execution root is invalid") from error
        if root.job_id != row["job_id"] or root.job_execution_revision != row[
            "job_execution_revision"
        ]:
            raise M2StorageIntegrityError("job root identity/revision mismatch")
        task_rows = connection.execute(
            "SELECT * FROM m2_task_routes WHERE job_id = ? ORDER BY task_id",
            (root.job_id,),
        ).fetchall()
        if tuple(item["task_id"] for item in task_rows) != tuple(
            item.definition.task_id for item in root.tasks
        ):
            raise M2StorageIntegrityError("task routing set disagrees with job root")
        for task, route_row in zip(root.tasks, task_rows, strict=True):
            expected = canonical_json(_task_route_payload(task))
            if (
                route_row["route_json"] != expected
                or route_row["route_sha256"] != sha256_text(expected)
                or route_row["job_id"] != task.definition.job_id
                or route_row["source_revision"] != task.definition.source_revision
                or route_row["source_handoff_id"] != task.definition.source_handoff_id
                or route_row["definition_version"] != task.definition.definition_version
                or route_row["supersedes_task_id"] != task.definition.supersedes_task_id
            ):
                raise M2StorageIntegrityError("task routing projection mismatch")
        assignment_rows = connection.execute(
            "SELECT * FROM m2_assignment_routes WHERE job_id = ? ORDER BY assignment_id",
            (root.job_id,),
        ).fetchall()
        if tuple(item["assignment_id"] for item in assignment_rows) != tuple(
            item.assignment_id for item in root.assignments
        ):
            raise M2StorageIntegrityError("assignment routing set disagrees with job root")
        for assignment, route_row in zip(root.assignments, assignment_rows, strict=True):
            expected = canonical_json(_assignment_route_payload(assignment))
            members = tuple(
                item["worker_id"]
                for item in connection.execute(
                    """
                    SELECT worker_id FROM m2_assignment_members
                    WHERE assignment_id = ? ORDER BY worker_id
                    """,
                    (assignment.assignment_id,),
                ).fetchall()
            )
            plans = tuple(
                item["plan_day_id"]
                for item in connection.execute(
                    """
                    SELECT plan_day_id FROM m2_assignment_plan_days
                    WHERE assignment_id = ? ORDER BY plan_day_id
                    """,
                    (assignment.assignment_id,),
                ).fetchall()
            )
            if (
                route_row["route_json"] != expected
                or route_row["route_sha256"] != sha256_text(expected)
                or route_row["task_id"] != assignment.task_id
                or route_row["definition_version"]
                != assignment.task_definition_version
                or members != assignment.member_worker_ids
                or plans != assignment.plan_day_ids
            ):
                raise M2StorageIntegrityError("assignment routing projection mismatch")
        return root

    @staticmethod
    def _plan_day_from_row(row: sqlite3.Row) -> PlanDayRoot:
        try:
            verify_canonical_document(row["canonical_root_json"], row["content_sha256"])
            root = deserialize_plan_day_root(row["canonical_root_json"])
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError("stored plan day root is invalid") from error
        if (
            root.plan_day_id != row["plan_day_id"]
            or root.plan_day_revision != row["plan_day_revision"]
            or root.worker_id != row["worker_id"]
            or root.business_date.isoformat() != row["business_date"]
            or root.status.value != row["status"]
        ):
            raise M2StorageIntegrityError("plan day projection mismatch")
        return root

    @staticmethod
    def _directive_from_row(row: sqlite3.Row) -> DirectiveRoot:
        try:
            verify_canonical_document(row["canonical_root_json"], row["content_sha256"])
            root = deserialize_directive_root(row["canonical_root_json"])
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError("stored directive root is invalid") from error
        stream_kind, stream_id = _directive_stream(root)
        definition = root.definition
        expected = (
            root.directive_id,
            root.worker_id,
            root.job_id,
            root.task_id,
            root.assignment_id,
            root.plan_day_id,
            stream_kind,
            stream_id,
            root.issuance_sequence,
            root.supersedes_directive_id,
            definition.directive_type.value,
            definition.directive_class.value,
            definition.issued_at.isoformat(),
            definition.escalation_due_at.isoformat()
            if definition.escalation_due_at is not None
            else None,
            definition.proposed_plan_reference,
            root.delivery_evidence.value,
            root.acknowledged_event_id,
            root.exception_event_id,
            int(root.e1_escalated),
            int(root.stop_in_force),
            root.directive_revision,
        )
        actual = tuple(
            row[name]
            for name in (
                "directive_id",
                "worker_id",
                "job_id",
                "task_id",
                "assignment_id",
                "plan_day_id",
                "stream_kind",
                "stream_id",
                "issuance_sequence",
                "supersedes_directive_id",
                "directive_type",
                "directive_class",
                "issued_at",
                "escalation_due_at",
                "proposed_plan_reference",
                "delivery_evidence",
                "acknowledged_event_id",
                "exception_event_id",
                "e1_escalated",
                "stop_in_force",
                "directive_revision",
            )
        )
        if expected != actual:
            raise M2StorageIntegrityError("directive root projection mismatch")
        return root

    # -- Directive creation validation. --

    def _validate_new_directive(
        self,
        connection: sqlite3.Connection,
        root: DirectiveRoot,
        stream_kind: str,
        stream_id: str,
    ) -> None:
        registry = self._worker_registry_from_connection(connection)
        job_ids: set[str] = set()
        plan_ids: set[str] = set()
        if root.job_id is not None:
            job_ids.add(root.job_id)
        if root.task_id is not None:
            route = connection.execute(
                "SELECT * FROM m2_task_routes WHERE task_id = ?", (root.task_id,)
            ).fetchone()
            if route is None:
                raise M2NotFoundError("directive task route does not exist")
            job_ids.add(route["job_id"])
        if root.assignment_id is not None:
            route = connection.execute(
                "SELECT * FROM m2_assignment_routes WHERE assignment_id = ?",
                (root.assignment_id,),
            ).fetchone()
            if route is None:
                raise M2NotFoundError("directive assignment route does not exist")
            job_ids.add(route["job_id"])
        if root.plan_day_id is not None:
            plan_ids.add(root.plan_day_id)
        jobs = tuple(self._load_job(connection, item) for item in sorted(job_ids))
        plans = tuple(self._load_plan(connection, item) for item in sorted(plan_ids))
        predecessor_rows = []
        predecessor_id = root.supersedes_directive_id
        seen_predecessors: set[str] = set()
        while predecessor_id is not None:
            if predecessor_id in seen_predecessors:
                raise M2ConflictError("directive supersession chain is cyclic")
            seen_predecessors.add(predecessor_id)
            predecessor = connection.execute(
                "SELECT * FROM m2_directive_roots WHERE directive_id = ?",
                (predecessor_id,),
            ).fetchone()
            if predecessor is None:
                raise M2NotFoundError("superseded directive does not exist")
            predecessor_rows.append(predecessor)
            predecessor_id = predecessor["supersedes_directive_id"]
        publications = self._load_publications_for_jobs(connection, jobs)
        roots_for_receipts = (
            root,
            *(self._directive_from_row(item) for item in predecessor_rows),
        )
        receipts = self._load_ack_receipts(
            connection,
            roots_for_receipts,
        )
        scope = M2ReductionScope(
            publications=publications,
            worker_registry=registry,
            job_execution_roots=jobs,
            plan_day_roots=plans,
            directive_roots=tuple(
                sorted(
                    (root, *(self._directive_from_row(item) for item in predecessor_rows)),
                    key=lambda item: item.directive_id,
                )
            ),
            processed_events=ProcessedEventLedger(receipts),
        )
        errors = scope.directive_scope_errors(root)
        if errors:
            raise M2ConflictError("invalid directive scope: " + ",".join(errors))
        if root.delivery_evidence.value == "ACKED":
            if not scope.directive_worker_informed(root.directive_id):
                raise M2ConflictError(
                    "ACKED directive creation requires an exact durable ACK receipt"
                )
        elif root.acknowledged_event_id is not None:
            raise M2ConflictError(
                "non-ACKED directive cannot carry acknowledged_event_id"
            )
        duplicate = connection.execute(
            """
            SELECT directive_id FROM m2_directive_roots
            WHERE worker_id = ? AND stream_kind = ? AND stream_id = ?
              AND issuance_sequence = ?
            """,
            (root.worker_id, stream_kind, stream_id, root.issuance_sequence),
        ).fetchone()
        if duplicate is not None:
            raise M2ConflictError("issuance_sequence is occupied in directive stream")

    # -- Hydration/routing. --

    def _hydrate(
        self, connection: sqlite3.Connection, explicit_input: ExplicitInput
    ) -> _Hydration:
        registry = self._worker_registry_from_connection(connection)
        job_ids: set[str] = set()
        plan_ids: set[str] = set()
        directive_ids: set[str] = set()
        task_route_ids: set[str] = set()
        assignment_route_ids: set[str] = set()
        receipt_ids: set[str] = set()
        evidence_ids: set[str] = set()
        complete_plan_day_assignment_scope_ids: tuple[str, ...] = ()

        if isinstance(explicit_input, FieldEventInput):
            event = explicit_input.event
            if event.job_id is not None:
                job_ids.add(event.job_id)
            if event.task_id is not None:
                task_route_ids.add(event.task_id)
            if event.assignment_id is not None:
                assignment_route_ids.add(event.assignment_id)
            if event.plan_day_id is not None:
                plan_ids.add(event.plan_day_id)
            if event.directive_id is not None:
                directive_ids.add(event.directive_id)
            if event.against_event_id is not None:
                receipt_ids.add(event.against_event_id)
            evidence_ids.update(item.evidence_id for item in event.attachments)
            if (
                event.event_type is FieldEventType.UNAVAILABLE_TODAY_REPORTED
                and event.plan_day_id is not None
            ):
                linked_rows = connection.execute(
                    """
                    SELECT route.*
                    FROM m2_assignment_plan_days AS plan_link
                    JOIN m2_assignment_routes AS route
                      ON route.assignment_id=plan_link.assignment_id
                    WHERE plan_link.plan_day_id=?
                    ORDER BY route.assignment_id
                    """,
                    (event.plan_day_id,),
                ).fetchall()
                for route in linked_rows:
                    self._verify_route_row(route, "assignment")
                    job_ids.add(route["job_id"])
                    assignment_route_ids.add(route["assignment_id"])
                    task_route_ids.add(route["task_id"])
                complete_plan_day_assignment_scope_ids = (event.plan_day_id,)
        else:
            if explicit_input.task_id is not None:
                task_route_ids.add(explicit_input.task_id)
            if explicit_input.plan_day_id is not None:
                plan_ids.add(explicit_input.plan_day_id)
            if explicit_input.directive_id is not None:
                directive_ids.add(explicit_input.directive_id)

        # Evidence identity is global. Its first canonical owner is routed as
        # authority so an identical ID cannot migrate to another business scope.
        for evidence_id in tuple(sorted(evidence_ids)):
            self._validate_prior_completed_evidence_authority(
                connection, evidence_id
            )
            identity = connection.execute(
                "SELECT * FROM m2_evidence_identities WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if identity is None:
                continue
            try:
                stored_identity = verify_canonical_document(
                    identity["canonical_evidence_json"],
                    identity["content_sha256"],
                )
            except (SerializationError, ValueError) as error:
                raise M2StorageIntegrityError(
                    "stored evidence identity is invalid"
                ) from error
            expected_identity = {
                "evidence": {
                    "content_reference": identity["content_reference"],
                    "evidence_id": identity["evidence_id"],
                    "kind": identity["evidence_kind"],
                },
                "owner_scope": {
                    "assignment_id": identity["owner_assignment_id"],
                    "job_id": identity["owner_job_id"],
                    "stage_id": identity["owner_stage_id"],
                    "task_id": identity["owner_task_id"],
                },
            }
            if stored_identity != expected_identity:
                raise M2StorageIntegrityError(
                    "stored evidence identity projection mismatch"
                )
            if identity["owner_job_id"] is not None:
                job_ids.add(identity["owner_job_id"])
            if identity["owner_task_id"] is not None:
                task_route_ids.add(identity["owner_task_id"])
            if identity["owner_assignment_id"] is not None:
                assignment_route_ids.add(identity["owner_assignment_id"])
            usage_rows = connection.execute(
                """
                SELECT * FROM m2_evidence_usages
                WHERE input_namespace='FIELD_EVENT' AND evidence_id=?
                """,
                (evidence_id,),
            ).fetchall()
            for usage in usage_rows:
                self._verify_evidence_usage(usage)
            receipt_ids.update(item["input_id"] for item in usage_rows)

        # Stable routes resolve independently; conflicts remain visible to reducer.
        for task_id in tuple(sorted(task_route_ids)):
            route = connection.execute(
                "SELECT * FROM m2_task_routes WHERE task_id = ?", (task_id,)
            ).fetchone()
            if route is not None:
                self._verify_route_row(route, "task")
                job_ids.add(route["job_id"])
        for assignment_id in tuple(sorted(assignment_route_ids)):
            route = connection.execute(
                "SELECT * FROM m2_assignment_routes WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if route is not None:
                self._verify_route_row(route, "assignment")
                job_ids.add(route["job_id"])
                task_route_ids.add(route["task_id"])

        # Completion authority sees every applicable STOP directive.
        if isinstance(explicit_input, FieldEventInput) and explicit_input.event.event_type in {
            FieldEventType.TASK_COMPLETION_REPORTED,
            FieldEventType.STAGE_COMPLETION_REPORTED,
            FieldEventType.VISIT_COMPLETION_REPORTED,
        }:
            event = explicit_input.event
            rows = connection.execute(
                """
                SELECT directive_id FROM m2_directive_roots
                WHERE directive_class = 'STOP'
                  AND stop_in_force = 1
                  AND (job_id IS NULL OR job_id = ?)
                  AND (
                      (task_id IS NOT NULL AND task_id = ?)
                      OR (task_id IS NULL AND assignment_id IS NOT NULL
                          AND assignment_id = ?)
                      OR (task_id IS NULL AND assignment_id IS NULL
                          AND plan_day_id IS NOT NULL AND plan_day_id = ?)
                      OR (task_id IS NULL AND assignment_id IS NULL
                          AND plan_day_id IS NULL AND worker_id = ?)
                  )
                """,
                (
                    event.job_id,
                    event.task_id,
                    event.assignment_id,
                    event.plan_day_id,
                    event.actor_id,
                ),
            ).fetchall()
            directive_ids.update(item["directive_id"] for item in rows)

        # Load target directives, their predecessor chains, and ACK stream ordering peers.
        directive_rows: dict[str, sqlite3.Row] = {}
        pending = list(sorted(directive_ids))
        while pending:
            directive_id = pending.pop(0)
            if directive_id in directive_rows:
                continue
            row = connection.execute(
                "SELECT * FROM m2_directive_roots WHERE directive_id = ?",
                (directive_id,),
            ).fetchone()
            if row is None:
                continue
            root = self._directive_from_row(row)
            directive_rows[directive_id] = row
            if root.supersedes_directive_id is not None:
                pending.append(root.supersedes_directive_id)
            if root.job_id is not None:
                job_ids.add(root.job_id)
            if root.task_id is not None:
                task_route_ids.add(root.task_id)
            if root.assignment_id is not None:
                assignment_route_ids.add(root.assignment_id)
            if root.plan_day_id is not None:
                plan_ids.add(root.plan_day_id)
            if root.acknowledged_event_id is not None:
                receipt_ids.add(root.acknowledged_event_id)
            needs_stream_authority = (
                isinstance(explicit_input, FieldEventInput)
                and explicit_input.event.event_type
                is FieldEventType.WORKER_ACKNOWLEDGED
                and explicit_input.event.directive_id == directive_id
            ) or (
                isinstance(explicit_input, SystemSignal)
                and explicit_input.signal_type
                is SystemSignalType.DIRECTIVE_E1_ELAPSED
                and explicit_input.directive_id == directive_id
            )
            if needs_stream_authority:
                peers = connection.execute(
                    """
                    SELECT directive_id FROM m2_directive_roots
                    WHERE worker_id = ? AND stream_kind = ? AND stream_id = ?
                    """,
                    (row["worker_id"], row["stream_kind"], row["stream_id"]),
                ).fetchall()
                pending.extend(item["directive_id"] for item in peers)

        # Resolve references discovered from directives.
        for task_id in tuple(sorted(task_route_ids)):
            row = connection.execute(
                "SELECT * FROM m2_task_routes WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is not None:
                self._verify_route_row(row, "task")
                job_ids.add(row["job_id"])
        for assignment_id in tuple(sorted(assignment_route_ids)):
            row = connection.execute(
                "SELECT * FROM m2_assignment_routes WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if row is not None:
                self._verify_route_row(row, "assignment")
                job_ids.add(row["job_id"])

        jobs = tuple(self._load_job(connection, item) for item in sorted(job_ids) if self._job_exists(connection, item))
        plans = tuple(self._load_plan(connection, item) for item in sorted(plan_ids) if self._plan_exists(connection, item))
        directives = tuple(
            self._directive_from_row(directive_rows[item])
            for item in sorted(directive_rows)
        )
        publications = self._load_publications_for_jobs(connection, jobs)

        if complete_plan_day_assignment_scope_ids:
            plan_day_id = complete_plan_day_assignment_scope_ids[0]
            plan = next(
                (item for item in plans if item.plan_day_id == plan_day_id), None
            )
            if plan is not None:
                canonical_links = tuple(
                    sorted(
                        (
                            assignment.assignment_id,
                            assignment.job_id,
                            assignment.task_id,
                            assignment.task_definition_version,
                        )
                        for root in jobs
                        for assignment in root.assignments
                        if plan_day_id in assignment.plan_day_ids
                    )
                )
                projected_links = tuple(
                    (
                        row["assignment_id"],
                        row["job_id"],
                        row["task_id"],
                        row["definition_version"],
                    )
                    for row in connection.execute(
                        """
                        SELECT route.assignment_id, route.job_id, route.task_id,
                               route.definition_version
                        FROM m2_assignment_plan_days AS plan_link
                        JOIN m2_assignment_routes AS route
                          ON route.assignment_id=plan_link.assignment_id
                        WHERE plan_link.plan_day_id=?
                        ORDER BY route.assignment_id
                        """,
                        (plan_day_id,),
                    ).fetchall()
                )
                if projected_links != canonical_links:
                    raise M2StorageIntegrityError(
                        "plan day assignment projection disagrees with canonical roots"
                    )
                for root in jobs:
                    for assignment in root.assignments:
                        if (
                            plan_day_id in assignment.plan_day_ids
                            and plan.worker_id not in assignment.member_worker_ids
                        ):
                            raise M2StorageIntegrityError(
                                "plan day worker is not a member of its linked assignment"
                            )

        # Relevant history: exact references, ACK receipts, task unsafe history,
        # and receipts owning incoming evidence identities.
        task_ids = set(task_route_ids)
        if isinstance(explicit_input, FieldEventInput) and explicit_input.event.task_id:
            task_ids.add(explicit_input.event.task_id)
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            rows = connection.execute(
                """
                SELECT input_id FROM m2_input_inbox
                WHERE input_namespace = 'FIELD_EVENT'
                  AND processing_status = 'COMPLETED'
                  AND appended_receipt_json IS NOT NULL
                  AND raw_task_id IN (""" + placeholders + ")",
                tuple(sorted(task_ids)),
            ).fetchall()
            receipt_ids.update(item["input_id"] for item in rows)
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            rows = connection.execute(
                """
                SELECT * FROM m2_evidence_usages
                WHERE input_namespace = 'FIELD_EVENT'
                  AND evidence_id IN (""" + placeholders + ")",
                tuple(sorted(evidence_ids)),
            ).fetchall()
            for usage in rows:
                self._verify_evidence_usage(usage)
            receipt_ids.update(item["input_id"] for item in rows)
        receipts = self._load_receipts(connection, tuple(sorted(receipt_ids)))

        scope = M2ReductionScope(
            publications=publications,
            worker_registry=registry,
            job_execution_roots=jobs,
            plan_day_roots=plans,
            directive_roots=directives,
            processed_events=ProcessedEventLedger(receipts),
        )
        routing = RoutingVector(
            job_root_ids=tuple(item.job_id for item in jobs),
            plan_day_ids=tuple(item.plan_day_id for item in plans),
            directive_ids=tuple(item.directive_id for item in directives),
            publication_keys=tuple(
                (item.job_id, item.source_revision, item.handoff_id)
                for item in publications
            ),
            receipt_event_ids=tuple(item.event.event_id for item in receipts),
            evidence_ids=tuple(sorted(evidence_ids)),
        )
        preconditions = self._build_preconditions(
            connection, jobs, plans, directives, publications, receipts, evidence_ids
        )
        preconditions = self._classify_root_access(
            preconditions,
            self._allowed_mutation_roots(explicit_input, scope),
        )
        return _Hydration(
            scope,
            routing,
            preconditions,
            complete_plan_day_assignment_scope_ids,
        )

    def _validate_prior_completed_evidence_authority(
        self,
        connection: sqlite3.Connection,
        evidence_id: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT inbox.*
            FROM m2_input_inbox AS inbox
            WHERE inbox.input_namespace='FIELD_EVENT'
              AND inbox.processing_status='COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM json_each(
                      json_extract(inbox.canonical_input_json, '$.payload.attachments')
                  ) AS attachment
                  WHERE json_extract(attachment.value, '$.evidence_id') = ?
              )
            ORDER BY inbox.input_id
            """,
            (evidence_id,),
        ).fetchall()
        for row in rows:
            self._verify_inbox_input(row)
            self._completed_result_from_row(connection, row, replayed=True)

    # -- Hydration helpers. --

    @staticmethod
    def _job_exists(connection: sqlite3.Connection, job_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM m2_job_execution_roots WHERE job_id = ?", (job_id,)
        ).fetchone() is not None

    @staticmethod
    def _plan_exists(connection: sqlite3.Connection, plan_day_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM m2_plan_day_roots WHERE plan_day_id = ?", (plan_day_id,)
        ).fetchone() is not None

    def _load_job(self, connection: sqlite3.Connection, job_id: str) -> M2JobExecutionRoot:
        row = connection.execute(
            "SELECT * FROM m2_job_execution_roots WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise M2NotFoundError(f"unknown job execution root: {job_id}")
        return self._job_root_from_connection(connection, row)

    def _load_plan(self, connection: sqlite3.Connection, plan_id: str) -> PlanDayRoot:
        row = connection.execute(
            "SELECT * FROM m2_plan_day_roots WHERE plan_day_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise M2NotFoundError(f"unknown plan day root: {plan_id}")
        return self._plan_day_from_row(row)

    @staticmethod
    def _verify_route_row(row: sqlite3.Row, kind: str) -> None:
        try:
            verify_canonical_document(row["route_json"], row["route_sha256"])
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError(f"stored {kind} route is invalid") from error

    @staticmethod
    def _verify_evidence_usage(row: sqlite3.Row) -> None:
        try:
            value = verify_canonical_document(row["scope_json"], row["scope_sha256"])
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError("stored evidence usage is invalid") from error
        expected = {
            "assignment_id": row["assignment_id"],
            "job_id": row["job_id"],
            "stage_id": row["stage_id"],
            "task_id": row["task_id"],
        }
        if value != expected:
            raise M2StorageIntegrityError("stored evidence usage scope mismatch")

    @staticmethod
    def _evidence_identity_proof_from_row(
        row: sqlite3.Row,
    ) -> _EvidenceIdentityProof:
        try:
            value = verify_canonical_document(
                row["canonical_evidence_json"], row["content_sha256"]
            )
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError(
                "stored evidence identity is invalid"
            ) from error
        expected = {
            "evidence": {
                "content_reference": row["content_reference"],
                "evidence_id": row["evidence_id"],
                "kind": row["evidence_kind"],
            },
            "owner_scope": {
                "assignment_id": row["owner_assignment_id"],
                "job_id": row["owner_job_id"],
                "stage_id": row["owner_stage_id"],
                "task_id": row["owner_task_id"],
            },
        }
        if value != expected:
            raise M2StorageIntegrityError(
                "stored evidence identity projection mismatch"
            )
        return _EvidenceIdentityProof(
            row["evidence_id"],
            row["evidence_kind"],
            row["content_reference"],
            row["owner_job_id"],
            row["owner_task_id"],
            row["owner_assignment_id"],
            row["owner_stage_id"],
            row["canonical_evidence_json"],
            row["content_sha256"],
        )

    @staticmethod
    def _evidence_usage_proof_from_row(row: sqlite3.Row) -> _EvidenceUsageProof:
        M2DurableRepository._verify_evidence_usage(row)
        return _EvidenceUsageProof(
            row["input_namespace"],
            row["input_id"],
            row["evidence_id"],
            row["job_id"],
            row["task_id"],
            row["assignment_id"],
            row["stage_id"],
            row["scope_json"],
            row["scope_sha256"],
        )

    def _load_publications_for_jobs(
        self,
        connection: sqlite3.Connection,
        jobs: tuple[M2JobExecutionRoot, ...],
    ):
        keys = sorted(
            {
                (
                    task.definition.job_id,
                    task.definition.source_revision,
                    task.definition.source_handoff_id,
                )
                for job in jobs
                for task in job.tasks
            }
        )
        publications = []
        for job_id, source_revision, handoff_id in keys:
            row = connection.execute(
                """
                SELECT * FROM m1_handoff_publications
                WHERE job_id = ? AND source_revision = ? AND handoff_id = ?
                """,
                (job_id, source_revision, handoff_id),
            ).fetchone()
            if row is None:
                raise M2StorageIntegrityError("task provenance publication is missing")
            try:
                publications.append(
                    M1BoundaryPublicationRepository._publication_from_row(row)
                )
            except Exception as error:
                raise M2StorageIntegrityError("stored M1 publication is invalid") from error
        return tuple(publications)

    def _load_ack_receipts(
        self,
        connection: sqlite3.Connection,
        directives: tuple[DirectiveRoot, ...],
    ) -> tuple[ProcessedEventReceipt, ...]:
        ids = tuple(
            item.acknowledged_event_id
            for item in directives
            if item.acknowledged_event_id is not None
        )
        return self._load_receipts(connection, ids)

    def _load_receipts(
        self,
        connection: sqlite3.Connection,
        event_ids: tuple[str, ...] | None,
    ) -> tuple[ProcessedEventReceipt, ...]:
        if event_ids == ():
            return ()
        sql = """
            SELECT * FROM m2_input_inbox
            WHERE input_namespace = 'FIELD_EVENT'
              AND processing_status = 'COMPLETED'
              AND appended_receipt_json IS NOT NULL
        """
        parameters: tuple[object, ...] = ()
        if event_ids is not None:
            unique_ids = tuple(sorted(set(event_ids)))
            if not unique_ids:
                return ()
            sql += " AND input_id IN (" + ",".join("?" for _ in unique_ids) + ")"
            parameters = unique_ids
        sql += " ORDER BY input_id"
        rows = connection.execute(sql, parameters).fetchall()
        receipts: list[ProcessedEventReceipt] = []
        for row in rows:
            try:
                self._verify_inbox_input(row)
                verify_canonical_document(
                    row["appended_receipt_json"], row["appended_receipt_sha256"]
                )
                receipt = deserialize_processed_event_receipt(
                    row["appended_receipt_json"]
                )
                stored_event = deserialize_field_event_envelope(
                    row["canonical_input_json"]
                )
                stored_response = deserialize_effects(row["response_effects_json"])
                stored_missing = deserialize_string_tuple(
                    row["missing_requirements_json"], "MissingRequirements"
                )
                stored_reasons = deserialize_string_tuple(
                    row["reason_codes_json"], "ReasonCodes"
                )
            except (SerializationError, ValueError) as error:
                raise M2StorageIntegrityError("stored event receipt is invalid") from error
            if (
                receipt.event.event_id != row["input_id"]
                or receipt.event != stored_event
                or receipt.server_event_id != row["presented_server_event_id"]
                or receipt.outcome.value != row["outcome"]
                or receipt.response_effects != stored_response
                or receipt.missing_requirements != stored_missing
                or receipt.reason_codes != stored_reasons
            ):
                raise M2StorageIntegrityError("stored receipt identity mismatch")
            receipts.append(receipt)
        return tuple(receipts)

    # -- Preconditions and CAS. --

    def _registry_precondition(self, connection: sqlite3.Connection) -> RootPrecondition:
        row = connection.execute(
            "SELECT registry_revision, content_sha256 FROM m2_worker_registry WHERE registry_key='GLOBAL'"
        ).fetchone()
        if row is None:
            raise M2NotFoundError("canonical worker registry is not initialized")
        return RootPrecondition(
            "WORKER_REGISTRY",
            "GLOBAL",
            RootAccess.READ_AUTHORITY,
            row["registry_revision"],
            row["content_sha256"],
        )

    def _build_preconditions(
        self,
        connection: sqlite3.Connection,
        jobs,
        plans,
        directives,
        publications,
        receipts,
        evidence_ids: set[str],
    ) -> PreconditionVector:
        roots: list[RootPrecondition] = [self._registry_precondition(connection)]
        for kind, values, table, id_name, revision_name in (
            ("JOB_EXECUTION", jobs, "m2_job_execution_roots", "job_id", "job_execution_revision"),
            ("PLAN_DAY", plans, "m2_plan_day_roots", "plan_day_id", "plan_day_revision"),
            ("DIRECTIVE", directives, "m2_directive_roots", "directive_id", "directive_revision"),
        ):
            for value in values:
                root_id = getattr(value, id_name)
                row = connection.execute(
                    f"SELECT {revision_name}, content_sha256 FROM {table} WHERE {id_name} = ?",
                    (root_id,),
                ).fetchone()
                assert row is not None
                roots.append(
                    RootPrecondition(
                        kind,
                        root_id,
                        RootAccess.READ_AUTHORITY,
                        row[revision_name],
                        row["content_sha256"],
                    )
                )
        immutable = self._build_immutable_preconditions(
            connection,
            jobs,
            publications,
            receipts,
            evidence_ids,
        )
        return PreconditionVector(tuple(roots), tuple(immutable))

    def _build_immutable_preconditions(
        self,
        connection: sqlite3.Connection,
        jobs,
        publications,
        receipts,
        evidence_ids: set[str],
    ) -> list[ImmutablePrecondition]:
        """Build append-only authority without consulting mutable root rows."""

        immutable: list[ImmutablePrecondition] = []
        for publication in publications:
            immutable.append(
                ImmutablePrecondition(
                    "M1_PUBLICATION",
                    canonical_json(
                        [
                            publication.job_id,
                            publication.source_revision,
                            publication.handoff_id,
                        ]
                    ),
                    publication.content_sha256,
                )
            )
        for job in jobs:
            for task in job.tasks:
                row = connection.execute(
                    "SELECT route_sha256 FROM m2_task_routes WHERE task_id = ?",
                    (task.definition.task_id,),
                ).fetchone()
                assert row is not None
                immutable.append(
                    ImmutablePrecondition("TASK_ROUTE", task.definition.task_id, row[0])
                )
            for assignment in job.assignments:
                row = connection.execute(
                    "SELECT route_sha256 FROM m2_assignment_routes WHERE assignment_id = ?",
                    (assignment.assignment_id,),
                ).fetchone()
                assert row is not None
                immutable.append(
                    ImmutablePrecondition("ASSIGNMENT_ROUTE", assignment.assignment_id, row[0])
                )
        for receipt in receipts:
            row = connection.execute(
                """
                SELECT appended_receipt_sha256 FROM m2_input_inbox
                WHERE input_namespace='FIELD_EVENT' AND input_id=?
                """,
                (receipt.event.event_id,),
            ).fetchone()
            assert row is not None
            immutable.append(
                ImmutablePrecondition("EVENT_RECEIPT", receipt.event.event_id, row[0])
            )
        for evidence_id in sorted(evidence_ids):
            row = connection.execute(
                "SELECT content_sha256 FROM m2_evidence_identities WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if row is not None:
                immutable.append(
                    ImmutablePrecondition("EVIDENCE", evidence_id, row[0])
                )
            usage_rows = connection.execute(
                """
                SELECT input_namespace, input_id, evidence_id, scope_sha256
                FROM m2_evidence_usages WHERE evidence_id=?
                ORDER BY input_namespace, input_id
                """,
                (evidence_id,),
            ).fetchall()
            immutable.extend(
                ImmutablePrecondition(
                    "EVIDENCE_USAGE",
                    canonical_json(
                        [
                            item["input_namespace"],
                            item["input_id"],
                            item["evidence_id"],
                        ]
                    ),
                    item["scope_sha256"],
                )
                for item in usage_rows
            )
        return immutable

    @staticmethod
    def _allowed_mutation_roots(
        explicit_input: ExplicitInput,
        scope: M2ReductionScope,
    ) -> frozenset[tuple[RootKind, str]]:
        """Return structural mutation capability without consulting a Reduction."""

        allowed: set[tuple[RootKind, str]] = set()

        def allow_plan(plan_day_id: str | None) -> None:
            if plan_day_id is not None and scope.plan_day(plan_day_id) is not None:
                allowed.add((RootKind.PLAN_DAY, plan_day_id))

        def allow_task_job(task_id: str | None) -> None:
            task = scope.task(task_id) if task_id is not None else None
            if (
                task is not None
                and scope.job_execution(task.definition.job_id) is not None
            ):
                allowed.add((RootKind.JOB_EXECUTION, task.definition.job_id))

        def allow_directive(directive_id: str | None) -> DirectiveRoot | None:
            directive = (
                scope.directive(directive_id) if directive_id is not None else None
            )
            if directive is not None:
                allowed.add((RootKind.DIRECTIVE, directive.directive_id))
            return directive

        if isinstance(explicit_input, FieldEventInput):
            event = explicit_input.event
            if event.event_type in {
                FieldEventType.DAY_PLAN_ACTIVATED,
                FieldEventType.UNAVAILABLE_TODAY_REPORTED,
                FieldEventType.DAY_CLOSE_REPORTED,
            }:
                allow_plan(event.plan_day_id)
            elif event.event_type in {
                FieldEventType.WORK_START_BLOCKED,
                FieldEventType.SITE_PROBLEM_REPORTED,
                FieldEventType.TASK_COMPLETION_REPORTED,
                FieldEventType.STAGE_COMPLETION_REPORTED,
                FieldEventType.TECHNICAL_WAIT_REPORTED,
                FieldEventType.VISIT_COMPLETION_REPORTED,
                FieldEventType.COMPLETION_DISPUTED,
            }:
                allow_task_job(event.task_id)
            elif event.event_type is FieldEventType.RESOURCE_MISSING_REPORTED:
                allow_task_job(event.task_id)
            elif event.event_type is FieldEventType.WORKER_ACKNOWLEDGED:
                directive = allow_directive(event.directive_id)
                if (
                    directive is not None
                    and directive.directive_class is DirectiveClass.ACTION
                    and directive.proposed_plan_reference is not None
                ):
                    allow_plan(directive.plan_day_id)
            elif event.event_type is FieldEventType.WORKER_ACTION_EXCEPTION:
                directive = allow_directive(event.directive_id)
                if (
                    directive is not None
                    and directive.directive_class is DirectiveClass.STOP
                ):
                    allow_task_job(directive.task_id)
            elif event.event_type in {
                FieldEventType.START_DELAY_REPORTED,
                FieldEventType.START_EXCEPTION_REPORTED,
                FieldEventType.SCOPE_FACT_REPORTED,
            }:
                pass
            else:  # pragma: no cover - every current FieldEventType is classified
                raise M2StorageIntegrityError(
                    "field event has no structural mutation classification"
                )
        else:
            if explicit_input.signal_type in {
                SystemSignalType.START_WINDOW_ELAPSED,
                SystemSignalType.END_OF_DAY_POLICY_SATISFIED,
            }:
                allow_plan(explicit_input.plan_day_id)
            elif explicit_input.signal_type is SystemSignalType.DIRECTIVE_E1_ELAPSED:
                allow_directive(explicit_input.directive_id)
            elif explicit_input.signal_type in {
                SystemSignalType.EARLY_FINISH_EVALUATED,
                SystemSignalType.STATE_REHYDRATED,
                SystemSignalType.SYNC_REPLAYED,
                SystemSignalType.DAY_ROLLED_OVER,
            }:
                pass
            else:  # pragma: no cover - every current SystemSignalType is classified
                raise M2StorageIntegrityError(
                    "system signal has no structural mutation classification"
                )

        return frozenset(allowed)

    @staticmethod
    def _classify_root_access(
        vector: PreconditionVector,
        allowed_mutations: frozenset[tuple[RootKind, str]],
    ) -> PreconditionVector:
        allowed = {
            (root_kind.value, root_id)
            for root_kind, root_id in allowed_mutations
        }
        hydrated = {(item.root_kind, item.root_id) for item in vector.roots}
        if allowed - hydrated:
            raise M2StorageIntegrityError(
                "mutation policy referenced a root outside hydrated scope"
            )
        roots = tuple(
            replace(
                item,
                access=RootAccess.MUTATE
                if (item.root_kind, item.root_id) in allowed
                else RootAccess.READ_AUTHORITY,
            )
            for item in vector.roots
        )
        return PreconditionVector(roots, vector.immutable_records)

    @staticmethod
    def _required_applied_mutation_roots(
        explicit_input: ExplicitInput,
        scope: M2ReductionScope,
    ) -> frozenset[tuple[RootKind, str]]:
        """Return roots an APPLIED result cannot legitimately omit.

        This is deliberately narrower than mutation capability.  Some reporting
        operations are APPLIED even when their idempotent state replacement is
        structurally unchanged; completion, ACK, activation, and applied system
        transitions always carry the roots below.
        """

        required: set[tuple[RootKind, str]] = set()

        def require_task_job(task_id: str | None) -> None:
            task = scope.task(task_id) if task_id is not None else None
            if task is not None:
                required.add((RootKind.JOB_EXECUTION, task.definition.job_id))

        if isinstance(explicit_input, FieldEventInput):
            event = explicit_input.event
            if event.event_type is FieldEventType.DAY_PLAN_ACTIVATED:
                if event.plan_day_id is not None and scope.plan_day(event.plan_day_id):
                    required.add((RootKind.PLAN_DAY, event.plan_day_id))
            elif event.event_type is FieldEventType.SITE_PROBLEM_REPORTED:
                require_task_job(event.task_id)
            elif event.event_type in {
                FieldEventType.TASK_COMPLETION_REPORTED,
                FieldEventType.STAGE_COMPLETION_REPORTED,
                FieldEventType.VISIT_COMPLETION_REPORTED,
            }:
                require_task_job(event.task_id)
            elif event.event_type is FieldEventType.WORKER_ACKNOWLEDGED:
                directive = (
                    scope.directive(event.directive_id)
                    if event.directive_id is not None
                    else None
                )
                if directive is not None:
                    required.add((RootKind.DIRECTIVE, directive.directive_id))
            elif event.event_type is FieldEventType.WORKER_ACTION_EXCEPTION:
                directive = (
                    scope.directive(event.directive_id)
                    if event.directive_id is not None
                    else None
                )
                if directive is not None:
                    required.add((RootKind.DIRECTIVE, directive.directive_id))
                    if directive.directive_class is DirectiveClass.STOP:
                        require_task_job(directive.task_id)
        elif explicit_input.signal_type in {
            SystemSignalType.START_WINDOW_ELAPSED,
            SystemSignalType.END_OF_DAY_POLICY_SATISFIED,
        }:
            if (
                explicit_input.plan_day_id is not None
                and scope.plan_day(explicit_input.plan_day_id)
            ):
                required.add((RootKind.PLAN_DAY, explicit_input.plan_day_id))
        elif explicit_input.signal_type is SystemSignalType.DIRECTIVE_E1_ELAPSED:
            directive = (
                scope.directive(explicit_input.directive_id)
                if explicit_input.directive_id is not None
                else None
            )
            if directive is not None:
                required.add((RootKind.DIRECTIVE, directive.directive_id))

        return frozenset(required)

    @staticmethod
    def _validate_reduction_result(
        previous: M2ReductionScope,
        reduction: Reduction,
        preconditions: PreconditionVector,
    ) -> None:
        """Validate the complete reducer result before the first canonical UPDATE."""

        delta_identities = tuple(
            (delta.root_kind, delta.root_id) for delta in reduction.root_deltas
        )
        if len(delta_identities) != len(set(delta_identities)):
            raise M2StorageIntegrityError("root delta identities must be unique")
        if delta_identities != tuple(
            sorted(delta_identities, key=lambda item: (item[0].value, item[1]))
        ):
            raise M2StorageIntegrityError("root deltas must use canonical ordering")

        precondition_by_id = {
            (item.root_kind, item.root_id): item for item in preconditions.roots
        }
        delta_by_id = {
            (delta.root_kind, delta.root_id): delta
            for delta in reduction.root_deltas
        }

        for delta in reduction.root_deltas:
            precondition = precondition_by_id.get(
                (delta.root_kind.value, delta.root_id)
            )
            if precondition is None:
                raise M2StorageIntegrityError(
                    "reducer mutated a root outside hydrated scope"
                )
            if precondition.access is not RootAccess.MUTATE:
                raise M2StorageIntegrityError(
                    "reducer mutated a READ_AUTHORITY root"
                )
            if delta.expected_revision != precondition.expected_revision:
                raise M2StorageIntegrityError(
                    "root delta expected revision differs from hydrated root"
                )
            if delta.resulting_revision != delta.expected_revision + 1:
                raise M2StorageIntegrityError(
                    "root delta revision is not exactly +1"
                )

        if previous.publications != reduction.state.publications:
            raise M2StorageIntegrityError("reducer changed hydrated M1 publications")
        if previous.worker_registry != reduction.state.worker_registry:
            raise M2StorageIntegrityError("reducer changed worker registry")

        root_groups = (
            (
                RootKind.JOB_EXECUTION,
                previous.job_execution_roots,
                reduction.state.job_execution_roots,
                lambda item: item.job_id,
                "job_execution_revision",
            ),
            (
                RootKind.PLAN_DAY,
                previous.plan_day_roots,
                reduction.state.plan_day_roots,
                lambda item: item.plan_day_id,
                "plan_day_revision",
            ),
            (
                RootKind.DIRECTIVE,
                previous.directive_roots,
                reduction.state.directive_roots,
                lambda item: item.directive_id,
                "directive_revision",
            ),
        )
        for (
            root_kind,
            previous_roots,
            next_roots,
            identity,
            revision_name,
        ) in root_groups:
            previous_by_id = {identity(item): item for item in previous_roots}
            next_by_id = {identity(item): item for item in next_roots}
            if previous_by_id.keys() != next_by_id.keys():
                raise M2StorageIntegrityError(
                    "reducer added or removed a canonical root"
                )
            for root_id, previous_root in previous_by_id.items():
                next_root = next_by_id[root_id]
                delta = delta_by_id.get((root_kind, root_id))
                previous_revision = getattr(previous_root, revision_name)
                comparable_next = replace(
                    next_root,
                    **{revision_name: previous_revision},
                )
                changed = previous_root != comparable_next
                if not changed and previous_root != next_root:
                    raise M2StorageIntegrityError(
                        "reducer changed only a canonical root revision"
                    )
                if changed != (delta is not None):
                    raise M2StorageIntegrityError(
                        "canonical root change and root delta disagree"
                    )
                if delta is None:
                    continue
                if delta.next_root != next_root:
                    raise M2StorageIntegrityError(
                        "root delta next_root differs from Reduction.state"
                    )
                if delta.expected_revision != previous_revision:
                    raise M2StorageIntegrityError(
                        "root delta expected revision differs from previous state"
                    )
                if delta.resulting_revision != getattr(next_root, revision_name):
                    raise M2StorageIntegrityError(
                        "root delta resulting revision differs from next state"
                    )

        if reduction.appended_receipt is None:
            expected_receipts = previous.processed_events
        else:
            try:
                expected_receipts = ProcessedEventLedger(
                    (*previous.processed_events.receipts, reduction.appended_receipt)
                )
            except ValueError as error:
                raise M2StorageIntegrityError(
                    "reducer appended an invalid processed event receipt"
                ) from error
        if reduction.state.processed_events != expected_receipts:
            raise M2StorageIntegrityError(
                "Reduction.state processed-event ledger is inconsistent"
            )

    def _recheck_preconditions(
        self, connection: sqlite3.Connection, vector: PreconditionVector
    ) -> None:
        root_specs = {
            "WORKER_REGISTRY": (
                "m2_worker_registry", "registry_key", "registry_revision"
            ),
            "JOB_EXECUTION": (
                "m2_job_execution_roots", "job_id", "job_execution_revision"
            ),
            "PLAN_DAY": ("m2_plan_day_roots", "plan_day_id", "plan_day_revision"),
            "DIRECTIVE": (
                "m2_directive_roots", "directive_id", "directive_revision"
            ),
        }
        for item in vector.roots:
            table, identity, revision = root_specs[item.root_kind]
            row = connection.execute(
                f"SELECT {revision}, content_sha256 FROM {table} WHERE {identity} = ?",
                (item.root_id,),
            ).fetchone()
            if row is None or (
                row[revision] != item.expected_revision
                or row["content_sha256"] != item.expected_content_sha256
            ):
                raise M2StaleRevisionError(
                    f"stale authority precondition: {item.root_kind}/{item.root_id}"
                )
        self._recheck_immutable_preconditions(
            connection, vector.immutable_records
        )

    @staticmethod
    def _recheck_immutable_preconditions(
        connection: sqlite3.Connection,
        immutable_records: tuple[ImmutablePrecondition, ...],
    ) -> None:
        for item in immutable_records:
            if item.record_kind == "M1_PUBLICATION":
                identity = verify_canonical_document(item.record_id)
                if not isinstance(identity, list) or len(identity) != 3:
                    raise M2StorageIntegrityError("invalid publication precondition identity")
                job_id, revision, handoff_id = identity
                row = connection.execute(
                    """
                    SELECT content_sha256 FROM m1_handoff_publications
                    WHERE job_id=? AND source_revision=? AND handoff_id=?
                    """,
                    (job_id, revision, handoff_id),
                ).fetchone()
                actual = row[0] if row else None
            elif item.record_kind == "TASK_ROUTE":
                row = connection.execute(
                    "SELECT route_sha256 FROM m2_task_routes WHERE task_id=?",
                    (item.record_id,),
                ).fetchone()
                actual = row[0] if row else None
            elif item.record_kind == "ASSIGNMENT_ROUTE":
                row = connection.execute(
                    "SELECT route_sha256 FROM m2_assignment_routes WHERE assignment_id=?",
                    (item.record_id,),
                ).fetchone()
                actual = row[0] if row else None
            elif item.record_kind == "EVENT_RECEIPT":
                row = connection.execute(
                    """
                    SELECT appended_receipt_sha256 FROM m2_input_inbox
                    WHERE input_namespace='FIELD_EVENT' AND input_id=?
                    """,
                    (item.record_id,),
                ).fetchone()
                actual = row[0] if row else None
            elif item.record_kind == "EVIDENCE":
                row = connection.execute(
                    "SELECT content_sha256 FROM m2_evidence_identities WHERE evidence_id=?",
                    (item.record_id,),
                ).fetchone()
                actual = row[0] if row else None
            elif item.record_kind == "EVIDENCE_USAGE":
                identity = verify_canonical_document(item.record_id)
                if not isinstance(identity, list) or len(identity) != 3:
                    raise M2StorageIntegrityError("invalid evidence usage identity")
                namespace, input_id, evidence_id = identity
                row = connection.execute(
                    """
                    SELECT scope_sha256 FROM m2_evidence_usages
                    WHERE input_namespace=? AND input_id=? AND evidence_id=?
                    """,
                    (namespace, input_id, evidence_id),
                ).fetchone()
                actual = row[0] if row else None
            else:  # pragma: no cover - impossible from local builder
                raise M2StorageIntegrityError("unknown immutable precondition kind")
            if actual != item.content_sha256:
                raise M2StaleRevisionError(
                    f"stale immutable precondition: {item.record_kind}/{item.record_id}"
                )

    def _apply_deltas(
        self, connection: sqlite3.Connection, deltas: tuple[RootDelta, ...]
    ) -> ResultingRevisionVector:
        results: list[ResultingRevision] = []
        for ordinal, delta in enumerate(deltas):
            if delta.resulting_revision != delta.expected_revision + 1:
                raise M2StorageIntegrityError("root delta revision is not exactly +1")
            if delta.root_kind is RootKind.JOB_EXECUTION:
                root = delta.next_root
                if not isinstance(root, M2JobExecutionRoot):
                    raise M2StorageIntegrityError("job delta contains another root type")
                raw = serialize_job_execution_root(root)
                digest = sha256_text(raw)
                cursor = connection.execute(
                    """
                    UPDATE m2_job_execution_roots
                    SET job_execution_revision=?, canonical_root_json=?, content_sha256=?
                    WHERE job_id=? AND job_execution_revision=?
                    """,
                    (
                        root.job_execution_revision,
                        raw,
                        digest,
                        root.job_id,
                        delta.expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise M2StaleRevisionError("stale job execution root")
                row = connection.execute(
                    "SELECT * FROM m2_job_execution_roots WHERE job_id=?", (root.job_id,)
                ).fetchone()
                assert row is not None
                self._job_root_from_connection(connection, row)
            elif delta.root_kind is RootKind.PLAN_DAY:
                root = delta.next_root
                if not isinstance(root, PlanDayRoot):
                    raise M2StorageIntegrityError("plan delta contains another root type")
                raw = serialize_plan_day_root(root)
                digest = sha256_text(raw)
                cursor = connection.execute(
                    """
                    UPDATE m2_plan_day_roots
                    SET worker_id=?, business_date=?, status=?, plan_day_revision=?,
                        canonical_root_json=?, content_sha256=?
                    WHERE plan_day_id=? AND plan_day_revision=?
                    """,
                    (
                        root.worker_id,
                        root.business_date.isoformat(),
                        root.status.value,
                        root.plan_day_revision,
                        raw,
                        digest,
                        root.plan_day_id,
                        delta.expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise M2StaleRevisionError("stale plan day root")
            else:
                root = delta.next_root
                if not isinstance(root, DirectiveRoot):
                    raise M2StorageIntegrityError("directive delta contains another root type")
                raw = serialize_directive_root(root)
                digest = sha256_text(raw)
                cursor = connection.execute(
                    """
                    UPDATE m2_directive_roots
                    SET delivery_evidence=?, acknowledged_event_id=?, exception_event_id=?,
                        e1_escalated=?, stop_in_force=?, directive_revision=?,
                        canonical_root_json=?, content_sha256=?
                    WHERE directive_id=? AND directive_revision=?
                    """,
                    (
                        root.delivery_evidence.value,
                        root.acknowledged_event_id,
                        root.exception_event_id,
                        int(root.e1_escalated),
                        int(root.stop_in_force),
                        root.directive_revision,
                        raw,
                        digest,
                        root.directive_id,
                        delta.expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise M2StaleRevisionError("stale directive root")
            results.append(
                ResultingRevision(
                    delta.root_kind.value,
                    delta.root_id,
                    delta.resulting_revision,
                    digest,
                )
            )
            if ordinal == 0:
                self._fault("after_first_root_update")
        return ResultingRevisionVector(tuple(results))

    @staticmethod
    def _resulting_vector_for_reduction(
        reduction: Reduction,
    ) -> ResultingRevisionVector:
        values: list[ResultingRevision] = []
        for delta in reduction.root_deltas:
            if delta.root_kind is RootKind.JOB_EXECUTION:
                raw = serialize_job_execution_root(delta.next_root)
            elif delta.root_kind is RootKind.PLAN_DAY:
                raw = serialize_plan_day_root(delta.next_root)
            else:
                raw = serialize_directive_root(delta.next_root)
            values.append(
                ResultingRevision(
                    delta.root_kind.value,
                    delta.root_id,
                    delta.resulting_revision,
                    sha256_text(raw),
                )
            )
        return ResultingRevisionVector(tuple(values))

    # -- Durable receipt/evidence/outbox/result. --

    @staticmethod
    def _evidence_projection_scope_is_canonical(
        scope: M2ReductionScope,
        event: FieldEventEnvelope,
    ) -> bool:
        """Return whether an attachment can enter canonical evidence projections.

        A rejected input and its attachments remain durable in the inbox receipt even
        when stale or cross-scope references cannot establish canonical evidence
        ownership.  Such attachments must not create evidence identity/usage rows.
        """

        if not event.attachments:
            return True
        job = scope.job_execution(event.job_id) if event.job_id is not None else None
        task = scope.task(event.task_id) if event.task_id is not None else None
        assignment = (
            scope.assignment(event.assignment_id)
            if event.assignment_id is not None
            else None
        )
        plan = (
            scope.plan_day(event.plan_day_id)
            if event.plan_day_id is not None
            else None
        )
        if event.job_id is not None and job is None:
            return False
        if event.task_id is not None:
            if (
                task is None
                or event.job_id is None
                or task.definition.job_id != event.job_id
            ):
                return False
        if event.assignment_id is not None:
            if (
                assignment is None
                or task is None
                or event.job_id is None
                or assignment.job_id != event.job_id
                or assignment.task_id != task.definition.task_id
                or assignment.task_definition_version
                != task.definition.definition_version
            ):
                return False
        if event.plan_day_id is not None:
            if plan is None:
                return False
            if (
                assignment is not None
                and event.plan_day_id not in assignment.plan_day_ids
            ):
                return False
        if event.stage_id is not None and (
            task is None or event.stage_id not in task.definition.stage_ids
        ):
            return False
        return True

    def _persist_evidence(
        self, connection: sqlite3.Connection, receipt: ProcessedEventReceipt
    ) -> None:
        event = receipt.event
        for evidence in event.attachments:
            existing = connection.execute(
                "SELECT * FROM m2_evidence_identities WHERE evidence_id=?",
                (evidence.evidence_id,),
            ).fetchone()
            scope_identity = (
                event.job_id,
                event.task_id,
                event.assignment_id,
                event.stage_id,
            )
            raw = _evidence_identity_document(evidence, scope_identity)
            digest = sha256_text(raw)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO m2_evidence_identities(
                        evidence_id, evidence_kind, content_reference,
                        owner_job_id, owner_task_id, owner_assignment_id,
                        owner_stage_id,
                        canonical_evidence_json, content_sha256
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence.evidence_id,
                        evidence.kind.value,
                        evidence.content_reference,
                        *scope_identity,
                        raw,
                        digest,
                    ),
                )
            elif (
                existing["evidence_kind"] != evidence.kind.value
                or existing["content_reference"] != evidence.content_reference
            ):
                # The reducer has already rejected the conflicting event. The first
                # immutable evidence identity remains authoritative.
                if receipt.outcome is not ReductionOutcome.REJECTED:
                    raise M2StorageIntegrityError(
                        "reducer accepted conflicting evidence identity content"
                    )
                continue
            elif (
                existing["owner_job_id"] != event.job_id
                or existing["owner_task_id"] != event.task_id
                or (
                    existing["owner_assignment_id"] is not None
                    and event.assignment_id is not None
                    and existing["owner_assignment_id"] != event.assignment_id
                )
                or (
                    existing["owner_stage_id"] is not None
                    and event.stage_id is not None
                    and existing["owner_stage_id"] != event.stage_id
                )
            ):
                if receipt.outcome is not ReductionOutcome.REJECTED:
                    raise M2StorageIntegrityError(
                        "reducer accepted cross-scope evidence identity reuse"
                    )
                continue
            scope = canonical_json(_evidence_scope_payload(event))
            connection.execute(
                """
                INSERT INTO m2_evidence_usages(
                    input_namespace, input_id, evidence_id, job_id, task_id,
                    assignment_id, stage_id, scope_json, scope_sha256
                ) VALUES('FIELD_EVENT', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    evidence.evidence_id,
                    event.job_id,
                    event.task_id,
                    event.assignment_id,
                    event.stage_id,
                    scope,
                    sha256_text(scope),
                ),
            )

    def _insert_outbox(
        self,
        connection: sqlite3.Connection,
        namespace: str,
        input_id: str,
        effects: tuple[SystemEffect, ...],
        recorded_at: datetime,
    ) -> None:
        for ordinal, effect in enumerate(effects):
            raw = serialize_effect(effect)
            connection.execute(
                """
                INSERT INTO m2_effect_outbox(
                    input_namespace, input_id, effect_ordinal, effect_type,
                    canonical_effect_json, effect_sha256, dispatch_status, recorded_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'RECORDED', ?)
                """,
                (
                    namespace,
                    input_id,
                    ordinal,
                    effect.effect_type.value,
                    raw,
                    sha256_text(raw),
                    recorded_at.isoformat(),
                ),
            )

    @staticmethod
    def _normalize_reduction_for_durability(
        previous: M2ReductionScope,
        explicit_input: ExplicitInput,
        reduction: Reduction,
    ) -> Reduction:
        """Attach the field receipt required by the durable inbox contract.

        The pure reducer rejects an unsupported rule before it enters the normal
        field-event handler and therefore cannot build its usual receipt.  At the
        persistence boundary the event has nevertheless been durably accepted and
        owns its server-event claim, so the deterministic rejection must enter the
        same immutable field-event ledger as every other completed field input.
        """

        if not (
            isinstance(explicit_input, FieldEventInput)
            and reduction.outcome is ReductionOutcome.REJECTED
            and reduction.appended_receipt is None
            and reduction.reason_codes == ("UNSUPPORTED_RULE_VERSION",)
        ):
            return reduction
        receipt = ProcessedEventReceipt(
            event=explicit_input.event,
            server_event_id=explicit_input.server_event_id,
            outcome=reduction.outcome,
            response_effects=reduction.response_effects,
            missing_requirements=reduction.missing_requirements,
            reason_codes=reduction.reason_codes,
        )
        ledger = ProcessedEventLedger((*previous.processed_events.receipts, receipt))
        return replace(
            reduction,
            state=replace(reduction.state, processed_events=ledger),
            appended_receipt=receipt,
            server_event_id=explicit_input.server_event_id,
        )

    @staticmethod
    def _evidence_preimage_from_preconditions(
        connection: sqlite3.Connection,
        preconditions: PreconditionVector,
    ) -> tuple[
        tuple[_EvidenceIdentityProof, ...], tuple[_EvidenceUsageProof, ...]
    ]:
        identities: list[_EvidenceIdentityProof] = []
        usages: list[_EvidenceUsageProof] = []
        for item in preconditions.immutable_records:
            if item.record_kind == "EVIDENCE":
                row = connection.execute(
                    "SELECT * FROM m2_evidence_identities WHERE evidence_id=?",
                    (item.record_id,),
                ).fetchone()
                if row is None:
                    raise M2StaleRevisionError(
                        "evidence input authority disappeared before completion"
                    )
                proof = M2DurableRepository._evidence_identity_proof_from_row(row)
                if proof.content_sha256 != item.content_sha256:
                    raise M2StaleRevisionError(
                        "evidence input authority changed before completion"
                    )
                identities.append(proof)
            elif item.record_kind == "EVIDENCE_USAGE":
                identity = verify_canonical_document(item.record_id)
                if not isinstance(identity, list) or len(identity) != 3:
                    raise M2StorageIntegrityError(
                        "invalid evidence usage precondition identity"
                    )
                row = connection.execute(
                    """
                    SELECT * FROM m2_evidence_usages
                    WHERE input_namespace=? AND input_id=? AND evidence_id=?
                    """,
                    tuple(identity),
                ).fetchone()
                if row is None:
                    raise M2StaleRevisionError(
                        "evidence usage input authority disappeared before completion"
                    )
                proof = M2DurableRepository._evidence_usage_proof_from_row(row)
                if proof.scope_sha256 != item.content_sha256:
                    raise M2StaleRevisionError(
                        "evidence usage input authority changed before completion"
                    )
                usages.append(proof)
        return tuple(sorted(identities)), tuple(sorted(usages))

    @staticmethod
    def _serialize_reduction_input_proof(
        scope: M2ReductionScope,
        evidence_identities: tuple[_EvidenceIdentityProof, ...] = (),
        evidence_usages: tuple[_EvidenceUsageProof, ...] = (),
        complete_plan_day_assignment_scope_ids: tuple[str, ...] = (),
    ) -> str:
        """Serialize only the command-specific reducer preimage needed for replay.

        This immutable audit proof is not a canonical aggregate or owner. It
        preserves the exact historical reducer input so a later state cannot
        reinterpret the first completed result. Proof v2 additionally certifies
        exhaustive event-time plan-day assignment scope for the bridge.
        """

        schema_version = (
            "m2-reduction-input-proof-v2"
            if complete_plan_day_assignment_scope_ids
            else "m2-reduction-input-proof-v1"
        )
        payload: dict[str, object] = {
            "directive_roots": [
                verify_canonical_document(serialize_directive_root(item))
                for item in scope.directive_roots
            ],
            "evidence_identities": [
                {
                    "canonical_evidence": verify_canonical_document(
                        item.canonical_evidence_json,
                        item.content_sha256,
                    ),
                    "content_reference": item.content_reference,
                    "content_sha256": item.content_sha256,
                    "evidence_id": item.evidence_id,
                    "evidence_kind": item.evidence_kind,
                    "owner_assignment_id": item.owner_assignment_id,
                    "owner_job_id": item.owner_job_id,
                    "owner_stage_id": item.owner_stage_id,
                    "owner_task_id": item.owner_task_id,
                }
                for item in evidence_identities
            ],
            "evidence_usages": [
                {
                    "assignment_id": item.assignment_id,
                    "evidence_id": item.evidence_id,
                    "input_id": item.input_id,
                    "input_namespace": item.input_namespace,
                    "job_id": item.job_id,
                    "scope": verify_canonical_document(
                        item.scope_json, item.scope_sha256
                    ),
                    "scope_sha256": item.scope_sha256,
                    "stage_id": item.stage_id,
                    "task_id": item.task_id,
                }
                for item in evidence_usages
            ],
            "job_execution_roots": [
                verify_canonical_document(serialize_job_execution_root(item))
                for item in scope.job_execution_roots
            ],
            "plan_day_roots": [
                verify_canonical_document(serialize_plan_day_root(item))
                for item in scope.plan_day_roots
            ],
            "processed_event_receipts": [
                verify_canonical_document(serialize_processed_event_receipt(item))
                for item in scope.processed_events.receipts
            ],
            "worker_registry": verify_canonical_document(
                serialize_worker_registry(scope.worker_registry)
            ),
        }
        if schema_version == "m2-reduction-input-proof-v2":
            payload["complete_plan_day_assignment_scope_ids"] = list(
                complete_plan_day_assignment_scope_ids
            )
        return canonical_json(
            {
                "document_type": "M2ReductionInputProof",
                "payload": payload,
                "schema_version": schema_version,
            }
        )

    @staticmethod
    def _deserialize_reduction_input_proof(
        raw: str,
        expected_sha256: str,
    ) -> _ReductionInputProof:
        document = verify_canonical_document(raw, expected_sha256)
        if not isinstance(document, Mapping) or set(document) != {
            "document_type",
            "payload",
            "schema_version",
        }:
            raise SerializationError("reduction input proof document is malformed")
        schema_version = document["schema_version"]
        if document["document_type"] != "M2ReductionInputProof" or schema_version not in {
            "m2-reduction-input-proof-v1",
            "m2-reduction-input-proof-v2",
        }:
            raise SerializationError("reduction input proof type/version mismatch")
        payload = document["payload"]
        expected_keys = {
            "directive_roots",
            "evidence_identities",
            "evidence_usages",
            "job_execution_roots",
            "plan_day_roots",
            "processed_event_receipts",
            "worker_registry",
        }
        if schema_version == "m2-reduction-input-proof-v2":
            expected_keys.add("complete_plan_day_assignment_scope_ids")
        if not isinstance(payload, Mapping) or set(payload) != expected_keys:
            raise SerializationError("reduction input proof payload is malformed")

        complete_scope_ids: tuple[str, ...] = ()
        if schema_version == "m2-reduction-input-proof-v2":
            raw_scope_ids = payload["complete_plan_day_assignment_scope_ids"]
            if not isinstance(raw_scope_ids, list) or not all(
                isinstance(item, str) and item.strip() for item in raw_scope_ids
            ):
                raise SerializationError(
                    "reduction proof completeness certification is malformed"
                )
            complete_scope_ids = tuple(raw_scope_ids)
            if (
                complete_scope_ids != tuple(sorted(set(complete_scope_ids)))
                or len(complete_scope_ids) != 1
            ):
                raise SerializationError(
                    "reduction proof completeness certification is invalid"
                )

        def documents(name: str) -> tuple[Mapping[str, object], ...]:
            values = payload[name]
            if not isinstance(values, list) or not all(
                isinstance(item, Mapping) for item in values
            ):
                raise SerializationError(f"reduction proof {name} is malformed")
            return tuple(values)

        registry_document = payload["worker_registry"]
        if not isinstance(registry_document, Mapping):
            raise SerializationError("reduction proof worker registry is malformed")
        def optional_string(value: object, name: str) -> str | None:
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise SerializationError(f"reduction proof {name} is malformed")
            return value

        identity_values = payload["evidence_identities"]
        usage_values = payload["evidence_usages"]
        if not isinstance(identity_values, list) or not isinstance(usage_values, list):
            raise SerializationError("reduction proof evidence lists are malformed")
        evidence_identities: list[_EvidenceIdentityProof] = []
        for raw_identity in identity_values:
            if not isinstance(raw_identity, Mapping) or set(raw_identity) != {
                "canonical_evidence",
                "content_reference",
                "content_sha256",
                "evidence_id",
                "evidence_kind",
                "owner_assignment_id",
                "owner_job_id",
                "owner_stage_id",
                "owner_task_id",
            }:
                raise SerializationError("reduction proof evidence identity is malformed")
            evidence_id = optional_string(raw_identity["evidence_id"], "evidence_id")
            evidence_kind = optional_string(raw_identity["evidence_kind"], "evidence_kind")
            content_reference = optional_string(
                raw_identity["content_reference"], "content_reference"
            )
            digest = optional_string(raw_identity["content_sha256"], "content_sha256")
            assert evidence_id and evidence_kind and content_reference and digest
            evidence_raw = canonical_json(raw_identity["canonical_evidence"])
            verify_canonical_document(evidence_raw, digest)
            evidence_identities.append(
                _EvidenceIdentityProof(
                    evidence_id,
                    evidence_kind,
                    content_reference,
                    optional_string(raw_identity["owner_job_id"], "owner_job_id"),
                    optional_string(raw_identity["owner_task_id"], "owner_task_id"),
                    optional_string(
                        raw_identity["owner_assignment_id"], "owner_assignment_id"
                    ),
                    optional_string(raw_identity["owner_stage_id"], "owner_stage_id"),
                    evidence_raw,
                    digest,
                )
            )
        evidence_usages: list[_EvidenceUsageProof] = []
        for raw_usage in usage_values:
            if not isinstance(raw_usage, Mapping) or set(raw_usage) != {
                "assignment_id",
                "evidence_id",
                "input_id",
                "input_namespace",
                "job_id",
                "scope",
                "scope_sha256",
                "stage_id",
                "task_id",
            }:
                raise SerializationError("reduction proof evidence usage is malformed")
            namespace = optional_string(
                raw_usage["input_namespace"], "usage.input_namespace"
            )
            input_id = optional_string(raw_usage["input_id"], "usage.input_id")
            evidence_id = optional_string(
                raw_usage["evidence_id"], "usage.evidence_id"
            )
            digest = optional_string(raw_usage["scope_sha256"], "usage.scope_sha256")
            assert namespace and input_id and evidence_id and digest
            scope_raw = canonical_json(raw_usage["scope"])
            verify_canonical_document(scope_raw, digest)
            evidence_usages.append(
                _EvidenceUsageProof(
                    namespace,
                    input_id,
                    evidence_id,
                    optional_string(raw_usage["job_id"], "usage.job_id"),
                    optional_string(raw_usage["task_id"], "usage.task_id"),
                    optional_string(
                        raw_usage["assignment_id"], "usage.assignment_id"
                    ),
                    optional_string(raw_usage["stage_id"], "usage.stage_id"),
                    scope_raw,
                    digest,
                )
            )
        if (
            tuple(evidence_identities) != tuple(sorted(evidence_identities))
            or len({item.evidence_id for item in evidence_identities})
            != len(evidence_identities)
            or tuple(evidence_usages) != tuple(sorted(evidence_usages))
            or len(
                {
                    (item.input_namespace, item.input_id, item.evidence_id)
                    for item in evidence_usages
                }
            )
            != len(evidence_usages)
        ):
            raise SerializationError(
                "reduction proof evidence authority is unordered or duplicated"
            )

        proof = _ReductionInputProof(
            schema_version=schema_version,
            worker_registry=deserialize_worker_registry(
                canonical_json(registry_document)
            ),
            job_execution_roots=tuple(
                deserialize_job_execution_root(canonical_json(item))
                for item in documents("job_execution_roots")
            ),
            plan_day_roots=tuple(
                deserialize_plan_day_root(canonical_json(item))
                for item in documents("plan_day_roots")
            ),
            directive_roots=tuple(
                deserialize_directive_root(canonical_json(item))
                for item in documents("directive_roots")
            ),
            processed_events=ProcessedEventLedger(
                tuple(
                    deserialize_processed_event_receipt(canonical_json(item))
                    for item in documents("processed_event_receipts")
                )
            ),
            evidence_identities=tuple(evidence_identities),
            evidence_usages=tuple(evidence_usages),
            complete_plan_day_assignment_scope_ids=complete_scope_ids,
        )
        return proof

    def _complete_inbox(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        reduction: Reduction,
        reduction_input_scope: M2ReductionScope,
        routing: RoutingVector,
        preconditions: PreconditionVector,
        resulting: ResultingRevisionVector,
        completed_at: datetime,
        *,
        complete_plan_day_assignment_scope_ids: tuple[str, ...] = (),
    ) -> None:
        routing_raw = serialize_routing_vector(routing)
        preconditions_raw = serialize_precondition_vector(preconditions)
        evidence_identities, evidence_usages = (
            self._evidence_preimage_from_preconditions(
                connection, preconditions
            )
        )
        proof_raw = self._serialize_reduction_input_proof(
            reduction_input_scope,
            evidence_identities,
            evidence_usages,
            complete_plan_day_assignment_scope_ids,
        )
        resulting_raw = serialize_resulting_revision_vector(resulting)
        reasons_raw = serialize_string_tuple(reduction.reason_codes, "ReasonCodes")
        missing_raw = serialize_string_tuple(
            reduction.missing_requirements, "MissingRequirements"
        )
        response_raw = serialize_effects(reduction.response_effects)
        emitted_raw = serialize_effects(reduction.emitted_effects)
        receipt_raw = (
            serialize_processed_event_receipt(reduction.appended_receipt)
            if reduction.appended_receipt is not None
            else None
        )
        cursor = connection.execute(
            """
            UPDATE m2_input_inbox
            SET processing_status='COMPLETED', routing_json=?, routing_sha256=?,
                precondition_vector_json=?, precondition_vector_sha256=?,
                reduction_input_proof_json=?, reduction_input_proof_sha256=?,
                resulting_revision_vector_json=?, resulting_revision_vector_sha256=?,
                outcome=?, reason_codes_json=?, missing_requirements_json=?,
                response_effects_json=?, emitted_effects_json=?,
                appended_receipt_json=?, appended_receipt_sha256=?, completed_at=?
            WHERE input_namespace=? AND input_id=? AND processing_status='RECEIVED'
            """,
            (
                routing_raw,
                sha256_text(routing_raw),
                preconditions_raw,
                sha256_text(preconditions_raw),
                proof_raw,
                sha256_text(proof_raw),
                resulting_raw,
                sha256_text(resulting_raw),
                reduction.outcome.value,
                reasons_raw,
                missing_raw,
                response_raw,
                emitted_raw,
                receipt_raw,
                sha256_text(receipt_raw) if receipt_raw is not None else None,
                completed_at.isoformat(),
                row["input_namespace"],
                row["input_id"],
            ),
        )
        if cursor.rowcount != 1:
            raise M2StaleRevisionError("inbox completion lost its RECEIVED claim")

    # -- Inbox strict read and replay. --

    def _verify_inbox_input(self, row: sqlite3.Row) -> None:
        try:
            verify_canonical_document(row["canonical_input_json"], row["payload_sha256"])
            verify_canonical_document(
                row["policy_context_json"], row["policy_context_sha256"]
            )
            deserialize_policy_time_context(row["policy_context_json"])
            if row["input_namespace"] == FIELD_EVENT:
                value = deserialize_field_event_envelope(row["canonical_input_json"])
                if (
                    row["input_schema_version"] != "m2-field-event-envelope-v1"
                    or value.event_id != row["input_id"]
                    or row["presented_server_event_id"] is None
                    or tuple(
                        row[name]
                        for name in (
                            "raw_job_id",
                            "raw_task_id",
                            "raw_assignment_id",
                            "raw_plan_day_id",
                            "raw_directive_id",
                            "raw_against_event_id",
                        )
                    )
                    != (
                        value.job_id,
                        value.task_id,
                        value.assignment_id,
                        value.plan_day_id,
                        value.directive_id,
                        value.against_event_id,
                    )
                ):
                    raise SerializationError("field inbox identity mismatch")
            elif row["input_namespace"] == SYSTEM_SIGNAL:
                value = deserialize_system_signal(row["canonical_input_json"])
                if (
                    row["input_schema_version"] != "m2-system-signal-v1"
                    or value.signal_id != row["input_id"]
                    or row["presented_server_event_id"] is not None
                    or row["raw_job_id"] is not None
                    or row["raw_assignment_id"] is not None
                    or row["raw_against_event_id"] is not None
                    or (row["raw_task_id"], row["raw_plan_day_id"], row["raw_directive_id"])
                    != (value.task_id, value.plan_day_id, value.directive_id)
                ):
                    raise SerializationError("signal inbox identity mismatch")
            else:
                raise SerializationError("unknown input namespace")
            context = deserialize_policy_time_context(row["policy_context_json"])
            if context.rule_version != row["rule_version"]:
                raise SerializationError("stored rule version disagrees with context")
            first_received = datetime.fromisoformat(row["first_received_at"])
            _require_aware(first_received, "stored first_received_at")
        except (SerializationError, ValueError) as error:
            raise M2StorageIntegrityError("stored inbox input is invalid") from error

    def _explicit_input_from_row(self, row: sqlite3.Row) -> ExplicitInput:
        if row["input_namespace"] == FIELD_EVENT:
            event = deserialize_field_event_envelope(row["canonical_input_json"])
            return FieldEventInput(event, row["presented_server_event_id"])
        return deserialize_system_signal(row["canonical_input_json"])

    def _completed_result_from_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        replayed: bool,
        validate_current_roots: bool = True,
    ) -> DurableReductionResult:
        if row["processing_status"] != "COMPLETED":
            raise M2StorageIntegrityError("attempted to replay incomplete input")
        try:
            verify_canonical_document(row["routing_json"], row["routing_sha256"])
            verify_canonical_document(
                row["precondition_vector_json"],
                row["precondition_vector_sha256"],
            )
            verify_canonical_document(
                row["resulting_revision_vector_json"],
                row["resulting_revision_vector_sha256"],
            )
            routing = deserialize_routing_vector(row["routing_json"])
            preconditions = deserialize_precondition_vector(
                row["precondition_vector_json"]
            )
            proof_scope = self._deserialize_reduction_input_proof(
                row["reduction_input_proof_json"],
                row["reduction_input_proof_sha256"],
            )
            resulting = deserialize_resulting_revision_vector(
                row["resulting_revision_vector_json"]
            )
            reasons = deserialize_string_tuple(row["reason_codes_json"], "ReasonCodes")
            missing = deserialize_string_tuple(
                row["missing_requirements_json"], "MissingRequirements"
            )
            response = deserialize_effects(row["response_effects_json"])
            emitted_first = deserialize_effects(row["emitted_effects_json"])
            receipt = None
            if row["appended_receipt_json"] is not None:
                verify_canonical_document(
                    row["appended_receipt_json"], row["appended_receipt_sha256"]
                )
                receipt = deserialize_processed_event_receipt(
                    row["appended_receipt_json"]
                )
            outcome = ReductionOutcome(row["outcome"])
            if receipt is not None:
                stored_event = deserialize_field_event_envelope(
                    row["canonical_input_json"]
                )
                if (
                    row["input_namespace"] != FIELD_EVENT
                    or receipt.event != stored_event
                    or receipt.server_event_id != row["presented_server_event_id"]
                    or receipt.outcome is not outcome
                    or receipt.response_effects != response
                    or receipt.missing_requirements != missing
                    or receipt.reason_codes != reasons
                ):
                    raise SerializationError(
                        "completed inbox result disagrees with its event receipt"
                    )
            self._validate_completed_authority(
                connection,
                row,
                routing,
                preconditions,
                resulting,
                outcome,
                reasons,
                missing,
                receipt,
                emitted_first,
                response,
                proof_scope,
                validate_current_roots=validate_current_roots,
            )
            completed = datetime.fromisoformat(row["completed_at"])
            _require_aware(completed, "stored completed_at")
            outbox_rows = self._outbox_rows_for_completed_result(connection, row)
            if tuple(item[0] for item in outbox_rows) != tuple(
                range(len(emitted_first))
            ):
                raise SerializationError("durable outbox ordinals are incomplete")
            for effect, outbox in zip(emitted_first, outbox_rows, strict=True):
                raw = serialize_effect(effect)
                if (
                    outbox[1] != effect.effect_type.value
                    or outbox[2] != raw
                    or outbox[3] != sha256_text(raw)
                ):
                    raise SerializationError("durable outbox payload mismatch")
        except (SerializationError, ValueError, TypeError) as error:
            raise M2StorageIntegrityError("stored completed result is invalid") from error
        # A replay never re-emits effects and never proposes another root write.
        return DurableReductionResult(
            input_namespace=row["input_namespace"],
            input_id=row["input_id"],
            outcome=outcome,
            root_deltas=(),
            appended_receipt=None,
            emitted_effects=(),
            response_effects=response,
            server_event_id=receipt.server_event_id if receipt is not None else None,
            missing_requirements=missing,
            reason_codes=reasons,
            replayed=replayed,
        )

    def _completed_scope_from_proof(
        self,
        connection: sqlite3.Connection,
        routing: RoutingVector,
        proof_scope: _ReductionInputProof,
    ) -> M2ReductionScope:
        expected_root_ids = (
            tuple(item.job_id for item in proof_scope.job_execution_roots),
            tuple(item.plan_day_id for item in proof_scope.plan_day_roots),
            tuple(item.directive_id for item in proof_scope.directive_roots),
        )
        if expected_root_ids != (
            routing.job_root_ids,
            routing.plan_day_ids,
            routing.directive_ids,
        ):
            raise SerializationError(
                "completed routing disagrees with its historical reducer proof"
            )
        publications = self._load_publications_for_jobs(
            connection, proof_scope.job_execution_roots
        )
        publication_keys = tuple(
            sorted(
                (item.job_id, item.source_revision, item.handoff_id)
                for item in publications
            )
        )
        if routing.publication_keys != publication_keys:
            raise SerializationError(
                "completed routing omitted or changed a publication key"
            )
        receipt_ids = tuple(
            item.event.event_id for item in proof_scope.processed_events.receipts
        )
        if routing.receipt_event_ids != tuple(sorted(receipt_ids)):
            raise SerializationError(
                "completed routing omitted or changed a receipt identity"
            )
        durable_receipts = self._load_receipts(connection, receipt_ids)
        if durable_receipts != proof_scope.processed_events.receipts:
            raise SerializationError(
                "historical reducer proof disagrees with durable receipts"
            )
        try:
            return M2ReductionScope(
                publications=publications,
                worker_registry=proof_scope.worker_registry,
                job_execution_roots=proof_scope.job_execution_roots,
                plan_day_roots=proof_scope.plan_day_roots,
                directive_roots=proof_scope.directive_roots,
                processed_events=proof_scope.processed_events,
            )
        except ValueError as error:
            raise SerializationError(
                "historical reducer proof has invalid publication provenance"
            ) from error

    def _validate_completed_preconditions_from_proof(
        self,
        connection: sqlite3.Connection,
        explicit_input: ExplicitInput,
        scope: M2ReductionScope,
        scope_proof: _ReductionInputProof,
        routing: RoutingVector,
        stored: PreconditionVector,
        *,
        validate_current_roots: bool,
    ) -> None:
        allowed = {
            (kind.value, root_id)
            for kind, root_id in self._allowed_mutation_roots(explicit_input, scope)
        }
        expected_roots: list[RootPrecondition] = []
        root_values = (
            (
                "WORKER_REGISTRY",
                "GLOBAL",
                scope.worker_registry.registry_revision,
                serialize_worker_registry(scope.worker_registry),
            ),
            *(
                (
                    RootKind.JOB_EXECUTION.value,
                    item.job_id,
                    item.job_execution_revision,
                    serialize_job_execution_root(item),
                )
                for item in scope.job_execution_roots
            ),
            *(
                (
                    RootKind.PLAN_DAY.value,
                    item.plan_day_id,
                    item.plan_day_revision,
                    serialize_plan_day_root(item),
                )
                for item in scope.plan_day_roots
            ),
            *(
                (
                    RootKind.DIRECTIVE.value,
                    item.directive_id,
                    item.directive_revision,
                    serialize_directive_root(item),
                )
                for item in scope.directive_roots
            ),
        )
        for kind, root_id, revision, raw in root_values:
            expected_roots.append(
                RootPrecondition(
                    kind,
                    root_id,
                    RootAccess.MUTATE
                    if (kind, root_id) in allowed
                    else RootAccess.READ_AUTHORITY,
                    revision,
                    sha256_text(raw),
                )
            )
        if stored.roots != PreconditionVector(tuple(expected_roots)).roots:
            raise SerializationError(
                "completed root preconditions disagree with historical reducer input"
            )

        # Every immutable authority record, including evidence identity/usages that
        # existed before reduction, is reconstructed exactly from the proof.
        if validate_current_roots:
            base = self._build_preconditions(
                connection,
                scope.job_execution_roots,
                scope.plan_day_roots,
                scope.directive_roots,
                scope.publications,
                scope.processed_events.receipts,
                set(),
            ).immutable_records
        else:
            base = tuple(
                self._build_immutable_preconditions(
                    connection,
                    scope.job_execution_roots,
                    scope.publications,
                    scope.processed_events.receipts,
                    set(),
                )
            )
        evidence_preconditions = tuple(
            ImmutablePrecondition(
                "EVIDENCE", item.evidence_id, item.content_sha256
            )
            for item in scope_proof.evidence_identities
        ) + tuple(
            ImmutablePrecondition(
                "EVIDENCE_USAGE",
                canonical_json(
                    [item.input_namespace, item.input_id, item.evidence_id]
                ),
                item.scope_sha256,
            )
            for item in scope_proof.evidence_usages
        )
        expected_immutable = PreconditionVector(
            immutable_records=(*base, *evidence_preconditions)
        ).immutable_records
        if stored.immutable_records != expected_immutable:
            raise SerializationError(
                "completed immutable preconditions are incomplete or extraneous"
            )
        for proof in scope_proof.evidence_identities:
            if proof.evidence_id not in routing.evidence_ids:
                raise SerializationError(
                    "completed evidence precondition is outside routing"
                )
            row = connection.execute(
                "SELECT * FROM m2_evidence_identities WHERE evidence_id=?",
                (proof.evidence_id,),
            ).fetchone()
            if row is None or self._evidence_identity_proof_from_row(row) != proof:
                raise SerializationError(
                    "completed evidence input authority is missing or changed"
                )
        for proof in scope_proof.evidence_usages:
            if proof.evidence_id not in routing.evidence_ids:
                raise SerializationError(
                    "completed evidence usage precondition is outside routing"
                )
            row = connection.execute(
                """
                SELECT * FROM m2_evidence_usages
                WHERE input_namespace=? AND input_id=? AND evidence_id=?
                """,
                (proof.input_namespace, proof.input_id, proof.evidence_id),
            ).fetchone()
            if row is None or self._evidence_usage_proof_from_row(row) != proof:
                raise SerializationError(
                    "completed evidence usage input authority is missing or changed"
                )
        self._recheck_immutable_preconditions(
            connection, stored.immutable_records
        )

    def _validate_completed_evidence_result(
        self,
        connection: sqlite3.Connection,
        explicit_input: ExplicitInput,
        historical_scope: M2ReductionScope,
        scope_proof: _ReductionInputProof,
    ) -> None:
        if not isinstance(explicit_input, FieldEventInput):
            return
        event = explicit_input.event
        prior_by_id = {
            item.evidence_id: item for item in scope_proof.evidence_identities
        }
        canonical_scope = self._evidence_projection_scope_is_canonical(
            historical_scope, event
        )
        expected_usages: dict[str, _EvidenceUsageProof] = {}
        for evidence in event.attachments:
            prior = prior_by_id.get(evidence.evidence_id)
            content_matches = prior is None or (
                prior.evidence_kind == evidence.kind.value
                and prior.content_reference == evidence.content_reference
            )
            owner_matches = prior is None or (
                prior.owner_job_id == event.job_id
                and prior.owner_task_id == event.task_id
                and (
                    prior.owner_assignment_id is None
                    or event.assignment_id is None
                    or prior.owner_assignment_id == event.assignment_id
                )
                and (
                    prior.owner_stage_id is None
                    or event.stage_id is None
                    or prior.owner_stage_id == event.stage_id
                )
            )
            projected = canonical_scope and content_matches and owner_matches
            if projected:
                if prior is None:
                    scope_identity = (
                        event.job_id,
                        event.task_id,
                        event.assignment_id,
                        event.stage_id,
                    )
                    raw = _evidence_identity_document(evidence, scope_identity)
                    expected_identity = _EvidenceIdentityProof(
                        evidence.evidence_id,
                        evidence.kind.value,
                        evidence.content_reference,
                        *scope_identity,
                        raw,
                        sha256_text(raw),
                    )
                    row = connection.execute(
                        "SELECT * FROM m2_evidence_identities WHERE evidence_id=?",
                        (evidence.evidence_id,),
                    ).fetchone()
                    if (
                        row is None
                        or self._evidence_identity_proof_from_row(row)
                        != expected_identity
                    ):
                        raise SerializationError(
                            "result-created evidence identity is missing or changed"
                        )
                scope_raw = canonical_json(_evidence_scope_payload(event))
                expected_usages[evidence.evidence_id] = _EvidenceUsageProof(
                    FIELD_EVENT,
                    event.event_id,
                    evidence.evidence_id,
                    event.job_id,
                    event.task_id,
                    event.assignment_id,
                    event.stage_id,
                    scope_raw,
                    sha256_text(scope_raw),
                )

        rows = connection.execute(
            """
            SELECT * FROM m2_evidence_usages
            WHERE input_namespace='FIELD_EVENT' AND input_id=?
            ORDER BY evidence_id
            """,
            (event.event_id,),
        ).fetchall()
        actual_usages = {
            row["evidence_id"]: self._evidence_usage_proof_from_row(row)
            for row in rows
        }
        if actual_usages != expected_usages:
            raise SerializationError(
                "completed evidence usage projection disagrees with its result"
            )

    def _validate_completed_authority(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        routing: RoutingVector,
        preconditions: PreconditionVector,
        resulting: ResultingRevisionVector,
        outcome: ReductionOutcome,
        reasons: tuple[str, ...],
        missing: tuple[str, ...],
        receipt: ProcessedEventReceipt | None,
        emitted_effects: tuple[SystemEffect, ...],
        response_effects: tuple[SystemEffect, ...],
        proof_scope: _ReductionInputProof,
        *,
        validate_current_roots: bool = True,
    ) -> None:
        """Prove that a COMPLETED result still has its durable authority.

        Root rows are latest-state snapshots, so a later legal revision is valid.
        At the exact recorded revision, however, the canonical content hash must
        equal the result produced by this input.  An APPLIED result with structural
        mutation capability must record at least one resulting revision.
        """

        is_field = row["input_namespace"] == FIELD_EVENT
        claim_collision = row["server_claim_owner_input_id"] is not None
        required_mutations: frozenset[tuple[RootKind, str]] = frozenset()
        if is_field and not claim_collision:
            if receipt is None or row["claimed_server_event_id"] != row[
                "presented_server_event_id"
            ]:
                raise SerializationError(
                    "completed field input lacks its exact durable receipt/claim"
                )
        elif is_field:
            if (
                receipt is not None
                or row["claimed_server_event_id"] is not None
                or outcome is not ReductionOutcome.REJECTED
                or reasons != ("SERVER_EVENT_ID_CONFLICT",)
                or resulting.roots
                or emitted_effects
            ):
                raise SerializationError(
                    "server-event collision result has invalid durable authority"
                )
        elif receipt is not None:
            raise SerializationError("system signal cannot append a field-event receipt")

        explicit_input = self._explicit_input_from_row(row)
        historical_scope = self._completed_scope_from_proof(
            connection, routing, proof_scope
        )
        self._validate_reduction_proof_version(
            explicit_input, historical_scope, proof_scope
        )
        expected_evidence_ids = (
            tuple(sorted(item.evidence_id for item in explicit_input.event.attachments))
            if isinstance(explicit_input, FieldEventInput)
            else ()
        )
        if routing.evidence_ids != expected_evidence_ids:
            raise SerializationError(
                "completed evidence routing disagrees with its original input"
            )
        self._validate_completed_preconditions_from_proof(
            connection,
            explicit_input,
            historical_scope,
            proof_scope,
            routing,
            preconditions,
            validate_current_roots=validate_current_roots,
        )

        if claim_collision:
            expected_reduction = Reduction(
                state=historical_scope,
                outcome=ReductionOutcome.REJECTED,
                reason_codes=("SERVER_EVENT_ID_CONFLICT",),
            )
        else:
            context = deserialize_policy_time_context(row["policy_context_json"])
            expected_reduction = reduce(
                historical_scope,
                explicit_input,
                context,
            )
            expected_reduction = self._normalize_reduction_for_durability(
                historical_scope,
                explicit_input,
                expected_reduction,
            )
            if expected_reduction.replayed:
                raise SerializationError(
                    "completed input proof already contained its own event receipt"
                )
            if outcome is ReductionOutcome.APPLIED:
                required_mutations = self._required_applied_mutation_roots(
                    explicit_input, historical_scope
                )

        expected_resulting = self._resulting_vector_for_reduction(
            expected_reduction
        )
        if (
            expected_reduction.outcome is not outcome
            or expected_reduction.reason_codes != reasons
            or expected_reduction.missing_requirements != missing
            or expected_reduction.response_effects != response_effects
            or expected_reduction.emitted_effects != emitted_effects
            or expected_reduction.appended_receipt != receipt
            or expected_resulting != resulting
        ):
            raise SerializationError(
                "completed durable artifacts are not the original reducer result"
            )
        self._validate_completed_evidence_result(
            connection,
            explicit_input,
            historical_scope,
            proof_scope,
        )

        routed_roots = {
            *((RootKind.JOB_EXECUTION.value, item) for item in routing.job_root_ids),
            *((RootKind.PLAN_DAY.value, item) for item in routing.plan_day_ids),
            *((RootKind.DIRECTIVE.value, item) for item in routing.directive_ids),
        }
        root_preconditions = {
            (item.root_kind, item.root_id): item for item in preconditions.roots
        }
        non_registry_preconditions = {
            identity
            for identity in root_preconditions
            if identity != ("WORKER_REGISTRY", "GLOBAL")
        }
        if (
            non_registry_preconditions != routed_roots
            or ("WORKER_REGISTRY", "GLOBAL") not in root_preconditions
        ):
            raise SerializationError(
                "completed routing disagrees with its root preconditions"
            )

        root_specs = {
            "WORKER_REGISTRY": (
                "m2_worker_registry",
                "registry_key",
                "registry_revision",
            ),
            RootKind.JOB_EXECUTION.value: (
                "m2_job_execution_roots",
                "job_id",
                "job_execution_revision",
            ),
            RootKind.PLAN_DAY.value: (
                "m2_plan_day_roots",
                "plan_day_id",
                "plan_day_revision",
            ),
            RootKind.DIRECTIVE.value: (
                "m2_directive_roots",
                "directive_id",
                "directive_revision",
            ),
        }
        current: dict[tuple[str, str], tuple[int, str]] = {}
        if not validate_current_roots:
            if not {
                (item.root_kind, item.root_id) for item in resulting.roots
            }.issuperset(
                (root_kind.value, root_id)
                for root_kind, root_id in required_mutations
            ):
                raise SerializationError(
                    "APPLIED result omitted its canonical root mutation proof"
                )
            return
        for identity, precondition in root_preconditions.items():
            spec = root_specs.get(precondition.root_kind)
            if spec is None:
                raise SerializationError("completed result names an unknown root kind")
            table, identity_column, revision_column = spec
            root_row = connection.execute(
                f"SELECT * FROM {table} WHERE {identity_column}=?",
                (precondition.root_id,),
            ).fetchone()
            if root_row is None:
                raise SerializationError("completed result authority root is missing")
            if precondition.root_kind == "WORKER_REGISTRY":
                self._worker_registry_from_connection(connection)
            elif precondition.root_kind == RootKind.JOB_EXECUTION.value:
                self._job_root_from_connection(connection, root_row)
            elif precondition.root_kind == RootKind.PLAN_DAY.value:
                self._plan_day_from_row(root_row)
            else:
                self._directive_from_row(root_row)
            revision = root_row[revision_column]
            digest = root_row["content_sha256"]
            if revision < precondition.expected_revision or (
                revision == precondition.expected_revision
                and digest != precondition.expected_content_sha256
            ):
                raise SerializationError(
                    "completed result authority predates/disagrees with its precondition"
                )
            current[identity] = (revision, digest)

        resulting_by_identity = {
            (item.root_kind, item.root_id): item for item in resulting.roots
        }
        for identity, result in resulting_by_identity.items():
            precondition = root_preconditions.get(identity)
            if (
                precondition is None
                or precondition.access is not RootAccess.MUTATE
                or result.resulting_revision
                != precondition.expected_revision + 1
            ):
                raise SerializationError(
                    "resulting revision lacks an exact MUTATE precondition"
                )
            revision, digest = current[identity]
            if revision < result.resulting_revision:
                raise SerializationError(
                    "canonical root does not prove its completed resulting revision"
                )
            if revision == result.resulting_revision:
                if digest != result.resulting_content_sha256:
                    raise SerializationError(
                        "canonical root does not prove its completed resulting revision"
                    )
            else:
                self._validate_completed_root_history(
                    connection,
                    row,
                    precondition,
                    result,
                    revision,
                    digest,
                )

        if not {
            (item.root_kind, item.root_id) for item in resulting.roots
        }.issuperset(
            (root_kind.value, root_id)
            for root_kind, root_id in required_mutations
        ):
            raise SerializationError(
                "APPLIED result omitted its canonical root mutation proof"
            )

    @staticmethod
    def _validate_reduction_proof_version(
        explicit_input: ExplicitInput,
        scope: M2ReductionScope,
        proof: _ReductionInputProof,
    ) -> None:
        if proof.schema_version == "m2-reduction-input-proof-v1":
            if proof.complete_plan_day_assignment_scope_ids:
                raise SerializationError("v1 reduction proof certifies v2 scope")
            return
        if proof.schema_version != "m2-reduction-input-proof-v2":
            raise SerializationError("unknown reduction proof version")
        if (
            not isinstance(explicit_input, FieldEventInput)
            or explicit_input.event.event_type
            is not FieldEventType.UNAVAILABLE_TODAY_REPORTED
            or explicit_input.event.plan_day_id is None
            or proof.complete_plan_day_assignment_scope_ids
            != (explicit_input.event.plan_day_id,)
        ):
            raise SerializationError(
                "v2 reduction proof lacks its exact unavailable plan-day certification"
            )
        plan = scope.plan_day(explicit_input.event.plan_day_id)
        if plan is None:
            raise SerializationError("v2 reduction proof lacks its plan-day preimage")
        linked_job_ids: set[str] = set()
        for root in scope.job_execution_roots:
            linked = tuple(
                assignment
                for assignment in root.assignments
                if plan.plan_day_id in assignment.plan_day_ids
            )
            if not linked:
                raise SerializationError(
                    "v2 reduction proof contains an unlinked job root"
                )
            linked_job_ids.add(root.job_id)
            for assignment in linked:
                if plan.worker_id not in assignment.member_worker_ids:
                    raise SerializationError(
                        "v2 linked assignment omits the plan-day worker"
                    )
        if linked_job_ids != {
            root.job_id for root in scope.job_execution_roots
        }:
            raise SerializationError("v2 reduction proof job scope is inconsistent")

    @staticmethod
    def _validate_completed_root_history(
        connection: sqlite3.Connection,
        completed_row: sqlite3.Row,
        completed_precondition: RootPrecondition,
        completed_result: ResultingRevision,
        current_revision: int,
        current_digest: str,
    ) -> None:
        """Prove an older result through the immutable contiguous inbox chain."""

        rows = connection.execute(
            """
            SELECT input_namespace, input_id, precondition_vector_json,
                   precondition_vector_sha256, resulting_revision_vector_json,
                   resulting_revision_vector_sha256
            FROM m2_input_inbox
            WHERE processing_status='COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM json_each(
                      json_extract(
                          resulting_revision_vector_json,
                          '$.payload.roots'
                      )
                  ) AS result_root
                  WHERE json_extract(result_root.value, '$.root_kind') = ?
                    AND json_extract(result_root.value, '$.root_id') = ?
              )
            ORDER BY input_namespace, input_id
            """,
            (completed_result.root_kind, completed_result.root_id),
        ).fetchall()
        transitions: dict[
            int, list[tuple[str, str, int, str, str]]
        ] = {}
        for row in rows:
            verify_canonical_document(
                row["precondition_vector_json"],
                row["precondition_vector_sha256"],
            )
            verify_canonical_document(
                row["resulting_revision_vector_json"],
                row["resulting_revision_vector_sha256"],
            )
            preconditions = deserialize_precondition_vector(
                row["precondition_vector_json"]
            )
            results = deserialize_resulting_revision_vector(
                row["resulting_revision_vector_json"]
            )
            identity = (
                completed_result.root_kind,
                completed_result.root_id,
            )
            result = next(
                (
                    item
                    for item in results.roots
                    if (item.root_kind, item.root_id) == identity
                ),
                None,
            )
            if result is None:
                continue
            precondition = next(
                (
                    item
                    for item in preconditions.roots
                    if (item.root_kind, item.root_id) == identity
                ),
                None,
            )
            if (
                precondition is None
                or precondition.access is not RootAccess.MUTATE
                or result.resulting_revision != precondition.expected_revision + 1
            ):
                raise SerializationError(
                    "completed root history contains an invalid transition"
                )
            transitions.setdefault(result.resulting_revision, []).append(
                (
                    row["input_namespace"],
                    row["input_id"],
                    precondition.expected_revision,
                    precondition.expected_content_sha256,
                    result.resulting_content_sha256,
                )
            )

        target = transitions.get(completed_result.resulting_revision, [])
        target_identity = (
            completed_row["input_namespace"],
            completed_row["input_id"],
            completed_precondition.expected_revision,
            completed_precondition.expected_content_sha256,
            completed_result.resulting_content_sha256,
        )
        if target != [target_identity]:
            raise SerializationError(
                "completed root revision does not have one authoritative transition"
            )

        previous_digest = completed_result.resulting_content_sha256
        for revision in range(
            completed_result.resulting_revision + 1,
            current_revision + 1,
        ):
            candidates = [
                item
                for item in transitions.get(revision, [])
                if item[2] == revision - 1 and item[3] == previous_digest
            ]
            if len(candidates) != 1:
                raise SerializationError(
                    "canonical root revision history is incomplete or ambiguous"
                )
            previous_digest = candidates[0][4]
        if previous_digest != current_digest:
            raise SerializationError(
                "canonical root history does not reach its current content"
            )

    def _outbox_rows_for_completed_result(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> tuple[tuple[int, str, str, str], ...]:
        values = connection.execute(
            """
            SELECT effect_ordinal, effect_type, canonical_effect_json, effect_sha256
            FROM m2_effect_outbox
            WHERE input_namespace=? AND input_id=?
            ORDER BY effect_ordinal
            """,
            (row["input_namespace"], row["input_id"]),
        ).fetchall()
        return tuple((item[0], item[1], item[2], item[3]) for item in values)

    @staticmethod
    def _input_document(explicit_input: ExplicitInput):
        if isinstance(explicit_input, FieldEventInput):
            return (
                FIELD_EVENT,
                explicit_input.event.event_id,
                serialize_field_event_envelope(explicit_input.event),
                "m2-field-event-envelope-v1",
                explicit_input.server_event_id,
            )
        return (
            SYSTEM_SIGNAL,
            explicit_input.signal_id,
            serialize_system_signal(explicit_input),
            "m2-system-signal-v1",
            None,
        )

    @staticmethod
    def _raw_references(explicit_input: ExplicitInput) -> Mapping[str, str | None]:
        if isinstance(explicit_input, FieldEventInput):
            value = explicit_input.event
            return {
                "job_id": value.job_id,
                "task_id": value.task_id,
                "assignment_id": value.assignment_id,
                "plan_day_id": value.plan_day_id,
                "directive_id": value.directive_id,
                "against_event_id": value.against_event_id,
            }
        return {
            "job_id": None,
            "task_id": explicit_input.task_id,
            "assignment_id": None,
            "plan_day_id": explicit_input.plan_day_id,
            "directive_id": explicit_input.directive_id,
            "against_event_id": None,
        }


__all__ = [
    "DurableOutboxEffect",
    "DurableReductionResult",
    "FIELD_EVENT",
    "HistoricalPlanDayTransition",
    "IngressAcceptance",
    "M2ConflictError",
    "M2DurableRepository",
    "M2InputPendingError",
    "M2InsufficientHistoricalEvidenceError",
    "M2NotFoundError",
    "M2PersistenceError",
    "M2StaleRevisionError",
    "M2StorageIntegrityError",
    "SYSTEM_SIGNAL",
    "UnavailableHistoricalReconstruction",
    "VerifiedActivationLineage",
]
