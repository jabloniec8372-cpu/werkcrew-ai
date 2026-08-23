"""Canonical frozen M8.0 DEMO_SYNTHETIC business-rule data."""

from __future__ import annotations

from decimal import Decimal

from werkcrew_ai.catalog.models import (
    DATA_CLASSIFICATION_DEMO,
    CatalogLabel,
    ChunkingPolicy,
    CrewMember,
    CrewRole,
    M8BusinessConfiguration,
    ServiceCatalogItem,
    SkillRating,
    SkuPlanningProfile,
    UnitDisplayAlias,
    UnitType,
    VehicleClass,
    VehicleKind,
    VehiclePolicy,
    VehiclePreference,
    VehicleProfile,
)


SERVICE_CATALOG_VERSION = "m8-service-catalog-v1"
SKU_PLANNING_PROFILE_VERSION = "m8-sku-planning-v1"
SKILL_MATRIX_VERSION = "m8-demo-skills-v1"
VEHICLE_POLICY_VERSION = "m8-vehicle-policy-v1"
I18N_CATALOG_VERSION = "m8-catalog-i18n-v1"


SERVICE_CATALOG = (
    ServiceCatalogItem("painting.wash", UnitType.SQUARE_METER),
    ServiceCatalogItem("painting.strip", UnitType.SQUARE_METER),
    ServiceCatalogItem("painting.mold", UnitType.SQUARE_METER),
    ServiceCatalogItem("painting.fill", UnitType.SQUARE_METER),
    ServiceCatalogItem("painting.prime", UnitType.SQUARE_METER),
    ServiceCatalogItem("painting.coat1", UnitType.SQUARE_METER),
    ServiceCatalogItem("painting.coat2", UnitType.SQUARE_METER),
    ServiceCatalogItem("furniture.shift", UnitType.ROOM),
    ServiceCatalogItem("plaster.skim", UnitType.SQUARE_METER),
    ServiceCatalogItem("plaster.repair", UnitType.SQUARE_METER),
    ServiceCatalogItem("tile.floor", UnitType.SQUARE_METER),
    ServiceCatalogItem("tile.wall", UnitType.SQUARE_METER),
    ServiceCatalogItem("waterproof.bath", UnitType.SQUARE_METER),
    ServiceCatalogItem("drywall.partition", UnitType.SQUARE_METER),
    ServiceCatalogItem("drywall.ceiling", UnitType.SQUARE_METER),
    ServiceCatalogItem("demo.interior", UnitType.WORKER_HOUR),
    ServiceCatalogItem("sanitary.install", UnitType.WORKER_HOUR),
    ServiceCatalogItem("white.toilet", UnitType.PIECE),
    ServiceCatalogItem("white.sink", UnitType.PIECE),
    ServiceCatalogItem("white.shower", UnitType.PIECE),
    ServiceCatalogItem("white.faucet", UnitType.PIECE),
    ServiceCatalogItem("mount.curtain", UnitType.PIECE),
    ServiceCatalogItem("mount.small", UnitType.WORKER_HOUR),
    ServiceCatalogItem("furniture.carry", UnitType.ROOM),
)


def _profile(
    sku: str,
    unit: UnitType,
    norm: str,
    min_skill: int,
    vehicle: VehicleClass,
    verify: bool,
    predecessors: tuple[str, ...] = (),
) -> SkuPlanningProfile:
    return SkuPlanningProfile(
        sku=sku,
        unit=unit,
        norm_worker_hours_per_unit=Decimal(norm),
        required_skill_key=sku,
        min_skill_level=min_skill,
        vehicle_class=vehicle,
        site_verification_required=verify,
        predecessor_if_present=predecessors,
    )


