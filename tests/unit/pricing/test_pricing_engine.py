from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from werkcrew_ai.infrastructure.demo_repository import load_demo_site_visit_report
from werkcrew_ai.infrastructure.demo_workflow_store import DemoWorkflowStore
from werkcrew_ai.pricing import (
    CostCategory,
    EmployeeRateSnapshot,
    PlanPricingInput,
    PricingInputError,
    PricingStatus,
    TaxTreatment,
    VehicleRateSnapshot,
    calculate_plan_pricing,
    calculate_plan_quotes,
    compare_plan_pricing,
)


def prepared_store(**kwargs) -> DemoWorkflowStore:
    store = DemoWorkflowStore(**kwargs)
    store.assess_job()
    store.create_site_visit()
    store.submit_report(load_demo_site_visit_report())
    store.generate_plans()
    return store


def context_and_inputs():
    snapshot = prepared_store().get()
    employee_by_id = {item.id: item for item in snapshot.employees}
    vehicle_by_id = {item.id: item for item in snapshot.vehicles}
    inputs = []
    for plan in snapshot.planning_result.plans:
        inputs.append(
            PlanPricingInput(
                job_request_id=snapshot.current_job_request.id,
                plan=plan,
                planning_rule_version=snapshot.planning_result.rule_version,
                employee_rates=tuple(
                    EmployeeRateSnapshot(
                        employee_id=employee_by_id[item].id,
                        employee_name=employee_by_id[item].name,
                        hourly_cost_amount=employee_by_id[item].hourly_cost_amount,
                        currency=employee_by_id[item].cost_currency,
                        data_classification="DEMO_SYNTHETIC",
                    )
                    for item in plan.employee_ids
                ),
                vehicle_rates=tuple(
                    VehicleRateSnapshot(
                        vehicle_id=vehicle_by_id[item].id,
                        vehicle_name=vehicle_by_id[item].name,
                        cost_per_km_amount=vehicle_by_id[item].cost_per_km_amount,
                        currency=vehicle_by_id[item].cost_currency,
                        data_classification="DEMO_SYNTHETIC",
                    )
                    for item in plan.vehicle_ids
                ),
                vehicle_usages=tuple(
                    item for item in snapshot.vehicle_usages if item.plan_id == plan.id
                ),
                plan_specific_direct_costs=(),
                data_classification="DEMO_SYNTHETIC",
            )
        )
    return snapshot.pricing_context, tuple(inputs)


def issue_codes(result) -> set[str]:
    return {item.code for item in result.issues}


def test_demo_oracle_plan_a_and_plan_b() -> None:
    store = prepared_store()
    snapshot = store.calculate_plan_quotes()
    plan_a, plan_b = snapshot.pricing_results

    assert plan_a.status is PricingStatus.COMPLETE
    assert plan_a.category_total(CostCategory.LABOR) == Decimal("1452.00")
    assert plan_a.category_total(CostCategory.VEHICLE_TRAVEL) == Decimal("115.20")
    assert plan_a.category_total(CostCategory.MATERIAL) == Decimal("718.34")
    assert plan_a.category_total(CostCategory.DELIVERY) == Decimal("60.00")
    assert plan_a.category_total(CostCategory.DISPOSAL) == Decimal("140.00")
    assert plan_a.direct_cost == Decimal("2485.54")
    assert plan_a.overhead_amount == Decimal("372.83")
    assert plan_a.cost_before_risk == Decimal("2858.37")
    assert plan_a.risk_reserve_amount == Decimal("142.92")
    assert plan_a.modeled_company_cost == Decimal("3001.29")
    assert plan_a.recommended_net_price == Decimal("3751.61")
    assert plan_a.tax_amount == Decimal("712.81")
    assert plan_a.gross_price == Decimal("4464.42")

    assert plan_b.status is PricingStatus.COMPLETE
    assert plan_b.category_total(CostCategory.LABOR) == Decimal("1560.00")
    assert plan_b.category_total(CostCategory.VEHICLE_TRAVEL) == Decimal("115.20")
    assert plan_b.category_total(CostCategory.MATERIAL) == Decimal("718.34")
    assert plan_b.category_total(CostCategory.DELIVERY) == Decimal("60.00")
    assert plan_b.category_total(CostCategory.DISPOSAL) == Decimal("140.00")
    assert plan_b.direct_cost == Decimal("2593.54")
    assert plan_b.overhead_amount == Decimal("389.03")
    assert plan_b.cost_before_risk == Decimal("2982.57")
    assert plan_b.risk_reserve_amount == Decimal("149.13")
    assert plan_b.modeled_company_cost == Decimal("3131.70")
    assert plan_b.recommended_net_price == Decimal("3914.63")
    assert plan_b.tax_amount == Decimal("743.78")
    assert plan_b.gross_price == Decimal("4658.41")


