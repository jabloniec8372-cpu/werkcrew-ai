from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from werkcrew_ai.catalog import (
    DATA_CLASSIFICATION_DEMO,
    EXPECTED_SKUS,
    I18N_CATALOG_VERSION,
    M8_CONFIGURATION,
    SERVICE_CATALOG_VERSION,
    SKILL_MATRIX_VERSION,
    SKU_PLANNING_PROFILE_VERSION,
    VEHICLE_POLICY_VERSION,
    SkillRating,
    UnitType,
    VehicleClass,
    VehicleKind,
    VehiclePreference,
    assert_valid_m8_configuration,
    is_worker_schedulable,
    validate_m8_configuration,
)


def _codes(config) -> set[str]:
    return {item.code for item in validate_m8_configuration(config)}


def test_canonical_configuration_is_complete_versioned_and_valid() -> None:
    assert SERVICE_CATALOG_VERSION == "m8-service-catalog-v1"
    assert SKU_PLANNING_PROFILE_VERSION == "m8-sku-planning-v1"
    assert SKILL_MATRIX_VERSION == "m8-demo-skills-v1"
    assert VEHICLE_POLICY_VERSION == "m8-vehicle-policy-v1"
    assert I18N_CATALOG_VERSION == "m8-catalog-i18n-v1"
    assert M8_CONFIGURATION.data_classification == DATA_CLASSIFICATION_DEMO
    assert len(EXPECTED_SKUS) == 24
    assert len(M8_CONFIGURATION.service_catalog) == 24
    assert len(M8_CONFIGURATION.planning_profiles) == 24
    assert len(M8_CONFIGURATION.labels) == 24
    assert len(M8_CONFIGURATION.crew) == 6
    assert sum(len(worker.skills) for worker in M8_CONFIGURATION.crew) == 144
    assert tuple(item.sku for item in M8_CONFIGURATION.service_catalog) == EXPECTED_SKUS
    assert tuple(item.sku for item in M8_CONFIGURATION.planning_profiles) == EXPECTED_SKUS
    assert validate_m8_configuration(M8_CONFIGURATION) == ()
    assert_valid_m8_configuration(M8_CONFIGURATION)


EXPECTED_PROFILES = {
    "painting.wash": (UnitType.SQUARE_METER, "0.08", 2, VehicleClass.NONE, False, ()),
    "painting.strip": (UnitType.SQUARE_METER, "0.40", 2, VehicleClass.LIGHT, False, ()),
    "painting.mold": (UnitType.SQUARE_METER, "0.50", 3, VehicleClass.LIGHT, True, ()),
    "painting.fill": (UnitType.SQUARE_METER, "0.20", 2, VehicleClass.NONE, False, ()),
    "painting.prime": (UnitType.SQUARE_METER, "0.10", 2, VehicleClass.NONE, False, ("painting.wash", "painting.strip", "painting.mold", "painting.fill", "plaster.skim", "plaster.repair")),
    "painting.coat1": (UnitType.SQUARE_METER, "0.12", 2, VehicleClass.NONE, False, ("painting.prime",)),
    "painting.coat2": (UnitType.SQUARE_METER, "0.22", 2, VehicleClass.NONE, False, ("painting.coat1",)),
    "furniture.shift": (UnitType.ROOM, "1.50", 2, VehicleClass.NONE, False, ()),
    "plaster.skim": (UnitType.SQUARE_METER, "0.40", 2, VehicleClass.LIGHT, False, ()),
    "plaster.repair": (UnitType.SQUARE_METER, "0.60", 2, VehicleClass.LIGHT, True, ()),
    "tile.floor": (UnitType.SQUARE_METER, "0.80", 2, VehicleClass.GENERAL_CARGO, False, ("waterproof.bath",)),
    "tile.wall": (UnitType.SQUARE_METER, "1.00", 2, VehicleClass.GENERAL_CARGO, False, ("waterproof.bath",)),
    "waterproof.bath": (UnitType.SQUARE_METER, "0.40", 3, VehicleClass.LIGHT, True, ()),
    "drywall.partition": (UnitType.SQUARE_METER, "0.70", 2, VehicleClass.GENERAL_CARGO, False, ()),
    "drywall.ceiling": (UnitType.SQUARE_METER, "0.90", 2, VehicleClass.GENERAL_CARGO, False, ()),
    "demo.interior": (UnitType.WORKER_HOUR, "1.00", 2, VehicleClass.GENERAL_CARGO, True, ()),
    "sanitary.install": (UnitType.WORKER_HOUR, "1.00", 3, VehicleClass.GENERAL_CARGO, True, ()),
    "white.toilet": (UnitType.PIECE, "3.00", 2, VehicleClass.LIGHT, False, ("sanitary.install",)),
    "white.sink": (UnitType.PIECE, "2.00", 2, VehicleClass.LIGHT, False, ("sanitary.install",)),
    "white.shower": (UnitType.PIECE, "4.50", 2, VehicleClass.GENERAL_CARGO, False, ("sanitary.install",)),
    "white.faucet": (UnitType.PIECE, "1.00", 2, VehicleClass.NONE, False, ()),
    "mount.curtain": (UnitType.PIECE, "0.75", 2, VehicleClass.NONE, False, ()),
    "mount.small": (UnitType.WORKER_HOUR, "1.00", 2, VehicleClass.NONE, False, ()),
    "furniture.carry": (UnitType.ROOM, "2.00", 2, VehicleClass.LIGHT, False, ()),
}


