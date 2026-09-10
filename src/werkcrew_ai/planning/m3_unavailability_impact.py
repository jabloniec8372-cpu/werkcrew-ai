"""Pure M3-A impact evaluation against an independently trusted M3 association.

The host must obtain ``authority`` from its trusted M3 snapshot producer, never
from the same untrusted submission as ``scope``. These DTOs and their hashes do
not authenticate a producer. Production snapshot persistence is outside M3-A.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime
from enum import StrEnum

from werkcrew_ai.field.models import (
    AssignmentKind,
    PlanDayStatus,
    TaskStatus,
    UnavailableReason,
)
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.planning.m2_bridge import (
    REQUEST_SCHEMA_VERSION,
    ActivationLineageContext,
    ActivationTransitionContext,
    AssignmentImpactContext,
    M3FeasibilityEvaluationRequest,
    OperationalContextStatus,
    OperationalImpactSnapshot,
    PlanDayImpactContext,
    UnavailableEffectSource,
)


SCOPE_SCHEMA_VERSION = "m3-current-planning-scope-v1"
AUTHORITY_SCHEMA_VERSION = "m3-planning-scope-authority-v1"
RESULT_SCHEMA_VERSION = "m3-unavailability-impact-result-v1"
RULE_VERSION = "m3-unavailability-impact-v1"


class M3ImpactBoundaryError(ValueError):
    """Deterministic boundary failure; never an impact business outcome."""

    def __init__(self, code: str, field_name: str) -> None:
        self.code = code
        self.field_name = field_name
        super().__init__(f"{code}: {field_name}")


class M3ImpactValidationError(M3ImpactBoundaryError):
    """Malformed DTO, identity, schema, or source semantics."""


class M3ImpactFingerprintError(M3ImpactBoundaryError):
    """Content does not match its fingerprint or the trusted snapshot pin."""


class M3ImpactAssociationError(M3ImpactBoundaryError):
    """Missing or mismatched association with the trusted M3 planning scope."""


class M3ImpactOutcome(StrEnum):
    NO_LINKED_COMMITMENTS = "NO_LINKED_COMMITMENTS"
    NO_REPLAN_REQUIRED = "NO_REPLAN_REQUIRED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


def _require(condition: bool, field_name: str) -> None:
    if not condition:
        raise M3ImpactValidationError("INVALID_VALUE", field_name)


def _canonical_text(value: str, field_name: str) -> None:
    # Reject non-scalar Unicode before canonical JSON reaches UTF-8 hashing.
    _require(
        type(value) is str
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in value),
        field_name,
    )


def _literal(value: str, expected: str, field_name: str) -> None:
    _require(type(value) is str and value == expected, field_name)


def _dto(value: object, expected_type: type, field_name: str) -> None:
    _require(type(value) is expected_type, field_name)
    # Unsafe deserialization/reflection can also leave frozen slots unset.
    _require(all(hasattr(value, item.name) for item in fields(expected_type)), field_name)


def _identity(value: str, field_name: str) -> None:
    _canonical_text(value, field_name)
    _require(
        bool(value) and value == value.strip()
        and not any(ord(character) < 32 or ord(character) == 127 for character in value),
        field_name,
    )


def _revision(value: int, field_name: str) -> None:
    _require(type(value) is int and value >= 0, field_name)


def _digest(value: str, field_name: str) -> None:
    _require(
        type(value) is str and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        field_name,
    )


def _identities(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    _require(type(values) in (tuple, list), field_name)
    for value in values:
        _identity(value, field_name)
    _require(len(values) == len(set(values)), field_name)
    return tuple(sorted(values))


def _binding_fields(value: object) -> None:
    for name in ("company_plan_id", "plan_day_id", "unavailable_worker_id"):
        _identity(getattr(value, name), name)
    _revision(getattr(value, "company_plan_revision"), "company_plan_revision")
    _require(type(getattr(value, "business_date")) is date, "business_date")
    _digest(getattr(value, "source_request_fingerprint"), "source_request_fingerprint")


@dataclass(frozen=True, slots=True, kw_only=True)
class M3CurrentCommitment:
    """One active commitment in the producer-certified worker-day scope.

    Membership/liveness in this scope is an M3 fact, not inferred from M2 task
    status. A replacement may retain lineage while assigning different workers.
    """

    commitment_id: str
    job_id: str
    task_id: str
    assigned_worker_ids: tuple[str, ...]
    source_m2_assignment_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("commitment_id", "job_id", "task_id"):
            _identity(getattr(self, name), name)
        for name in ("assigned_worker_ids", "source_m2_assignment_ids"):
            object.__setattr__(self, name, _identities(getattr(self, name), name))


def _validate_commitment(value, expected_type) -> None:
    _dto(value, expected_type, "commitment")
    names = ("assigned_worker_ids", "source_m2_assignment_ids")
    if expected_type is M3AffectedCommitment:
        names += ("matched_historical_assignment_ids",)
    for name in names:
        _require(type(getattr(value, name)) is tuple, name)
    # Constructor checks cover every identity, duplicate, canonical order and
    # matched-lineage subset. Never normalize an already constructed child DTO.
    _require(replace(value) == value, "commitment.canonical_order")


def _ordered_commitments(values, expected_type):
    _require(type(values) in (tuple, list), "commitments")
    for item in values:
        _validate_commitment(item, expected_type)
    _require(len({item.commitment_id for item in values}) == len(values), "commitment_id")
    return tuple(sorted(values, key=lambda item: (item.job_id, item.task_id, item.commitment_id)))


@dataclass(frozen=True, slots=True, kw_only=True)
class M3CurrentPlanningScope:
    schema_version: str
    company_plan_id: str
    company_plan_revision: int
    plan_day_id: str
    unavailable_worker_id: str
    business_date: date
    source_request_fingerprint: str
    coverage_complete: bool
    commitments: tuple[M3CurrentCommitment, ...]
    snapshot_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _literal(self.schema_version, SCOPE_SCHEMA_VERSION, "scope.schema_version")
        _binding_fields(self)
        _require(type(self.coverage_complete) is bool, "coverage_complete")
        object.__setattr__(
            self, "commitments", _ordered_commitments(self.commitments, M3CurrentCommitment)
        )
        object.__setattr__(self, "snapshot_fingerprint", sha256_text(_scope_content(self)))


@dataclass(frozen=True, slots=True, kw_only=True)
class M3PlanningScopeAuthority:
    """Trusted host input pinning one authorized M3 association and snapshot.

    Construction is NOT authentication. Only the host's trusted M3 producer may
    select this value. The evaluator deliberately has no auto-trust factory.
    """

    schema_version: str
    company_plan_id: str
    company_plan_revision: int
    plan_day_id: str
    unavailable_worker_id: str
    business_date: date
    source_request_fingerprint: str
    expected_snapshot_fingerprint: str

    def __post_init__(self) -> None:
        _literal(self.schema_version, AUTHORITY_SCHEMA_VERSION, "authority.schema_version")
        _binding_fields(self)
        _digest(self.expected_snapshot_fingerprint, "expected_snapshot_fingerprint")


@dataclass(frozen=True, slots=True, kw_only=True)
class M3AffectedCommitment(M3CurrentCommitment):
    matched_historical_assignment_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        M3CurrentCommitment.__post_init__(self)
        matched = _identities(self.matched_historical_assignment_ids, "matched_historical_assignment_ids")
        _require(set(matched).issubset(self.source_m2_assignment_ids), "matched_historical_assignment_ids")
        object.__setattr__(self, "matched_historical_assignment_ids", matched)


@dataclass(frozen=True, slots=True, kw_only=True)
class M3UnavailabilityImpactResult:
    source_request_fingerprint: str
    current_planning_snapshot_fingerprint: str
    company_plan_id: str
    company_plan_revision: int
    plan_day_id: str
    unavailable_worker_id: str
    business_date: date
    outcome: M3ImpactOutcome
    affected_commitments: tuple[M3AffectedCommitment, ...]
    reason_codes: tuple[str, ...]
    schema_version: str = field(default=RESULT_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    evaluation_fingerprint: str = field(init=False)
    evaluation_id: str = field(init=False)

    def __post_init__(self) -> None:
        _binding_fields(self)
        _digest(self.current_planning_snapshot_fingerprint, "current_planning_snapshot_fingerprint")
        _require(type(self.outcome) is M3ImpactOutcome, "outcome")
        affected = _ordered_commitments(self.affected_commitments, M3AffectedCommitment)
        _require(
            all(self.unavailable_worker_id in item.assigned_worker_ids for item in affected),
            "affected_commitments",
        )
        if self.outcome is M3ImpactOutcome.REPLAN_REQUIRED:
            _require(bool(affected), "affected_commitments")
        if self.outcome in (M3ImpactOutcome.NO_LINKED_COMMITMENTS, M3ImpactOutcome.NO_REPLAN_REQUIRED):
            _require(not affected, "affected_commitments")
        object.__setattr__(self, "affected_commitments", affected)
        object.__setattr__(self, "reason_codes", _identities(self.reason_codes, "reason_codes"))
        digest = sha256_text(canonical_json(_document(self, {"evaluation_fingerprint", "evaluation_id"})))
        object.__setattr__(self, "evaluation_fingerprint", digest)
        object.__setattr__(self, "evaluation_id", f"m3-impact-{digest}")


def _value(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is date:
        return value.isoformat()
    if type(value) is tuple:
        return [_value(item) for item in value]
    if type(value) in (M3CurrentCommitment, M3AffectedCommitment):
        return {item.name: _value(getattr(value, item.name)) for item in fields(value)}
    return value


def _document(value, excluded: set[str]) -> dict[str, object]:
    return {item.name: _value(getattr(value, item.name)) for item in fields(value) if item.name not in excluded}


def _scope_content(scope: M3CurrentPlanningScope) -> str:
    return canonical_json(_document(scope, {"snapshot_fingerprint"}))


def serialize_current_planning_scope(scope: M3CurrentPlanningScope) -> str:
    """Canonical full snapshot document, including its content fingerprint."""
    _validate_scope(scope)
    return canonical_json(_document(scope, set()))


def serialize_unavailability_impact_result(result: M3UnavailabilityImpactResult) -> str:
    _validate_result(result)
    return canonical_json(_document(result, set()))


def _validate_result(result: M3UnavailabilityImpactResult) -> None:
    _dto(result, M3UnavailabilityImpactResult, "result")
    _literal(result.schema_version, RESULT_SCHEMA_VERSION, "result.schema_version")
    _literal(result.rule_version, RULE_VERSION, "result.rule_version")
    _require(type(result.affected_commitments) is tuple, "result.affected_commitments")
    _require(type(result.reason_codes) is tuple, "result.reason_codes")
    _digest(result.evaluation_fingerprint, "result.evaluation_fingerprint")
    _identity(result.evaluation_id, "result.evaluation_id")
    # Reconstruction recursively validates children before hashing. Equality
    # then checks ordering, all semantic fields and the original fingerprint/ID.
    _require(replace(result) == result, "result")


def _validate_scope(scope: M3CurrentPlanningScope) -> None:
    _dto(scope, M3CurrentPlanningScope, "scope")
    # Revalidate on consumption as well as construction; frozen DTOs are not a
    # security barrier against Python reflection or an unsafe deserializer.
    _require(type(scope.commitments) is tuple, "scope.commitments")
    _digest(scope.snapshot_fingerprint, "scope.snapshot_fingerprint")
    reconstructed = replace(scope)
    if reconstructed.snapshot_fingerprint != scope.snapshot_fingerprint:
        raise M3ImpactFingerprintError("SCOPE_FINGERPRINT_MISMATCH", "scope")
    _require(reconstructed == scope, "scope.canonical_order")


def _validate_request(request: M3FeasibilityEvaluationRequest) -> None:
    _dto(request, M3FeasibilityEvaluationRequest, "request")
    _literal(request.schema_version, REQUEST_SCHEMA_VERSION, "request.schema_version")
    _literal(request.evaluation_kind, "FRESH_FEASIBILITY", "request.evaluation_kind")
    _require(request.requires_fresh_evaluation is True, "request.requires_fresh_evaluation")
    _require(request.authoritative_plan_mutation is False, "request.authoritative_plan_mutation")
    snapshot = request.snapshot
    _dto(snapshot, OperationalImpactSnapshot, "request.snapshot")
    _dto(snapshot.plan_day, PlanDayImpactContext, "request.plan_day")
    _dto(snapshot.source, UnavailableEffectSource, "request.source")
    _dto(snapshot.activation_lineage, ActivationLineageContext, "request.activation_lineage")
    _require(type(snapshot.reason) is UnavailableReason, "request.reason")
    plan, source = snapshot.plan_day, snapshot.source
    for name in ("plan_day_id", "worker_id"):
        _identity(getattr(plan, name), f"request.{name}")
    _require(type(plan.business_date) is date, "request.business_date")
    _require(type(plan.start_at) is datetime and plan.start_at.utcoffset() is not None, "request.start_at")
    _require(type(plan.status) is PlanDayStatus, "request.status")
    _require(plan.worker_available is False, "request.worker_available")
    _require(
        type(plan.day_close_reported) is bool and type(plan.start_unknown_escalated) is bool,
        "request.plan_flags",
    )
    if plan.confirmed_plan_reference is not None:
        # This is an opaque M2 value, not an M3 identity. Preserve M2's nonblank
        # string semantics without imposing the M3 ID grammar on its contents.
        _canonical_text(plan.confirmed_plan_reference, "request.confirmed_plan_reference")
        _require(bool(plan.confirmed_plan_reference.strip()), "request.confirmed_plan_reference")
    _revision(plan.plan_day_revision, "request.plan_day_revision")
    for name in ("preimage_sha256", "result_sha256"):
        _digest(getattr(plan, name), f"request.{name}")
    _literal(source.input_namespace, "FIELD_EVENT", "request.source.input_namespace")
    _literal(source.effect_type, "UNAVAILABLE_TODAY_RECORDED", "request.source.effect_type")
    _literal(source.input_schema_version, "m2-field-event-envelope-v1", "request.source.input_schema_version")
    _literal(
        source.reduction_proof_schema_version, "m2-reduction-input-proof-v2",
        "request.source.proof_version",
    )
    _revision(source.effect_ordinal, "request.source.effect_ordinal")
    for name in ("event_id", "server_event_id"):
        _identity(getattr(source, name), f"request.source.{name}")
    for name in ("input_payload_sha256", "reduction_proof_sha256", "effect_sha256"):
        _digest(getattr(source, name), f"request.source.{name}")
    _require(
        type(snapshot.assignments) is tuple and type(snapshot.job_root_preimages) is tuple,
        "request.historical_scope",
    )
    roots = set()
    for root in snapshot.job_root_preimages:
        _require(type(root) is tuple and len(root) == 3, "request.job_root")
        _identity(root[0], "request.job_id")
        _revision(root[1], "request.job_revision")
        _digest(root[2], "request.job_hash")
        roots.add(root)
    _require(len({root[0] for root in roots}) == len(snapshot.job_root_preimages), "request.job_roots")
    linked_roots, assignment_ids = set(), set()
    for item in snapshot.assignments:
        _dto(item, AssignmentImpactContext, "request.assignment")
        for name in ("assignment_id", "job_id", "task_id", "task_definition_version", "task_source_handoff_id"):
            _identity(getattr(item, name), f"request.assignment.{name}")
        _require(item.assignment_id not in assignment_ids, "request.assignment_id")
        assignment_ids.add(item.assignment_id)
        for name in ("member_worker_ids", "plan_day_ids", "released_worker_ids"):
            values = getattr(item, name)
            _require(type(values) is tuple and _identities(values, name) == values, name)
        _require(
            plan.plan_day_id in item.plan_day_ids and plan.worker_id in item.member_worker_ids,
            "request.assignment_scope",
        )
        _require(
            type(item.assignment_kind) is AssignmentKind and type(item.task_status) is TaskStatus,
            "request.assignment_enums",
        )
        _revision(item.job_execution_revision, "request.assignment.job_revision")
        _revision(item.task_source_revision, "request.assignment.task_source_revision")
        _digest(item.job_root_sha256, "request.assignment.job_hash")
        for name in ("lead_worker_id", "supersedes_assignment_id", "supersedes_task_id"):
            if getattr(item, name) is not None:
                _identity(getattr(item, name), f"request.assignment.{name}")
        linked_roots.add((item.job_id, item.job_execution_revision, item.job_root_sha256))
    _require(linked_roots == roots, "request.assignment_roots")
    expected_status = (
        OperationalContextStatus.EVALUATION_REQUIRED
        if assignment_ids else OperationalContextStatus.NO_LINKED_ASSIGNMENTS
    )
    _require(snapshot.context_status is expected_status, "request.context_status")
    lineage = snapshot.activation_lineage
    _require(type(lineage.transitions) is tuple and bool(lineage.transitions), "request.activation_transitions")
    for item in lineage.transitions:
        _dto(item, ActivationTransitionContext, "request.activation_transitions")
    for item in (lineage, *lineage.transitions):
        for descriptor in fields(item):
            name, value = descriptor.name, getattr(item, descriptor.name)
            if name.endswith("sha256"):
                _digest(value, f"request.lineage.{name}")
            elif name.endswith(("revision", "ordinal")):
                _revision(value, f"request.lineage.{name}")
            elif name != "transitions":
                _identity(value, f"request.lineage.{name}")
    # The existing DTO owns the v2 fingerprint algorithm. Do not duplicate it
    # or rerun M2 proof/activation verification in the M3 domain evaluator.
    try:
        replace(request)
    except ValueError as error:
        raise M3ImpactFingerprintError("REQUEST_FINGERPRINT_MISMATCH", "request") from error
    _require(replace(snapshot) == snapshot, "request.canonical_order")


def evaluate_unavailability_impact(
    request: M3FeasibilityEvaluationRequest,
    scope: M3CurrentPlanningScope,
    *,
    authority: M3PlanningScopeAuthority | None = None,
) -> M3UnavailabilityImpactResult:
    """Evaluate current worker dependence; never claim replacement feasibility.

    ``authority`` is independently trusted host input, not a client credential.
    Incomplete coverage takes precedence over any partial impact findings.
    """
    if authority is None:
        raise M3ImpactAssociationError("M3_ASSOCIATION_REQUIRED", "authority")
    _dto(authority, M3PlanningScopeAuthority, "authority")
    _require(replace(authority) == authority, "authority")
    _validate_request(request)
    _validate_scope(scope)
    for name in (
        "company_plan_id", "company_plan_revision", "plan_day_id",
        "unavailable_worker_id", "business_date", "source_request_fingerprint",
    ):
        if getattr(scope, name) != getattr(authority, name):
            raise M3ImpactAssociationError("M3_ASSOCIATION_MISMATCH", name)
    for name, expected in (
        ("plan_day_id", request.snapshot.plan_day.plan_day_id),
        ("unavailable_worker_id", request.snapshot.plan_day.worker_id),
        ("business_date", request.snapshot.plan_day.business_date),
        ("source_request_fingerprint", request.request_fingerprint),
    ):
        if getattr(authority, name) != expected:
            raise M3ImpactAssociationError("M2_M3_ASSOCIATION_MISMATCH", name)
    if authority.expected_snapshot_fingerprint != scope.snapshot_fingerprint:
        raise M3ImpactFingerprintError("UNAUTHORIZED_SCOPE_FINGERPRINT", "scope")
    historical = {item.assignment_id: item for item in request.snapshot.assignments}
    affected, reasons = [], set()
    for commitment in scope.commitments:
        matched = tuple(sorted(set(commitment.source_m2_assignment_ids).intersection(historical)))
        if any(historical[identity].job_id != commitment.job_id for identity in matched):
            raise M3ImpactAssociationError("CROSS_JOB_ASSIGNMENT_LINEAGE", "commitment")
        worker_assigned = scope.unavailable_worker_id in commitment.assigned_worker_ids
        if not worker_assigned and not matched:
            raise M3ImpactAssociationError("COMMITMENT_OUTSIDE_SCOPE", "commitment")
        if worker_assigned:
            affected.append(M3AffectedCommitment(
                commitment_id=commitment.commitment_id, job_id=commitment.job_id,
                task_id=commitment.task_id, assigned_worker_ids=commitment.assigned_worker_ids,
                source_m2_assignment_ids=commitment.source_m2_assignment_ids,
                matched_historical_assignment_ids=matched,
            ))
            if not matched:
                reasons.add("M2_ASSIGNMENT_LINEAGE_MISSING")
    if not scope.coverage_complete:
        outcome = M3ImpactOutcome.INSUFFICIENT_INFORMATION
        reasons.add("CURRENT_SCOPE_INCOMPLETE")
    elif affected:
        outcome = M3ImpactOutcome.REPLAN_REQUIRED
        reasons.add("UNAVAILABLE_WORKER_STILL_ASSIGNED")
    elif not historical and not scope.commitments:
        outcome = M3ImpactOutcome.NO_LINKED_COMMITMENTS
        reasons.add("HISTORICAL_AND_CURRENT_SCOPE_EMPTY")
    else:
        outcome = M3ImpactOutcome.NO_REPLAN_REQUIRED
        reasons.add("NO_CURRENT_WORKER_DEPENDENCE")
    return M3UnavailabilityImpactResult(
        source_request_fingerprint=request.request_fingerprint,
        current_planning_snapshot_fingerprint=scope.snapshot_fingerprint,
        company_plan_id=scope.company_plan_id, company_plan_revision=scope.company_plan_revision,
        plan_day_id=scope.plan_day_id, unavailable_worker_id=scope.unavailable_worker_id,
        business_date=scope.business_date, outcome=outcome,
        affected_commitments=tuple(affected), reason_codes=tuple(sorted(reasons)),
    )
