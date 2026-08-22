"""Deterministic Decimal-only pricing of immutable M3 plan variants."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Iterable

from werkcrew_ai.pricing.models import (
    CategoryTotal,
    CostCategory,
    CostLine,
    DirectCostInput,
    JobPricingContext,
    PlanPricingComparison,
    PlanPricingInput,
    PlanPricingResult,
    PricingIssue,
    PricingStatus,
    TaxTreatment,
)
from werkcrew_ai.pricing.tax import apply_tax_policy

PRICING_RULE_VERSION = "plan-pricing-v1"
DEMO_DATA_CLASSIFICATION = "DEMO_SYNTHETIC"


class PricingInputError(ValueError):
    """Raised when a batch cannot be associated with any plan result."""


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return format(normalized, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def pricing_input_fingerprint(
    context: JobPricingContext, plan_input: PlanPricingInput
) -> str:
    payload = json.dumps(
        _canonical({"context": context, "plan_input": plan_input}),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _money(value: Decimal, scale: Decimal) -> Decimal:
    return value.quantize(scale, rounding=ROUND_HALF_UP)


def _issue(issues: list[PricingIssue], code: str, description: str) -> None:
    item = PricingIssue(code=code, description=description)
    if item not in issues:
        issues.append(item)


def _validate_policy(context: JobPricingContext, issues: list[PricingIssue]) -> None:
    policy = context.pricing_policy
    if policy.overhead_rate is None:
        _issue(issues, "MISSING_OVERHEAD_RATE", "PricingPolicy nie zawiera overhead_rate.")
    elif policy.overhead_rate < 0:
        _issue(issues, "INVALID_OVERHEAD_RATE", "overhead_rate nie może być ujemne.")
    if policy.risk_reserve_rate is None:
        _issue(
            issues,
            "MISSING_RISK_RESERVE_RATE",
            "PricingPolicy nie zawiera risk_reserve_rate.",
        )
    elif policy.risk_reserve_rate < 0:
        _issue(
            issues,
            "INVALID_RISK_RESERVE_RATE",
            "risk_reserve_rate nie może być ujemne.",
        )
    if policy.target_margin_rate is None:
        _issue(
            issues,
            "MISSING_TARGET_MARGIN_RATE",
            "PricingPolicy nie zawiera target_margin_rate.",
        )
    elif policy.target_margin_rate < 0:
        _issue(
            issues,
            "INVALID_TARGET_MARGIN_RATE",
            "target_margin_rate nie może być ujemne.",
        )
    elif policy.target_margin_rate >= 1:
        _issue(
            issues,
            "INVALID_TARGET_MARGIN_RATE",
            "target_margin_rate musi być mniejsze od 1.",
        )
    if policy.rounding_mode != "ROUND_HALF_UP":
        _issue(
            issues,
            "UNSUPPORTED_ROUNDING_MODE",
            "M5 obsługuje wyłącznie ROUND_HALF_UP.",
        )
    if policy.money_scale <= 0:
        _issue(issues, "INVALID_MONEY_SCALE", "money_scale musi być dodatnie.")
    if not policy.currency:
        _issue(issues, "MISSING_POLICY_CURRENCY", "PricingPolicy wymaga waluty.")
    if policy.data_classification != context.data_classification:
        _issue(
            issues,
            "DATA_CLASSIFICATION_MISMATCH",
            "PricingPolicy i JobPricingContext mają różne klasyfikacje danych.",
        )


def _validate_tax(context: JobPricingContext, issues: list[PricingIssue]) -> None:
    if context.tax_treatment is None:
        _issue(issues, "MISSING_TAX_TREATMENT", "Brak jawnego TaxTreatment.")
    elif context.tax_treatment is TaxTreatment.STANDARD_19:
        if context.standard_tax_rate is None:
            _issue(issues, "MISSING_STANDARD_TAX_RATE", "Brak standard_tax_rate.")
        elif context.standard_tax_rate < 0:
            _issue(
                issues,
                "INVALID_STANDARD_TAX_RATE",
                "standard_tax_rate nie może być ujemne.",
            )


def _category_totals(lines: Iterable[CostLine], scale: Decimal) -> tuple[CategoryTotal, ...]:
    totals: dict[CostCategory, Decimal] = {}
    for line in lines:
        totals[line.category] = totals.get(line.category, Decimal("0")) + line.total
    return tuple(
        CategoryTotal(category=category, total=_money(totals[category], scale))
        for category in CostCategory
        if category in totals
    )


def calculate_plan_pricing(
    context: JobPricingContext,
    plan_input: PlanPricingInput,
) -> PlanPricingResult:
    """Price one plan independently without modifying the M3 plan."""

    policy = context.pricing_policy
    scale = policy.money_scale if policy.money_scale > 0 else Decimal("0.01")
    fingerprint = pricing_input_fingerprint(context, plan_input)
    issues: list[PricingIssue] = []
    lines: list[CostLine] = []
    source_ids: set[str] = set()

    def claim_source(cost_source_id: str) -> bool:
        if cost_source_id in source_ids:
            _issue(
                issues,
                "DUPLICATE_COST_SOURCE_ID",
                f"Powielony cost_source_id: {cost_source_id}.",
            )
            return False
        source_ids.add(cost_source_id)
        return True

    def add_line(line: CostLine) -> None:
        if claim_source(line.cost_source_id):
            lines.append(line)

    _validate_policy(context, issues)
    _validate_tax(context, issues)
    if context.job_request_id != plan_input.job_request_id:
        _issue(
            issues,
            "JOB_REQUEST_ID_MISMATCH",
            "JobPricingContext i PlanPricingInput dotyczą różnych zleceń.",
        )
    if plan_input.plan.job_request_id != plan_input.job_request_id:
        _issue(
            issues,
            "PLAN_JOB_REQUEST_ID_MISMATCH",
            f"Plan {plan_input.plan.id} dotyczy innego job_request_id.",
        )
    if plan_input.data_classification != context.data_classification:
        _issue(
            issues,
            "DATA_CLASSIFICATION_MISMATCH",
            "PlanPricingInput i JobPricingContext mają różne klasyfikacje danych.",
        )

    employee_rates = {item.employee_id: item for item in plan_input.employee_rates}
    if len(employee_rates) != len(plan_input.employee_rates):
        _issue(issues, "DUPLICATE_EMPLOYEE_RATE", "Powielono snapshot stawki pracownika.")
    for assignment in plan_input.plan.assignments:
        source_id = (
            f"labor:{plan_input.plan.id}:{assignment.work_item_id}:"
            f"{assignment.employee_id}"
        )
        if not claim_source(source_id):
            continue
        duration_seconds = Decimal(str((assignment.end_at - assignment.start_at).total_seconds()))
        duration_hours = duration_seconds / Decimal("3600")
        if duration_hours <= 0:
            _issue(
                issues,
                "INVALID_TASK_DURATION",
                f"Zadanie {assignment.work_item_id} nie ma dodatniego czasu.",
            )
            continue
        rate = employee_rates.get(assignment.employee_id)
        if rate is None or rate.hourly_cost_amount is None:
            _issue(
                issues,
                "MISSING_HOURLY_COST",
                f"Brak hourly_cost_amount dla {assignment.employee_id}.",
            )
            continue
        if rate.hourly_cost_amount < 0:
            _issue(
                issues,
                "INVALID_HOURLY_COST",
                f"Ujemny hourly_cost_amount dla {assignment.employee_id}.",
            )
            continue
        if rate.currency != policy.currency:
            _issue(
                issues,
                "CURRENCY_MISMATCH",
                f"Stawka {assignment.employee_id} ma walutę {rate.currency}.",
            )
            continue
        lines.append(
            CostLine(
                cost_source_id=source_id,
                category=CostCategory.LABOR,
                description=f"{assignment.work_item_name} — {rate.employee_name}",
                quantity=duration_hours,
                unit="hour",
                unit_cost=rate.hourly_cost_amount,
                total=_money(duration_hours * rate.hourly_cost_amount, scale),
                source_reference=assignment.work_item_id,
                data_classification=plan_input.data_classification,
            )
        )

    vehicle_rates = {item.vehicle_id: item for item in plan_input.vehicle_rates}
    if len(vehicle_rates) != len(plan_input.vehicle_rates):
        _issue(issues, "DUPLICATE_VEHICLE_RATE", "Powielono snapshot stawki pojazdu.")
    usages_by_vehicle: dict[str, list[Any]] = {}
    for usage in plan_input.vehicle_usages:
        usages_by_vehicle.setdefault(usage.vehicle_id, []).append(usage)
    assigned_vehicle_ids = {
        item.vehicle_id for item in plan_input.plan.assignments if item.vehicle_id
    }
    if assigned_vehicle_ids != set(plan_input.plan.vehicle_ids):
        _issue(
            issues,
            "PLAN_VEHICLE_REFERENCE_MISMATCH",
            "vehicle_ids planu nie odpowiadają pojazdom zapisanym w zadaniach.",
        )
    for vehicle_id in plan_input.plan.vehicle_ids:
        usages = usages_by_vehicle.get(vehicle_id, [])
        if not usages:
            _issue(
                issues,
                "MISSING_VEHICLE_DISTANCE",
                f"Brak jawnego distance_km_total dla {vehicle_id}.",
            )
            continue
        if len(usages) != 1:
            _issue(
                issues,
                "DUPLICATE_VEHICLE_USAGE",
                f"Pojazd {vehicle_id} ma więcej niż jedno użycie w planie.",
            )
        usage = usages[0]
        if not claim_source(usage.cost_source_id):
            continue
        if usage.plan_id != plan_input.plan.id:
            _issue(
                issues,
                "VEHICLE_USAGE_PLAN_MISMATCH",
                f"Użycie {usage.cost_source_id} dotyczy innego planu.",
            )
            continue
        if usage.distance_km_total is None:
            _issue(
                issues,
                "MISSING_VEHICLE_DISTANCE",
                f"Brak jawnego distance_km_total dla {vehicle_id}.",
            )
            continue
        if usage.distance_km_total < 0:
            _issue(
                issues,
                "INVALID_VEHICLE_DISTANCE",
                f"Ujemny distance_km_total dla {vehicle_id}.",
            )
            continue
        if usage.purpose != "JOB_EXECUTION":
            _issue(
                issues,
                "INVALID_VEHICLE_PURPOSE",
                f"Nieobsługiwany purpose użycia {vehicle_id}.",
            )
            continue
        rate = vehicle_rates.get(vehicle_id)
        if rate is None or rate.cost_per_km_amount is None:
            _issue(
                issues,
                "MISSING_VEHICLE_RATE",
                f"Brak cost_per_km_amount dla {vehicle_id}.",
            )
            continue
        if usage.currency != policy.currency or rate.currency != policy.currency:
            _issue(
                issues,
                "CURRENCY_MISMATCH",
                f"Dane pojazdu {vehicle_id} nie są w {policy.currency}.",
            )
            continue
        lines.append(
            CostLine(
                cost_source_id=usage.cost_source_id,
                category=CostCategory.VEHICLE_TRAVEL,
                description=f"{usage.purpose} — {rate.vehicle_name}",
                quantity=usage.distance_km_total,
                unit="km",
                unit_cost=rate.cost_per_km_amount,
                total=_money(usage.distance_km_total * rate.cost_per_km_amount, scale),
                source_reference=usage.source_reference,
                data_classification=usage.data_classification,
            )
        )

    for material in context.materials:
        if not claim_source(material.cost_source_id):
            continue
        if material.quantity_base is None:
            _issue(
                issues,
                "MISSING_MATERIAL_QUANTITY",
                f"Brak quantity_base dla {material.cost_source_id}.",
            )
            continue
        if material.purchase_net_unit_cost is None:
            _issue(
                issues,
                "MISSING_MATERIAL_UNIT_COST",
                f"Brak purchase_net_unit_cost dla {material.cost_source_id}.",
            )
            continue
        if material.waste_rate is None:
            _issue(
                issues,
                "MISSING_WASTE_RATE",
                f"Brak waste_rate dla {material.cost_source_id}.",
            )
            continue
        if material.quantity_base <= 0:
            _issue(
                issues,
                "INVALID_MATERIAL_QUANTITY",
                f"quantity_base dla {material.cost_source_id} musi być dodatnie.",
            )
            continue
        if material.purchase_net_unit_cost < 0:
            _issue(
                issues,
                "INVALID_MATERIAL_UNIT_COST",
                f"Ujemny koszt materiału {material.cost_source_id}.",
            )
            continue
        if material.waste_rate < 0:
            _issue(
                issues,
                "INVALID_WASTE_RATE",
                f"Ujemny waste_rate dla {material.cost_source_id}.",
            )
            continue
        if material.currency != policy.currency:
            _issue(
                issues,
                "CURRENCY_MISMATCH",
                f"Materiał {material.cost_source_id} ma walutę {material.currency}.",
            )
            continue
        effective_quantity = material.quantity_base * (Decimal("1") + material.waste_rate)
        lines.append(
            CostLine(
                cost_source_id=material.cost_source_id,
                category=CostCategory.MATERIAL,
                description=material.description,
                quantity=effective_quantity,
                unit=material.unit,
                unit_cost=material.purchase_net_unit_cost,
                total=_money(effective_quantity * material.purchase_net_unit_cost, scale),
                source_reference=material.source_reference,
                data_classification=material.data_classification,
            )
        )

    direct_inputs = (*context.common_direct_costs, *plan_input.plan_specific_direct_costs)
    for direct_input in direct_inputs:
        if not claim_source(direct_input.cost_source_id):
            continue
        _add_direct_cost_line(direct_input, policy.currency, scale, lines, issues)

    category_totals = _category_totals(lines, scale)
    if issues:
        return _result(
            context,
            plan_input,
            fingerprint,
            PricingStatus.INCOMPLETE,
            tuple(lines),
            category_totals,
            tuple(issues),
        )

    direct_cost = _money(sum((item.total for item in lines), Decimal("0")), scale)
    overhead_amount = _money(direct_cost * policy.overhead_rate, scale)  # type: ignore[arg-type]
    cost_before_risk = direct_cost + overhead_amount
    risk_reserve_amount = _money(
        cost_before_risk * policy.risk_reserve_rate, scale  # type: ignore[arg-type]
    )
    modeled_company_cost = cost_before_risk + risk_reserve_amount
    recommended_net_price = _money(
        modeled_company_cost / (Decimal("1") - policy.target_margin_rate),  # type: ignore[operator]
        scale,
    )

    tax = apply_tax_policy(
        recommended_net_price=recommended_net_price,
        treatment=context.tax_treatment,  # type: ignore[arg-type]
        standard_tax_rate=context.standard_tax_rate,
        money_scale=scale,
    )

    return _result(
        context,
        plan_input,
        fingerprint,
        tax.status,
        tuple(lines),
        category_totals,
        tax.issues,
        direct_cost=direct_cost,
        overhead_amount=overhead_amount,
        cost_before_risk=cost_before_risk,
        risk_reserve_amount=risk_reserve_amount,
        modeled_company_cost=modeled_company_cost,
        recommended_net_price=recommended_net_price,
        tax_amount=tax.tax_amount,
        gross_price=tax.gross_price,
    )


def _add_direct_cost_line(
    item: DirectCostInput,
    currency: str,
    scale: Decimal,
    lines: list[CostLine],
    issues: list[PricingIssue],
) -> None:
    if item.category in {
        CostCategory.LABOR,
        CostCategory.VEHICLE_TRAVEL,
        CostCategory.MATERIAL,
    }:
        _issue(
            issues,
            "INVALID_DIRECT_COST_CATEGORY",
            f"{item.category.value} nie może być naliczone przez DirectCostInput.",
        )
        return
    if item.quantity is None:
        _issue(issues, "MISSING_DIRECT_COST_QUANTITY", f"Brak quantity dla {item.cost_source_id}.")
        return
    if item.unit_cost is None:
        _issue(issues, "MISSING_DIRECT_COST_UNIT_COST", f"Brak unit_cost dla {item.cost_source_id}.")
        return
    if item.quantity <= 0 or item.unit_cost < 0:
        _issue(issues, "INVALID_DIRECT_COST", f"Niespójna wartość {item.cost_source_id}.")
        return
    if item.currency != currency:
        _issue(issues, "CURRENCY_MISMATCH", f"Koszt {item.cost_source_id} ma walutę {item.currency}.")
        return
    lines.append(
        CostLine(
            cost_source_id=item.cost_source_id,
            category=item.category,
            description=item.description,
            quantity=item.quantity,
            unit=item.unit,
            unit_cost=item.unit_cost,
            total=_money(item.quantity * item.unit_cost, scale),
            source_reference=item.source_reference,
            data_classification=item.data_classification,
        )
    )


def _result(
    context: JobPricingContext,
    plan_input: PlanPricingInput,
    fingerprint: str,
    status: PricingStatus,
    lines: tuple[CostLine, ...],
    category_totals: tuple[CategoryTotal, ...],
    issues: tuple[PricingIssue, ...],
    *,
    direct_cost: Decimal | None = None,
    overhead_amount: Decimal | None = None,
    cost_before_risk: Decimal | None = None,
    risk_reserve_amount: Decimal | None = None,
    modeled_company_cost: Decimal | None = None,
    recommended_net_price: Decimal | None = None,
    tax_amount: Decimal | None = None,
    gross_price: Decimal | None = None,
) -> PlanPricingResult:
    return PlanPricingResult(
        result_id=f"pricing-{plan_input.plan.id}-{fingerprint[:16]}",
        job_request_id=plan_input.job_request_id,
        plan_id=plan_input.plan.id,
        plan_label=plan_input.plan.label,
        status=status,
        employee_rates=plan_input.employee_rates,
        vehicle_rates=plan_input.vehicle_rates,
        materials=context.materials,
        vehicle_usages=plan_input.vehicle_usages,
        direct_cost_inputs=(*context.common_direct_costs, *plan_input.plan_specific_direct_costs),
        cost_lines=lines,
        pricing_policy=context.pricing_policy,
        tax_treatment=context.tax_treatment,
        planning_rule_version=plan_input.planning_rule_version,
        pricing_rule_version=PRICING_RULE_VERSION,
        category_totals=category_totals,
        direct_cost=direct_cost,
        overhead_amount=overhead_amount,
        cost_before_risk=cost_before_risk,
        risk_reserve_amount=risk_reserve_amount,
        modeled_company_cost=modeled_company_cost,
        recommended_net_price=recommended_net_price,
        tax_amount=tax_amount,
        gross_price=gross_price,
        issues=issues,
        data_classification=context.data_classification,
        input_fingerprint=fingerprint,
    )


def calculate_plan_quotes(
    context: JobPricingContext,
    plan_inputs: tuple[PlanPricingInput, ...],
    existing_results: tuple[PlanPricingResult, ...] = (),
) -> tuple[PlanPricingResult, ...]:
    """Calculate each existing plan independently and reuse identical snapshots."""

    if not plan_inputs:
        raise PricingInputError("NO_PLANS: brak PlanVariant do wyceny")
    existing_by_fingerprint = {item.input_fingerprint: item for item in existing_results}
    results = []
    for plan_input in plan_inputs:
        fingerprint = pricing_input_fingerprint(context, plan_input)
        result = existing_by_fingerprint.get(fingerprint)
        if result is None:
            result = calculate_plan_pricing(context, plan_input)
        results.append(result)
    return tuple(results)


def compare_plan_pricing(
    results: tuple[PlanPricingResult, ...],
) -> PlanPricingComparison | None:
    """Compare exactly two complete variants without recommending either one."""

    if len(results) != 2 or any(item.status is not PricingStatus.COMPLETE for item in results):
        return None
    base, compared = results
    base_categories = {item.category: item.total for item in base.category_totals}
    compared_categories = {item.category: item.total for item in compared.category_totals}
    category_differences = {
        category: compared_categories.get(category, Decimal("0"))
        - base_categories.get(category, Decimal("0"))
        for category in CostCategory
    }
    largest_driver = max(
        CostCategory,
        key=lambda category: abs(category_differences[category]),
    )
    return PlanPricingComparison(
        base_plan_id=base.plan_id,
        compared_plan_id=compared.plan_id,
        modeled_company_cost_difference=(
            compared.modeled_company_cost - base.modeled_company_cost  # type: ignore[operator]
        ),
        recommended_net_price_difference=(
            compared.recommended_net_price - base.recommended_net_price  # type: ignore[operator]
        ),
        largest_cost_driver=largest_driver,
        largest_cost_driver_difference=category_differences[largest_driver],
        data_classification=base.data_classification,
    )