def test_all_24_profiles_match_frozen_oracle() -> None:
    actual = {
        profile.sku: (
            profile.unit,
            str(profile.norm_worker_hours_per_unit),
            profile.min_skill_level,
            profile.vehicle_class,
            profile.site_verification_required,
            profile.predecessor_if_present,
        )
        for profile in M8_CONFIGURATION.planning_profiles
    }
    assert actual == EXPECTED_PROFILES
    assert all(profile.required_skill_key == profile.sku for profile in M8_CONFIGURATION.planning_profiles)
    assert {
        item.sku: item.unit for item in M8_CONFIGURATION.service_catalog
    } == {sku: values[0] for sku, values in EXPECTED_PROFILES.items()}


EXPECTED_SKILL_ROWS = {
    "painting.wash": (3, 0, 3, 2, 2, 0),
    "painting.strip": (3, 0, 3, 1, 0, 0),
    "painting.mold": (3, 0, 3, 1, 0, 0),
    "painting.fill": (3, 0, 3, 2, 2, 0),
    "painting.prime": (3, 0, 3, 2, 2, 0),
    "painting.coat1": (3, 0, 3, 1, 2, 0),
    "painting.coat2": (3, 0, 3, 1, 2, 0),
    "furniture.shift": (2, 1, 3, 2, 2, 1),
    "plaster.skim": (3, 0, 2, 3, 0, 0),
    "plaster.repair": (3, 0, 2, 3, 0, 0),
    "tile.floor": (2, 3, 0, 0, 0, 3),
    "tile.wall": (2, 3, 0, 0, 0, 3),
    "waterproof.bath": (2, 3, 0, 0, 0, 3),
    "drywall.partition": (2, 0, 0, 3, 0, 0),
    "drywall.ceiling": (2, 0, 0, 3, 0, 0),
    "demo.interior": (2, 1, 0, 3, 0, 0),
    "sanitary.install": (2, 3, 0, 0, 0, 2),
    "white.toilet": (2, 2, 0, 0, 0, 3),
    "white.sink": (2, 2, 0, 0, 0, 3),
    "white.shower": (2, 2, 0, 0, 0, 3),
    "white.faucet": (2, 2, 0, 0, 0, 3),
    "mount.curtain": (2, 1, 2, 2, 2, 2),
    "mount.small": (2, 1, 2, 2, 1, 2),
    "furniture.carry": (2, 1, 3, 2, 2, 1),
}


def test_exact_six_by_twenty_four_skill_matrix_and_worker_flags() -> None:
    assert tuple(worker.name for worker in M8_CONFIGURATION.crew) == (
        "Stefan Müller",
        "Peter Berger",
        "Thomas Becker",
        "Andreas Hoffmann",
        "Jonas Klein",
        "Anna Fischer",
    )
    flags = {
        worker.name: (
            worker.role.value,
            worker.license_b,
            worker.private_vehicle_id,
            worker.saturday,
            worker.overnight,
            worker.owner_last_resort,
        )
        for worker in M8_CONFIGURATION.crew
    }
    assert flags == {
        "Stefan Müller": ("OWNER", True, "private-stefan-passat", True, True, True),
        "Peter Berger": ("WORKER", True, None, False, True, False),
        "Thomas Becker": ("WORKER", True, "private-thomas-astra", True, True, False),
        "Andreas Hoffmann": ("WORKER", True, "private-andreas-golf", True, False, False),
        "Jonas Klein": ("WORKER", False, None, True, True, False),
        "Anna Fischer": ("WORKER", True, "private-anna-fabia", False, True, False),
    }
    for sku, levels in EXPECTED_SKILL_ROWS.items():
        assert tuple(
            next(skill.level for skill in worker.skills if skill.sku == sku)
            for worker in M8_CONFIGURATION.crew
        ) == levels


