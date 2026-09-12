from __future__ import annotations

import inspect
import sqlite3
from datetime import timedelta
from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_UP,
    localcontext,
)

from tests.integration.test_feasibility_support_repository import (
    NOW,
    advance_worker_registry,
    prerequisites,
)
from tests.integration.test_internal_cost_support_repository import table_rows
from werkcrew_ai.planning.feasibility_support import (
    AvailabilityWindowEvidence,
    ReadinessState,
    TaskReadinessSource,
    WorkerAvailabilitySource,
)
from werkcrew_ai.pricing.internal_cost_repository import M5InternalCostSupportRepository
from werkcrew_ai.pricing.internal_cost_support import (
    V1_INTERNAL_LABOR_RATES,
    LaborCostSubjectScope,
    rate_source_semantic_json,
)
from werkcrew_ai.pricing.internal_labor_cost_consequence import (
    LaborCostCompleteness,
    M3InternalLaborCostConsequenceService,
    serialize_internal_labor_cost_consequence_result,
)


def feasible_fixture(path):
    repository, evaluation, revision, m2, *_ = prerequisites(path)
    current = m2.get_worker_registry()
    advance_worker_registry(
        m2,
        tuple(sorted(set(current.worker_ids) | {"stefan-mueller"})),
    )
    repository.record_worker_availability(
        WorkerAvailabilitySource(
            worker_id="stefan-mueller",
            coverage_start=NOW,
            coverage_end=NOW + timedelta(hours=10),
            available_windows=(
                AvailabilityWindowEvidence(
                    start_at=NOW, end_at=NOW + timedelta(hours=10)
                ),
            ),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="m3-d-test-stefan-availability",
        )
    )
    repository.record_task_readiness(
        TaskReadinessSource(
            job_id="job-1",
            task_id="task-1",
            task_definition_version="task-1-v1",
            status=ReadinessState.READY,
            expected_at=None,
            blocker_references=(),
            source_revision=0,
            previous_source_record_id=None,
            provenance_reference="m3-d-test-task-ready",
        )
    )
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    captured_registry = m2.get_worker_registry()
    advance_worker_registry(
        m2,
        tuple(
            sorted(
                set(captured_registry.worker_ids)
                | {worker_id for worker_id, _ in V1_INTERNAL_LABOR_RATES}
            )
        ),
    )
    return M5InternalCostSupportRepository(path), evaluation, support, revision, m2


