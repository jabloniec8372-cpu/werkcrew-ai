"""M3-B0 canonical planning intent; no operational M2 state or scheduling engine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import date

from werkcrew_ai.field.serialization import canonical_json, sha256_text


REVISION_SCHEMA = "m3-plan-revision-v1"
DEPENDENCY_RELATION = "FINISH_BEFORE_START"


class M3BootstrapError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code, self.field_name = code, field_name
        super().__init__(f"{code}: {field_name}")


class M3BootstrapValidationError(M3BootstrapError):
    pass


class M3BootstrapConflictError(M3BootstrapError):
    pass


class M3BootstrapStorageError(M3BootstrapError):
    pass


def require(condition: bool, name: str) -> None:
    if not condition:
        raise M3BootstrapValidationError("INVALID_VALUE", name)


def identity(value: str, name: str) -> None:
    require(type(value) is str and bool(value) and value == value.strip(), name)
    require(not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value), name)


def revision_number(value: int) -> None:
    require(type(value) is int and 0 <= value <= 9223372036854775807, "revision")


def record(value, expected) -> None:
    require(type(value) is expected, "dto_type")
    require(all(hasattr(value, item.name) for item in fields(expected)), "dto_fields")


def identities(values, name: str) -> tuple[str, ...]:
    require(type(values) in (list, tuple), name)
    for value in values:
        identity(value, name)
    require(len(set(values)) == len(values), name)
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True, kw_only=True)
class CompanyPlan:
    company_plan_id: str
    provenance_reference: str

    def __post_init__(self) -> None:
        identity(self.company_plan_id, "company_plan_id")
        identity(self.provenance_reference, "provenance_reference")


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanDayAssociation:
    plan_day_id: str
    worker_id: str
    business_date: date

    def __post_init__(self) -> None:
        identity(self.plan_day_id, "plan_day_id")
        identity(self.worker_id, "worker_id")
        require(type(self.business_date) is date, "business_date")


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanningCommitment:
    """One dated intended placement of an existing canonical task, not execution."""

    commitment_id: str
    job_id: str
    task_id: str
    task_definition_version: str
    source_handoff_id: str
    source_revision: int
    business_date: date
    planned_worker_ids: tuple[str, ...]
    source_m2_assignment_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("commitment_id", "job_id", "task_id", "task_definition_version", "source_handoff_id"):
            identity(getattr(self, name), name)
        revision_number(self.source_revision)
        require(self.source_revision > 0, "source_revision")
        require(type(self.business_date) is date, "business_date")
        for name in ("planned_worker_ids", "source_m2_assignment_ids"):
            object.__setattr__(self, name, identities(getattr(self, name), name))


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskDependency:
    """Explicit finish-before-start constraint; no dependency is inferred."""

    predecessor_task_id: str
    successor_task_id: str
    provenance_reference: str
    relation: str = DEPENDENCY_RELATION

    def __post_init__(self) -> None:
        for name in ("predecessor_task_id", "successor_task_id", "provenance_reference"):
            identity(getattr(self, name), name)
        require(type(self.relation) is str and self.relation == DEPENDENCY_RELATION, "relation")
        require(self.predecessor_task_id != self.successor_task_id, "self_dependency")


def _records(values, expected, key):
    require(type(values) in (tuple, list), "collection")
    for value in values:
        record(value, expected)
        if expected is PlanningCommitment:
            require(type(value.planned_worker_ids) is tuple, "planned_worker_ids")
            require(type(value.source_m2_assignment_ids) is tuple, "source_m2_assignment_ids")
        require(replace(value) == value, "canonical_child")
    keys = [key(value) for value in values]
    require(len(set(keys)) == len(keys), "duplicate_identity")
    return tuple(sorted(values, key=key))


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanRevision:
    company_plan_id: str
    revision: int
    previous_revision_id: str | None
    provenance_reference: str
    plan_days: tuple[PlanDayAssociation, ...]
    commitments: tuple[PlanningCommitment, ...]
    dependencies: tuple[TaskDependency, ...] = ()
    schema_version: str = field(default=REVISION_SCHEMA, init=False)
    fingerprint: str = field(init=False)
    revision_id: str = field(init=False)

    def __post_init__(self) -> None:
        identity(self.company_plan_id, "company_plan_id")
        identity(self.provenance_reference, "provenance_reference")
        revision_number(self.revision)
        require((self.revision == 0) == (self.previous_revision_id is None), "previous_revision_id")
        if self.previous_revision_id is not None:
            identity(self.previous_revision_id, "previous_revision_id")
        days = _records(self.plan_days, PlanDayAssociation, lambda d: d.plan_day_id)
        require(len({(d.worker_id, d.business_date) for d in days}) == len(days), "worker_day")
        commitments = _records(self.commitments, PlanningCommitment, lambda c: (c.job_id, c.task_id, c.commitment_id))
        require(len({c.commitment_id for c in commitments}) == len(commitments), "commitment_id")
        # Bootstrap deliberately has one unambiguous intended placement per task.
        require(len({c.task_id for c in commitments}) == len(commitments), "task_placement")
        worker_days = {(d.worker_id, d.business_date) for d in days}
        for commitment in commitments:
            require(all((worker, commitment.business_date) in worker_days for worker in commitment.planned_worker_ids), "placement_worker_day")
        dependencies = _records(self.dependencies, TaskDependency, lambda e: (e.predecessor_task_id, e.successor_task_id))
        tasks = {c.task_id for c in commitments}
        require(all(e.predecessor_task_id in tasks and e.successor_task_id in tasks for e in dependencies), "dependency_endpoint")
        remaining = set(tasks)
        while remaining:
            blocked = {e.successor_task_id for e in dependencies if e.predecessor_task_id in remaining}
            ready = remaining - blocked
            require(bool(ready), "dependency_cycle")
            remaining -= ready
        object.__setattr__(self, "plan_days", days)
        object.__setattr__(self, "commitments", commitments)
        object.__setattr__(self, "dependencies", dependencies)
        digest = sha256_text(canonical_json(_content(self)))
        object.__setattr__(self, "fingerprint", digest)
        object.__setattr__(self, "revision_id", "m3-plan-revision-" + digest)


def _value(value):
    if type(value) is date:
        return value.isoformat()
    if type(value) is tuple:
        return [_value(item) for item in value]
    if type(value) in (PlanDayAssociation, PlanningCommitment, TaskDependency):
        return {f.name: _value(getattr(value, f.name)) for f in fields(value)}
    return value


def _content(revision: PlanRevision) -> dict:
    return {f.name: _value(getattr(revision, f.name)) for f in fields(revision) if f.name not in ("revision_id", "fingerprint")}


def serialize_plan_revision(revision: PlanRevision) -> str:
    record(revision, PlanRevision)
    for name in ("plan_days", "commitments", "dependencies"):
        require(type(getattr(revision, name)) is tuple, name)
    require(replace(revision) == revision, "revision_integrity")
    return canonical_json(_content(revision))


def deserialize_plan_revision(raw: str) -> PlanRevision:
    """Read the exact persisted schema; never repair or drop unknown fields."""
    try:
        value = json.loads(raw)
        require(type(value) is dict and canonical_json(value) == raw, "canonical_document")
        require(value.pop("schema_version") == REVISION_SCHEMA, "schema_version")
        require(all(type(value[name]) is list for name in ("plan_days", "commitments", "dependencies")), "collections")
        days, commitments, dependencies = [], [], []
        for item in value["plan_days"]:
            require(type(item) is dict, "plan_day")
            days.append(PlanDayAssociation(**{**item, "business_date": date.fromisoformat(item["business_date"])}))
        for item in value["commitments"]:
            require(type(item) is dict, "commitment")
            commitments.append(PlanningCommitment(**{**item, "business_date": date.fromisoformat(item["business_date"])}))
        for item in value["dependencies"]:
            require(type(item) is dict, "dependency")
            dependencies.append(TaskDependency(**item))
        value.update(plan_days=tuple(days), commitments=tuple(commitments), dependencies=tuple(dependencies))
        revision = PlanRevision(**value)
        require(serialize_plan_revision(revision) == raw, "canonical_order")
        return revision
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise M3BootstrapStorageError("INVALID_REVISION_DOCUMENT", "revision") from error
