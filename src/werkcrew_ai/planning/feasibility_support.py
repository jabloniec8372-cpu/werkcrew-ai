"""Immutable M3-C0 feasibility evidence; no candidates or verdicts.

Source records are written only through the privileged host repository.  The
support snapshot is a deterministic projection of those records plus the exact
M3-B EvaluationInput, B0 PlanRevision and frozen M8 configuration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from werkcrew_ai.catalog.m8_demo import M8_CONFIGURATION
from werkcrew_ai.catalog.models import M8BusinessConfiguration
from werkcrew_ai.catalog.validation import assert_valid_m8_configuration
from werkcrew_ai.field.serialization import (
    canonical_json,
    deserialize_worker_registry,
    sha256_text,
)


FEASIBILITY_SOURCE_SCHEMA_VERSION = "m3-feasibility-source-v1"
SOURCE_SELECTION_CUT_SCHEMA_VERSION = "m3-feasibility-source-cut-v1"
FEASIBILITY_SUPPORT_SCHEMA_VERSION = "m3-feasibility-support-v1"
FEASIBILITY_SUPPORT_RULE_VERSION = "m3-feasibility-evidence-selection-v1"
M8_FEASIBILITY_CONFIGURATION_SCHEMA_VERSION = "m8-feasibility-configuration-v1"
WORKER_REGISTRY_PROVENANCE_SCHEMA_VERSION = (
    "m3-feasibility-worker-registry-provenance-v1"
)
WORKER_REGISTRY_CAPTURE_SCHEMA_VERSION = (
    "m3-feasibility-worker-registry-capture-v1"
)
WORKER_REGISTRY_CAPTURE_KIND = "WORKER_REGISTRY_PROVENANCE"
M2_WORKER_REGISTRY_SCHEMA_VERSION = "m2-worker-registry-v1"


class M3FeasibilitySupportError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code, self.field_name = code, field_name
        super().__init__(f"{code}: {field_name}")


class M3FeasibilitySupportValidationError(M3FeasibilitySupportError):
    pass


class M3FeasibilitySupportConflictError(M3FeasibilitySupportError):
    pass


class M3FeasibilitySupportStorageError(M3FeasibilitySupportError):
    pass


def _require(condition: bool, name: str) -> None:
    if not condition:
        raise M3FeasibilitySupportValidationError("INVALID_VALUE", name)


def _identity(value: str, name: str) -> None:
    _require(type(value) is str and bool(value) and value == value.strip(), name)
    _require(
        not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ),
        name,
    )


def _optional_identity(value: str | None, name: str) -> None:
    _require(value is None or type(value) is str, name)
    if value is not None:
        _identity(value, name)


def _digest(value: str, name: str) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        name,
    )


def _revision(value: int, name: str) -> None:
    _require(
        type(value) is int and 0 <= value <= 9223372036854775807,
        name,
    )


def _positive_seconds(value: int, name: str) -> None:
    _require(type(value) is int and 0 < value <= 315576000, name)


def _instant(value: datetime, name: str) -> datetime:
    _require(
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        name,
    )
    return value.astimezone(timezone.utc)


def _record(value: object, expected: type, name: str) -> None:
    _require(type(value) is expected, name)
    _require(all(hasattr(value, item.name) for item in fields(expected)), name)
    if expected is TaskConstraintSource:
        value._validate_base_semantics()
        value._validate_m8_semantics()
        _require(
            value.subject_fingerprint
            == task_subject_fingerprint(
                value.job_id, value.task_id, value.task_definition_version
            ),
            name,
        )
        semantic = canonical_json(
            {
                item.name: _primitive(getattr(value, item.name))
                for item in fields(value)
                if item.name not in ("source_fingerprint", "source_record_id")
            }
        )
        _require(value.source_fingerprint == sha256_text(semantic), name)
        _require(
            value.source_record_id
            == "m3-feasibility-source-" + value.source_fingerprint,
            name,
        )
    else:
        _require(replace(value) == value, name)


def _identities(values, name: str) -> tuple[str, ...]:
    _require(type(values) in (tuple, list), name)
    for value in values:
        _identity(value, name)
    _require(len(values) == len(set(values)), name)
    return tuple(sorted(values))


def _records(values, expected: type, key, name: str):
    _require(type(values) in (tuple, list), name)
    result = []
    for value in values:
        _record(value, expected, name)
        result.append(value)
    keys = [key(value) for value in result]
    _require(len(keys) == len(set(keys)), name)
    return tuple(sorted(result, key=key))


def _primitive(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) in (date, datetime):
        return value.isoformat()
    if type(value) is tuple:
        return [_primitive(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
        }
    return value


def _fingerprint(payload: object) -> str:
    return sha256_text(canonical_json(_primitive(payload)))


def _source_subject(kind: "FeasibilitySourceKind", payload: dict[str, object]) -> str:
    return _fingerprint(
        {
            "schema_version": "m3-feasibility-source-subject-v1",
            "source_kind": kind.value,
            "subject": payload,
        }
    )


def current_m8_configuration() -> M8BusinessConfiguration:
    """Return the host-selected M8 configuration used for new evidence."""

    return M8_CONFIGURATION


def m8_feasibility_configuration_json(
    configuration: M8BusinessConfiguration | None = None,
) -> str:
    """Serialize the exact immutable M8 facts used by technical feasibility."""

    selected = current_m8_configuration() if configuration is None else configuration
    assert_valid_m8_configuration(selected)
    return canonical_json(
        {
            "schema_version": M8_FEASIBILITY_CONFIGURATION_SCHEMA_VERSION,
            "service_catalog_version": selected.service_catalog_version,
            "planning_profile_version": selected.sku_planning_profile_version,
            "skill_matrix_version": selected.skill_matrix_version,
            "vehicle_policy_version": selected.vehicle_policy_version,
            "profiles": [
                {
                    "sku": item.sku,
                    "required_skill_key": item.required_skill_key,
                    "minimum_skill_level": item.min_skill_level,
                    "vehicle_class": item.vehicle_class.value,
                }
                for item in sorted(selected.planning_profiles, key=lambda item: item.sku)
            ],
            "workers": [
                {
                    "worker_id": worker.worker_id,
                    "license_b": worker.license_b,
                    "skills": [
                        {"skill_key": skill.sku, "level": skill.level}
                        for skill in sorted(worker.skills, key=lambda item: item.sku)
                    ],
                }
                for worker in sorted(selected.crew, key=lambda item: item.worker_id)
            ],
            "vehicles": [
                {
                    "vehicle_id": vehicle.vehicle_id,
                    "kind": vehicle.kind.value,
                    "capabilities": sorted(
                        item.value for item in vehicle.capabilities
                    ),
                    "assigned_worker_id": vehicle.assigned_worker_id,
                }
                for vehicle in sorted(
                    selected.vehicles, key=lambda item: item.vehicle_id
                )
            ],
        }
    )


def restore_m8_feasibility_configuration(raw: str, fingerprint: str) -> dict:
    """Restore historical M8 evidence without consulting the current config."""

    try:
        document = json.loads(raw)
        _require(type(document) is dict and canonical_json(document) == raw, "m8_configuration")
        _require(
            document.get("schema_version")
            == M8_FEASIBILITY_CONFIGURATION_SCHEMA_VERSION,
            "m8_configuration_schema_version",
        )
        _require(sha256_text(raw) == fingerprint, "m8_configuration_fingerprint")
        for name in (
            "service_catalog_version",
            "planning_profile_version",
            "skill_matrix_version",
            "vehicle_policy_version",
        ):
            _identity(document[name], f"m8_configuration.{name}")
        for collection_name, identity_name in (
            ("profiles", "sku"),
            ("workers", "worker_id"),
            ("vehicles", "vehicle_id"),
        ):
            values = document[collection_name]
            _require(type(values) is list, f"m8_configuration.{collection_name}")
            identities = []
            for value in values:
                _require(type(value) is dict, f"m8_configuration.{collection_name}")
                _identity(value[identity_name], f"m8_configuration.{identity_name}")
                identities.append(value[identity_name])
            _require(
                identities == sorted(set(identities)),
                f"m8_configuration.{collection_name}",
            )
        for profile in document["profiles"]:
            _identity(profile["required_skill_key"], "m8_configuration.required_skill_key")
            _require(
                type(profile["minimum_skill_level"]) is int
                and 0 <= profile["minimum_skill_level"] <= 3,
                "m8_configuration.minimum_skill_level",
            )
            _identity(profile["vehicle_class"], "m8_configuration.vehicle_class")
        for worker in document["workers"]:
            _require(type(worker["license_b"]) is bool, "m8_configuration.license_b")
            _require(type(worker["skills"]) is list, "m8_configuration.skills")
            skill_keys = []
            for skill in worker["skills"]:
                _require(type(skill) is dict, "m8_configuration.skill")
                _identity(skill["skill_key"], "m8_configuration.skill_key")
                _require(
                    type(skill["level"]) is int and 0 <= skill["level"] <= 3,
                    "m8_configuration.skill_level",
                )
                skill_keys.append(skill["skill_key"])
            _require(skill_keys == sorted(set(skill_keys)), "m8_configuration.skills")
        for vehicle in document["vehicles"]:
            _identity(vehicle["kind"], "m8_configuration.vehicle_kind")
            _optional_identity(
                vehicle["assigned_worker_id"],
                "m8_configuration.assigned_worker_id",
            )
            _require(type(vehicle["capabilities"]) is list, "m8_configuration.capabilities")
            for capability in vehicle["capabilities"]:
                _identity(capability, "m8_configuration.capability")
            _require(
                vehicle["capabilities"] == sorted(set(vehicle["capabilities"])),
                "m8_configuration.capabilities",
            )
        return document
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, M3FeasibilitySupportError):
            raise M3FeasibilitySupportStorageError(
                "INVALID_M8_CONFIGURATION", "m8_configuration"
            ) from error
        raise M3FeasibilitySupportStorageError(
            "INVALID_M8_CONFIGURATION", "m8_configuration"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerRegistryProvenance:
    """Immutable M3-C0 capture of one authoritative M2 registry revision."""

    worker_registry_revision: int
    worker_registry_fingerprint: str
    canonical_worker_registry_json: str
    worker_registry_schema_version: str = field(
        default=M2_WORKER_REGISTRY_SCHEMA_VERSION, init=False
    )
    schema_version: str = field(
        default=WORKER_REGISTRY_PROVENANCE_SCHEMA_VERSION, init=False
    )
    worker_registry_provenance_fingerprint: str = field(init=False)
    worker_registry_provenance_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _require(
            type(self.canonical_worker_registry_json) is str,
            "canonical_worker_registry_json",
        )
        try:
            registry = deserialize_worker_registry(
                self.canonical_worker_registry_json
            )
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise M3FeasibilitySupportValidationError(
                "INVALID_VALUE", "canonical_worker_registry_json"
            ) from error
        _require(
            registry.registry_revision == self.worker_registry_revision,
            "worker_registry_revision",
        )
        _require(
            sha256_text(self.canonical_worker_registry_json)
            == self.worker_registry_fingerprint,
            "worker_registry_fingerprint",
        )
        digest = sha256_text(worker_registry_provenance_semantic_json(self))
        object.__setattr__(
            self, "worker_registry_provenance_fingerprint", digest
        )
        object.__setattr__(
            self,
            "worker_registry_provenance_id",
            "m3-feasibility-worker-registry-" + digest,
        )

    @property
    def worker_ids(self) -> tuple[str, ...]:
        return deserialize_worker_registry(
            self.canonical_worker_registry_json
        ).worker_ids


def worker_registry_provenance_semantic_json(
    value: WorkerRegistryProvenance,
) -> str:
    return canonical_json(
        {
            "canonical_worker_registry_json": value.canonical_worker_registry_json,
            "schema_version": value.schema_version,
            "worker_registry_fingerprint": value.worker_registry_fingerprint,
            "worker_registry_revision": value.worker_registry_revision,
            "worker_registry_schema_version": value.worker_registry_schema_version,
        }
    )


def restore_worker_registry_provenance(
    raw_semantic: str,
    fingerprint: str,
    provenance_id: str,
) -> WorkerRegistryProvenance:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_worker_registry_provenance",
        )
        _require(
            document.pop("schema_version")
            == WORKER_REGISTRY_PROVENANCE_SCHEMA_VERSION,
            "worker_registry_provenance_schema_version",
        )
        _require(
            document.pop("worker_registry_schema_version")
            == M2_WORKER_REGISTRY_SCHEMA_VERSION,
            "worker_registry_schema_version",
        )
        value = WorkerRegistryProvenance(**document)
        _require(
            value.worker_registry_provenance_fingerprint == fingerprint,
            "worker_registry_provenance_fingerprint",
        )
        _require(
            value.worker_registry_provenance_id == provenance_id,
            "worker_registry_provenance_id",
        )
        _require(
            worker_registry_provenance_semantic_json(value) == raw_semantic,
            "worker_registry_provenance_canonical_order",
        )
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise M3FeasibilitySupportStorageError(
            "INVALID_WORKER_REGISTRY_PROVENANCE", "worker_registry_provenance"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerRegistryProvenanceCapture:
    """One authoritative registry capture in the shared feasibility chronology."""

    ledger_generation: int
    worker_registry_provenance_id: str
    worker_registry_provenance_fingerprint: str
    worker_registry_revision: int
    worker_registry_fingerprint: str
    capture_kind: str = field(default=WORKER_REGISTRY_CAPTURE_KIND, init=False)
    schema_version: str = field(
        default=WORKER_REGISTRY_CAPTURE_SCHEMA_VERSION, init=False
    )
    worker_registry_capture_fingerprint: str = field(init=False)
    worker_registry_capture_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.ledger_generation, "ledger_generation")
        _require(self.ledger_generation > 0, "ledger_generation")
        _identity(
            self.worker_registry_provenance_id,
            "worker_registry_provenance_id",
        )
        for name in (
            "worker_registry_provenance_fingerprint",
            "worker_registry_fingerprint",
        ):
            _digest(getattr(self, name), name)
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _require(
            self.worker_registry_provenance_id
            == "m3-feasibility-worker-registry-"
            + self.worker_registry_provenance_fingerprint,
            "worker_registry_provenance_id",
        )
        digest = sha256_text(worker_registry_capture_semantic_json(self))
        object.__setattr__(
            self, "worker_registry_capture_fingerprint", digest
        )
        object.__setattr__(
            self,
            "worker_registry_capture_id",
            "m3-feasibility-worker-registry-capture-" + digest,
        )


def worker_registry_capture_semantic_json(
    value: WorkerRegistryProvenanceCapture,
) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name
            not in (
                "worker_registry_capture_fingerprint",
                "worker_registry_capture_id",
            )
        }
    )


def restore_worker_registry_capture(
    raw_semantic: str,
    fingerprint: str,
    capture_id: str,
) -> WorkerRegistryProvenanceCapture:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_worker_registry_capture",
        )
        _require(
            document.pop("schema_version")
            == WORKER_REGISTRY_CAPTURE_SCHEMA_VERSION,
            "worker_registry_capture_schema_version",
        )
        _require(
            document.pop("capture_kind") == WORKER_REGISTRY_CAPTURE_KIND,
            "worker_registry_capture_kind",
        )
        value = WorkerRegistryProvenanceCapture(**document)
        _require(
            value.worker_registry_capture_fingerprint == fingerprint,
            "worker_registry_capture_fingerprint",
        )
        _require(
            value.worker_registry_capture_id == capture_id,
            "worker_registry_capture_id",
        )
        _require(
            worker_registry_capture_semantic_json(value) == raw_semantic,
            "worker_registry_capture_canonical_order",
        )
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise M3FeasibilitySupportStorageError(
            "INVALID_WORKER_REGISTRY_CAPTURE", "worker_registry_capture"
        ) from error


def _set_source_identity(value, kind: "FeasibilitySourceKind") -> None:
    _revision(value.source_revision, "source_revision")
    _identity(value.provenance_reference, "provenance_reference")
    _optional_identity(value.previous_source_record_id, "previous_source_record_id")
    _require(
        (value.source_revision == 0) == (value.previous_source_record_id is None),
        "previous_source_record_id",
    )
    object.__setattr__(value, "source_kind", kind)
    semantic = {
        item.name: _primitive(getattr(value, item.name))
        for item in fields(value)
        if item.name not in ("source_fingerprint", "source_record_id")
    }
    digest = sha256_text(canonical_json(semantic))
    object.__setattr__(value, "source_fingerprint", digest)
    object.__setattr__(value, "source_record_id", "m3-feasibility-source-" + digest)


class FeasibilitySourceKind(StrEnum):
    TASK_CONSTRAINT = "TASK_CONSTRAINT"
    PLAN_SCHEDULE = "PLAN_SCHEDULE"
    WORKER_AVAILABILITY = "WORKER_AVAILABILITY"
    TASK_READINESS = "TASK_READINESS"
    VEHICLE_AVAILABILITY = "VEHICLE_AVAILABILITY"
    ROUTE = "ROUTE"


class ConstraintKnowledge(StrEnum):
    KNOWN = "KNOWN"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class AvailabilityKnowledge(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class ReadinessState(StrEnum):
    READY = "READY"
    EXPECTED = "EXPECTED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class RouteKnowledge(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskConstraintSource:
    job_id: str
    task_id: str
    task_definition_version: str
    source_handoff_id: str
    source_revision_number: int
    m8_sku: str
    duration_seconds: int
    location_reference: str
    location_fingerprint: str
    customer_window_state: ConstraintKnowledge
    customer_window_start: datetime | None
    customer_window_end: datetime | None
    hard_deadline_state: ConstraintKnowledge
    hard_deadline: datetime | None
    source_revision: int
    previous_source_record_id: str | None
    provenance_reference: str
    source_kind: FeasibilitySourceKind = field(init=False)
    schema_version: str = field(
        default=FEASIBILITY_SOURCE_SCHEMA_VERSION, init=False
    )
    m8_service_catalog_version: str = field(init=False)
    m8_planning_profile_version: str = field(init=False)
    m8_configuration_fingerprint: str = field(init=False)
    required_skill_key: str = field(init=False)
    minimum_skill_level: int = field(init=False)
    required_vehicle_class: str = field(init=False)
    subject_fingerprint: str = field(init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        self._validate_base_semantics()
        configuration = current_m8_configuration()
        configuration_json = m8_feasibility_configuration_json(configuration)
        try:
            profile = configuration.profile(self.m8_sku)
            configuration.catalog_item(self.m8_sku)
        except KeyError as error:
            raise M3FeasibilitySupportValidationError(
                "UNKNOWN_M8_SKU", "m8_sku"
            ) from error
        object.__setattr__(
            self, "m8_service_catalog_version", configuration.service_catalog_version
        )
        object.__setattr__(
            self,
            "m8_planning_profile_version",
            configuration.sku_planning_profile_version,
        )
        object.__setattr__(
            self,
            "m8_configuration_fingerprint",
            sha256_text(configuration_json),
        )
        object.__setattr__(self, "required_skill_key", profile.required_skill_key)
        object.__setattr__(self, "minimum_skill_level", profile.min_skill_level)
        object.__setattr__(self, "required_vehicle_class", profile.vehicle_class.value)
        self._validate_m8_semantics()
        self._finish_identity()

    def _validate_base_semantics(self) -> None:
        for name in (
            "job_id",
            "task_id",
            "task_definition_version",
            "source_handoff_id",
            "location_reference",
        ):
            _identity(getattr(self, name), name)
        _revision(self.source_revision_number, "source_revision_number")
        _require(self.source_revision_number > 0, "source_revision_number")
        _digest(self.location_fingerprint, "location_fingerprint")
        _positive_seconds(self.duration_seconds, "duration_seconds")
        _require(type(self.customer_window_state) is ConstraintKnowledge, "customer_window_state")
        _require(type(self.hard_deadline_state) is ConstraintKnowledge, "hard_deadline_state")
        window_start = (
            None
            if self.customer_window_start is None
            else _instant(self.customer_window_start, "customer_window_start")
        )
        window_end = (
            None
            if self.customer_window_end is None
            else _instant(self.customer_window_end, "customer_window_end")
        )
        if self.customer_window_state is ConstraintKnowledge.KNOWN:
            _require(
                window_start is not None
                and window_end is not None
                and window_end > window_start,
                "customer_window",
            )
        else:
            _require(window_start is None and window_end is None, "customer_window")
        deadline = (
            None
            if self.hard_deadline is None
            else _instant(self.hard_deadline, "hard_deadline")
        )
        _require(
            (self.hard_deadline_state is ConstraintKnowledge.KNOWN)
            == (deadline is not None),
            "hard_deadline",
        )
        object.__setattr__(self, "customer_window_start", window_start)
        object.__setattr__(self, "customer_window_end", window_end)
        object.__setattr__(self, "hard_deadline", deadline)

    def _validate_m8_semantics(self) -> None:
        for name in (
            "m8_sku",
            "m8_service_catalog_version",
            "m8_planning_profile_version",
            "required_skill_key",
            "required_vehicle_class",
        ):
            _identity(getattr(self, name), name)
        _digest(self.m8_configuration_fingerprint, "m8_configuration_fingerprint")
        _require(
            type(self.minimum_skill_level) is int
            and 0 <= self.minimum_skill_level <= 3,
            "minimum_skill_level",
        )

    def _finish_identity(self) -> None:
        object.__setattr__(
            self,
            "subject_fingerprint",
            task_subject_fingerprint(
                self.job_id, self.task_id, self.task_definition_version
            ),
        )
        _set_source_identity(self, FeasibilitySourceKind.TASK_CONSTRAINT)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduledPlacementEvidence:
    commitment_id: str
    job_id: str
    task_id: str
    task_definition_version: str
    business_date: date
    worker_ids: tuple[str, ...]
    vehicle_id: str | None
    planned_start: datetime
    planned_end: datetime

    def __post_init__(self) -> None:
        for name in ("commitment_id", "job_id", "task_id", "task_definition_version"):
            _identity(getattr(self, name), name)
        _require(type(self.business_date) is date, "business_date")
        object.__setattr__(self, "worker_ids", _identities(self.worker_ids, "worker_ids"))
        _optional_identity(self.vehicle_id, "vehicle_id")
        start = _instant(self.planned_start, "planned_start")
        end = _instant(self.planned_end, "planned_end")
        _require(end > start, "planned_interval")
        object.__setattr__(self, "planned_start", start)
        object.__setattr__(self, "planned_end", end)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanScheduleSource:
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    business_timezone: str
    placements: tuple[ScheduledPlacementEvidence, ...]
    source_revision: int
    previous_source_record_id: str | None
    provenance_reference: str
    source_kind: FeasibilitySourceKind = field(init=False)
    schema_version: str = field(
        default=FEASIBILITY_SOURCE_SCHEMA_VERSION, init=False
    )
    subject_fingerprint: str = field(init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.company_plan_id, "company_plan_id")
        _revision(self.base_plan_revision, "base_plan_revision")
        _identity(self.base_plan_revision_id, "base_plan_revision_id")
        _digest(self.base_plan_revision_fingerprint, "base_plan_revision_fingerprint")
        _require(
            self.base_plan_revision_id
            == "m3-plan-revision-" + self.base_plan_revision_fingerprint,
            "base_plan_revision_id",
        )
        _identity(self.business_timezone, "business_timezone")
        try:
            business_zone = ZoneInfo(self.business_timezone)
        except ZoneInfoNotFoundError as error:
            raise M3FeasibilitySupportValidationError(
                "UNKNOWN_TIMEZONE", "business_timezone"
            ) from error
        placements = _records(
            self.placements,
            ScheduledPlacementEvidence,
            lambda item: (item.job_id, item.task_id, item.commitment_id),
            "placements",
        )
        _require(
            all(
                item.planned_start.astimezone(business_zone).date()
                == item.business_date
                for item in placements
            ),
            "placement_business_date",
        )
        object.__setattr__(self, "placements", placements)
        object.__setattr__(
            self,
            "subject_fingerprint",
            schedule_subject_fingerprint(
                self.company_plan_id, self.base_plan_revision_id
            ),
        )
        _set_source_identity(self, FeasibilitySourceKind.PLAN_SCHEDULE)


@dataclass(frozen=True, slots=True, kw_only=True)
class AvailabilityWindowEvidence:
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        start = _instant(self.start_at, "availability_start")
        end = _instant(self.end_at, "availability_end")
        _require(end > start, "availability_window")
        object.__setattr__(self, "start_at", start)
        object.__setattr__(self, "end_at", end)


def _canonical_windows(
    values: tuple[AvailabilityWindowEvidence, ...] | list[AvailabilityWindowEvidence],
    coverage_start: datetime,
    coverage_end: datetime,
) -> tuple[AvailabilityWindowEvidence, ...]:
    _require(type(values) in (tuple, list), "available_windows")
    normalized = []
    for value in values:
        _record(value, AvailabilityWindowEvidence, "available_windows")
        _require(
            coverage_start <= value.start_at < value.end_at <= coverage_end,
            "available_windows",
        )
        normalized.append(value)
    normalized.sort(key=lambda item: (item.start_at, item.end_at))
    merged: list[AvailabilityWindowEvidence] = []
    for value in normalized:
        if merged and value.start_at <= merged[-1].end_at:
            merged[-1] = AvailabilityWindowEvidence(
                start_at=merged[-1].start_at,
                end_at=max(merged[-1].end_at, value.end_at),
            )
        else:
            merged.append(value)
    return tuple(merged)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerAvailabilitySource:
    worker_id: str
    coverage_start: datetime
    coverage_end: datetime
    available_windows: tuple[AvailabilityWindowEvidence, ...]
    source_revision: int
    previous_source_record_id: str | None
    provenance_reference: str
    source_kind: FeasibilitySourceKind = field(init=False)
    schema_version: str = field(
        default=FEASIBILITY_SOURCE_SCHEMA_VERSION, init=False
    )
    subject_fingerprint: str = field(init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.worker_id, "worker_id")
        start = _instant(self.coverage_start, "coverage_start")
        end = _instant(self.coverage_end, "coverage_end")
        _require(end > start, "availability_coverage")
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(
            self,
            "available_windows",
            _canonical_windows(self.available_windows, start, end),
        )
        object.__setattr__(
            self, "subject_fingerprint", worker_subject_fingerprint(self.worker_id)
        )
        _set_source_identity(self, FeasibilitySourceKind.WORKER_AVAILABILITY)


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskReadinessSource:
    job_id: str
    task_id: str
    task_definition_version: str
    status: ReadinessState
    expected_at: datetime | None
    blocker_references: tuple[str, ...]
    source_revision: int
    previous_source_record_id: str | None
    provenance_reference: str
    source_kind: FeasibilitySourceKind = field(init=False)
    schema_version: str = field(
        default=FEASIBILITY_SOURCE_SCHEMA_VERSION, init=False
    )
    subject_fingerprint: str = field(init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("job_id", "task_id", "task_definition_version"):
            _identity(getattr(self, name), name)
        _require(type(self.status) is ReadinessState, "readiness_status")
        expected_at = (
            None
            if self.expected_at is None
            else _instant(self.expected_at, "expected_at")
        )
        _require(
            (self.status is ReadinessState.EXPECTED) == (expected_at is not None),
            "expected_at",
        )
        object.__setattr__(self, "expected_at", expected_at)
        object.__setattr__(
            self,
            "blocker_references",
            _identities(self.blocker_references, "blocker_references"),
        )
        object.__setattr__(
            self,
            "subject_fingerprint",
            task_readiness_subject_fingerprint(
                self.job_id, self.task_id, self.task_definition_version
            ),
        )
        _set_source_identity(self, FeasibilitySourceKind.TASK_READINESS)


@dataclass(frozen=True, slots=True, kw_only=True)
class VehicleAvailabilitySource:
    vehicle_id: str
    coverage_start: datetime
    coverage_end: datetime
    available_windows: tuple[AvailabilityWindowEvidence, ...]
    source_revision: int
    previous_source_record_id: str | None
    provenance_reference: str
    source_kind: FeasibilitySourceKind = field(init=False)
    schema_version: str = field(
        default=FEASIBILITY_SOURCE_SCHEMA_VERSION, init=False
    )
    subject_fingerprint: str = field(init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.vehicle_id, "vehicle_id")
        start = _instant(self.coverage_start, "coverage_start")
        end = _instant(self.coverage_end, "coverage_end")
        _require(end > start, "availability_coverage")
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(
            self,
            "available_windows",
            _canonical_windows(self.available_windows, start, end),
        )
        object.__setattr__(
            self, "subject_fingerprint", vehicle_subject_fingerprint(self.vehicle_id)
        )
        _set_source_identity(self, FeasibilitySourceKind.VEHICLE_AVAILABILITY)


@dataclass(frozen=True, slots=True, kw_only=True)
class RouteSource:
    origin_reference: str
    origin_fingerprint: str
    destination_reference: str
    destination_fingerprint: str
    transport_mode: str
    departure_time_basis: str
    distance_meters: int
    travel_duration_seconds: int
    buffer_seconds: int
    buffer_rule_version: str
    route_snapshot_version: str
    provider: str
    source_revision: int
    previous_source_record_id: str | None
    provenance_reference: str
    source_kind: FeasibilitySourceKind = field(init=False)
    schema_version: str = field(
        default=FEASIBILITY_SOURCE_SCHEMA_VERSION, init=False
    )
    subject_fingerprint: str = field(init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "origin_reference",
            "destination_reference",
            "transport_mode",
            "departure_time_basis",
            "buffer_rule_version",
            "route_snapshot_version",
            "provider",
        ):
            _identity(getattr(self, name), name)
        for name in ("origin_fingerprint", "destination_fingerprint"):
            _digest(getattr(self, name), name)
        _require(
            type(self.distance_meters) is int and self.distance_meters >= 0,
            "distance_meters",
        )
        _require(
            type(self.travel_duration_seconds) is int
            and self.travel_duration_seconds >= 0,
            "travel_duration_seconds",
        )
        _require(
            type(self.buffer_seconds) is int and self.buffer_seconds >= 0,
            "buffer_seconds",
        )
        _require(
            (self.origin_reference, self.origin_fingerprint)
            != (self.destination_reference, self.destination_fingerprint),
            "route_endpoints",
        )
        object.__setattr__(
            self,
            "subject_fingerprint",
            route_subject_fingerprint(
                self.origin_reference,
                self.origin_fingerprint,
                self.destination_reference,
                self.destination_fingerprint,
                self.transport_mode,
            ),
        )
        _set_source_identity(self, FeasibilitySourceKind.ROUTE)


FeasibilitySource = (
    TaskConstraintSource
    | PlanScheduleSource
    | WorkerAvailabilitySource
    | TaskReadinessSource
    | VehicleAvailabilitySource
    | RouteSource
)


def task_subject_fingerprint(job_id: str, task_id: str, version: str) -> str:
    return _source_subject(
        FeasibilitySourceKind.TASK_CONSTRAINT,
        {"job_id": job_id, "task_id": task_id, "task_definition_version": version},
    )


def task_readiness_subject_fingerprint(
    job_id: str, task_id: str, version: str
) -> str:
    return _source_subject(
        FeasibilitySourceKind.TASK_READINESS,
        {"job_id": job_id, "task_id": task_id, "task_definition_version": version},
    )


def schedule_subject_fingerprint(company_plan_id: str, revision_id: str) -> str:
    return _source_subject(
        FeasibilitySourceKind.PLAN_SCHEDULE,
        {"company_plan_id": company_plan_id, "base_plan_revision_id": revision_id},
    )


def worker_subject_fingerprint(worker_id: str) -> str:
    return _source_subject(
        FeasibilitySourceKind.WORKER_AVAILABILITY, {"worker_id": worker_id}
    )


def vehicle_subject_fingerprint(vehicle_id: str) -> str:
    return _source_subject(
        FeasibilitySourceKind.VEHICLE_AVAILABILITY, {"vehicle_id": vehicle_id}
    )


def route_subject_fingerprint(
    origin_reference: str,
    origin_fingerprint: str,
    destination_reference: str,
    destination_fingerprint: str,
    transport_mode: str,
) -> str:
    return _source_subject(
        FeasibilitySourceKind.ROUTE,
        {
            "origin_reference": origin_reference,
            "origin_fingerprint": origin_fingerprint,
            "destination_reference": destination_reference,
            "destination_fingerprint": destination_fingerprint,
            "transport_mode": transport_mode,
        },
    )


def source_semantic_json(value: FeasibilitySource) -> str:
    _record(value, type(value), "source")
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in ("source_fingerprint", "source_record_id")
        }
    )


def _source_kwargs(document: dict[str, object]) -> dict[str, object]:
    values = dict(document)
    for name in (
        "source_kind",
        "schema_version",
        "subject_fingerprint",
        "source_fingerprint",
        "source_record_id",
    ):
        values.pop(name, None)
    return values


def _restore_task_constraint(document: dict[str, object]) -> TaskConstraintSource:
    value = object.__new__(TaskConstraintSource)
    converted = {
        **document,
        "source_kind": FeasibilitySourceKind(document["source_kind"]),
        "customer_window_state": ConstraintKnowledge(
            document["customer_window_state"]
        ),
        "customer_window_start": _parse_datetime(document["customer_window_start"]),
        "customer_window_end": _parse_datetime(document["customer_window_end"]),
        "hard_deadline_state": ConstraintKnowledge(document["hard_deadline_state"]),
        "hard_deadline": _parse_datetime(document["hard_deadline"]),
    }
    for item in fields(TaskConstraintSource):
        if item.name in ("source_fingerprint", "source_record_id"):
            continue
        _require(item.name in converted, f"task_constraint.{item.name}")
        object.__setattr__(value, item.name, converted[item.name])
    _require(
        value.schema_version == FEASIBILITY_SOURCE_SCHEMA_VERSION,
        "source_schema_version",
    )
    _require(
        value.source_kind is FeasibilitySourceKind.TASK_CONSTRAINT,
        "source_kind",
    )
    value._validate_base_semantics()
    value._validate_m8_semantics()
    value._finish_identity()
    return value


def restore_source(
    raw_semantic: str,
    fingerprint: str,
    source_record_id: str,
) -> FeasibilitySource:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_source_document",
        )
        _require(
            document.get("schema_version") == FEASIBILITY_SOURCE_SCHEMA_VERSION,
            "source_schema_version",
        )
        kind = FeasibilitySourceKind(document["source_kind"])
        values = _source_kwargs(document)
        if kind is FeasibilitySourceKind.TASK_CONSTRAINT:
            result = _restore_task_constraint(document)
        elif kind is FeasibilitySourceKind.PLAN_SCHEDULE:
            values["placements"] = tuple(
                ScheduledPlacementEvidence(
                    **{
                        **item,
                        "business_date": date.fromisoformat(item["business_date"]),
                        "worker_ids": tuple(item["worker_ids"]),
                        "planned_start": datetime.fromisoformat(item["planned_start"]),
                        "planned_end": datetime.fromisoformat(item["planned_end"]),
                    }
                )
                for item in values["placements"]
            )
            result = PlanScheduleSource(**values)
        elif kind in (
            FeasibilitySourceKind.WORKER_AVAILABILITY,
            FeasibilitySourceKind.VEHICLE_AVAILABILITY,
        ):
            values.update(
                coverage_start=datetime.fromisoformat(values["coverage_start"]),
                coverage_end=datetime.fromisoformat(values["coverage_end"]),
                available_windows=tuple(
                    AvailabilityWindowEvidence(
                        start_at=datetime.fromisoformat(item["start_at"]),
                        end_at=datetime.fromisoformat(item["end_at"]),
                    )
                    for item in values["available_windows"]
                ),
            )
            result = (
                WorkerAvailabilitySource(**values)
                if kind is FeasibilitySourceKind.WORKER_AVAILABILITY
                else VehicleAvailabilitySource(**values)
            )
        elif kind is FeasibilitySourceKind.TASK_READINESS:
            values.update(
                status=ReadinessState(values["status"]),
                expected_at=_parse_datetime(values["expected_at"]),
                blocker_references=tuple(values["blocker_references"]),
            )
            result = TaskReadinessSource(**values)
        else:
            result = RouteSource(**values)
        _require(result.source_fingerprint == fingerprint, "source_fingerprint")
        _require(result.source_record_id == source_record_id, "source_record_id")
        _require(source_semantic_json(result) == raw_semantic, "source_canonical_order")
        return result
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        if isinstance(error, M3FeasibilitySupportError):
            raise M3FeasibilitySupportStorageError(
                "INVALID_FEASIBILITY_SOURCE", "source"
            ) from error
        raise M3FeasibilitySupportStorageError(
            "INVALID_FEASIBILITY_SOURCE", "source"
        ) from error


def _parse_datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def m8_feasibility_configuration_fingerprint(
    configuration: M8BusinessConfiguration | None = None,
) -> str:
    return sha256_text(m8_feasibility_configuration_json(configuration))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerSkillLevelEvidence:
    required_skill_key: str
    level: int | None

    def __post_init__(self) -> None:
        _identity(self.required_skill_key, "required_skill_key")
        _require(
            self.level is None
            or (type(self.level) is int and 0 <= self.level <= 3),
            "skill_level",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerTechnicalEvidence:
    worker_id: str
    m8_worker_known: bool
    skill_matrix_version: str
    license_b: bool | None
    skill_levels: tuple[WorkerSkillLevelEvidence, ...]

    def __post_init__(self) -> None:
        _identity(self.worker_id, "worker_id")
        _identity(self.skill_matrix_version, "skill_matrix_version")
        _require(type(self.m8_worker_known) is bool, "m8_worker_known")
        _require(
            (self.m8_worker_known and type(self.license_b) is bool)
            or (not self.m8_worker_known and self.license_b is None),
            "license_b",
        )
        levels = _records(
            self.skill_levels,
            WorkerSkillLevelEvidence,
            lambda item: item.required_skill_key,
            "skill_levels",
        )
        _require(
            all((item.level is not None) == self.m8_worker_known for item in levels),
            "skill_levels",
        )
        object.__setattr__(self, "skill_levels", levels)


@dataclass(frozen=True, slots=True, kw_only=True)
class AvailabilityEvidence:
    subject_id: str
    knowledge: AvailabilityKnowledge
    coverage_start: datetime | None
    coverage_end: datetime | None
    available_windows: tuple[AvailabilityWindowEvidence, ...]
    source_record_id: str | None
    source_fingerprint: str | None
    source_revision: int | None

    def __post_init__(self) -> None:
        _identity(self.subject_id, "availability_subject_id")
        _require(type(self.knowledge) is AvailabilityKnowledge, "availability_knowledge")
        if self.knowledge is AvailabilityKnowledge.UNKNOWN:
            _require(
                self.coverage_start is None
                and self.coverage_end is None
                and not self.available_windows
                and self.source_record_id is None
                and self.source_fingerprint is None
                and self.source_revision is None,
                "unknown_availability",
            )
            object.__setattr__(self, "available_windows", ())
            return
        start = _instant(self.coverage_start, "coverage_start")
        end = _instant(self.coverage_end, "coverage_end")
        _require(end > start, "availability_coverage")
        _identity(self.source_record_id, "source_record_id")
        _digest(self.source_fingerprint, "source_fingerprint")
        _revision(self.source_revision, "source_revision")
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(
            self,
            "available_windows",
            _canonical_windows(self.available_windows, start, end),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadinessEvidence:
    job_id: str
    task_id: str
    task_definition_version: str
    status: ReadinessState
    expected_at: datetime | None
    blocker_references: tuple[str, ...]
    source_record_id: str | None
    source_fingerprint: str | None
    source_revision: int | None

    def __post_init__(self) -> None:
        for name in ("job_id", "task_id", "task_definition_version"):
            _identity(getattr(self, name), name)
        _require(type(self.status) is ReadinessState, "readiness_status")
        expected = (
            None
            if self.expected_at is None
            else _instant(self.expected_at, "expected_at")
        )
        _require(
            (self.status is ReadinessState.EXPECTED) == (expected is not None),
            "expected_at",
        )
        blockers = _identities(self.blocker_references, "blocker_references")
        if self.source_record_id is None:
            _require(
                self.status is ReadinessState.UNKNOWN
                and self.source_fingerprint is None
                and self.source_revision is None
                and not blockers,
                "missing_readiness_source",
            )
        else:
            _identity(self.source_record_id, "source_record_id")
            _digest(self.source_fingerprint, "source_fingerprint")
            _revision(self.source_revision, "source_revision")
        object.__setattr__(self, "expected_at", expected)
        object.__setattr__(self, "blocker_references", blockers)


@dataclass(frozen=True, slots=True, kw_only=True)
class VehicleTechnicalEvidence:
    vehicle_id: str
    kind: str
    capabilities: tuple[str, ...]
    assigned_worker_id: str | None
    private_authority_evidence_included: bool = False

    def __post_init__(self) -> None:
        _identity(self.vehicle_id, "vehicle_id")
        _identity(self.kind, "vehicle_kind")
        object.__setattr__(self, "capabilities", _identities(self.capabilities, "capabilities"))
        _optional_identity(self.assigned_worker_id, "assigned_worker_id")
        _require(
            type(self.private_authority_evidence_included) is bool
            and not self.private_authority_evidence_included,
            "private_authority_evidence_included",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RouteEvidence:
    origin_reference: str
    origin_fingerprint: str
    destination_reference: str
    destination_fingerprint: str
    transport_mode: str
    knowledge: RouteKnowledge
    departure_time_basis: str | None
    distance_meters: int | None
    travel_duration_seconds: int | None
    buffer_seconds: int | None
    buffer_rule_version: str | None
    route_snapshot_version: str | None
    provider: str | None
    source_record_id: str | None
    source_fingerprint: str | None
    source_revision: int | None

    def __post_init__(self) -> None:
        for name in ("origin_reference", "destination_reference", "transport_mode"):
            _identity(getattr(self, name), name)
        for name in ("origin_fingerprint", "destination_fingerprint"):
            _digest(getattr(self, name), name)
        _require(type(self.knowledge) is RouteKnowledge, "route_knowledge")
        optional = (
            self.departure_time_basis,
            self.distance_meters,
            self.travel_duration_seconds,
            self.buffer_seconds,
            self.buffer_rule_version,
            self.route_snapshot_version,
            self.provider,
            self.source_record_id,
            self.source_fingerprint,
            self.source_revision,
        )
        if self.knowledge is RouteKnowledge.UNKNOWN:
            _require(all(value is None for value in optional), "unknown_route")
            return
        for name in (
            "departure_time_basis",
            "buffer_rule_version",
            "route_snapshot_version",
            "provider",
            "source_record_id",
        ):
            _identity(getattr(self, name), name)
        _digest(self.source_fingerprint, "source_fingerprint")
        _revision(self.source_revision, "source_revision")
        _require(type(self.distance_meters) is int and self.distance_meters >= 0, "distance_meters")
        _require(
            type(self.travel_duration_seconds) is int
            and self.travel_duration_seconds >= 0,
            "travel_duration_seconds",
        )
        _require(type(self.buffer_seconds) is int and self.buffer_seconds >= 0, "buffer_seconds")


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceSelection:
    source_kind: FeasibilitySourceKind
    subject_fingerprint: str
    source_record_id: str | None
    source_revision: int | None
    source_fingerprint: str | None

    def __post_init__(self) -> None:
        _require(type(self.source_kind) is FeasibilitySourceKind, "source_kind")
        _digest(self.subject_fingerprint, "subject_fingerprint")
        selected = (
            self.source_record_id,
            self.source_revision,
            self.source_fingerprint,
        )
        _require(
            all(value is None for value in selected)
            or all(value is not None for value in selected),
            "source_selection",
        )
        if self.source_record_id is None:
            return
        _identity(self.source_record_id, "source_record_id")
        _revision(self.source_revision, "source_revision")
        _digest(self.source_fingerprint, "source_fingerprint")
        _require(
            self.source_record_id
            == "m3-feasibility-source-" + self.source_fingerprint,
            "source_record_id",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceSelectionCut:
    evaluation_input_id: str
    evaluation_input_fingerprint: str
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    worker_registry_provenance_id: str
    worker_registry_provenance_fingerprint: str
    worker_registry_capture_id: str
    worker_registry_capture_fingerprint: str
    worker_registry_capture_generation: int
    source_cut_generation: int
    selections: tuple[SourceSelection, ...]
    schema_version: str = field(
        default=SOURCE_SELECTION_CUT_SCHEMA_VERSION, init=False
    )
    source_cut_fingerprint: str = field(init=False)
    source_cut_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "evaluation_input_id",
            "company_plan_id",
            "base_plan_revision_id",
            "worker_registry_provenance_id",
            "worker_registry_capture_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "evaluation_input_fingerprint",
            "base_plan_revision_fingerprint",
            "worker_registry_provenance_fingerprint",
            "worker_registry_capture_fingerprint",
        ):
            _digest(getattr(self, name), name)
        _revision(self.base_plan_revision, "base_plan_revision")
        _revision(
            self.worker_registry_capture_generation,
            "worker_registry_capture_generation",
        )
        _revision(self.source_cut_generation, "source_cut_generation")
        _require(
            self.evaluation_input_id
            == "m3-evaluation-input-" + self.evaluation_input_fingerprint,
            "evaluation_input_id",
        )
        _require(
            self.base_plan_revision_id
            == "m3-plan-revision-" + self.base_plan_revision_fingerprint,
            "base_plan_revision_id",
        )
        _require(
            self.worker_registry_provenance_id
            == "m3-feasibility-worker-registry-"
            + self.worker_registry_provenance_fingerprint,
            "worker_registry_provenance_id",
        )
        _require(
            self.worker_registry_capture_id
            == "m3-feasibility-worker-registry-capture-"
            + self.worker_registry_capture_fingerprint,
            "worker_registry_capture_id",
        )
        selections = _records(
            self.selections,
            SourceSelection,
            lambda item: (item.source_kind.value, item.subject_fingerprint),
            "source_selections",
        )
        object.__setattr__(self, "selections", selections)
        digest = sha256_text(source_cut_semantic_json(self))
        object.__setattr__(self, "source_cut_fingerprint", digest)
        object.__setattr__(self, "source_cut_id", "m3-feasibility-source-cut-" + digest)


def source_cut_semantic_json(value: SourceSelectionCut) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in ("source_cut_fingerprint", "source_cut_id")
        }
    )


def restore_source_selection_cut(
    raw_semantic: str,
    fingerprint: str,
    source_cut_id: str,
) -> SourceSelectionCut:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_source_cut_document",
        )
        _require(
            document.pop("schema_version") == SOURCE_SELECTION_CUT_SCHEMA_VERSION,
            "source_cut_schema_version",
        )
        selections = tuple(
            SourceSelection(
                **{
                    **item,
                    "source_kind": FeasibilitySourceKind(item["source_kind"]),
                }
            )
            for item in document.pop("selections")
        )
        value = SourceSelectionCut(**document, selections=selections)
        _require(value.source_cut_fingerprint == fingerprint, "source_cut_fingerprint")
        _require(value.source_cut_id == source_cut_id, "source_cut_id")
        _require(source_cut_semantic_json(value) == raw_semantic, "source_cut_canonical_order")
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise M3FeasibilitySupportStorageError(
            "INVALID_SOURCE_SELECTION_CUT", "source_selection_cut"
        ) from error


@dataclass(frozen=True, slots=True, kw_only=True)
class FeasibilitySupportSnapshot:
    evaluation_input_id: str
    evaluation_input_fingerprint: str
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    source_cut_id: str
    source_cut_generation: int
    source_cut_fingerprint: str
    m8_service_catalog_version: str
    m8_planning_profile_version: str
    m8_skill_matrix_version: str
    m8_vehicle_policy_version: str
    m8_configuration_fingerprint: str
    worker_registry_provenance_id: str
    worker_registry_provenance_fingerprint: str
    worker_registry_capture_id: str
    worker_registry_capture_fingerprint: str
    worker_registry_capture_generation: int
    worker_registry_revision: int
    worker_registry_fingerprint: str
    task_constraints: tuple[TaskConstraintSource, ...]
    schedule: PlanScheduleSource
    worker_technical_evidence: tuple[WorkerTechnicalEvidence, ...]
    worker_availability: tuple[AvailabilityEvidence, ...]
    readiness: tuple[ReadinessEvidence, ...]
    vehicle_technical_evidence: tuple[VehicleTechnicalEvidence, ...]
    vehicle_availability: tuple[AvailabilityEvidence, ...]
    routes: tuple[RouteEvidence, ...]
    schema_version: str = field(
        default=FEASIBILITY_SUPPORT_SCHEMA_VERSION, init=False
    )
    rule_version: str = field(
        default=FEASIBILITY_SUPPORT_RULE_VERSION, init=False
    )
    support_fingerprint: str = field(init=False)
    support_snapshot_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "evaluation_input_id",
            "company_plan_id",
            "base_plan_revision_id",
            "source_cut_id",
            "m8_service_catalog_version",
            "m8_planning_profile_version",
            "m8_skill_matrix_version",
            "m8_vehicle_policy_version",
            "worker_registry_provenance_id",
            "worker_registry_capture_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "evaluation_input_fingerprint",
            "base_plan_revision_fingerprint",
            "source_cut_fingerprint",
            "m8_configuration_fingerprint",
            "worker_registry_provenance_fingerprint",
            "worker_registry_capture_fingerprint",
            "worker_registry_fingerprint",
        ):
            _digest(getattr(self, name), name)
        _revision(self.base_plan_revision, "base_plan_revision")
        _revision(self.source_cut_generation, "source_cut_generation")
        _revision(
            self.worker_registry_capture_generation,
            "worker_registry_capture_generation",
        )
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _require(
            self.evaluation_input_id
            == "m3-evaluation-input-" + self.evaluation_input_fingerprint,
            "evaluation_input_id",
        )
        _require(
            self.base_plan_revision_id
            == "m3-plan-revision-" + self.base_plan_revision_fingerprint,
            "base_plan_revision_id",
        )
        _require(
            self.source_cut_id
            == "m3-feasibility-source-cut-" + self.source_cut_fingerprint,
            "source_cut_id",
        )
        _require(
            self.worker_registry_provenance_id
            == "m3-feasibility-worker-registry-"
            + self.worker_registry_provenance_fingerprint,
            "worker_registry_provenance_id",
        )
        _require(
            self.worker_registry_capture_id
            == "m3-feasibility-worker-registry-capture-"
            + self.worker_registry_capture_fingerprint,
            "worker_registry_capture_id",
        )
        constraints = _records(
            self.task_constraints,
            TaskConstraintSource,
            lambda item: (item.job_id, item.task_id),
            "task_constraints",
        )
        _record(self.schedule, PlanScheduleSource, "schedule")
        workers = _records(
            self.worker_technical_evidence,
            WorkerTechnicalEvidence,
            lambda item: item.worker_id,
            "worker_technical_evidence",
        )
        worker_availability = _records(
            self.worker_availability,
            AvailabilityEvidence,
            lambda item: item.subject_id,
            "worker_availability",
        )
        readiness = _records(
            self.readiness,
            ReadinessEvidence,
            lambda item: (item.job_id, item.task_id),
            "readiness",
        )
        vehicles = _records(
            self.vehicle_technical_evidence,
            VehicleTechnicalEvidence,
            lambda item: item.vehicle_id,
            "vehicle_technical_evidence",
        )
        vehicle_availability = _records(
            self.vehicle_availability,
            AvailabilityEvidence,
            lambda item: item.subject_id,
            "vehicle_availability",
        )
        routes = _records(
            self.routes,
            RouteEvidence,
            lambda item: (
                item.origin_reference,
                item.origin_fingerprint,
                item.destination_reference,
                item.destination_fingerprint,
                item.transport_mode,
            ),
            "routes",
        )
        _require(
            {item.worker_id for item in workers}
            == {item.subject_id for item in worker_availability},
            "worker_availability_coverage",
        )
        _require(
            {item.vehicle_id for item in vehicles}
            == {item.subject_id for item in vehicle_availability},
            "vehicle_availability_coverage",
        )
        object.__setattr__(self, "task_constraints", constraints)
        object.__setattr__(self, "worker_technical_evidence", workers)
        object.__setattr__(self, "worker_availability", worker_availability)
        object.__setattr__(self, "readiness", readiness)
        object.__setattr__(self, "vehicle_technical_evidence", vehicles)
        object.__setattr__(self, "vehicle_availability", vehicle_availability)
        object.__setattr__(self, "routes", routes)
        digest = sha256_text(snapshot_semantic_json(self))
        object.__setattr__(self, "support_fingerprint", digest)
        object.__setattr__(self, "support_snapshot_id", "m3-feasibility-support-" + digest)


def snapshot_semantic_json(value: FeasibilitySupportSnapshot) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in ("support_fingerprint", "support_snapshot_id")
        }
    )


def serialize_feasibility_support(value: FeasibilitySupportSnapshot) -> str:
    _record(value, FeasibilitySupportSnapshot, "feasibility_support")
    document = json.loads(snapshot_semantic_json(value))
    document["support_fingerprint"] = value.support_fingerprint
    document["support_snapshot_id"] = value.support_snapshot_id
    return canonical_json(document)


def deserialize_feasibility_support(raw: str) -> FeasibilitySupportSnapshot:
    try:
        document = json.loads(raw)
        _require(
            type(document) is dict and canonical_json(document) == raw,
            "canonical_support_document",
        )
        fingerprint = document.pop("support_fingerprint")
        support_snapshot_id = document.pop("support_snapshot_id")
        return restore_feasibility_support(
            canonical_json(document), fingerprint, support_snapshot_id
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        if isinstance(error, M3FeasibilitySupportStorageError):
            raise
        raise M3FeasibilitySupportStorageError(
            "INVALID_FEASIBILITY_SUPPORT", "feasibility_support"
        ) from error


def _availability_from_document(item: dict[str, object]) -> AvailabilityEvidence:
    return AvailabilityEvidence(
        **{
            **item,
            "knowledge": AvailabilityKnowledge(item["knowledge"]),
            "coverage_start": _parse_datetime(item["coverage_start"]),
            "coverage_end": _parse_datetime(item["coverage_end"]),
            "available_windows": tuple(
                AvailabilityWindowEvidence(
                    start_at=datetime.fromisoformat(window["start_at"]),
                    end_at=datetime.fromisoformat(window["end_at"]),
                )
                for window in item["available_windows"]
            ),
        }
    )


def restore_feasibility_support(
    raw_semantic: str,
    fingerprint: str,
    support_snapshot_id: str,
) -> FeasibilitySupportSnapshot:
    try:
        document = json.loads(raw_semantic)
        _require(
            type(document) is dict and canonical_json(document) == raw_semantic,
            "canonical_support_document",
        )
        _require(
            document.pop("schema_version") == FEASIBILITY_SUPPORT_SCHEMA_VERSION,
            "support_schema_version",
        )
        _require(
            document.pop("rule_version") == FEASIBILITY_SUPPORT_RULE_VERSION,
            "support_rule_version",
        )
        constraints = tuple(
            restore_source(
                source_semantic_json_from_document(item),
                item["source_fingerprint"],
                item["source_record_id"],
            )
            for item in document.pop("task_constraints")
        )
        schedule_document = document.pop("schedule")
        schedule = restore_source(
            source_semantic_json_from_document(schedule_document),
            schedule_document["source_fingerprint"],
            schedule_document["source_record_id"],
        )
        worker_technical = tuple(
            WorkerTechnicalEvidence(
                **{
                    **item,
                    "skill_levels": tuple(
                        WorkerSkillLevelEvidence(**level)
                        for level in item["skill_levels"]
                    ),
                }
            )
            for item in document.pop("worker_technical_evidence")
        )
        worker_availability = tuple(
            _availability_from_document(item)
            for item in document.pop("worker_availability")
        )
        readiness = tuple(
            ReadinessEvidence(
                **{
                    **item,
                    "status": ReadinessState(item["status"]),
                    "expected_at": _parse_datetime(item["expected_at"]),
                    "blocker_references": tuple(item["blocker_references"]),
                }
            )
            for item in document.pop("readiness")
        )
        vehicles = tuple(
            VehicleTechnicalEvidence(
                **{**item, "capabilities": tuple(item["capabilities"])}
            )
            for item in document.pop("vehicle_technical_evidence")
        )
        vehicle_availability = tuple(
            _availability_from_document(item)
            for item in document.pop("vehicle_availability")
        )
        routes = tuple(
            RouteEvidence(
                **{**item, "knowledge": RouteKnowledge(item["knowledge"])}
            )
            for item in document.pop("routes")
        )
        value = FeasibilitySupportSnapshot(
            **document,
            task_constraints=constraints,
            schedule=schedule,
            worker_technical_evidence=worker_technical,
            worker_availability=worker_availability,
            readiness=readiness,
            vehicle_technical_evidence=vehicles,
            vehicle_availability=vehicle_availability,
            routes=routes,
        )
        _require(value.support_fingerprint == fingerprint, "support_fingerprint")
        _require(value.support_snapshot_id == support_snapshot_id, "support_snapshot_id")
        _require(snapshot_semantic_json(value) == raw_semantic, "support_canonical_order")
        return value
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ) as error:
        raise M3FeasibilitySupportStorageError(
            "INVALID_FEASIBILITY_SUPPORT", "feasibility_support"
        ) from error


def source_semantic_json_from_document(document: dict[str, object]) -> str:
    return canonical_json(
        {
            key: value
            for key, value in document.items()
            if key not in ("source_fingerprint", "source_record_id")
        }
    )