def all_business_tables(path):
    with sqlite3.connect(path) as connection:
        return tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table'
                  AND (name LIKE 'm2_%' OR name LIKE 'm3_%' OR name LIKE 'm5_%')
                ORDER BY name
                """
            )
        )


def test_repository_service_restart_is_deterministic_and_read_only(tmp_path):
    path = tmp_path / "m3-d-restart.sqlite3"
    repository, evaluation, support, *_ = feasible_fixture(path)
    repository.install_v1_configuration()
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    names = all_business_tables(path)
    before = table_rows(path, names)

    first = M3InternalLaborCostConsequenceService(repository).calculate(cut.support_id)
    replay = M3InternalLaborCostConsequenceService(type(repository)(path)).calculate(
        cut.support_id
    )

    assert first == replay
    assert first.result_id == replay.result_id
    assert serialize_internal_labor_cost_consequence_result(first) == (
        serialize_internal_labor_cost_consequence_result(replay)
    )
    assert first.consequences
    assert all(
        item.completeness is LaborCostCompleteness.INCOMPLETE
        for item in first.consequences
    )
    assert table_rows(path, names) == before


def test_sqlite_restart_replay_ignores_ambient_decimal_context(tmp_path):
    path = tmp_path / "m3-d-decimal-context-replay.sqlite3"
    repository, evaluation, support, *_ = feasible_fixture(path)
    repository.install_v1_configuration()
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    names = all_business_tables(path)
    before = table_rows(path, names)
    contexts = (
        ("prec1-down", 1, ROUND_DOWN),
        ("prec2-down", 2, ROUND_DOWN),
        ("prec2-up", 2, ROUND_UP),
        ("default", 28, ROUND_HALF_EVEN),
        ("prec80-ceiling", 80, ROUND_CEILING),
    )
    snapshots = {}

    for label, precision, rounding in contexts:
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            reader = type(repository)(path)
            evidence = reader.get_internal_cost_consequence_evidence(cut.support_id)
            replayed_cut = evidence[3]
            stefan = next(
                source
                for source in evidence[4]
                if source.worker_id == "stefan-mueller"
            )
            value = M3InternalLaborCostConsequenceService(
                type(repository)(path)
            ).calculate(cut.support_id)
            snapshots[label] = (
                stefan.rate_amount,
                stefan.source_fingerprint,
                stefan.source_record_id,
                replayed_cut.support_fingerprint,
                replayed_cut.support_id,
                tuple(
                    (
                        line.rate_amount,
                        line.labor_amount,
                        line.line_fingerprint,
                        line.line_id,
                    )
                    for consequence in value.consequences
                    for line in consequence.baseline_lines + consequence.candidate_lines
                ),
                tuple(
                    (
                        consequence.baseline_internal_labor_cost,
                        consequence.candidate_internal_labor_cost,
                        consequence.internal_labor_cost_delta,
                        consequence.consequence_fingerprint,
                        consequence.consequence_id,
                    )
                    for consequence in value.consequences
                ),
                value.result_fingerprint,
                value.result_id,
                serialize_internal_labor_cost_consequence_result(value),
            )

    baseline = snapshots["default"]
    assert baseline[0] == Decimal("45.00")
    assert baseline[4] == cut.support_id
    assert all(snapshot == baseline for snapshot in snapshots.values())
    assert table_rows(path, names) == before
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            """
            SELECT canonical_semantic_json
            FROM m5_internal_labor_rate_sources
            WHERE worker_id='stefan-mueller' AND source_revision=0
            """
        ).fetchone()[0]
    assert '"rate_amount":"45.00"' in stored


def test_predecessor_large_rate_survives_sqlite_history_and_m3d_replay(tmp_path):
    path = tmp_path / "m3-d-predecessor-large-rate.sqlite3"
    repository, evaluation, support, *_ = feasible_fixture(path)
    sources = repository.install_v1_configuration()
    stefan_r0 = next(
        source for source in sources if source.worker_id == "stefan-mueller"
    )
    initial_cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    stefan_starts = tuple(
        subject.interval_start
        for subject in initial_cut.subjects
        if subject.worker_id == "stefan-mueller"
        and subject.scope is LaborCostSubjectScope.CANDIDATE
    )
    predecessor_rate = Decimal("1234567890123456789012345678.00")
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_HALF_EVEN
        historical_source = repository.append_internal_labor_rate_revision(
            worker_id="stefan-mueller",
            rate_amount=predecessor_rate,
            effective_from=min(stefan_starts) - timedelta(hours=1),
            expected_previous_source_record_id=stefan_r0.source_record_id,
        )
    historical_cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    names = all_business_tables(path)
    before = table_rows(path, names)
    contexts = (
        ("prec1-down", 1, ROUND_DOWN),
        ("prec2-up", 2, ROUND_UP),
        ("prec3-floor", 3, ROUND_FLOOR),
        ("default", 28, ROUND_HALF_EVEN),
        ("prec80-ceiling", 80, ROUND_CEILING),
    )
    snapshots = {}

    for label, precision, rounding in contexts:
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            reader = type(repository)(path)
            evidence = reader.get_internal_cost_consequence_evidence(
                historical_cut.support_id
            )
            replayed_source = next(
                source
                for source in evidence[4]
                if source.source_record_id == historical_source.source_record_id
            )
            value = M3InternalLaborCostConsequenceService(
                type(repository)(path)
            ).calculate(historical_cut.support_id)
            consequence, line = next(
                (consequence, line)
                for consequence in value.consequences
                for line in consequence.candidate_lines
                if line.source_record_id == historical_source.source_record_id
            )
            snapshots[label] = (
                replayed_source.rate_amount,
                rate_source_semantic_json(replayed_source),
                replayed_source.source_fingerprint,
                replayed_source.source_record_id,
                evidence[3].support_fingerprint,
                evidence[3].support_id,
                line.labor_amount,
                line.line_fingerprint,
                line.line_id,
                consequence.baseline_internal_labor_cost,
                consequence.candidate_internal_labor_cost,
                consequence.internal_labor_cost_delta,
                consequence.consequence_fingerprint,
                consequence.consequence_id,
                value.result_fingerprint,
                value.result_id,
                serialize_internal_labor_cost_consequence_result(value),
            )

    baseline = snapshots["default"]
    assert historical_source.rate_amount == predecessor_rate
    assert historical_source.rate_amount.as_tuple().exponent == -2
    assert f'"rate_amount":"{predecessor_rate}"' in rate_source_semantic_json(
        historical_source
    )
    assert baseline[0] == predecessor_rate
    assert baseline[3] == historical_source.source_record_id
    assert baseline[5] == historical_cut.support_id
    assert baseline[6] == predecessor_rate
    assert all(snapshot == baseline for snapshot in snapshots.values())
    assert table_rows(path, names) == before


def test_historical_no_source_never_becomes_zero_after_configuration_install(tmp_path):
    path = tmp_path / "m3-d-historical-no-source.sqlite3"
    repository, evaluation, support, *_ = feasible_fixture(path)
    old_cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    old_before = M3InternalLaborCostConsequenceService(repository).calculate(
        old_cut.support_id
    )

    repository.install_v1_configuration()
    old_after = M3InternalLaborCostConsequenceService(repository).calculate(
        old_cut.support_id
    )
    current_cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    current = M3InternalLaborCostConsequenceService(repository).calculate(
        current_cut.support_id
    )

    assert old_after == old_before
    assert all(
        line.rate_amount is None and line.labor_amount is None
        for item in old_after.consequences
        for line in item.baseline_lines + item.candidate_lines
    )
    assert all(item.internal_labor_cost_delta is None for item in old_after.consequences)
    assert any(
        line.rate_amount is not None
        for item in current.consequences
        for line in item.candidate_lines
    )


def test_later_rate_revision_does_not_reinterpret_old_cost_support(tmp_path):
    path = tmp_path / "m3-d-historical-rate.sqlite3"
    repository, evaluation, support, *_ = feasible_fixture(path)
    sources = repository.install_v1_configuration()
    old_cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    old = M3InternalLaborCostConsequenceService(repository).calculate(old_cut.support_id)
    old_consequence, old_line = next(
        (item, line)
        for item in old.consequences
        for line in item.candidate_lines
        if line.rate_amount is not None
    )
    r0 = next(item for item in sources if item.worker_id == old_line.worker_id)
    worker_starts = tuple(
        subject.interval_start
        for subject in old_cut.subjects
        if subject.worker_id == old_line.worker_id
        and subject.scope is LaborCostSubjectScope.CANDIDATE
    )
    r1 = repository.append_internal_labor_rate_revision(
        worker_id=r0.worker_id,
        rate_amount=r0.rate_amount + Decimal("1.00"),
        effective_from=min(worker_starts) - timedelta(hours=1),
        expected_previous_source_record_id=r0.source_record_id,
    )
    current_cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    current = M3InternalLaborCostConsequenceService(repository).calculate(
        current_cut.support_id
    )
    current_line = next(
        line
        for item in current.consequences
        if item.candidate_id == old_consequence.candidate_id
        for line in item.candidate_lines
        if line.commitment_id == old_line.commitment_id
    )

    assert r1.source_revision == 1
    assert old_line.source_record_id == r0.source_record_id
    assert current_line.source_record_id == r1.source_record_id
    assert current_line.rate_amount == r1.rate_amount
    assert M3InternalLaborCostConsequenceService(repository).calculate(
        old_cut.support_id
    ) == old


def test_m3d_api_accepts_only_the_internal_cost_support_identity():
    parameters = inspect.signature(
        M3InternalLaborCostConsequenceService.calculate
    ).parameters
    assert tuple(parameters) == ("self", "internal_cost_support_id")
