"""Immutable domain models for deterministic plan pricing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from werkcrew_ai.domain import PlanVariant


class CostCategory(StrEnum):
    LABOR = "LABOR"
    VEHICLE_TRAVEL = "VEHICLE_TRAVEL"
    DELIVERY = "DELIVERY"
    MATERIAL = "MATERIAL"
    TOOL_RENTAL = "TOOL_RENTAL"
    DISPOSAL = "DISPOSAL"
    SUBCONTRACTOR = "SUBCONTRACTOR"
    PARKING_TOLL = "PARKING_TOLL"


class PricingStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class TaxTreatment(StrEnum):
    STANDARD_19 = "STANDARD_19"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REVERSE_CHARGE_13B = "REVERSE_CHARGE_13B"


@dataclass(frozen=True, slots=True)
class PricingPolicy:
    policy_id: str
    policy_version: str
    currency: str
    overhead_rate: Decimal | None
    risk_reserve_rate: Decimal | None
    target_margin_rate: Decimal | None
    rounding_mode: str
    money_scale: Decimal
    data_classification: str


@dataclass(frozen=True, slots=True)
class MaterialCostInput:
    cost_source_id: str
    description: str
    quantity_base: Decimal | None
    unit: str
    purchase_net_unit_cost: Decimal | None
    currency: str
    waste_rate: Decimal | None
    source_reference: str
    data_classification: str


@dataclass(frozen=True, slots=True)
class DirectCostInput:
    cost_source_id: str
    category: CostCategory
    description: str
    quantity: Decimal | None
    unit: str
    unit_cost: Decimal | None
    currency: str
    source_reference: str
    data_classification: str


@dataclass(frozen=True, slots=True)
class VehicleUsageInput:
    cost_source_id: str
    plan_id: str
    vehicle_id: str
    purpose: str
    distance_km_total: Decimal | None
    currency: str
    source_reference: str
    data_classification: str


@dataclass(frozen=True, slots=True)
class EmployeeRateSnapshot:
    employee_id: str
    employee_name: str
    hourly_cost_amount: Decimal | None
    currency: str
    data_classification: str


@dataclass(frozen=True, slots=True)
class VehicleRateSnapshot:
    vehicle_id: str
    vehicle_name: str
    cost_per_km_amount: Decimal | None
    currency: str
    data_classification: str


@dataclass(frozen=True, slots=True)
class JobPricingContext:
    job_request_id: str
    pricing_policy: PricingPolicy
    tax_treatment: TaxTreatment | None
    standard_tax_rate: Decimal | None
    materials: tuple[MaterialCostInput, ...]
    common_direct_costs: tuple[DirectCostInput, ...]
    data_classification: str


@dataclass(frozen=True, slots=True)
class PlanPricingInput:
    job_request_id: str
    plan: PlanVariant
    planning_rule_version: str
    employee_rates: tuple[EmployeeRateSnapshot, ...]
    vehicle_rates: tuple[VehicleRateSnapshot, ...]
    vehicle_usages: tuple[VehicleUsageInput, ...]
    plan_specific_direct_costs: tuple[DirectCostInput, ...]
    data_classification: str


@dataclass(frozen=True, slots=True)
class CostLine:
    cost_source_id: str
    category: CostCategory
    description: str
    quantity: Decimal
    unit: str
    unit_cost: Decimal
    total: Decimal
    source_reference: str
    data_classification: str


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category: CostCategory
    total: Decimal


@dataclass(frozen=True, slots=True)
class PricingIssue:
    code: str
    description: str


@dataclass(frozen=True, slots=True)
class PlanPricingResult:
    result_id: str
    job_request_id: str
    plan_id: str
    plan_label: str
    status: PricingStatus
    employee_rates: tuple[EmployeeRateSnapshot, ...]
    vehicle_rates: tuple[VehicleRateSnapshot, ...]
    materials: tuple[MaterialCostInput, ...]
    vehicle_usages: tuple[VehicleUsageInput, ...]
    direct_cost_inputs: tuple[DirectCostInput, ...]
    cost_lines: tuple[CostLine, ...]
    pricing_policy: PricingPolicy
    tax_treatment: TaxTreatment | None
    planning_rule_version: str
    pricing_rule_version: str
    category_totals: tuple[CategoryTotal, ...]
    direct_cost: Decimal | None
    overhead_amount: Decimal | None
    cost_before_risk: Decimal | None
    risk_reserve_amount: Decimal | None
    modeled_company_cost: Decimal | None
    recommended_net_price: Decimal | None
    tax_amount: Decimal | None
    gross_price: Decimal | None
    issues: tuple[PricingIssue, ...]
    data_classification: str
    input_fingerprint: str

    def category_total(self, category: CostCategory) -> Decimal | None:
        return next(
            (item.total for item in self.category_totals if item.category is category),
            None,
        )


@dataclass(frozen=True, slots=True)
class PlanPricingComparison:
    base_plan_id: str
    compared_plan_id: str
    modeled_company_cost_difference: Decimal
    recommended_net_price_difference: Decimal
    largest_cost_driver: CostCategory
    largest_cost_driver_difference: Decimal
    data_classification: str