SKU_PLANNING_PROFILES = (
    _profile("painting.wash", UnitType.SQUARE_METER, "0.08", 2, VehicleClass.NONE, False),
    _profile("painting.strip", UnitType.SQUARE_METER, "0.40", 2, VehicleClass.LIGHT, False),
    _profile("painting.mold", UnitType.SQUARE_METER, "0.50", 3, VehicleClass.LIGHT, True),
    _profile("painting.fill", UnitType.SQUARE_METER, "0.20", 2, VehicleClass.NONE, False),
    _profile(
        "painting.prime",
        UnitType.SQUARE_METER,
        "0.10",
        2,
        VehicleClass.NONE,
        False,
        (
            "painting.wash",
            "painting.strip",
            "painting.mold",
            "painting.fill",
            "plaster.skim",
            "plaster.repair",
        ),
    ),
    _profile(
        "painting.coat1",
        UnitType.SQUARE_METER,
        "0.12",
        2,
        VehicleClass.NONE,
        False,
        ("painting.prime",),
    ),
    _profile(
        "painting.coat2",
        UnitType.SQUARE_METER,
        "0.22",
        2,
        VehicleClass.NONE,
        False,
        ("painting.coat1",),
    ),
    _profile("furniture.shift", UnitType.ROOM, "1.50", 2, VehicleClass.NONE, False),
    _profile("plaster.skim", UnitType.SQUARE_METER, "0.40", 2, VehicleClass.LIGHT, False),
    _profile("plaster.repair", UnitType.SQUARE_METER, "0.60", 2, VehicleClass.LIGHT, True),
    _profile(
        "tile.floor",
        UnitType.SQUARE_METER,
        "0.80",
        2,
        VehicleClass.GENERAL_CARGO,
        False,
        ("waterproof.bath",),
    ),
    _profile(
        "tile.wall",
        UnitType.SQUARE_METER,
        "1.00",
        2,
        VehicleClass.GENERAL_CARGO,
        False,
        ("waterproof.bath",),
    ),
    _profile("waterproof.bath", UnitType.SQUARE_METER, "0.40", 3, VehicleClass.LIGHT, True),
    _profile("drywall.partition", UnitType.SQUARE_METER, "0.70", 2, VehicleClass.GENERAL_CARGO, False),
    _profile("drywall.ceiling", UnitType.SQUARE_METER, "0.90", 2, VehicleClass.GENERAL_CARGO, False),
    _profile("demo.interior", UnitType.WORKER_HOUR, "1.00", 2, VehicleClass.GENERAL_CARGO, True),
    _profile("sanitary.install", UnitType.WORKER_HOUR, "1.00", 3, VehicleClass.GENERAL_CARGO, True),
    _profile(
        "white.toilet",
        UnitType.PIECE,
        "3.00",
        2,
        VehicleClass.LIGHT,
        False,
        ("sanitary.install",),
    ),
    _profile(
        "white.sink",
        UnitType.PIECE,
        "2.00",
        2,
        VehicleClass.LIGHT,
        False,
        ("sanitary.install",),
    ),
    _profile(
        "white.shower",
        UnitType.PIECE,
        "4.50",
        2,
        VehicleClass.GENERAL_CARGO,
        False,
        ("sanitary.install",),
    ),
    _profile("white.faucet", UnitType.PIECE, "1.00", 2, VehicleClass.NONE, False),
    _profile("mount.curtain", UnitType.PIECE, "0.75", 2, VehicleClass.NONE, False),
    _profile("mount.small", UnitType.WORKER_HOUR, "1.00", 2, VehicleClass.NONE, False),
    _profile("furniture.carry", UnitType.ROOM, "2.00", 2, VehicleClass.LIGHT, False),
)


_WORKERS = (
    ("stefan-mueller", "Stefan Müller", CrewRole.OWNER, True, "private-stefan-passat", True, True, True),
    ("peter-berger", "Peter Berger", CrewRole.WORKER, True, None, False, True, False),
    ("thomas-becker", "Thomas Becker", CrewRole.WORKER, True, "private-thomas-astra", True, True, False),
    ("andreas-hoffmann", "Andreas Hoffmann", CrewRole.WORKER, True, "private-andreas-golf", True, False, False),
    ("jonas-klein", "Jonas Klein", CrewRole.WORKER, False, None, True, True, False),
    ("anna-fischer", "Anna Fischer", CrewRole.WORKER, True, "private-anna-fabia", False, True, False),
)


_SKILL_ROWS = (
    ("painting.wash", 3, 0, 3, 2, 2, 0),
    ("painting.strip", 3, 0, 3, 1, 0, 0),
    ("painting.mold", 3, 0, 3, 1, 0, 0),
    ("painting.fill", 3, 0, 3, 2, 2, 0),
    ("painting.prime", 3, 0, 3, 2, 2, 0),
    ("painting.coat1", 3, 0, 3, 1, 2, 0),
    ("painting.coat2", 3, 0, 3, 1, 2, 0),
    ("furniture.shift", 2, 1, 3, 2, 2, 1),
    ("plaster.skim", 3, 0, 2, 3, 0, 0),
    ("plaster.repair", 3, 0, 2, 3, 0, 0),
    ("tile.floor", 2, 3, 0, 0, 0, 3),
    ("tile.wall", 2, 3, 0, 0, 0, 3),
    ("waterproof.bath", 2, 3, 0, 0, 0, 3),
    ("drywall.partition", 2, 0, 0, 3, 0, 0),
    ("drywall.ceiling", 2, 0, 0, 3, 0, 0),
    ("demo.interior", 2, 1, 0, 3, 0, 0),
    ("sanitary.install", 2, 3, 0, 0, 0, 2),
    ("white.toilet", 2, 2, 0, 0, 0, 3),
    ("white.sink", 2, 2, 0, 0, 0, 3),
    ("white.shower", 2, 2, 0, 0, 0, 3),
    ("white.faucet", 2, 2, 0, 0, 0, 3),
    ("mount.curtain", 2, 1, 2, 2, 2, 2),
    ("mount.small", 2, 1, 2, 2, 1, 2),
    ("furniture.carry", 2, 1, 3, 2, 2, 1),
)


