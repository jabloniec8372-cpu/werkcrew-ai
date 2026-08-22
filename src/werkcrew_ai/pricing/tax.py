"""Deterministic tax boundary applied after recommended net pricing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from werkcrew_ai.pricing.models import PricingIssue, PricingStatus, TaxTreatment


@dataclass(frozen=True, slots=True)
class TaxCalculation:
    status: PricingStatus
    tax_amount: Decimal | None
    gross_price: Decimal | None
    issues: tuple[PricingIssue, ...]


def apply_tax_policy(
    *,
    recommended_net_price: Decimal,
    treatment: TaxTreatment,
    standard_tax_rate: Decimal | None,
    money_scale: Decimal,
) -> TaxCalculation:
    """Finalize only explicitly configured DEMO tax; never infer treatment."""

    if treatment is TaxTreatment.STANDARD_19:
        if standard_tax_rate is None:
            raise ValueError("STANDARD_19 wymaga standard_tax_rate")
        tax_amount = (recommended_net_price * standard_tax_rate).quantize(
            money_scale,
            rounding=ROUND_HALF_UP,
        )
        return TaxCalculation(
            status=PricingStatus.COMPLETE,
            tax_amount=tax_amount,
            gross_price=recommended_net_price + tax_amount,
            issues=(),
        )
    code = (
        "TAX_MANUAL_REVIEW"
        if treatment is TaxTreatment.MANUAL_REVIEW
        else "REVERSE_CHARGE_13B_REVIEW"
    )
    return TaxCalculation(
        status=PricingStatus.REVIEW_REQUIRED,
        tax_amount=None,
        gross_price=None,
        issues=(
            PricingIssue(
                code=code,
                description=(
                    "TaxTreatment wymaga decyzji człowieka; VAT i gross nie są "
                    "finalizowane."
                ),
            ),
        ),
    )