def test_skill_level_one_is_informational_not_schedulable() -> None:
    peter = next(worker for worker in M8_CONFIGURATION.crew if worker.name == "Peter Berger")
    shift = M8_CONFIGURATION.profile("furniture.shift")
    tile = M8_CONFIGURATION.profile("tile.floor")
    assert is_worker_schedulable(peter, shift) is False
    assert is_worker_schedulable(peter, tile) is True


def test_vehicle_capabilities_and_frozen_preferences() -> None:
    vehicles = {item.vehicle_id: item for item in M8_CONFIGURATION.vehicles}
    assert len(vehicles) == 10
    assert vehicles["company-caddy-maxi"].capabilities == (VehicleClass.LIGHT, VehicleClass.GENERAL_CARGO)
    assert (vehicles["company-caddy-maxi"].plate, vehicles["company-caddy-maxi"].seats, vehicles["company-caddy-maxi"].cargo_approx_cubic_meters) == ("TF-WK 110", 3, Decimal("3.2"))
    assert vehicles["company-sprinter"].capabilities == (VehicleClass.LIGHT, VehicleClass.GENERAL_CARGO, VehicleClass.LARGE_CARGO)
    assert (vehicles["company-sprinter"].plate, vehicles["company-sprinter"].seats, vehicles["company-sprinter"].cargo_approx_cubic_meters) == ("TF-WK 220", 3, Decimal("11"))
    assert vehicles["company-octavia"].capabilities == (VehicleClass.LIGHT,)
    assert (vehicles["company-octavia"].plate, vehicles["company-octavia"].seats, vehicles["company-octavia"].cargo_approx_cubic_meters) == ("TF-WK 330", 5, Decimal("0.55"))
    private = tuple(item for item in vehicles.values() if item.kind.value == "PRIVATE")
    assert len(private) == 4
    assert all(item.capabilities == (VehicleClass.LIGHT,) for item in private)
    preferences = {
        item.vehicle_class: item.vehicle_ids
        for item in M8_CONFIGURATION.vehicle_policy.execution_preferences
    }
    assert preferences == {
        VehicleClass.LIGHT: ("company-caddy-maxi", "company-octavia", "company-sprinter"),
        VehicleClass.GENERAL_CARGO: ("company-sprinter", "company-caddy-maxi"),
        VehicleClass.LARGE_CARGO: ("company-sprinter",),
    }
    assert M8_CONFIGURATION.vehicle_policy.site_visit_preference == (
        "company-octavia",
        "company-caddy-maxi",
    )
    assert M8_CONFIGURATION.vehicle_policy.unavailable_company_fallback_order == (
        VehicleKind.PRIVATE,
        VehicleKind.RENTAL,
    )
    rental = {
        item.vehicle_class: item.vehicle_ids
        for item in M8_CONFIGURATION.vehicle_policy.rental_preferences
    }
    assert rental == {
        VehicleClass.LIGHT: ("rental-light",),
        VehicleClass.GENERAL_CARGO: ("rental-cargo-van",),
        VehicleClass.LARGE_CARGO: ("rental-large-van",),
    }
    assert M8_CONFIGURATION.vehicle_policy.rental_daily_net_rate == Decimal("95.00")
    assert M8_CONFIGURATION.vehicle_policy.rental_currency == "EUR"