def _crew() -> tuple[CrewMember, ...]:
    members: list[CrewMember] = []
    for worker_index, worker in enumerate(_WORKERS):
        worker_id, name, role, license_b, private_vehicle, saturday, overnight, last_resort = worker
        skills = tuple(
            SkillRating(sku=row[0], level=row[worker_index + 1])
            for row in _SKILL_ROWS
        )
        members.append(
            CrewMember(
                worker_id=worker_id,
                name=name,
                role=role,
                license_b=license_b,
                private_vehicle_id=private_vehicle,
                saturday=saturday,
                overnight=overnight,
                owner_last_resort=last_resort,
                skills=skills,
            )
        )
    return tuple(members)


CREW = _crew()


VEHICLES = (
    VehicleProfile(
        "company-caddy-maxi",
        "VW Caddy Maxi",
        VehicleKind.COMPANY,
        (VehicleClass.LIGHT, VehicleClass.GENERAL_CARGO),
        plate="TF-WK 110",
        seats=3,
        cargo_approx_cubic_meters=Decimal("3.2"),
    ),
    VehicleProfile(
        "company-sprinter",
        "Mercedes Sprinter",
        VehicleKind.COMPANY,
        (VehicleClass.LIGHT, VehicleClass.GENERAL_CARGO, VehicleClass.LARGE_CARGO),
        plate="TF-WK 220",
        seats=3,
        cargo_approx_cubic_meters=Decimal("11"),
    ),
    VehicleProfile(
        "company-octavia",
        "Škoda Octavia",
        VehicleKind.COMPANY,
        (VehicleClass.LIGHT,),
        plate="TF-WK 330",
        seats=5,
        cargo_approx_cubic_meters=Decimal("0.55"),
    ),
    VehicleProfile("private-stefan-passat", "Passat", VehicleKind.PRIVATE, (VehicleClass.LIGHT,), assigned_worker_id="stefan-mueller"),
    VehicleProfile("private-thomas-astra", "Astra", VehicleKind.PRIVATE, (VehicleClass.LIGHT,), assigned_worker_id="thomas-becker"),
    VehicleProfile("private-andreas-golf", "Golf", VehicleKind.PRIVATE, (VehicleClass.LIGHT,), assigned_worker_id="andreas-hoffmann"),
    VehicleProfile("private-anna-fabia", "Fabia", VehicleKind.PRIVATE, (VehicleClass.LIGHT,), assigned_worker_id="anna-fischer"),
    VehicleProfile("rental-light", "Rental light vehicle", VehicleKind.RENTAL, (VehicleClass.LIGHT,)),
    VehicleProfile("rental-cargo-van", "Rental cargo van", VehicleKind.RENTAL, (VehicleClass.LIGHT, VehicleClass.GENERAL_CARGO)),
    VehicleProfile("rental-large-van", "Rental large van", VehicleKind.RENTAL, (VehicleClass.LIGHT, VehicleClass.GENERAL_CARGO, VehicleClass.LARGE_CARGO)),
)


VEHICLE_POLICY = VehiclePolicy(
    execution_preferences=(
        VehiclePreference(VehicleClass.LIGHT, ("company-caddy-maxi", "company-octavia", "company-sprinter")),
        VehiclePreference(VehicleClass.GENERAL_CARGO, ("company-sprinter", "company-caddy-maxi")),
        VehiclePreference(VehicleClass.LARGE_CARGO, ("company-sprinter",)),
    ),
    site_visit_preference=("company-octavia", "company-caddy-maxi"),
    unavailable_company_fallback_order=(VehicleKind.PRIVATE, VehicleKind.RENTAL),
    rental_preferences=(
        VehiclePreference(VehicleClass.LIGHT, ("rental-light",)),
        VehiclePreference(VehicleClass.GENERAL_CARGO, ("rental-cargo-van",)),
        VehiclePreference(VehicleClass.LARGE_CARGO, ("rental-large-van",)),
    ),
    rental_daily_net_rate=Decimal("95.00"),
    rental_currency="EUR",
)