def test_material_atomic_rounding_and_unique_cost_sources() -> None:
    result = prepared_store().calculate_plan_quotes().pricing_results[0]
    material_totals = {
        line.cost_source_id: line.total
        for line in result.cost_lines
        if line.category is CostCategory.MATERIAL
    }
    assert material_totals == {
        "material-waterproofing-demo": Decimal("103.85"),
        "material-tile-adhesive-demo": Decimal("210.25"),
        "material-grout-silicone-demo": Decimal("69.24"),
        "material-plumbing-technical-demo": Decimal("240.00"),
        "material-consumables-demo": Decimal("95.00"),
    }
    source_ids = [item.cost_source_id for item in result.cost_lines]
    assert len(source_ids) == len(set(source_ids))


def test_policy_and_explicit_vehicle_distance_are_snapshotted() -> None:
    result = prepared_store().calculate_plan_quotes().pricing_results[0]
    policy = result.pricing_policy
    assert policy.currency == "EUR"
    assert policy.overhead_rate == Decimal("0.15")
    assert policy.risk_reserve_rate == Decimal("0.05")
    assert policy.target_margin_rate == Decimal("0.20")
    assert policy.rounding_mode == "ROUND_HALF_UP"
    assert policy.money_scale == Decimal("0.01")
    assert result.vehicle_usages[0].distance_km_total == Decimal("240")
    assert result.category_total(CostCategory.VEHICLE_TRAVEL) == Decimal("115.20")


def test_no_plans_is_rejected() -> None:
    context, _ = context_and_inputs()
    with pytest.raises(PricingInputError, match="NO_PLANS"):
        calculate_plan_quotes(context, ())


def test_plan_for_another_job_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    other_plan = replace(inputs[0].plan, job_request_id="other-job")
    result = calculate_plan_pricing(context, replace(inputs[0], plan=other_plan))
    assert result.status is PricingStatus.INCOMPLETE
    assert "PLAN_JOB_REQUEST_ID_MISMATCH" in issue_codes(result)


def test_missing_hourly_cost_never_becomes_zero() -> None:
    context, inputs = context_and_inputs()
    rates = list(inputs[0].employee_rates)
    rates[0] = replace(rates[0], hourly_cost_amount=None)
    result = calculate_plan_pricing(context, replace(inputs[0], employee_rates=tuple(rates)))
    assert result.status is PricingStatus.INCOMPLETE
    assert "MISSING_HOURLY_COST" in issue_codes(result)
    assert result.direct_cost is None
    assert result.modeled_company_cost is None
    assert result.recommended_net_price is None
    assert result.gross_price is None


def test_zero_or_negative_task_duration_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    assignment = inputs[0].plan.assignments[0]
    invalid_assignment = replace(assignment, end_at=assignment.start_at)
    invalid_plan = replace(
        inputs[0].plan,
        assignments=(invalid_assignment, *inputs[0].plan.assignments[1:]),
    )
    result = calculate_plan_pricing(context, replace(inputs[0], plan=invalid_plan))
    assert result.status is PricingStatus.INCOMPLETE
    assert "INVALID_TASK_DURATION" in issue_codes(result)


def test_missing_explicit_vehicle_distance_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    result = calculate_plan_pricing(context, replace(inputs[0], vehicle_usages=()))
    assert result.status is PricingStatus.INCOMPLETE
    assert "MISSING_VEHICLE_DISTANCE" in issue_codes(result)
    assert result.category_total(CostCategory.VEHICLE_TRAVEL) is None


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("purchase_net_unit_cost", "MISSING_MATERIAL_UNIT_COST"),
        ("quantity_base", "MISSING_MATERIAL_QUANTITY"),
    ],
)
def test_missing_material_value_is_incomplete(field: str, code: str) -> None:
    context, inputs = context_and_inputs()
    material = replace(context.materials[0], **{field: None})
    result = calculate_plan_pricing(
        replace(context, materials=(material, *context.materials[1:])), inputs[0]
    )
    assert result.status is PricingStatus.INCOMPLETE
    assert code in issue_codes(result)
    assert result.direct_cost is None


def test_negative_waste_rate_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    material = replace(context.materials[0], waste_rate=Decimal("-0.01"))
    result = calculate_plan_pricing(
        replace(context, materials=(material, *context.materials[1:])), inputs[0]
    )
    assert "INVALID_WASTE_RATE" in issue_codes(result)


def test_mixed_currency_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    rates = list(inputs[0].employee_rates)
    rates[0] = replace(rates[0], currency="USD")
    result = calculate_plan_pricing(context, replace(inputs[0], employee_rates=tuple(rates)))
    assert result.status is PricingStatus.INCOMPLETE
    assert "CURRENCY_MISMATCH" in issue_codes(result)


