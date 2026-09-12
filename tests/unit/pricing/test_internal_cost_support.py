from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_UP,
    localcontext,
)

import pytest

from tests.unit.planning.test_bounded_feasibility import (
    ANNA,
    PETER,
    STEFAN,
    _evaluation,
    _support,
)
from werkcrew_ai.field.models import WorkerIdentityRegistry
from werkcrew_ai.field.serialization import canonical_json, serialize_worker_registry, sha256_text
from werkcrew_ai.planning.bounded_feasibility import generate_bounded_repair_candidates
from werkcrew_ai.pricing.internal_cost_repository import derive_internal_cost_subjects
from werkcrew_ai.pricing.internal_cost_support import (
    ACCESS_CLASSIFICATION,
    AUTOMATIC_FX,
    CONFIGURATION_ID,
    CURRENCY,
    RATE_SEMANTICS,
    SOURCE_CLASSIFICATION,
    V1_INTERNAL_LABOR_RATES,
    InternalCostCandidateBinding,
    InternalCostSourceCapture,
    InternalCostSupportCut,
    InternalLaborCostSubject,
    InternalLaborRateSelection,
    LaborCostSubjectScope,
    M5InternalCostSupportStorageError,
    M5InternalCostSupportValidationError,
    NoSourceReason,
    RateSelectionStatus,
    WorkerInternalCostRateRevision,
    canonical_rate,
    current_internal_labor_cost_rule,
    format_money,
    internal_cost_support_semantic_json,
    parse_rate_amount,
    rate_source_semantic_json,
    restore_internal_cost_support,
    restore_rate_source,
)


UTC = timezone.utc
START = datetime(2026, 9, 14, 8, tzinfo=UTC)
HOSTILE_DECIMAL_CONTEXTS = (
    (1, ROUND_DOWN),
    (2, ROUND_DOWN),
    (2, ROUND_UP),
    (3, ROUND_FLOOR),
    (3, ROUND_CEILING),
    (28, ROUND_HALF_EVEN),
    (80, ROUND_UP),
)
PREDECESSOR_LARGE_RATE_TEXTS = (
    "99999999999999999999999999.99",
    "100000000000000000000000000.00",
    "1234567890123456789012345678.00",
)


def registry():
    value = WorkerIdentityRegistry(tuple(worker for worker, _ in V1_INTERNAL_LABOR_RATES))
    raw = serialize_worker_registry(value)
    return value, raw, sha256_text(raw)


def source(worker_id: str = ANNA, amount: Decimal = Decimal("37.00")):
    worker_registry, raw, fingerprint = registry()
    rule = current_internal_labor_cost_rule()
    return WorkerInternalCostRateRevision(
        worker_id=worker_id,
        rate_amount=amount,
        source_revision=0,
        previous_source_record_id=None,
        effective_from=None,
        effective_until=None,
        worker_registry_revision=worker_registry.registry_revision,
        worker_registry_fingerprint=fingerprint,
        canonical_worker_registry_json=raw,
        rule_id=rule.rule_id,
        rule_fingerprint=rule.rule_fingerprint,
    )


def generated_case():
    evaluation = _evaluation()
    support = _support(evaluation, workers=(ANNA, STEFAN))
    result = generate_bounded_repair_candidates(evaluation, support)
    return evaluation, support, result


def no_source_selection(subject: InternalLaborCostSubject):
    return InternalLaborRateSelection(
        subject_id=subject.subject_id,
        subject_fingerprint=subject.subject_fingerprint,
        status=RateSelectionStatus.NO_SOURCE,
        source_record_id=None,
        source_fingerprint=None,
        source_revision=None,
        source_capture_id=None,
        source_capture_fingerprint=None,
        source_capture_generation=None,
        no_source_reason=NoSourceReason.NO_RATE_SOURCE,
    )


def support_cut():
    evaluation, support, result = generated_case()
    rule = current_internal_labor_cost_rule()
    subjects = derive_internal_cost_subjects(result, support)
    return InternalCostSupportCut(
        evaluation_input_id=evaluation.evaluation_input_id,
        evaluation_input_fingerprint=evaluation.evaluation_input_fingerprint,
        feasibility_support_snapshot_id=support.support_snapshot_id,
        feasibility_support_snapshot_fingerprint=support.support_fingerprint,
        company_plan_id=evaluation.company_plan_id,
        base_plan_revision=evaluation.base_plan_revision,
        base_plan_revision_id=evaluation.base_plan_revision_id,
        base_plan_revision_fingerprint=evaluation.base_plan_revision_fingerprint,
        m3_result_id=result.result_id,
        m3_result_fingerprint=result.result_fingerprint,
        candidate_bindings=tuple(
            InternalCostCandidateBinding(
                position=position,
                candidate_id=item.candidate_id,
                candidate_fingerprint=item.candidate_fingerprint,
            )
            for position, item in enumerate(result.candidates)
        ),
        rule_id=rule.rule_id,
        rule_revision=rule.rule_revision,
        rule_fingerprint=rule.rule_fingerprint,
        source_cut_generation=6,
        subjects=subjects,
        selections=tuple(no_source_selection(item) for item in subjects),
    )