def test_labels_and_units_are_complete_and_frozen() -> None:
    assert {
        item.sku: (item.english, item.german) for item in M8_CONFIGURATION.labels
    } == {
        "painting.wash": ("Wall washing", "Wände waschen"),
        "painting.strip": ("Old coating removal", "Alte Beschichtung entfernen"),
        "painting.mold": ("Mold removal and treatment", "Schimmel entfernen / behandeln"),
        "painting.fill": ("Local filling / patching", "Teilspachtelung"),
        "painting.prime": ("Priming", "Grundierung"),
        "painting.coat1": ("Painting — 1 coat", "Anstrich 1 Lage"),
        "painting.coat2": ("Painting — 2 coats", "Anstrich 2 Lagen"),
        "furniture.shift": ("Furniture moving within room", "Möbel verrücken"),
        "plaster.skim": ("Full-area skimming / Q2–Q3 filling", "Vollflächige Spachtelung Q2–Q3"),
        "plaster.repair": ("Local plaster repair", "Lokale Putzreparatur"),
        "tile.floor": ("Floor tiling", "Bodenfliesen verlegen"),
        "tile.wall": ("Wall tiling", "Wandfliesen verlegen"),
        "waterproof.bath": ("Wet-area waterproofing", "Abdichtung im Nassbereich"),
        "drywall.partition": ("Drywall partition", "Trockenbau-Trennwand"),
        "drywall.ceiling": ("Drywall ceiling", "Trockenbaudecke"),
        "demo.interior": ("Interior demolition", "Innenabbruch"),
        "sanitary.install": ("Sanitary installation", "Sanitärinstallation"),
        "white.toilet": ("Toilet installation", "WC-Montage"),
        "white.sink": ("Washbasin installation", "Waschbeckenmontage"),
        "white.shower": ("Shower / bathtub installation", "Dusch-/Badewannenmontage"),
        "white.faucet": ("Faucet installation", "Armaturenmontage"),
        "mount.curtain": ("Curtain rod installation", "Gardinenstange montieren"),
        "mount.small": ("Small mounting work", "Kleine Montagearbeiten"),
        "furniture.carry": ("Furniture removal from room", "Möbel aus dem Raum tragen"),
    }
    assert {item.unit: (item.short_label, item.english_label) for item in M8_CONFIGURATION.unit_aliases} == {
        UnitType.SQUARE_METER: ("m²", "m²"),
        UnitType.WORKER_HOUR: ("rbh", "worker-hour"),
        UnitType.PIECE: ("szt.", "piece"),
        UnitType.ROOM: ("pom.", "room"),
    }
    assert M8_CONFIGURATION.plaster_skim_limitation_en == "Q2 and Q3 are not priced or scheduled separately in the M8 demo."
    assert M8_CONFIGURATION.plaster_skim_limitation_de == "Q2 und Q3 werden in der M8-Demo nicht getrennt kalkuliert oder geplant."


def _replace_profile(sku: str, **changes):
    return tuple(
        replace(profile, **changes) if profile.sku == sku else profile
        for profile in M8_CONFIGURATION.planning_profiles
    )


@pytest.mark.parametrize(
    ("broken", "expected_code"),
    (
        (replace(M8_CONFIGURATION, service_catalog=M8_CONFIGURATION.service_catalog[:-1]), "MISSING_SKU"),
        (replace(M8_CONFIGURATION, planning_profiles=M8_CONFIGURATION.planning_profiles + (M8_CONFIGURATION.planning_profiles[0],)), "DUPLICATE_SKU_PROFILE"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", unit=UnitType.PIECE)), "PROFILE_UNIT_MISMATCH"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", norm_worker_hours_per_unit=Decimal("0"))), "INVALID_NORM"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", min_skill_level=1)), "INVALID_MIN_SKILL_LEVEL"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", required_skill_key="painting.fill")), "REQUIRED_SKILL_KEY_MISMATCH"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", vehicle_class="HEAVY")), "INVALID_VEHICLE_CLASS"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", site_verification_required=None)), "INVALID_SITE_VERIFICATION_FLAG"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.wash", predecessor_if_present=("unknown.sku",))), "UNKNOWN_PREDECESSOR"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("painting.coat1", predecessor_if_present=("painting.coat2",))), "PREDECESSOR_CYCLE"),
        (replace(M8_CONFIGURATION, planning_profiles=_replace_profile("white.shower", norm_worker_hours_per_unit=Decimal("8.01"))), "CHUNK_CAPACITY_ZERO"),
        (replace(M8_CONFIGURATION, labels=(replace(M8_CONFIGURATION.labels[0], english=""),) + M8_CONFIGURATION.labels[1:]), "MISSING_EN_LABEL"),
        (replace(M8_CONFIGURATION, labels=(replace(M8_CONFIGURATION.labels[0], german=""),) + M8_CONFIGURATION.labels[1:]), "MISSING_DE_LABEL"),
        (replace(M8_CONFIGURATION, service_catalog=M8_CONFIGURATION.service_catalog + (replace(M8_CONFIGURATION.service_catalog[0], sku="unknown.sku"),)), "UNKNOWN_SKU"),
        (replace(M8_CONFIGURATION, service_catalog_version=""), "MISSING_VERSION_IDENTIFIER"),
    ),
)
def test_validator_rejects_broken_catalog_and_profile_configuration(broken, expected_code: str) -> None:
    assert expected_code in _codes(broken)


