"""Stable fingerprints for the deterministic M6 owner decision boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


PRICING_GATE_FINGERPRINT_SCHEMA = "werkcrew-pricing-gate-v1"
OWNER_RESPONSE_FINGERPRINT_SCHEMA = "werkcrew-owner-response-v1"


def _hash_payload(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def pricing_gate_fingerprint(
    job_request_id: str,
    pricing_results: Sequence[Any],
) -> str:
    """Hash exactly the deterministic pricing view exposed to the owner."""

    variants = []
    for result in sorted(pricing_results, key=lambda item: item.plan_id):
        variants.append(
            {
                "plan_id": result.plan_id,
                "plan_label": result.plan_label,
                "result_id": result.result_id,
                "input_fingerprint": result.input_fingerprint,
                "pricing_rule_version": result.pricing_rule_version,
                "pricing_status": result.status.value,
                "currency": result.pricing_policy.currency,
                "category_totals": [
                    {
                        "category": item.category.value,
                        "total": str(item.total),
                    }
                    for item in sorted(
                        result.category_totals,
                        key=lambda item: item.category.value,
                    )
                ],
                "modeled_company_cost": (
                    str(result.modeled_company_cost)
                    if result.modeled_company_cost is not None
                    else None
                ),
                "recommended_net_price": (
                    str(result.recommended_net_price)
                    if result.recommended_net_price is not None
                    else None
                ),
                "tax_treatment": (
                    result.tax_treatment.value
                    if result.tax_treatment is not None
                    else None
                ),
                "tax_amount": (
                    str(result.tax_amount) if result.tax_amount is not None else None
                ),
                "gross_price": (
                    str(result.gross_price)
                    if result.gross_price is not None
                    else None
                ),
            }
        )
    return _hash_payload(
        {
            "schema": PRICING_GATE_FINGERPRINT_SCHEMA,
            "job_request_id": job_request_id,
            "variant_count": len(variants),
            "variants": variants,
        }
    )


def owner_response_fingerprint(response: Mapping[str, Any]) -> str:
    """Hash the canonical backend-created response passed to interruptResponse."""

    return _hash_payload(
        {
            "schema": OWNER_RESPONSE_FINGERPRINT_SCHEMA,
            "gate_id": response.get("gate_id"),
            "action": response.get("action"),
            "selected_plan_id": response.get("selected_plan_id"),
            "pricing_gate_fingerprint": response.get(
                "pricing_gate_fingerprint"
            ),
        }
    )