def test_master_configuration_is_exact_and_owner_is_not_zero():
    assert V1_INTERNAL_LABOR_RATES == (
        ("andreas-hoffmann", Decimal("35.00")),
        ("anna-fischer", Decimal("37.00")),
        ("jonas-klein", Decimal("31.00")),
        ("peter-berger", Decimal("38.00")),
        ("stefan-mueller", Decimal("45.00")),
        ("thomas-becker", Decimal("34.00")),
    )
    assert dict(V1_INTERNAL_LABOR_RATES)[STEFAN] == Decimal("45.00")
    assert CONFIGURATION_ID == "gen2-internal-labor-cost-rates-v1"
    assert RATE_SEMANTICS == "modeled_internal_labor_cost_rate"
    assert ACCESS_CLASSIFICATION == "INTERNAL_ONLY"
    assert SOURCE_CLASSIFICATION == "GEN2_SYNTHETIC_COMPANY_CONFIGURATION"


def test_internal_cost_rule_freezes_decimal_eur_and_labor_only_scope():
    rule = current_internal_labor_cost_rule()
    assert (rule.currency, rule.rate_scale, rule.final_money_scale) == (
        CURRENCY,
        Decimal("0.01"),
        Decimal("0.01"),
    )
    assert (rule.rounding, rule.automatic_fx, rule.component_scope) == (
        "ROUND_HALF_UP",
        AUTOMATIC_FX,
        "SCHEDULED_LABOR_ONLY",
    )
    assert "vehicle" not in internal_cost_support_semantic_json(support_cut()).lower()
    assert "customer_price" not in internal_cost_support_semantic_json(support_cut())


@pytest.mark.parametrize(
    "bad",
    [Decimal("-0.01"), Decimal("1.001"), Decimal("NaN"), Decimal("Infinity"), 1.0, "1.00"],
)
def test_invalid_rate_values_fail_closed(bad):
    with pytest.raises(M5InternalCostSupportValidationError):
        source(amount=bad)


@pytest.mark.parametrize("bad", ["1,00", "1.001", "NaN", "Infinity", "-1.00", "bad", " 1.00"])
def test_malformed_or_noncanonical_rate_strings_fail_closed(bad):
    with pytest.raises(M5InternalCostSupportValidationError):
        parse_rate_amount(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", "0.00"), ("1", "1.00"), ("1.2", "1.20"), ("45.00", "45.00")],
)
def test_decimal_money_serialization_is_locale_independent(raw, expected):
    assert format_money(parse_rate_amount(raw)) == expected


def test_frozen_stefan_rate_canonicalization_and_restore_ignore_decimal_context():
    trusted = source(worker_id=STEFAN, amount=Decimal("45.00"))
    raw = rate_source_semantic_json(trusted)
    expected = (
        Decimal("45.00"),
        trusted.source_fingerprint,
        trusted.source_record_id,
        raw,
    )
    snapshots = []

    for precision, rounding in HOSTILE_DECIMAL_CONTEXTS:
        for representation in ("45", "45.0", "45.00"):
            with localcontext() as context:
                context.prec = precision
                context.rounding = rounding
                assert canonical_rate(Decimal(representation)) == Decimal("45.00")
                assert parse_rate_amount(representation) == Decimal("45.00")
                replay = restore_rate_source(
                    raw, trusted.source_fingerprint, trusted.source_record_id
                )
                snapshots.append(
                    (
                        replay.rate_amount,
                        replay.source_fingerprint,
                        replay.source_record_id,
                        rate_source_semantic_json(replay),
                    )
                )

    assert snapshots and all(snapshot == expected for snapshot in snapshots)