LABELS = (
    CatalogLabel("painting.wash", "Wall washing", "Wände waschen"),
    CatalogLabel("painting.strip", "Old coating removal", "Alte Beschichtung entfernen"),
    CatalogLabel("painting.mold", "Mold removal and treatment", "Schimmel entfernen / behandeln"),
    CatalogLabel("painting.fill", "Local filling / patching", "Teilspachtelung"),
    CatalogLabel("painting.prime", "Priming", "Grundierung"),
    CatalogLabel("painting.coat1", "Painting — 1 coat", "Anstrich 1 Lage"),
    CatalogLabel("painting.coat2", "Painting — 2 coats", "Anstrich 2 Lagen"),
    CatalogLabel("furniture.shift", "Furniture moving within room", "Möbel verrücken"),
    CatalogLabel("plaster.skim", "Full-area skimming / Q2–Q3 filling", "Vollflächige Spachtelung Q2–Q3"),
    CatalogLabel("plaster.repair", "Local plaster repair", "Lokale Putzreparatur"),
    CatalogLabel("tile.floor", "Floor tiling", "Bodenfliesen verlegen"),
    CatalogLabel("tile.wall", "Wall tiling", "Wandfliesen verlegen"),
    CatalogLabel("waterproof.bath", "Wet-area waterproofing", "Abdichtung im Nassbereich"),
    CatalogLabel("drywall.partition", "Drywall partition", "Trockenbau-Trennwand"),
    CatalogLabel("drywall.ceiling", "Drywall ceiling", "Trockenbaudecke"),
    CatalogLabel("demo.interior", "Interior demolition", "Innenabbruch"),
    CatalogLabel("sanitary.install", "Sanitary installation", "Sanitärinstallation"),
    CatalogLabel("white.toilet", "Toilet installation", "WC-Montage"),
    CatalogLabel("white.sink", "Washbasin installation", "Waschbeckenmontage"),
    CatalogLabel("white.shower", "Shower / bathtub installation", "Dusch-/Badewannenmontage"),
    CatalogLabel("white.faucet", "Faucet installation", "Armaturenmontage"),
    CatalogLabel("mount.curtain", "Curtain rod installation", "Gardinenstange montieren"),
    CatalogLabel("mount.small", "Small mounting work", "Kleine Montagearbeiten"),
    CatalogLabel("furniture.carry", "Furniture removal from room", "Möbel aus dem Raum tragen"),
)


UNIT_ALIASES = (
    UnitDisplayAlias(UnitType.SQUARE_METER, "m²", "m²"),
    UnitDisplayAlias(UnitType.WORKER_HOUR, "rbh", "worker-hour"),
    UnitDisplayAlias(UnitType.PIECE, "szt.", "piece"),
    UnitDisplayAlias(UnitType.ROOM, "pom.", "room"),
)


M8_CONFIGURATION = M8BusinessConfiguration(
    service_catalog_version=SERVICE_CATALOG_VERSION,
    sku_planning_profile_version=SKU_PLANNING_PROFILE_VERSION,
    skill_matrix_version=SKILL_MATRIX_VERSION,
    vehicle_policy_version=VEHICLE_POLICY_VERSION,
    i18n_catalog_version=I18N_CATALOG_VERSION,
    data_classification=DATA_CLASSIFICATION_DEMO,
    service_catalog=SERVICE_CATALOG,
    planning_profiles=SKU_PLANNING_PROFILES,
    crew=CREW,
    vehicles=VEHICLES,
    vehicle_policy=VEHICLE_POLICY,
    labels=LABELS,
    unit_aliases=UNIT_ALIASES,
    chunking_policy=ChunkingPolicy(Decimal("8.00")),
    plaster_skim_limitation_en=(
        "Q2 and Q3 are not priced or scheduled separately in the M8 demo."
    ),
    plaster_skim_limitation_de=(
        "Q2 und Q3 werden in der M8-Demo nicht getrennt kalkuliert oder geplant."
    ),
)


# Import-time validation is deliberate: an invalid frozen configuration must not start.
from werkcrew_ai.catalog.validation import assert_valid_m8_configuration  # noqa: E402

assert_valid_m8_configuration(M8_CONFIGURATION)
