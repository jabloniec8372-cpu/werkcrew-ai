"""Typed, immutable contracts for the frozen M8.0 business configuration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


DATA_CLASSIFICATION_DEMO = "DEMO_SYNTHETIC"

EXPECTED_SKUS = (
    "painting.wash",
    "painting.strip",
    "painting.mold",
    "painting.fill",
    "painting.prime",
    "painting.coat1",
    "painting.coat2",
    "furniture.shift",
    "plaster.skim",
    "plaster.repair",
    "tile.floor",
    "tile.wall",
    "waterproof.bath",
    "drywall.partition",
    "drywall.ceiling",
    "demo.interior",
    "sanitary.install",
    "white.toilet",
    "white.sink",
    "white.shower",
    "white.faucet",
    "mount.curtain",
    "mount.small",
    "furniture.carry",
)


class UnitType(StrEnum):
    SQUARE_METER = "SQUARE_METER"
    WORKER_HOUR = "WORKER_HOUR"
    PIECE = "PIECE"
    ROOM = "ROOM"


class VehicleClass(StrEnum):
    NONE = "NONE"
    LIGHT = "LIGHT"
    GENERAL_CARGO = "GENERAL_CARGO"
    LARGE_CARGO = "LARGE_CARGO"


class CrewRole(StrEnum):
    OWNER = "OWNER"
    WORKER = "WORKER"


class VehicleKind(StrEnum):
    COMPANY = "COMPANY"
    PRIVATE = "PRIVATE"
    RENTAL = "RENTAL"


@dataclass(frozen=True, slots=True)
class UnitDisplayAlias:
    unit: UnitType
    short_label: str
    english_label: str


@dataclass(frozen=True, slots=True)
class ServiceCatalogItem:
    sku: str
    unit: UnitType


@dataclass(frozen=True, slots=True)
class SkuPlanningProfile:
    sku: str
    unit: UnitType
    norm_worker_hours_per_unit: Decimal
    required_skill_key: str
    min_skill_level: int
    vehicle_class: VehicleClass
    site_verification_required: bool
    predecessor_if_present: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillRating:
    sku: str
    level: int


@dataclass(frozen=True, slots=True)
class CrewMember:
    worker_id: str
    name: str
    role: CrewRole
    license_b: bool
    private_vehicle_id: str | None
    saturday: bool
    overnight: bool
    owner_last_resort: bool
    skills: tuple[SkillRating, ...]


@dataclass(frozen=True, slots=True)
class VehicleProfile:
    vehicle_id: str
    name: str
    kind: VehicleKind
    capabilities: tuple[VehicleClass, ...]
    plate: str | None = None
    seats: int | None = None
    cargo_approx_cubic_meters: Decimal | None = None
    assigned_worker_id: str | None = None
    data_classification: str = DATA_CLASSIFICATION_DEMO


@dataclass(frozen=True, slots=True)
class VehiclePreference:
    vehicle_class: VehicleClass
    vehicle_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VehiclePolicy:
    execution_preferences: tuple[VehiclePreference, ...]
    site_visit_preference: tuple[str, ...]
    unavailable_company_fallback_order: tuple[VehicleKind, ...]
    rental_preferences: tuple[VehiclePreference, ...]
    rental_daily_net_rate: Decimal
    rental_currency: str


@dataclass(frozen=True, slots=True)
class CatalogLabel:
    sku: str
    english: str
    german: str


@dataclass(frozen=True, slots=True)
class ChunkingPolicy:
    max_scheduled_hours_per_task_day: Decimal


@dataclass(frozen=True, slots=True)
class M8BusinessConfiguration:
    service_catalog_version: str
    sku_planning_profile_version: str
    skill_matrix_version: str
    vehicle_policy_version: str
    i18n_catalog_version: str
    data_classification: str
    service_catalog: tuple[ServiceCatalogItem, ...]
    planning_profiles: tuple[SkuPlanningProfile, ...]
    crew: tuple[CrewMember, ...]
    vehicles: tuple[VehicleProfile, ...]
    vehicle_policy: VehiclePolicy
    labels: tuple[CatalogLabel, ...]
    unit_aliases: tuple[UnitDisplayAlias, ...]
    chunking_policy: ChunkingPolicy
    plaster_skim_limitation_en: str
    plaster_skim_limitation_de: str

    def profile(self, sku: str) -> SkuPlanningProfile:
        matches = tuple(item for item in self.planning_profiles if item.sku == sku)
        if len(matches) != 1:
            raise KeyError(f"Expected exactly one planning profile for {sku}")
        return matches[0]

    def catalog_item(self, sku: str) -> ServiceCatalogItem:
        matches = tuple(item for item in self.service_catalog if item.sku == sku)
        if len(matches) != 1:
            raise KeyError(f"Expected exactly one service catalog item for {sku}")
        return matches[0]

    def label(self, sku: str) -> CatalogLabel:
        matches = tuple(item for item in self.labels if item.sku == sku)
        if len(matches) != 1:
            raise KeyError(f"Expected exactly one catalog label for {sku}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ScheduledChunk:
    chunk_id: str
    sequence: int
    quantity: Decimal
    estimated_worker_hours: Decimal
    predecessor_chunk_id: str | None


@dataclass(frozen=True, slots=True)
class ChunkedJobItem:
    job_id: str
    job_item_id: str
    sku: str
    chunks: tuple[ScheduledChunk, ...]


@dataclass(frozen=True, slots=True)
class ConfigurationIssue:
    code: str
    affected_id: str
    explanation: str