@pytest.mark.parametrize("raw_rate", PREDECESSOR_LARGE_RATE_TEXTS)
def test_predecessor_valid_large_rate_has_no_precision_boundary(raw_rate):
    expected_rate = Decimal(raw_rate)
    snapshots = []

    for precision, rounding in HOSTILE_DECIMAL_CONTEXTS:
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            assert canonical_rate(expected_rate) == expected_rate
            assert parse_rate_amount(raw_rate) == expected_rate
            trusted = source(worker_id=STEFAN, amount=expected_rate)
            replay = restore_rate_source(
                rate_source_semantic_json(trusted),
                trusted.source_fingerprint,
                trusted.source_record_id,
            )
            snapshots.append(
                (
                    replay.rate_amount,
                    replay.rate_amount.as_tuple().exponent,
                    replay.source_fingerprint,
                    replay.source_record_id,
                    rate_source_semantic_json(replay),
                )
            )

    assert snapshots
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert snapshots[0][0] == expected_rate
    assert snapshots[0][1] == -2
    assert f'"rate_amount":"{raw_rate}"' in snapshots[0][4]


def test_exact_rate_canonicalization_has_no_integer_string_digit_ceiling():
    predecessor_valid = Decimal("1e5000")
    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_DOWN
        canonical = canonical_rate(predecessor_valid)
        trusted = source(worker_id=STEFAN, amount=predecessor_valid)
        replay = restore_rate_source(
            rate_source_semantic_json(trusted),
            trusted.source_fingerprint,
            trusted.source_record_id,
        )

    assert canonical == predecessor_valid
    assert canonical.as_tuple().exponent == -2
    assert replay == trusted
    assert replay.rate_amount == predecessor_valid


def test_hostile_decimal_context_does_not_weaken_rate_validation():
    for precision, rounding in HOSTILE_DECIMAL_CONTEXTS:
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            assert canonical_rate(Decimal("0")) == Decimal("0.00")
            for invalid in (
                Decimal("-0.01"),
                Decimal("1.001"),
                Decimal("1.234"),
                Decimal("NaN"),
                Decimal("Infinity"),
                Decimal("-Infinity"),
            ):
                with pytest.raises(M5InternalCostSupportValidationError):
                    canonical_rate(invalid)
            for invalid in (
                "-0.01",
                "1.001",
                "1.234",
                "NaN",
                "Infinity",
                "-Infinity",
                "bad",
                " 45.00",
                "45,00",
            ):
                with pytest.raises(M5InternalCostSupportValidationError):
                    parse_rate_amount(invalid)


def test_hostile_decimal_context_still_rejects_refingerprinted_rate_corruption():
    trusted = source(worker_id=STEFAN, amount=Decimal("45.00"))
    document = json.loads(rate_source_semantic_json(trusted))
    document["rate_amount"] = "45.001"
    raw = canonical_json(document)
    fingerprint = sha256_text(raw)

    with localcontext() as context:
        context.prec = 1
        context.rounding = ROUND_DOWN
        with pytest.raises(M5InternalCostSupportStorageError):
            restore_rate_source(
                raw,
                fingerprint,
                "m5-internal-labor-rate-source-" + fingerprint,
            )


def test_rate_source_round_trip_and_identity_are_deterministic():
    first = source()
    replay = restore_rate_source(
        rate_source_semantic_json(first), first.source_fingerprint, first.source_record_id
    )
    assert replay == first
    assert replay.source_record_id == first.source_record_id
    assert '"rate_amount":"37.00"' in rate_source_semantic_json(first)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("currency", "USD"),
        ("rate_semantics", "salary"),
        ("configuration_id", "DEMO_workforce"),
        ("source_classification", "DEMO_SYNTHETIC"),
        ("access_classification", "PUBLIC"),
    ],
)
def test_refingerprinted_semantic_substitution_is_rejected(field, replacement):
    trusted = source()
    document = json.loads(rate_source_semantic_json(trusted))
    document[field] = replacement
    raw = canonical_json(document)
    fingerprint = sha256_text(raw)
    with pytest.raises(M5InternalCostSupportStorageError):
        restore_rate_source(
            raw,
            fingerprint,
            "m5-internal-labor-rate-source-" + fingerprint,
        )


def test_source_requires_worker_in_exact_captured_m2_registry():
    trusted = source()
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(trusted, worker_id="not-in-registry")


def test_source_revision_requires_parent_and_noninitial_effective_start():
    trusted = source()
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(trusted, source_revision=1, previous_source_record_id=None)
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(
            trusted,
            source_revision=1,
            previous_source_record_id=trusted.source_record_id,
            effective_from=None,
        )


def test_capture_identity_binds_generation_worker_revision_and_source():
    trusted = source()
    first = InternalCostSourceCapture(
        ledger_generation=1,
        source_record_id=trusted.source_record_id,
        source_fingerprint=trusted.source_fingerprint,
        worker_id=trusted.worker_id,
        source_revision=trusted.source_revision,
    )
    assert replace(first) == first
    assert replace(first, ledger_generation=2).capture_id != first.capture_id


