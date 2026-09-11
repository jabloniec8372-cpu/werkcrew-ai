"""Authoritative Gen2 M5 internal scheduled-labor-cost support contracts.

This module freezes source and support evidence only.  It deliberately contains
no duration-by-rate arithmetic, candidate comparison, ranking, authority, or
apply behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum

from werkcrew_ai.field.serialization import (
    canonical_json,
    deserialize_worker_registry,
    sha256_text,
)


CONFIGURATION_ID = "gen2-internal-labor-cost-rates-v1"
SOURCE_CLASSIFICATION = "GEN2_SYNTHETIC_COMPANY_CONFIGURATION"
ACCESS_CLASSIFICATION = "INTERNAL_ONLY"
RATE_SEMANTICS = "modeled_internal_labor_cost_rate"
CURRENCY = "EUR"
RATE_SCALE = Decimal("0.01")
FINAL_MONEY_SCALE = Decimal("0.01")
ROUNDING_MODE = "ROUND_HALF_UP"
AUTOMATIC_FX = "FORBIDDEN"
COMPONENT_SCOPE = "SCHEDULED_LABOR_ONLY"
RULE_SCHEMA_VERSION = "m5-internal-labor-cost-rule-v1"
RULE_VERSION = "m5-internal-scheduled-labor-cost-v1"
RATE_SOURCE_SCHEMA_VERSION = "m5-internal-labor-rate-source-v1"
SOURCE_CAPTURE_SCHEMA_VERSION = "m5-internal-cost-source-capture-v1"
SUBJECT_SCHEMA_VERSION = "m5-internal-labor-cost-subject-v1"
SELECTION_SCHEMA_VERSION = "m5-internal-labor-rate-selection-v1"
SUPPORT_SCHEMA_VERSION = "m5-internal-cost-support-v1"
M2_WORKER_REGISTRY_SCHEMA_VERSION = "m2-worker-registry-v1"

V1_INTERNAL_LABOR_RATES: tuple[tuple[str, Decimal], ...] = (
    ("andreas-hoffmann", Decimal("35.00")),
    ("anna-fischer", Decimal("37.00")),
    ("jonas-klein", Decimal("31.00")),
    ("peter-berger", Decimal("38.00")),
    ("stefan-mueller", Decimal("45.00")),
    ("thomas-becker", Decimal("34.00")),
)


class M5InternalCostSupportError(ValueError):
    def __init__(self, code: str, field_name: str) -> None:
        self.code, self.field_name = code, field_name
        super().__init__(f"{code}: {field_name}")


class M5InternalCostSupportValidationError(M5InternalCostSupportError):
    pass


class M5InternalCostSupportConflictError(M5InternalCostSupportError):
    pass


class M5InternalCostSupportStorageError(M5InternalCostSupportError):
    pass


class LaborCostSubjectScope(StrEnum):
    BASELINE = "BASELINE"
    CANDIDATE = "CANDIDATE"


class RateSelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    NO_SOURCE = "NO_SOURCE"


class NoSourceReason(StrEnum):
    NO_RATE_SOURCE = "NO_RATE_SOURCE"
    RATE_INTERVAL_NOT_COVERED = "RATE_INTERVAL_NOT_COVERED"
    AMBIGUOUS_RATE_SOURCE = "AMBIGUOUS_RATE_SOURCE"
    MULTI_REVISION_COVERAGE_UNSUPPORTED = "MULTI_REVISION_COVERAGE_UNSUPPORTED"


def _require(condition: bool, name: str) -> None:
    if not condition:
        raise M5InternalCostSupportValidationError("INVALID_VALUE", name)


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


def _digest(value: str, name: str) -> None:
    _require(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        name,
    )


def _revision(value: int, name: str, *, positive: bool = False) -> None:
    _require(
        type(value) is int
        and (value > 0 if positive else value >= 0)
        and value <= 9223372036854775807,
        name,
    )


def _instant(value: datetime, name: str) -> datetime:
    _require(
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        name,
    )
    normalized = value.astimezone(timezone.utc)
    _require(normalized.microsecond == 0, name)
    return normalized


def canonical_rate(value: Decimal) -> Decimal:
    """Validate and normalize a canonical non-negative two-decimal hourly rate."""

    _require(type(value) is Decimal and value.is_finite(), "rate_amount")
    _require(value >= 0, "rate_amount")
    normalized = value.quantize(RATE_SCALE, rounding=ROUND_HALF_UP)
    _require(value == normalized, "rate_amount")
    return normalized


def parse_rate_amount(value: str) -> Decimal:
    """Parse a locale-independent rate string; commas and excess scale fail closed."""

    _require(type(value) is str and bool(value) and value == value.strip(), "rate_amount")
    _require("," not in value, "rate_amount")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise M5InternalCostSupportValidationError(
            "INVALID_VALUE", "rate_amount"
        ) from error
    return canonical_rate(parsed)


def format_money(value: Decimal) -> str:
    return format(canonical_rate(value), ".2f")


def _primitive(value):
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is Decimal:
        return format(value, ".2f")
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is tuple:
        return [_primitive(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    return value


def _semantic_json(value, omitted: tuple[str, ...]) -> str:
    return canonical_json(
        {
            item.name: _primitive(getattr(value, item.name))
            for item in fields(value)
            if item.name not in omitted
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalLaborCostRule:
    rule_revision: int = field(default=0, init=False)
    rate_semantics: str = field(default=RATE_SEMANTICS, init=False)
    currency: str = field(default=CURRENCY, init=False)
    calculation_representation: str = field(default="Decimal", init=False)
    rate_scale: Decimal = field(default=RATE_SCALE, init=False)
    final_money_scale: Decimal = field(default=FINAL_MONEY_SCALE, init=False)
    rounding: str = field(default=ROUNDING_MODE, init=False)
    automatic_fx: str = field(default=AUTOMATIC_FX, init=False)
    component_scope: str = field(default=COMPONENT_SCOPE, init=False)
    schema_version: str = field(default=RULE_SCHEMA_VERSION, init=False)
    rule_version: str = field(default=RULE_VERSION, init=False)
    rule_fingerprint: str = field(init=False)
    rule_id: str = field(init=False)

    def __post_init__(self) -> None:
        digest = sha256_text(internal_cost_rule_semantic_json(self))
        object.__setattr__(self, "rule_fingerprint", digest)
        object.__setattr__(self, "rule_id", "m5-internal-cost-rule-" + digest)


def internal_cost_rule_semantic_json(value: InternalLaborCostRule) -> str:
    return _semantic_json(value, ("rule_fingerprint", "rule_id"))


def current_internal_labor_cost_rule() -> InternalLaborCostRule:
    return InternalLaborCostRule()


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerInternalCostRateRevision:
    worker_id: str
    rate_amount: Decimal
    source_revision: int
    previous_source_record_id: str | None
    effective_from: datetime | None
    effective_until: datetime | None
    worker_registry_revision: int
    worker_registry_fingerprint: str
    canonical_worker_registry_json: str
    rule_id: str
    rule_fingerprint: str
    configuration_id: str = field(default=CONFIGURATION_ID, init=False)
    source_classification: str = field(default=SOURCE_CLASSIFICATION, init=False)
    access_classification: str = field(default=ACCESS_CLASSIFICATION, init=False)
    rate_semantics: str = field(default=RATE_SEMANTICS, init=False)
    currency: str = field(default=CURRENCY, init=False)
    cost_rule_version: str = field(default=RULE_VERSION, init=False)
    schema_version: str = field(default=RATE_SOURCE_SCHEMA_VERSION, init=False)
    source_fingerprint: str = field(init=False)
    source_record_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.worker_id, "worker_id")
        object.__setattr__(self, "rate_amount", canonical_rate(self.rate_amount))
        _revision(self.source_revision, "source_revision")
        _require(
            (self.source_revision == 0) == (self.previous_source_record_id is None),
            "previous_source_record_id",
        )
        if self.previous_source_record_id is not None:
            _identity(self.previous_source_record_id, "previous_source_record_id")
        start = None if self.effective_from is None else _instant(self.effective_from, "effective_from")
        end = None if self.effective_until is None else _instant(self.effective_until, "effective_until")
        _require(self.source_revision == 0 or start is not None, "effective_from")
        _require(end is None or (start is not None and end > start), "effective_until")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "effective_until", end)
        _revision(self.worker_registry_revision, "worker_registry_revision")
        _digest(self.worker_registry_fingerprint, "worker_registry_fingerprint")
        _require(
            type(self.canonical_worker_registry_json) is str
            and sha256_text(self.canonical_worker_registry_json)
            == self.worker_registry_fingerprint,
            "canonical_worker_registry_json",
        )
        try:
            registry = deserialize_worker_registry(self.canonical_worker_registry_json)
        except (TypeError, ValueError) as error:
            raise M5InternalCostSupportValidationError(
                "INVALID_VALUE", "canonical_worker_registry_json"
            ) from error
        _require(
            registry.registry_revision == self.worker_registry_revision
            and self.worker_id in registry.worker_ids,
            "worker_registry_binding",
        )
        _identity(self.rule_id, "rule_id")
        _digest(self.rule_fingerprint, "rule_fingerprint")
        _require(self.rule_id == "m5-internal-cost-rule-" + self.rule_fingerprint, "rule_id")
        digest = sha256_text(rate_source_semantic_json(self))
        object.__setattr__(self, "source_fingerprint", digest)
        object.__setattr__(self, "source_record_id", "m5-internal-labor-rate-source-" + digest)


def rate_source_semantic_json(value: WorkerInternalCostRateRevision) -> str:
    return _semantic_json(value, ("source_fingerprint", "source_record_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalCostSourceCapture:
    ledger_generation: int
    source_record_id: str
    source_fingerprint: str
    worker_id: str
    source_revision: int
    configuration_id: str = field(default=CONFIGURATION_ID, init=False)
    source_classification: str = field(default=SOURCE_CLASSIFICATION, init=False)
    capture_kind: str = field(default="INTERNAL_LABOR_RATE_SOURCE", init=False)
    schema_version: str = field(default=SOURCE_CAPTURE_SCHEMA_VERSION, init=False)
    capture_fingerprint: str = field(init=False)
    capture_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.ledger_generation, "ledger_generation", positive=True)
        _identity(self.source_record_id, "source_record_id")
        _digest(self.source_fingerprint, "source_fingerprint")
        _require(
            self.source_record_id == "m5-internal-labor-rate-source-" + self.source_fingerprint,
            "source_record_id",
        )
        _identity(self.worker_id, "worker_id")
        _revision(self.source_revision, "source_revision")
        digest = sha256_text(source_capture_semantic_json(self))
        object.__setattr__(self, "capture_fingerprint", digest)
        object.__setattr__(self, "capture_id", "m5-internal-cost-source-capture-" + digest)


def source_capture_semantic_json(value: InternalCostSourceCapture) -> str:
    return _semantic_json(value, ("capture_fingerprint", "capture_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalCostCandidateBinding:
    position: int
    candidate_id: str
    candidate_fingerprint: str

    def __post_init__(self) -> None:
        _revision(self.position, "candidate_position")
        _identity(self.candidate_id, "candidate_id")
        _digest(self.candidate_fingerprint, "candidate_fingerprint")
        _require(self.candidate_id == "m3-candidate-" + self.candidate_fingerprint, "candidate_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalLaborCostSubject:
    candidate_position: int
    candidate_id: str
    candidate_fingerprint: str
    scope: LaborCostSubjectScope
    commitment_id: str
    worker_id: str
    interval_start: datetime
    interval_end: datetime
    schema_version: str = field(default=SUBJECT_SCHEMA_VERSION, init=False)
    subject_fingerprint: str = field(init=False)
    subject_id: str = field(init=False)

    def __post_init__(self) -> None:
        _revision(self.candidate_position, "candidate_position")
        _identity(self.candidate_id, "candidate_id")
        _digest(self.candidate_fingerprint, "candidate_fingerprint")
        _require(self.candidate_id == "m3-candidate-" + self.candidate_fingerprint, "candidate_id")
        _require(type(self.scope) is LaborCostSubjectScope, "scope")
        _identity(self.commitment_id, "commitment_id")
        _identity(self.worker_id, "worker_id")
        start = _instant(self.interval_start, "interval_start")
        end = _instant(self.interval_end, "interval_end")
        _require(end > start, "placement_interval")
        object.__setattr__(self, "interval_start", start)
        object.__setattr__(self, "interval_end", end)
        digest = sha256_text(labor_cost_subject_semantic_json(self))
        object.__setattr__(self, "subject_fingerprint", digest)
        object.__setattr__(self, "subject_id", "m5-internal-labor-cost-subject-" + digest)


def labor_cost_subject_semantic_json(value: InternalLaborCostSubject) -> str:
    return _semantic_json(value, ("subject_fingerprint", "subject_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalLaborRateSelection:
    subject_id: str
    subject_fingerprint: str
    status: RateSelectionStatus
    source_record_id: str | None
    source_fingerprint: str | None
    source_revision: int | None
    source_capture_id: str | None
    source_capture_fingerprint: str | None
    source_capture_generation: int | None
    no_source_reason: NoSourceReason | None
    schema_version: str = field(default=SELECTION_SCHEMA_VERSION, init=False)
    selection_fingerprint: str = field(init=False)
    selection_id: str = field(init=False)

    def __post_init__(self) -> None:
        _identity(self.subject_id, "subject_id")
        _digest(self.subject_fingerprint, "subject_fingerprint")
        _require(
            self.subject_id == "m5-internal-labor-cost-subject-" + self.subject_fingerprint,
            "subject_id",
        )
        _require(type(self.status) is RateSelectionStatus, "selection_status")
        selected = (
            self.source_record_id,
            self.source_fingerprint,
            self.source_revision,
            self.source_capture_id,
            self.source_capture_fingerprint,
            self.source_capture_generation,
        )
        if self.status is RateSelectionStatus.SELECTED:
            _require(all(value is not None for value in selected), "selected_source")
            _require(self.no_source_reason is None, "no_source_reason")
            _identity(self.source_record_id, "source_record_id")
            _digest(self.source_fingerprint, "source_fingerprint")
            _revision(self.source_revision, "source_revision")
            _identity(self.source_capture_id, "source_capture_id")
            _digest(self.source_capture_fingerprint, "source_capture_fingerprint")
            _revision(self.source_capture_generation, "source_capture_generation", positive=True)
            _require(
                self.source_record_id == "m5-internal-labor-rate-source-" + self.source_fingerprint,
                "source_record_id",
            )
            _require(
                self.source_capture_id == "m5-internal-cost-source-capture-" + self.source_capture_fingerprint,
                "source_capture_id",
            )
        else:
            _require(all(value is None for value in selected), "selected_source")
            _require(type(self.no_source_reason) is NoSourceReason, "no_source_reason")
        digest = sha256_text(rate_selection_semantic_json(self))
        object.__setattr__(self, "selection_fingerprint", digest)
        object.__setattr__(self, "selection_id", "m5-internal-labor-rate-selection-" + digest)


def rate_selection_semantic_json(value: InternalLaborRateSelection) -> str:
    return _semantic_json(value, ("selection_fingerprint", "selection_id"))


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalCostSupportCut:
    evaluation_input_id: str
    evaluation_input_fingerprint: str
    feasibility_support_snapshot_id: str
    feasibility_support_snapshot_fingerprint: str
    company_plan_id: str
    base_plan_revision: int
    base_plan_revision_id: str
    base_plan_revision_fingerprint: str
    m3_result_id: str
    m3_result_fingerprint: str
    candidate_bindings: tuple[InternalCostCandidateBinding, ...]
    rule_id: str
    rule_revision: int
    rule_fingerprint: str
    source_cut_generation: int
    subjects: tuple[InternalLaborCostSubject, ...]
    selections: tuple[InternalLaborRateSelection, ...]
    configuration_id: str = field(default=CONFIGURATION_ID, init=False)
    source_classification: str = field(default=SOURCE_CLASSIFICATION, init=False)
    access_classification: str = field(default=ACCESS_CLASSIFICATION, init=False)
    schema_version: str = field(default=SUPPORT_SCHEMA_VERSION, init=False)
    support_fingerprint: str = field(init=False)
    support_id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "evaluation_input_id",
            "feasibility_support_snapshot_id",
            "company_plan_id",
            "base_plan_revision_id",
            "m3_result_id",
            "rule_id",
        ):
            _identity(getattr(self, name), name)
        for name in (
            "evaluation_input_fingerprint",
            "feasibility_support_snapshot_fingerprint",
            "base_plan_revision_fingerprint",
            "m3_result_fingerprint",
            "rule_fingerprint",
        ):
            _digest(getattr(self, name), name)
        _require(self.evaluation_input_id == "m3-evaluation-input-" + self.evaluation_input_fingerprint, "evaluation_input_id")
        _require(self.feasibility_support_snapshot_id == "m3-feasibility-support-" + self.feasibility_support_snapshot_fingerprint, "feasibility_support_snapshot_id")
        _require(self.base_plan_revision_id == "m3-plan-revision-" + self.base_plan_revision_fingerprint, "base_plan_revision_id")
        _require(self.m3_result_id == "m3-feasibility-result-" + self.m3_result_fingerprint, "m3_result_id")
        _require(self.rule_id == "m5-internal-cost-rule-" + self.rule_fingerprint, "rule_id")
        _revision(self.base_plan_revision, "base_plan_revision")
        _revision(self.rule_revision, "rule_revision")
        _revision(self.source_cut_generation, "source_cut_generation")

        candidates = tuple(self.candidate_bindings)
        _require(all(type(item) is InternalCostCandidateBinding for item in candidates), "candidate_bindings")
        _require(tuple(item.position for item in candidates) == tuple(range(len(candidates))), "candidate_bindings")
        _require(len({item.candidate_id for item in candidates}) == len(candidates), "candidate_bindings")
        subjects = tuple(self.subjects)
        _require(all(type(item) is InternalLaborCostSubject for item in subjects), "subjects")
        subject_keys = tuple(
            (item.candidate_position, item.scope.value, item.commitment_id, item.worker_id, item.interval_start, item.interval_end)
            for item in subjects
        )
        _require(len(subject_keys) == len(set(subject_keys)), "subjects")
        subjects = tuple(sorted(subjects, key=lambda item: (
            item.candidate_position, item.scope.value, item.commitment_id,
            item.worker_id, item.interval_start, item.interval_end,
        )))
        candidate_by_position = {item.position: item for item in candidates}
        _require(all(
            item.candidate_position in candidate_by_position
            and (item.candidate_id, item.candidate_fingerprint)
            == (candidate_by_position[item.candidate_position].candidate_id,
                candidate_by_position[item.candidate_position].candidate_fingerprint)
            for item in subjects
        ), "subject_candidate_binding")
        selections = tuple(self.selections)
        _require(all(type(item) is InternalLaborRateSelection for item in selections), "selections")
        selections = tuple(sorted(selections, key=lambda item: item.subject_id))
        _require(
            tuple(sorted(item.subject_id for item in subjects))
            == tuple(item.subject_id for item in selections),
            "selection_subject_coverage",
        )
        _require(all(
            item.source_capture_generation is None
            or item.source_capture_generation <= self.source_cut_generation
            for item in selections
        ), "source_capture_generation")
        object.__setattr__(self, "candidate_bindings", candidates)
        object.__setattr__(self, "subjects", subjects)
        object.__setattr__(self, "selections", selections)
        digest = sha256_text(internal_cost_support_semantic_json(self))
        object.__setattr__(self, "support_fingerprint", digest)
        object.__setattr__(self, "support_id", "m5-internal-cost-support-" + digest)


def internal_cost_support_semantic_json(value: InternalCostSupportCut) -> str:
    return _semantic_json(value, ("support_fingerprint", "support_id"))


def _restore_document(raw: str, expected_schema: str) -> dict:
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise M5InternalCostSupportStorageError("INVALID_CANONICAL_JSON", "canonical_semantic_json") from error
    if type(document) is not dict or canonical_json(document) != raw or document.get("schema_version") != expected_schema:
        raise M5InternalCostSupportStorageError("INVALID_CANONICAL_JSON", "canonical_semantic_json")
    return document


def restore_internal_cost_rule(raw: str, fingerprint: str, rule_id: str) -> InternalLaborCostRule:
    document = _restore_document(raw, RULE_SCHEMA_VERSION)
    value = current_internal_labor_cost_rule()
    if document != json.loads(internal_cost_rule_semantic_json(value)) or value.rule_fingerprint != fingerprint or value.rule_id != rule_id:
        raise M5InternalCostSupportStorageError("INVALID_RULE_REVISION", "internal_cost_rule")
    return value


def restore_rate_source(raw: str, fingerprint: str, source_id: str) -> WorkerInternalCostRateRevision:
    document = _restore_document(raw, RATE_SOURCE_SCHEMA_VERSION)
    try:
        expected_constants = {
            "configuration_id": CONFIGURATION_ID,
            "source_classification": SOURCE_CLASSIFICATION,
            "access_classification": ACCESS_CLASSIFICATION,
            "rate_semantics": RATE_SEMANTICS,
            "currency": CURRENCY,
            "cost_rule_version": RULE_VERSION,
            "schema_version": RATE_SOURCE_SCHEMA_VERSION,
        }
        if any(document.pop(name) != expected for name, expected in expected_constants.items()):
            raise ValueError
        rate_amount = parse_rate_amount(document.pop("rate_amount"))
        effective_from_raw = document.pop("effective_from")
        effective_until_raw = document.pop("effective_until")
        value = WorkerInternalCostRateRevision(
            **document,
            rate_amount=rate_amount,
            effective_from=(None if effective_from_raw is None else datetime.fromisoformat(effective_from_raw)),
            effective_until=(None if effective_until_raw is None else datetime.fromisoformat(effective_until_raw)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise M5InternalCostSupportStorageError("INVALID_RATE_SOURCE", "rate_source") from error
    if value.source_fingerprint != fingerprint or value.source_record_id != source_id or rate_source_semantic_json(value) != raw:
        raise M5InternalCostSupportStorageError("INVALID_RATE_SOURCE", "rate_source")
    return value


def restore_source_capture(raw: str, fingerprint: str, capture_id: str) -> InternalCostSourceCapture:
    document = _restore_document(raw, SOURCE_CAPTURE_SCHEMA_VERSION)
    try:
        for name, expected in (
            ("configuration_id", CONFIGURATION_ID),
            ("source_classification", SOURCE_CLASSIFICATION),
            ("capture_kind", "INTERNAL_LABOR_RATE_SOURCE"),
            ("schema_version", SOURCE_CAPTURE_SCHEMA_VERSION),
        ):
            if document.pop(name) != expected:
                raise ValueError
        value = InternalCostSourceCapture(**document)
    except (KeyError, TypeError, ValueError) as error:
        raise M5InternalCostSupportStorageError("INVALID_SOURCE_CAPTURE", "source_capture") from error
    if value.capture_fingerprint != fingerprint or value.capture_id != capture_id or source_capture_semantic_json(value) != raw:
        raise M5InternalCostSupportStorageError("INVALID_SOURCE_CAPTURE", "source_capture")
    return value


def restore_internal_cost_support(raw: str, fingerprint: str, support_id: str) -> InternalCostSupportCut:
    document = _restore_document(raw, SUPPORT_SCHEMA_VERSION)
    try:
        for name, expected in (
            ("configuration_id", CONFIGURATION_ID),
            ("source_classification", SOURCE_CLASSIFICATION),
            ("access_classification", ACCESS_CLASSIFICATION),
            ("schema_version", SUPPORT_SCHEMA_VERSION),
        ):
            if document.pop(name) != expected:
                raise ValueError
        candidates = tuple(InternalCostCandidateBinding(**item) for item in document.pop("candidate_bindings"))
        subjects = tuple(
            _restore_subject(item) for item in document.pop("subjects")
        )
        selections = tuple(
            _restore_selection(item) for item in document.pop("selections")
        )
        value = InternalCostSupportCut(
            **document,
            candidate_bindings=candidates,
            subjects=subjects,
            selections=selections,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise M5InternalCostSupportStorageError("INVALID_SUPPORT_CUT", "internal_cost_support") from error
    if value.support_fingerprint != fingerprint or value.support_id != support_id or internal_cost_support_semantic_json(value) != raw:
        raise M5InternalCostSupportStorageError("INVALID_SUPPORT_CUT", "internal_cost_support")
    return value


def _restore_subject(item: dict) -> InternalLaborCostSubject:
    values = dict(item)
    values.pop("schema_version")
    fingerprint = values.pop("subject_fingerprint")
    subject_id = values.pop("subject_id")
    values["scope"] = LaborCostSubjectScope(values["scope"])
    values["interval_start"] = datetime.fromisoformat(values["interval_start"])
    values["interval_end"] = datetime.fromisoformat(values["interval_end"])
    value = InternalLaborCostSubject(**values)
    if value.subject_fingerprint != fingerprint or value.subject_id != subject_id:
        raise ValueError("subject identity mismatch")
    return value


def _restore_selection(item: dict) -> InternalLaborRateSelection:
    values = dict(item)
    values.pop("schema_version")
    fingerprint = values.pop("selection_fingerprint")
    selection_id = values.pop("selection_id")
    values["status"] = RateSelectionStatus(values["status"])
    values["no_source_reason"] = (
        None
        if values["no_source_reason"] is None
        else NoSourceReason(values["no_source_reason"])
    )
    value = InternalLaborRateSelection(**values)
    if value.selection_fingerprint != fingerprint or value.selection_id != selection_id:
        raise ValueError("selection identity mismatch")
    return value


__all__ = [
    "ACCESS_CLASSIFICATION",
    "AUTOMATIC_FX",
    "COMPONENT_SCOPE",
    "CONFIGURATION_ID",
    "CURRENCY",
    "FINAL_MONEY_SCALE",
    "InternalCostCandidateBinding",
    "InternalCostSourceCapture",
    "InternalCostSupportCut",
    "InternalLaborCostRule",
    "InternalLaborCostSubject",
    "InternalLaborRateSelection",
    "LaborCostSubjectScope",
    "M5InternalCostSupportConflictError",
    "M5InternalCostSupportStorageError",
    "M5InternalCostSupportValidationError",
    "NoSourceReason",
    "RATE_SCALE",
    "RATE_SEMANTICS",
    "ROUNDING_MODE",
    "RULE_VERSION",
    "RateSelectionStatus",
    "SOURCE_CLASSIFICATION",
    "V1_INTERNAL_LABOR_RATES",
    "WorkerInternalCostRateRevision",
    "canonical_rate",
    "current_internal_labor_cost_rule",
    "format_money",
    "internal_cost_rule_semantic_json",
    "internal_cost_support_semantic_json",
    "labor_cost_subject_semantic_json",
    "parse_rate_amount",
    "rate_selection_semantic_json",
    "rate_source_semantic_json",
    "restore_internal_cost_rule",
    "restore_internal_cost_support",
    "restore_rate_source",
    "restore_source_capture",
    "source_capture_semantic_json",
]