@pytest.mark.parametrize("margin", [Decimal("-0.01"), Decimal("1")])
def test_invalid_target_margin_is_incomplete(margin: Decimal) -> None:
    context, inputs = context_and_inputs()
    policy = replace(context.pricing_policy, target_margin_rate=margin)
    result = calculate_plan_pricing(replace(context, pricing_policy=policy), inputs[0])
    assert result.status is PricingStatus.INCOMPLETE
    assert "INVALID_TARGET_MARGIN_RATE" in issue_codes(result)


def test_missing_overhead_policy_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    policy = replace(context.pricing_policy, overhead_rate=None)
    result = calculate_plan_pricing(replace(context, pricing_policy=policy), inputs[0])
    assert result.status is PricingStatus.INCOMPLETE
    assert "MISSING_OVERHEAD_RATE" in issue_codes(result)


def test_duplicate_cost_source_id_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    duplicate = replace(
        context.common_direct_costs[0],
        cost_source_id=context.materials[0].cost_source_id,
    )
    result = calculate_plan_pricing(
        replace(context, common_direct_costs=(duplicate, *context.common_direct_costs[1:])),
        inputs[0],
    )
    assert result.status is PricingStatus.INCOMPLETE
    assert "DUPLICATE_COST_SOURCE_ID" in issue_codes(result)


def test_missing_tax_treatment_is_incomplete() -> None:
    context, inputs = context_and_inputs()
    result = calculate_plan_pricing(replace(context, tax_treatment=None), inputs[0])
    assert result.status is PricingStatus.INCOMPLETE
    assert "MISSING_TAX_TREATMENT" in issue_codes(result)
    assert result.recommended_net_price is None


@pytest.mark.parametrize(
    "treatment",
    [TaxTreatment.MANUAL_REVIEW, TaxTreatment.REVERSE_CHARGE_13B],
)
def test_non_final_tax_treatments_require_review(treatment: TaxTreatment) -> None:
    context, inputs = context_and_inputs()
    result = calculate_plan_pricing(replace(context, tax_treatment=treatment), inputs[0])
    assert result.status is PricingStatus.REVIEW_REQUIRED
    assert result.modeled_company_cost == Decimal("3001.29")
    assert result.recommended_net_price == Decimal("3751.61")
    assert result.tax_amount is None
    assert result.gross_price is None


def test_plan_a_complete_and_plan_b_incomplete_are_not_comparable() -> None:
    context, inputs = context_and_inputs()
    partial_inputs = (inputs[0], replace(inputs[1], vehicle_usages=()))
    results = calculate_plan_quotes(context, partial_inputs)
    assert [item.status for item in results] == [
        PricingStatus.COMPLETE,
        PricingStatus.INCOMPLETE,
    ]
    assert compare_plan_pricing(results) is None


def test_one_legal_plan_is_enough_when_complete() -> None:
    context, inputs = context_and_inputs()
    results = calculate_plan_quotes(context, (inputs[0],))
    assert len(results) == 1
    assert results[0].status is PricingStatus.COMPLETE
    assert compare_plan_pricing(results) is None


def test_identical_fingerprint_reuses_immutable_result() -> None:
    context, inputs = context_and_inputs()
    first = calculate_plan_quotes(context, inputs)
    second = calculate_plan_quotes(context, inputs, first)
    assert second[0] is first[0]
    assert second[1] is first[1]


def test_changed_input_creates_new_result_without_mutating_old_snapshot() -> None:
    context, inputs = context_and_inputs()
    old_result = calculate_plan_quotes(context, (inputs[0],))[0]
    changed_material = replace(
        context.materials[0], purchase_net_unit_cost=Decimal("12.00")
    )
    changed_context = replace(
        context, materials=(changed_material, *context.materials[1:])
    )
    new_result = calculate_plan_quotes(changed_context, (inputs[0],), (old_result,))[0]
    assert new_result.result_id != old_result.result_id
    assert new_result.input_fingerprint != old_result.input_fingerprint
    assert old_result.gross_price == Decimal("4464.42")
    assert new_result.gross_price != old_result.gross_price


def test_complete_comparison_derives_labor_as_largest_driver() -> None:
    results = prepared_store().calculate_plan_quotes().pricing_results
    comparison = compare_plan_pricing(results)
    assert comparison is not None
    assert comparison.modeled_company_cost_difference == Decimal("130.41")
    assert comparison.recommended_net_price_difference == Decimal("163.02")
    assert comparison.largest_cost_driver is CostCategory.LABOR
    assert comparison.largest_cost_driver_difference == Decimal("108.00")