def test_subject_universe_is_derived_from_exact_result_and_baseline_schedule():
    _, support, result = generated_case()
    subjects = derive_internal_cost_subjects(result, support)
    assert len(subjects) == sum(2 * len(item.modified_commitment_ids) for item in result.candidates)
    assert {item.scope for item in subjects} == {
        LaborCostSubjectScope.BASELINE,
        LaborCostSubjectScope.CANDIDATE,
    }
    for candidate_position, candidate in enumerate(result.candidates):
        scoped = tuple(item for item in subjects if item.candidate_position == candidate_position)
        assert {item.commitment_id for item in scoped} == set(candidate.modified_commitment_ids)
        assert all(item.candidate_id == candidate.candidate_id for item in scoped)


def test_subject_universe_includes_baseline_and_replacement_but_no_unrelated_worker():
    _, support, result = generated_case()
    subjects = derive_internal_cost_subjects(result, support)
    assert PETER in {item.worker_id for item in subjects if item.scope is LaborCostSubjectScope.BASELINE}
    assert ANNA in {item.worker_id for item in subjects if item.scope is LaborCostSubjectScope.CANDIDATE}
    assert "unrelated-worker" not in {item.worker_id for item in subjects}


def test_changed_candidate_or_interval_changes_subject_identity():
    subject = derive_internal_cost_subjects(*generated_case()[2:0:-1])[0]
    assert replace(subject, interval_end=subject.interval_end + timedelta(minutes=1)).subject_id != subject.subject_id
    assert replace(subject, worker_id=ANNA).subject_id != subject.subject_id


def test_no_source_is_explicit_and_cannot_carry_zero_or_selected_source():
    subject = derive_internal_cost_subjects(*generated_case()[2:0:-1])[0]
    selection = no_source_selection(subject)
    assert selection.status is RateSelectionStatus.NO_SOURCE
    assert selection.no_source_reason is NoSourceReason.NO_RATE_SOURCE
    assert not hasattr(selection, "rate_amount")
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(selection, source_revision=0)


def test_selected_source_requires_complete_exact_capture_tuple():
    subject = derive_internal_cost_subjects(*generated_case()[2:0:-1])[0]
    with pytest.raises(M5InternalCostSupportValidationError):
        InternalLaborRateSelection(
            subject_id=subject.subject_id,
            subject_fingerprint=subject.subject_fingerprint,
            status=RateSelectionStatus.SELECTED,
            source_record_id=None,
            source_fingerprint=None,
            source_revision=None,
            source_capture_id=None,
            source_capture_fingerprint=None,
            source_capture_generation=None,
            no_source_reason=None,
        )


def test_common_cut_canonicalizes_equivalent_subject_and_selection_order():
    trusted = support_cut()
    reordered = replace(
        trusted,
        subjects=tuple(reversed(trusted.subjects)),
        selections=tuple(reversed(trusted.selections)),
    )
    assert reordered == trusted
    assert reordered.support_id == trusted.support_id


def test_common_cut_round_trip_and_fingerprint_are_deterministic():
    trusted = support_cut()
    raw = internal_cost_support_semantic_json(trusted)
    replay = restore_internal_cost_support(raw, trusted.support_fingerprint, trusted.support_id)
    assert replay == trusted
    assert replay.support_id == trusted.support_id


@pytest.mark.parametrize(
    "change",
    [
        {"m3_result_fingerprint": "0" * 64},
        {"feasibility_support_snapshot_fingerprint": "0" * 64},
        {"base_plan_revision_fingerprint": "0" * 64},
        {"rule_fingerprint": "0" * 64},
    ],
)
def test_common_cut_rejects_mismatched_identity_bindings(change):
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(support_cut(), **change)


def test_common_cut_rejects_subject_omission_duplicate_and_candidate_order_change():
    trusted = support_cut()
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(trusted, subjects=trusted.subjects[:-1])
    with pytest.raises(M5InternalCostSupportValidationError):
        replace(trusted, subjects=trusted.subjects + (trusted.subjects[0],))
    if len(trusted.candidate_bindings) > 1:
        with pytest.raises(M5InternalCostSupportValidationError):
            replace(trusted, candidate_bindings=tuple(reversed(trusted.candidate_bindings)))


def test_models_are_frozen_and_expose_no_cost_delta_or_winner():
    trusted = support_cut()
    with pytest.raises(FrozenInstanceError):
        trusted.company_plan_id = "other"
    for forbidden in (
        "candidate_cost",
        "baseline_cost",
        "internal_cost_delta",
        "best_candidate",
        "recommended_candidate",
        "ranking_score",
    ):
        assert not hasattr(trusted, forbidden)
