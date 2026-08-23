"""Deterministic fail-fast validation for the frozen M8.0 configuration."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from werkcrew_ai.catalog.models import (
    DATA_CLASSIFICATION_DEMO,
    EXPECTED_SKUS,
    ConfigurationIssue,
    M8BusinessConfiguration,
    UnitType,
    VehicleClass,
    VehicleKind,
)


class M8ConfigurationError(RuntimeError):
    def __init__(self, issues: tuple[ConfigurationIssue, ...]) -> None:
        self.issues = issues
        summary = "; ".join(
            f"{item.code}[{item.affected_id}]: {item.explanation}"
            for item in issues
        )
        super().__init__(f"Invalid M8.0 business configuration: {summary}")


def _is_positive_finite_decimal(value: object) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value > Decimal("0")
    )


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    counts = Counter(values)
    return tuple(sorted(value for value, count in counts.items() if count > 1))


def _issue(
    issues: list[ConfigurationIssue],
    code: str,
    affected_id: str,
    explanation: str,
) -> None:
    issues.append(ConfigurationIssue(code, affected_id, explanation))


def _validate_predecessor_cycles(
    config: M8BusinessConfiguration,
    issues: list[ConfigurationIssue],
) -> None:
    graph = {
        profile.sku: tuple(
            predecessor
            for predecessor in profile.predecessor_if_present
            if predecessor in EXPECTED_SKUS
        )
        for profile in config.planning_profiles
        if profile.sku in EXPECTED_SKUS
    }
    visiting: list[str] = []
    visited: set[str] = set()
    reported: set[tuple[str, ...]] = set()

    def visit(sku: str) -> None:
        if sku in visiting:
            start = visiting.index(sku)
            cycle = tuple(visiting[start:] + [sku])
            canonical = tuple(sorted(set(cycle)))
            if canonical not in reported:
                reported.add(canonical)
                _issue(
                    issues,
                    "PREDECESSOR_CYCLE",
                    " -> ".join(cycle),
                    "predecessorIfPresent graph must be acyclic.",
                )
            return
        if sku in visited:
            return
        visiting.append(sku)
        for predecessor in sorted(graph.get(sku, ())):
            visit(predecessor)
        visiting.pop()
        visited.add(sku)

    for sku in sorted(graph):
        visit(sku)


def validate_m8_configuration(
    config: M8BusinessConfiguration,
) -> tuple[ConfigurationIssue, ...]:
    issues: list[ConfigurationIssue] = []
    expected = set(EXPECTED_SKUS)

    versions = {
        "SERVICE_CATALOG_VERSION": config.service_catalog_version,
        "SKU_PLANNING_PROFILE_VERSION": config.sku_planning_profile_version,
        "SKILL_MATRIX_VERSION": config.skill_matrix_version,
        "VEHICLE_POLICY_VERSION": config.vehicle_policy_version,
        "I18N_CATALOG_VERSION": config.i18n_catalog_version,
    }
    for name, value in versions.items():
        if not isinstance(value, str) or not value.strip():
            _issue(
                issues,
                "MISSING_VERSION_IDENTIFIER",
                name,
                "Frozen configuration version identifier is required.",
            )
    if config.data_classification != DATA_CLASSIFICATION_DEMO:
        _issue(
            issues,
            "INVALID_DATA_CLASSIFICATION",
            "configuration",
            "M8.0 frozen data must be explicitly DEMO_SYNTHETIC.",
        )

    catalog_skus = tuple(item.sku for item in config.service_catalog)
    profile_skus = tuple(item.sku for item in config.planning_profiles)
    label_skus = tuple(item.sku for item in config.labels)
    for sku in _duplicates(catalog_skus):
        _issue(issues, "DUPLICATE_CATALOG_SKU", sku, "Service catalog SKU occurs more than once.")
    for sku in _duplicates(profile_skus):
        _issue(issues, "DUPLICATE_SKU_PROFILE", sku, "Planning profile occurs more than once.")
    for sku in _duplicates(label_skus):
        _issue(issues, "DUPLICATE_CATALOG_LABEL", sku, "Catalog label occurs more than once.")

    for collection_name, values in (
        ("catalog", set(catalog_skus)),
        ("planning-profile", set(profile_skus)),
        ("label", set(label_skus)),
    ):
        for sku in sorted(expected - values):
            _issue(issues, "MISSING_SKU", f"{collection_name}:{sku}", "Expected frozen SKU is missing.")
        for sku in sorted(values - expected):
            _issue(issues, "UNKNOWN_SKU", f"{collection_name}:{sku}", "Unknown SKU is not allowed in the frozen catalog.")
    for sku in sorted(expected - set(label_skus)):
        _issue(issues, "MISSING_EN_LABEL", sku, "English catalog label is required.")
        _issue(issues, "MISSING_DE_LABEL", sku, "German catalog label is required.")

    catalog_by_sku = {item.sku: item for item in config.service_catalog}
    for item in config.service_catalog:
        if not isinstance(item.unit, UnitType):
            _issue(issues, "INVALID_CATALOG_UNIT", item.sku, "Catalog unit is not a known UnitType.")

    for profile in config.planning_profiles:
        catalog_item = catalog_by_sku.get(profile.sku)
        if catalog_item is not None and profile.unit != catalog_item.unit:
            _issue(issues, "PROFILE_UNIT_MISMATCH", profile.sku, "Planning profile unit differs from service catalog unit.")
        if not isinstance(profile.unit, UnitType):
            _issue(issues, "INVALID_PROFILE_UNIT", profile.sku, "Planning profile unit is unknown.")
        if not _is_positive_finite_decimal(profile.norm_worker_hours_per_unit):
            _issue(issues, "INVALID_NORM", profile.sku, "normWorkerHoursPerUnit must be a positive finite Decimal.")
        if profile.min_skill_level not in (2, 3):
            _issue(issues, "INVALID_MIN_SKILL_LEVEL", profile.sku, "minSkillLevel must be exactly 2 or 3.")
        if profile.required_skill_key != profile.sku:
            _issue(issues, "REQUIRED_SKILL_KEY_MISMATCH", profile.sku, "requiredSkillKey must equal the exact SKU.")
        if not isinstance(profile.vehicle_class, VehicleClass):
            _issue(issues, "INVALID_VEHICLE_CLASS", profile.sku, "Planning profile vehicleClass is unknown.")
        if type(profile.site_verification_required) is not bool:
            _issue(issues, "INVALID_SITE_VERIFICATION_FLAG", profile.sku, "siteVerificationRequired must be a boolean.")
        for predecessor in profile.predecessor_if_present:
            if predecessor not in expected:
                _issue(issues, "UNKNOWN_PREDECESSOR", f"{profile.sku}:{predecessor}", "predecessorIfPresent references an unknown SKU.")
        if (
            profile.unit in (UnitType.PIECE, UnitType.ROOM)
            and _is_positive_finite_decimal(profile.norm_worker_hours_per_unit)
            and _is_positive_finite_decimal(
                config.chunking_policy.max_scheduled_hours_per_task_day
            )
            and config.chunking_policy.max_scheduled_hours_per_task_day
            // profile.norm_worker_hours_per_unit
            < 1
        ):
            _issue(issues, "CHUNK_CAPACITY_ZERO", profile.sku, "A PIECE/ROOM unit cannot fit in one task day.")

    _validate_predecessor_cycles(config, issues)

    for label in config.labels:
        if not isinstance(label.english, str) or not label.english.strip():
            _issue(issues, "MISSING_EN_LABEL", label.sku, "English catalog label is required.")
        if not isinstance(label.german, str) or not label.german.strip():
            _issue(issues, "MISSING_DE_LABEL", label.sku, "German catalog label is required.")

    worker_ids = tuple(worker.worker_id for worker in config.crew)
    for worker_id in _duplicates(worker_ids):
        _issue(issues, "DUPLICATE_WORKER_ID", worker_id, "Frozen crew stable ID occurs more than once.")
    for worker in config.crew:
        skill_skus = tuple(skill.sku for skill in worker.skills)
        for sku in _duplicates(skill_skus):
            _issue(issues, "DUPLICATE_WORKER_SKILL", f"{worker.worker_id}:{sku}", "Worker has duplicate exact-SKU skill entry.")
        for sku in sorted(expected - set(skill_skus)):
            _issue(issues, "MISSING_WORKER_SKILL", f"{worker.worker_id}:{sku}", "Worker must have an explicit skill value for every SKU.")
        for sku in sorted(set(skill_skus) - expected):
            _issue(issues, "UNKNOWN_WORKER_SKILL", f"{worker.worker_id}:{sku}", "Worker skill references an unknown SKU.")
        for skill in worker.skills:
            if type(skill.level) is not int or not 0 <= skill.level <= 3:
                _issue(issues, "INVALID_SKILL_LEVEL", f"{worker.worker_id}:{skill.sku}", "Skill level must be an integer from 0 through 3.")

    vehicle_ids = tuple(vehicle.vehicle_id for vehicle in config.vehicles)
    for vehicle_id in _duplicates(vehicle_ids):
        _issue(issues, "DUPLICATE_VEHICLE_ID", vehicle_id, "Vehicle stable ID occurs more than once.")
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in config.vehicles}
    workers = set(worker_ids)
    for vehicle in config.vehicles:
        if vehicle.data_classification != DATA_CLASSIFICATION_DEMO:
            _issue(issues, "INVALID_VEHICLE_DATA_CLASSIFICATION", vehicle.vehicle_id, "Vehicle must be marked DEMO_SYNTHETIC.")
        for capability in vehicle.capabilities:
            if not isinstance(capability, VehicleClass) or capability is VehicleClass.NONE:
                _issue(issues, "UNKNOWN_VEHICLE_CAPABILITY", vehicle.vehicle_id, "Vehicle capability contains an unknown or NONE class.")
        if vehicle.kind is VehicleKind.PRIVATE:
            if vehicle.assigned_worker_id not in workers:
                _issue(issues, "INVALID_PRIVATE_VEHICLE_OWNER", vehicle.vehicle_id, "Private vehicle must belong to a frozen crew member.")
            if vehicle.capabilities != (VehicleClass.LIGHT,):
                _issue(issues, "INVALID_PRIVATE_VEHICLE_CAPABILITY", vehicle.vehicle_id, "M8 private vehicles support LIGHT only.")

    for worker in config.crew:
        if worker.private_vehicle_id is None:
            continue
        vehicle = vehicles.get(worker.private_vehicle_id)
        if vehicle is None or vehicle.kind is not VehicleKind.PRIVATE or vehicle.assigned_worker_id != worker.worker_id:
            _issue(issues, "PRIVATE_VEHICLE_BINDING_MISMATCH", worker.worker_id, "Worker private vehicle binding is invalid.")

    expected_preference_classes = {
        VehicleClass.LIGHT,
        VehicleClass.GENERAL_CARGO,
        VehicleClass.LARGE_CARGO,
    }

    def validate_preferences(preferences, *, expected_kind: VehicleKind, prefix: str) -> None:
        classes = tuple(item.vehicle_class for item in preferences)
        for value in classes:
            if not isinstance(value, VehicleClass) or value not in expected_preference_classes:
                _issue(issues, "INVALID_VEHICLE_CLASS", f"{prefix}:{value}", "Vehicle preference class is unknown.")
        for value in expected_preference_classes - set(classes):
            _issue(issues, "MISSING_VEHICLE_PREFERENCE", f"{prefix}:{value.value}", "Vehicle preference is required for this class.")
        for value in _duplicates(tuple(str(item) for item in classes)):
            _issue(issues, "DUPLICATE_VEHICLE_PREFERENCE", f"{prefix}:{value}", "Vehicle class preference occurs more than once.")
        for preference in preferences:
            if not isinstance(preference.vehicle_class, VehicleClass):
                continue
            for vehicle_id in preference.vehicle_ids:
                vehicle = vehicles.get(vehicle_id)
                if vehicle is None:
                    _issue(issues, "UNKNOWN_PREFERRED_VEHICLE", f"{prefix}:{vehicle_id}", "Vehicle preference references an unknown vehicle.")
                elif vehicle.kind is not expected_kind:
                    _issue(issues, "INVALID_PREFERRED_VEHICLE_KIND", f"{prefix}:{vehicle_id}", "Vehicle preference uses the wrong vehicle kind.")
                elif preference.vehicle_class not in vehicle.capabilities:
                    _issue(issues, "INCAPABLE_PREFERRED_VEHICLE", f"{preference.vehicle_class.value}:{vehicle_id}", "Preferred vehicle cannot satisfy the requested class.")

    validate_preferences(config.vehicle_policy.execution_preferences, expected_kind=VehicleKind.COMPANY, prefix="execution")
    validate_preferences(config.vehicle_policy.rental_preferences, expected_kind=VehicleKind.RENTAL, prefix="rental")
    if config.vehicle_policy.unavailable_company_fallback_order != (
        VehicleKind.PRIVATE,
        VehicleKind.RENTAL,
    ):
        _issue(
            issues,
            "INVALID_VEHICLE_FALLBACK_ORDER",
            "vehicle-policy",
            "Company-unavailable fallback must be assigned private vehicle, then rental.",
        )
    for vehicle_id in config.vehicle_policy.site_visit_preference:
        vehicle = vehicles.get(vehicle_id)
        if vehicle is None:
            _issue(issues, "UNKNOWN_PREFERRED_VEHICLE", f"site-visit:{vehicle_id}", "Site-visit preference references an unknown vehicle.")
        elif vehicle.kind is not VehicleKind.COMPANY or VehicleClass.LIGHT not in vehicle.capabilities:
            _issue(issues, "INCAPABLE_PREFERRED_VEHICLE", f"site-visit:{vehicle_id}", "Site-visit vehicle must be a LIGHT-capable company vehicle.")

    if not _is_positive_finite_decimal(config.vehicle_policy.rental_daily_net_rate):
        _issue(issues, "INVALID_RENTAL_DAILY_RATE", "vehicle-policy", "Rental daily rate must be a positive finite Decimal.")
    if config.vehicle_policy.rental_currency != "EUR":
        _issue(issues, "INVALID_RENTAL_CURRENCY", "vehicle-policy", "Frozen rental currency must be EUR.")

    if (
        not _is_positive_finite_decimal(config.chunking_policy.max_scheduled_hours_per_task_day)
        or config.chunking_policy.max_scheduled_hours_per_task_day != Decimal("8.00")
    ):
        _issue(issues, "INVALID_CHUNKING_LIMIT", "chunking-policy", "maxScheduledHoursPerTaskDay must be Decimal 8.00.")

    aliases = tuple(item.unit for item in config.unit_aliases)
    for unit in UnitType:
        if aliases.count(unit) != 1:
            _issue(issues, "INVALID_UNIT_ALIAS", unit.value, "Every UnitType requires exactly one display alias.")

    return tuple(sorted(issues, key=lambda item: (item.code, item.affected_id, item.explanation)))


def assert_valid_m8_configuration(config: M8BusinessConfiguration) -> None:
    issues = validate_m8_configuration(config)
    if issues:
        raise M8ConfigurationError(issues)