def test_validator_rejects_invalid_or_missing_worker_skill() -> None:
    worker = M8_CONFIGURATION.crew[0]
    invalid_worker = replace(
        worker,
        skills=(replace(worker.skills[0], level=4),) + worker.skills[1:],
    )
    broken = replace(M8_CONFIGURATION, crew=(invalid_worker,) + M8_CONFIGURATION.crew[1:])
    assert "INVALID_SKILL_LEVEL" in _codes(broken)

    missing_worker = replace(worker, skills=worker.skills[:-1])
    broken = replace(M8_CONFIGURATION, crew=(missing_worker,) + M8_CONFIGURATION.crew[1:])
    assert "MISSING_WORKER_SKILL" in _codes(broken)

    duplicate_id = replace(M8_CONFIGURATION.crew[1], worker_id=worker.worker_id)
    broken = replace(
        M8_CONFIGURATION,
        crew=(worker, duplicate_id) + M8_CONFIGURATION.crew[2:],
    )
    assert "DUPLICATE_WORKER_ID" in _codes(broken)


def test_validator_rejects_bad_vehicle_preference_and_capability() -> None:
    policy = M8_CONFIGURATION.vehicle_policy
    bad_large = tuple(
        VehiclePreference(item.vehicle_class, ("company-octavia",))
        if item.vehicle_class is VehicleClass.LARGE_CARGO
        else item
        for item in policy.execution_preferences
    )
    broken = replace(
        M8_CONFIGURATION,
        vehicle_policy=replace(policy, execution_preferences=bad_large),
    )
    assert "INCAPABLE_PREFERRED_VEHICLE" in _codes(broken)

    broken = replace(
        M8_CONFIGURATION,
        vehicle_policy=replace(
            policy,
            unavailable_company_fallback_order=(
                VehicleKind.RENTAL,
                VehicleKind.PRIVATE,
            ),
        ),
    )
    assert "INVALID_VEHICLE_FALLBACK_ORDER" in _codes(broken)

    private_index = next(
        index
        for index, vehicle in enumerate(M8_CONFIGURATION.vehicles)
        if vehicle.vehicle_id == "private-stefan-passat"
    )
    invalid_private = replace(
        M8_CONFIGURATION.vehicles[private_index],
        capabilities=(VehicleClass.GENERAL_CARGO,),
    )
    vehicles = list(M8_CONFIGURATION.vehicles)
    vehicles[private_index] = invalid_private
    assert "INVALID_PRIVATE_VEHICLE_CAPABILITY" in _codes(
        replace(M8_CONFIGURATION, vehicles=tuple(vehicles))
    )

    invalid_rental = replace(
        next(
            vehicle
            for vehicle in M8_CONFIGURATION.vehicles
            if vehicle.vehicle_id == "rental-light"
        ),
        capabilities=("HEAVY",),
    )
    vehicles = tuple(
        invalid_rental if item.vehicle_id == "rental-light" else item
        for item in M8_CONFIGURATION.vehicles
    )
    assert "UNKNOWN_VEHICLE_CAPABILITY" in _codes(
        replace(M8_CONFIGURATION, vehicles=vehicles)
    )


def test_configuration_error_contains_code_id_and_explanation() -> None:
    broken = replace(M8_CONFIGURATION, service_catalog_version="")
    with pytest.raises(RuntimeError) as exc_info:
        assert_valid_m8_configuration(broken)
    message = str(exc_info.value)
    assert "MISSING_VERSION_IDENTIFIER" in message
    assert "SERVICE_CATALOG_VERSION" in message
    assert "identifier is required" in message
