from __future__ import annotations

import inspect
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tests.integration.test_feasibility_support_repository import (
    advance_worker_registry,
    prerequisites,
)
from werkcrew_ai.field.serialization import canonical_json, sha256_text
from werkcrew_ai.planning.bounded_feasibility import M3BoundedFeasibilityService
from werkcrew_ai.pricing.internal_cost_repository import M5InternalCostSupportRepository
from werkcrew_ai.pricing.internal_cost_support import (
    CONFIGURATION_ID,
    RATE_SEMANTICS,
    V1_INTERNAL_LABOR_RATES,
    InternalCostSourceCapture,
    InternalCostCandidateBinding,
    InternalLaborRateSelection,
    LaborCostSubjectScope,
    M5InternalCostSupportConflictError,
    M5InternalCostSupportStorageError,
    NoSourceReason,
    RateSelectionStatus,
    WorkerInternalCostRateRevision,
    current_internal_labor_cost_rule,
    rate_source_semantic_json,
    source_capture_semantic_json,
)


M5_TABLES = (
    "m5_internal_cost_rules",
    "m5_internal_labor_rate_sources",
    "m5_internal_cost_source_captures",
    "m5_internal_cost_support_cuts",
    "m5_internal_cost_support_candidates",
    "m5_internal_cost_support_subjects",
    "m5_internal_cost_support_selections",
)


def fixture(path):
    repository, evaluation, revision, m2, _, _ = prerequisites(path)
    current = m2.get_worker_registry()
    workers = tuple(sorted(set(current.worker_ids) | {item[0] for item in V1_INTERNAL_LABOR_RATES}))
    advance_worker_registry(m2, workers)
    support = repository.record_feasibility_support(evaluation.evaluation_input_id)
    return (
        M5InternalCostSupportRepository(path),
        evaluation,
        support,
        revision,
        m2,
    )


def table_rows(path, names):
    with sqlite3.connect(path) as connection:
        return tuple(
            (name, tuple(connection.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()))
            for name in names
        )


def selected_for(cut, *, worker_id=None, scope=None):
    subject_by_id = {item.subject_id: item for item in cut.subjects}
    return tuple(
        item
        for item in cut.selections
        if (worker_id is None or subject_by_id[item.subject_id].worker_id == worker_id)
        and (scope is None or subject_by_id[item.subject_id].scope is scope)
    )


def rewrite_source_and_capture_outside_normal_guards(
    repository,
    original,
    *,
    source_record_id,
    source_fingerprint,
    canonical_semantic_json,
    rule_id,
    rule_fingerprint,
):
    """Model an attacker with direct DB access, not a supported write path."""

    connection = repository._connect()
    try:
        capture_row = connection.execute(
            "SELECT * FROM m5_internal_cost_source_captures WHERE source_record_id=?",
            (original.source_record_id,),
        ).fetchone()
        replacement_capture = InternalCostSourceCapture(
            ledger_generation=capture_row["ledger_generation"],
            source_record_id=source_record_id,
            source_fingerprint=source_fingerprint,
            worker_id=original.worker_id,
            source_revision=original.source_revision,
        )
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TRIGGER m5_internal_labor_rate_sources_no_update")
        connection.execute("DROP TRIGGER m5_internal_cost_source_captures_no_update")
        connection.execute(
            """
            UPDATE m5_internal_labor_rate_sources
            SET source_record_id=?, source_fingerprint=?, rule_id=?,
                rule_fingerprint=?, canonical_semantic_json=?
            WHERE source_record_id=?
            """,
            (
                source_record_id,
                source_fingerprint,
                rule_id,
                rule_fingerprint,
                canonical_semantic_json,
                original.source_record_id,
            ),
        )
        connection.execute(
            """
            UPDATE m5_internal_cost_source_captures
            SET capture_id=?, capture_fingerprint=?, source_record_id=?,
                source_fingerprint=?, canonical_semantic_json=?
            WHERE capture_id=?
            """,
            (
                replacement_capture.capture_id,
                replacement_capture.capture_fingerprint,
                replacement_capture.source_record_id,
                replacement_capture.source_fingerprint,
                source_capture_semantic_json(replacement_capture),
                capture_row["capture_id"],
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def rewrite_source_value_outside_normal_guards(repository, original, replacement):
    rewrite_source_and_capture_outside_normal_guards(
        repository,
        original,
        source_record_id=replacement.source_record_id,
        source_fingerprint=replacement.source_fingerprint,
        canonical_semantic_json=rate_source_semantic_json(replacement),
        rule_id=replacement.rule_id,
        rule_fingerprint=replacement.rule_fingerprint,
    )


def test_v1_install_persists_exact_six_sources_rule_and_authentic_captures(tmp_path):
    path = tmp_path / "install.sqlite3"
    repository, *_ = fixture(path)

    sources = repository.install_v1_configuration()
    replay = repository.install_v1_configuration()

    assert sources == replay
    assert tuple((item.worker_id, item.rate_amount) for item in sources) == V1_INTERNAL_LABOR_RATES
    assert all(item.configuration_id == CONFIGURATION_ID for item in sources)
    assert all(item.rate_semantics == RATE_SEMANTICS for item in sources)
    assert all(item.effective_from is None for item in sources)
    assert dict((item.worker_id, item.rate_amount) for item in sources)["stefan-mueller"] == Decimal("45.00")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM m5_internal_cost_rules").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM m5_internal_labor_rate_sources").fetchone() == (6,)
        assert connection.execute("SELECT count(*) FROM m5_internal_cost_source_captures").fetchone() == (6,)
        assert tuple(
            row[0]
            for row in connection.execute(
                "SELECT ledger_generation FROM m5_internal_cost_source_captures ORDER BY ledger_generation"
            )
        ) == tuple(range(1, 7))


def test_configuration_install_requires_exact_canonical_m2_worker_identities(tmp_path):
    path = tmp_path / "missing-worker.sqlite3"
    repository, *_ = prerequisites(path)
    with pytest.raises(M5InternalCostSupportConflictError, match="CANONICAL_WORKER_IDENTITY_MISSING"):
        M5InternalCostSupportRepository(path).install_v1_configuration()


def test_common_cut_binds_exact_reconstructed_m3_result_and_order(tmp_path):
    path = tmp_path / "cut.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    result = M3BoundedFeasibilityService(repository).evaluate(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )

    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    replay = repository.get_internal_cost_support(cut.support_id)

    assert replay == cut
    assert (cut.m3_result_id, cut.m3_result_fingerprint) == (
        result.result_id,
        result.result_fingerprint,
    )
    assert tuple(item.candidate_id for item in cut.candidate_bindings) == tuple(
        item.candidate_id for item in result.candidates
    )
    assert all(item.source_capture_generation <= cut.source_cut_generation for item in cut.selections if item.status is RateSelectionStatus.SELECTED)


def test_subject_universe_includes_exact_baseline_and_candidate_placements_only(tmp_path):
    path = tmp_path / "subjects.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    result = M3BoundedFeasibilityService(repository).evaluate(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )

    assert len(cut.subjects) == sum(2 * len(item.modified_commitment_ids) for item in result.candidates)
    for position, candidate in enumerate(result.candidates):
        scoped = tuple(item for item in cut.subjects if item.candidate_position == position)
        assert {item.commitment_id for item in scoped} == set(candidate.modified_commitment_ids)
        assert {item.scope for item in scoped} == {
            LaborCostSubjectScope.BASELINE,
            LaborCostSubjectScope.CANDIDATE,
        }
    required_commitments = {
        commitment_id
        for candidate in result.candidates
        for commitment_id in candidate.modified_commitment_ids
    }
    assert {item.commitment_id for item in cut.subjects} <= required_commitments


def test_known_candidate_rate_selected_and_missing_baseline_is_not_zero(tmp_path):
    path = tmp_path / "known-and-unknown.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )

    baseline = selected_for(cut, scope=LaborCostSubjectScope.BASELINE)
    candidates = selected_for(cut, scope=LaborCostSubjectScope.CANDIDATE)
    assert baseline
    assert all(item.status is RateSelectionStatus.NO_SOURCE for item in baseline)
    assert all(item.no_source_reason is NoSourceReason.NO_RATE_SOURCE for item in baseline)
    assert any(item.status is RateSelectionStatus.SELECTED for item in candidates)
    assert all(not hasattr(item, "rate_amount") for item in cut.selections)


def test_capture_before_configuration_records_no_source_and_history_does_not_reinterpret(tmp_path):
    path = tmp_path / "historical-unknown.sqlite3"
    repository, evaluation, support, *_ = fixture(path)

    old = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    assert old.source_cut_generation == 0
    assert old.selections and all(item.status is RateSelectionStatus.NO_SOURCE for item in old.selections)

    repository.install_v1_configuration()
    current = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    replay = repository.get_internal_cost_support(old.support_id)

    assert replay == old
    assert all(item.status is RateSelectionStatus.NO_SOURCE for item in replay.selections)
    assert current.source_cut_generation == 6
    assert any(item.status is RateSelectionStatus.SELECTED for item in current.selections)


def test_r0_r1_history_and_backdated_revision_cannot_change_old_cut(tmp_path):
    path = tmp_path / "history.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    old = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    worker = next(
        subject.worker_id
        for subject in old.subjects
        if subject.scope is LaborCostSubjectScope.CANDIDATE
        and subject.worker_id in dict(V1_INTERNAL_LABOR_RATES)
    )
    subject = next(item for item in old.subjects if item.worker_id == worker and item.scope is LaborCostSubjectScope.CANDIDATE)
    r0 = next(item for item in sources if item.worker_id == worker)
    r1 = repository.append_internal_labor_rate_revision(
        worker_id=worker,
        rate_amount=r0.rate_amount + Decimal("1.00"),
        effective_from=subject.interval_start - timedelta(hours=1),
        expected_previous_source_record_id=r0.source_record_id,
    )
    current = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    current_replay = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    old_replay = repository.get_internal_cost_support(old.support_id)

    assert r1.source_revision == 1
    assert current_replay == current
    assert any(item.source_record_id == r0.source_record_id for item in selected_for(old_replay, worker_id=worker))
    assert any(item.source_record_id == r1.source_record_id for item in selected_for(current, worker_id=worker))
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m5_internal_labor_rate_sources WHERE worker_id=?",
            (worker,),
        ).fetchone() == (2,)


def test_placement_crossing_rate_boundary_is_explicit_multi_revision_unknown(tmp_path):
    path = tmp_path / "boundary.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    before = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    subject = next(
        item for item in before.subjects
        if item.scope is LaborCostSubjectScope.CANDIDATE
        and item.worker_id in dict(V1_INTERNAL_LABOR_RATES)
    )
    r0 = next(item for item in sources if item.worker_id == subject.worker_id)
    repository.append_internal_labor_rate_revision(
        worker_id=subject.worker_id,
        rate_amount=r0.rate_amount + Decimal("2.00"),
        effective_from=subject.interval_start + (subject.interval_end - subject.interval_start) / 2,
        expected_previous_source_record_id=r0.source_record_id,
    )
    after = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    selection = next(item for item in after.selections if item.subject_id == subject.subject_id)
    assert selection.status is RateSelectionStatus.NO_SOURCE
    assert selection.no_source_reason is NoSourceReason.MULTI_REVISION_COVERAGE_UNSUPPORTED


def test_overlapping_effective_interval_append_is_rejected_atomically(tmp_path):
    path = tmp_path / "overlapping-append.sqlite3"
    repository, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    r0 = next(item for item in sources if item.worker_id == "peter-berger")
    r1 = repository.append_internal_labor_rate_revision(
        worker_id=r0.worker_id,
        rate_amount=Decimal("38.00"),
        effective_from=datetime(2027, 1, 1, tzinfo=timezone.utc),
        effective_until=datetime(2027, 2, 1, tzinfo=timezone.utc),
        expected_previous_source_record_id=r0.source_record_id,
    )

    with pytest.raises(
        M5InternalCostSupportConflictError,
        match="OVERLAPPING_EFFECTIVE_INTERVAL",
    ):
        repository.append_internal_labor_rate_revision(
            worker_id=r1.worker_id,
            rate_amount=Decimal("39.00"),
            effective_from=datetime(2027, 1, 15, tzinfo=timezone.utc),
            expected_previous_source_record_id=r1.source_record_id,
        )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m5_internal_labor_rate_sources WHERE worker_id=?",
            (r0.worker_id,),
        ).fetchone() == (2,)


def test_adjacent_effective_intervals_are_accepted_and_replayable(tmp_path):
    path = tmp_path / "adjacent-intervals.sqlite3"
    repository, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    r0 = next(item for item in sources if item.worker_id == "peter-berger")
    boundary = datetime(2027, 2, 1, tzinfo=timezone.utc)
    r1 = repository.append_internal_labor_rate_revision(
        worker_id=r0.worker_id,
        rate_amount=Decimal("38.00"),
        effective_from=datetime(2027, 1, 1, tzinfo=timezone.utc),
        effective_until=boundary,
        expected_previous_source_record_id=r0.source_record_id,
    )
    r2 = repository.append_internal_labor_rate_revision(
        worker_id=r1.worker_id,
        rate_amount=Decimal("39.00"),
        effective_from=boundary,
        effective_until=datetime(2027, 3, 1, tzinfo=timezone.utc),
        expected_previous_source_record_id=r1.source_record_id,
    )

    assert r2.source_revision == 2
    assert M5InternalCostSupportRepository(path).install_v1_configuration() == sources


def test_refingerprinted_overlapping_persisted_history_is_rejected_on_read(tmp_path):
    path = tmp_path / "overlapping-history.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    r0 = next(item for item in sources if item.worker_id == "peter-berger")
    r1 = repository.append_internal_labor_rate_revision(
        worker_id=r0.worker_id,
        rate_amount=Decimal("38.00"),
        effective_from=datetime(2027, 1, 1, tzinfo=timezone.utc),
        effective_until=datetime(2027, 2, 1, tzinfo=timezone.utc),
        expected_previous_source_record_id=r0.source_record_id,
    )
    forged_r2 = WorkerInternalCostRateRevision(
        worker_id=r1.worker_id,
        rate_amount=Decimal("39.00"),
        source_revision=2,
        previous_source_record_id=r1.source_record_id,
        effective_from=datetime(2027, 1, 15, tzinfo=timezone.utc),
        effective_until=None,
        worker_registry_revision=r1.worker_registry_revision,
        worker_registry_fingerprint=r1.worker_registry_fingerprint,
        canonical_worker_registry_json=r1.canonical_worker_registry_json,
        rule_id=r1.rule_id,
        rule_fingerprint=r1.rule_fingerprint,
    )
    with repository.transaction() as connection:
        repository._insert_source_and_capture(connection, forged_r2)

    with pytest.raises(
        M5InternalCostSupportStorageError,
        match="OVERLAPPING_EFFECTIVE_INTERVAL",
    ):
        repository.capture_internal_cost_support(
            evaluation.evaluation_input_id, support.support_snapshot_id
        )


def test_ordinary_single_revision_still_selects_authoritative_rate(tmp_path):
    path = tmp_path / "single-revision.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )

    selected = tuple(
        item
        for item in cut.selections
        if item.status is RateSelectionStatus.SELECTED
    )
    assert selected
    assert {item.source_record_id for item in selected} <= {
        item.source_record_id for item in sources
    }


@pytest.mark.parametrize(
    ("worker_id", "forged_amount"),
    (("anna-fischer", Decimal("99.00")), ("peter-berger", Decimal("88.00"))),
)
def test_refingerprinted_frozen_r0_money_is_rejected_on_capture(
    tmp_path, worker_id, forged_amount
):
    path = tmp_path / f"forged-r0-{worker_id}.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    original = next(item for item in sources if item.worker_id == worker_id)
    forged = replace(original, rate_amount=forged_amount)
    rewrite_source_value_outside_normal_guards(repository, original, forged)

    with pytest.raises(
        M5InternalCostSupportStorageError,
        match="V1_CONFIGURATION_MISMATCH",
    ):
        repository.capture_internal_cost_support(
            evaluation.evaluation_input_id, support.support_snapshot_id
        )


def test_self_consistent_foreign_rule_binding_is_rejected_on_capture(tmp_path):
    path = tmp_path / "foreign-rule.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    original = next(item for item in sources if item.worker_id == "anna-fischer")
    foreign_fingerprint = sha256_text("foreign-internal-cost-rule")
    forged = replace(
        original,
        rule_id="m5-internal-cost-rule-" + foreign_fingerprint,
        rule_fingerprint=foreign_fingerprint,
    )
    rewrite_source_value_outside_normal_guards(repository, original, forged)

    with pytest.raises(
        M5InternalCostSupportStorageError,
        match="RATE_SOURCE_RULE_AUTHORITY_MISMATCH",
    ):
        repository.capture_internal_cost_support(
            evaluation.evaluation_input_id, support.support_snapshot_id
        )


def test_existing_rule_id_with_wrong_fingerprint_is_rejected_on_capture(tmp_path):
    path = tmp_path / "wrong-rule-fingerprint.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    original = next(item for item in sources if item.worker_id == "anna-fischer")
    document = json.loads(rate_source_semantic_json(original))
    wrong_fingerprint = sha256_text("wrong-rule-fingerprint")
    document["rule_fingerprint"] = wrong_fingerprint
    raw = canonical_json(document)
    source_fingerprint = sha256_text(raw)
    rewrite_source_and_capture_outside_normal_guards(
        repository,
        original,
        source_record_id="m5-internal-labor-rate-source-" + source_fingerprint,
        source_fingerprint=source_fingerprint,
        canonical_semantic_json=raw,
        rule_id=original.rule_id,
        rule_fingerprint=wrong_fingerprint,
    )

    with pytest.raises(M5InternalCostSupportStorageError, match="INVALID_RATE_SOURCE"):
        repository.capture_internal_cost_support(
            evaluation.evaluation_input_id, support.support_snapshot_id
        )


def test_stale_parent_and_nonmonotonic_effective_revision_fail_atomically(tmp_path):
    path = tmp_path / "stale.sqlite3"
    repository, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    anna = next(item for item in sources if item.worker_id == "anna-fischer")
    with pytest.raises(M5InternalCostSupportConflictError, match="STALE_RATE_SOURCE_PARENT"):
        repository.append_internal_labor_rate_revision(
            worker_id=anna.worker_id,
            rate_amount=Decimal("38.00"),
            effective_from=anna.effective_from or __import__("datetime").datetime(2027, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            expected_previous_source_record_id="wrong-parent",
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m5_internal_labor_rate_sources WHERE worker_id=?",
            (anna.worker_id,),
        ).fetchone() == (1,)


def test_orphan_self_consistent_source_without_capture_is_rejected_on_read(tmp_path):
    path = tmp_path / "orphan.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    prior = next(item for item in sources if item.worker_id == "anna-fischer")
    worker_registry = prior.canonical_worker_registry_json
    forged = WorkerInternalCostRateRevision(
        worker_id=prior.worker_id,
        rate_amount=Decimal("39.00"),
        source_revision=1,
        previous_source_record_id=prior.source_record_id,
        effective_from=cut.subjects[0].interval_start,
        effective_until=None,
        worker_registry_revision=prior.worker_registry_revision,
        worker_registry_fingerprint=prior.worker_registry_fingerprint,
        canonical_worker_registry_json=worker_registry,
        rule_id=prior.rule_id,
        rule_fingerprint=prior.rule_fingerprint,
    )
    with repository.transaction() as connection:
        connection.execute(
            """
            INSERT INTO m5_internal_labor_rate_sources(
                source_record_id, source_fingerprint, worker_id, source_revision,
                previous_source_record_id, effective_from, effective_until,
                worker_registry_revision, worker_registry_fingerprint,
                canonical_worker_registry_json, rule_id, rule_fingerprint,
                schema_version, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                forged.source_record_id, forged.source_fingerprint, forged.worker_id,
                forged.source_revision, forged.previous_source_record_id,
                forged.effective_from.isoformat(), None, forged.worker_registry_revision,
                forged.worker_registry_fingerprint, forged.canonical_worker_registry_json,
                forged.rule_id, forged.rule_fingerprint, forged.schema_version,
                rate_source_semantic_json(forged),
            ),
        )
    with pytest.raises(M5InternalCostSupportStorageError, match="SOURCE_CAPTURE_COVERAGE_MISMATCH"):
        repository.get_internal_cost_support(cut.support_id)


def test_capture_generation_cannot_be_backdated_or_duplicated(tmp_path):
    path = tmp_path / "generation.sqlite3"
    repository, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    source = sources[0]
    forged = InternalCostSourceCapture(
        ledger_generation=6,
        source_record_id=source.source_record_id,
        source_fingerprint=source.source_fingerprint,
        worker_id=source.worker_id,
        source_revision=source.source_revision,
    )
    with repository.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO m5_internal_cost_source_captures VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    forged.capture_id, forged.capture_fingerprint,
                    forged.ledger_generation, forged.source_record_id,
                    forged.source_fingerprint, forged.worker_id,
                    forged.source_revision, forged.schema_version, "{}",
                ),
            )


@pytest.mark.parametrize("recursive", [0, 1])
@pytest.mark.parametrize("table", M5_TABLES)
def test_all_m5_identity_tables_are_without_rowid_and_immutable(tmp_path, recursive, table):
    path = tmp_path / f"guards-{recursive}-{table}.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    with repository.transaction() as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert "WITHOUT ROWID" in sql.upper()
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(f'SELECT rowid FROM "{table}"').fetchall()
        connection.execute(f"PRAGMA recursive_triggers={recursive}")
        for operation in (
            f'UPDATE "{table}" SET {connection.execute(f"PRAGMA table_info({table})").fetchone()[1]}={connection.execute(f"PRAGMA table_info({table})").fetchone()[1]}',
            f'DELETE FROM "{table}"',
            f'INSERT OR REPLACE INTO "{table}" SELECT * FROM "{table}"',
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(operation)


def test_restart_reconstructs_exact_cut_and_rate_history(tmp_path):
    path = tmp_path / "restart.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    sources = repository.install_v1_configuration()
    cut = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    restarted = M5InternalCostSupportRepository(path)
    assert restarted.install_v1_configuration() == sources
    assert restarted.get_internal_cost_support(cut.support_id) == cut
    assert restarted.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    ) == cut


def test_capture_does_not_mutate_closed_m2_or_m3_atoms(tmp_path):
    path = tmp_path / "read-only-predecessors.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    with sqlite3.connect(path) as connection:
        names = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND (name LIKE 'm2_%' OR name LIKE 'm3_%')
                ORDER BY name
                """
            )
        )
    before = table_rows(path, names)
    repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    assert table_rows(path, names) == before


def test_capture_api_accepts_no_worker_rate_candidate_or_selection_input():
    parameters = inspect.signature(
        M5InternalCostSupportRepository.capture_internal_cost_support
    ).parameters
    assert tuple(parameters) == (
        "self",
        "evaluation_input_id",
        "feasibility_support_snapshot_id",
    )


def test_gen1_and_public_pricing_identifiers_never_enter_authoritative_sources(tmp_path):
    import werkcrew_ai.pricing as legacy_pricing

    path = tmp_path / "separation.sqlite3"
    repository, *_ = fixture(path)
    repository.install_v1_configuration()
    with sqlite3.connect(path) as connection:
        raw = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT canonical_semantic_json FROM m5_internal_labor_rate_sources"
            )
        )
    for forbidden in (
        "emp-anna-demo",
        "emp-peter-demo",
        "EmployeeRateSnapshot",
        "DEMO_workforce",
        "waterproof.bath",
        "customer_rate",
        "billing_rate",
    ):
        assert forbidden not in raw
    assert not hasattr(legacy_pricing, "V1_INTERNAL_LABOR_RATES")
    assert not hasattr(legacy_pricing, "M5InternalCostSupportRepository")


def test_concurrent_install_and_capture_serialize_to_one_coherent_cut(tmp_path):
    path = tmp_path / "race.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    barrier = threading.Barrier(2)
    values = {}
    errors = []

    def install():
        try:
            barrier.wait()
            values["sources"] = M5InternalCostSupportRepository(path).install_v1_configuration()
        except Exception as error:  # pragma: no cover - diagnostic collection
            errors.append(error)

    def capture():
        try:
            barrier.wait()
            values["cut"] = M5InternalCostSupportRepository(path).capture_internal_cost_support(
                evaluation.evaluation_input_id, support.support_snapshot_id
            )
        except Exception as error:  # pragma: no cover - diagnostic collection
            errors.append(error)

    threads = (threading.Thread(target=install), threading.Thread(target=capture))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    cut = M5InternalCostSupportRepository(path).get_internal_cost_support(values["cut"].support_id)
    assert cut.source_cut_generation in (0, 6)
    known_candidate_selections = tuple(
        item
        for item in selected_for(cut, scope=LaborCostSubjectScope.CANDIDATE)
        if next(subject for subject in cut.subjects if subject.subject_id == item.subject_id).worker_id
        in dict(V1_INTERNAL_LABOR_RATES)
    )
    statuses = {item.status for item in known_candidate_selections}
    assert statuses in ({RateSelectionStatus.NO_SOURCE}, {RateSelectionStatus.SELECTED})


def test_common_cut_is_deterministic_and_all_candidates_share_one_source_head(tmp_path):
    path = tmp_path / "deterministic.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    first = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    replay = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    assert replay == first
    assert replay.support_id == first.support_id
    assert {
        item.source_capture_generation
        for item in replay.selections
        if item.status is RateSelectionStatus.SELECTED
    } <= set(range(1, replay.source_cut_generation + 1))


def persist_forged_cut(repository, value):
    with repository.transaction() as connection:
        repository._persist_cut(connection, value)


def test_known_source_cannot_be_forged_to_no_source_in_refingerprinted_cut(tmp_path):
    path = tmp_path / "forged-no-source.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    trusted = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    selected = next(item for item in trusted.selections if item.status is RateSelectionStatus.SELECTED)
    forged_selection = InternalLaborRateSelection(
        subject_id=selected.subject_id,
        subject_fingerprint=selected.subject_fingerprint,
        status=RateSelectionStatus.NO_SOURCE,
        source_record_id=None,
        source_fingerprint=None,
        source_revision=None,
        source_capture_id=None,
        source_capture_fingerprint=None,
        source_capture_generation=None,
        no_source_reason=NoSourceReason.NO_RATE_SOURCE,
    )
    forged = replace(
        trusted,
        selections=tuple(
            forged_selection if item.subject_id == selected.subject_id else item
            for item in trusted.selections
        ),
    )
    persist_forged_cut(repository, forged)
    with pytest.raises(M5InternalCostSupportStorageError, match="SUPPORT_DERIVATION_MISMATCH"):
        repository.get_internal_cost_support(forged.support_id)


def test_another_workers_valid_source_cannot_satisfy_subject(tmp_path):
    path = tmp_path / "wrong-worker-source.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    trusted = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    selected = next(item for item in trusted.selections if item.status is RateSelectionStatus.SELECTED)
    subject = next(item for item in trusted.subjects if item.subject_id == selected.subject_id)
    other_worker = next(worker for worker, _ in V1_INTERNAL_LABOR_RATES if worker != subject.worker_id)
    with repository._read_snapshot() as connection:
        source_row = connection.execute(
            "SELECT * FROM m5_internal_labor_rate_sources WHERE worker_id=? AND source_revision=0",
            (other_worker,),
        ).fetchone()
        capture_row = connection.execute(
            "SELECT * FROM m5_internal_cost_source_captures WHERE source_record_id=?",
            (source_row["source_record_id"],),
        ).fetchone()
    wrong = InternalLaborRateSelection(
        subject_id=subject.subject_id,
        subject_fingerprint=subject.subject_fingerprint,
        status=RateSelectionStatus.SELECTED,
        source_record_id=source_row["source_record_id"],
        source_fingerprint=source_row["source_fingerprint"],
        source_revision=source_row["source_revision"],
        source_capture_id=capture_row["capture_id"],
        source_capture_fingerprint=capture_row["capture_fingerprint"],
        source_capture_generation=capture_row["ledger_generation"],
        no_source_reason=None,
    )
    forged = replace(
        trusted,
        selections=tuple(
            wrong if item.subject_id == subject.subject_id else item
            for item in trusted.selections
        ),
    )
    persist_forged_cut(repository, forged)
    with pytest.raises(M5InternalCostSupportStorageError, match="SUPPORT_DERIVATION_MISMATCH"):
        repository.get_internal_cost_support(forged.support_id)


@pytest.mark.parametrize("mutation", ["omit", "add"])
def test_candidate_set_omission_or_addition_is_rejected_on_historical_read(tmp_path, mutation):
    path = tmp_path / f"candidate-{mutation}.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    trusted = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    assert len(trusted.candidate_bindings) > 1
    if mutation == "omit":
        removed_position = trusted.candidate_bindings[-1].position
        bindings = trusted.candidate_bindings[:-1]
        subjects = tuple(item for item in trusted.subjects if item.candidate_position != removed_position)
        subject_ids = {item.subject_id for item in subjects}
        selections = tuple(item for item in trusted.selections if item.subject_id in subject_ids)
    else:
        fingerprint = sha256_text("invented-candidate")
        bindings = trusted.candidate_bindings + (
            InternalCostCandidateBinding(
                position=len(trusted.candidate_bindings),
                candidate_id="m3-candidate-" + fingerprint,
                candidate_fingerprint=fingerprint,
            ),
        )
        subjects = trusted.subjects
        selections = trusted.selections
    forged = replace(
        trusted,
        candidate_bindings=bindings,
        subjects=subjects,
        selections=selections,
    )
    persist_forged_cut(repository, forged)
    with pytest.raises(M5InternalCostSupportStorageError, match="SUPPORT_DERIVATION_MISMATCH"):
        repository.get_internal_cost_support(forged.support_id)


def test_required_subject_omission_is_rejected_on_historical_read(tmp_path):
    path = tmp_path / "subject-omission.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    trusted = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    omitted = trusted.subjects[-1]
    forged = replace(
        trusted,
        subjects=trusted.subjects[:-1],
        selections=tuple(item for item in trusted.selections if item.subject_id != omitted.subject_id),
    )
    persist_forged_cut(repository, forged)
    with pytest.raises(M5InternalCostSupportStorageError, match="SUPPORT_DERIVATION_MISMATCH"):
        repository.get_internal_cost_support(forged.support_id)


def test_self_consistent_foreign_m3_result_identity_is_rejected(tmp_path):
    path = tmp_path / "foreign-result.sqlite3"
    repository, evaluation, support, *_ = fixture(path)
    repository.install_v1_configuration()
    trusted = repository.capture_internal_cost_support(
        evaluation.evaluation_input_id, support.support_snapshot_id
    )
    fingerprint = sha256_text("foreign-m3-result")
    forged = replace(
        trusted,
        m3_result_fingerprint=fingerprint,
        m3_result_id="m3-feasibility-result-" + fingerprint,
    )
    persist_forged_cut(repository, forged)
    with pytest.raises(M5InternalCostSupportStorageError, match="SUPPORT_DERIVATION_MISMATCH"):
        repository.get_internal_cost_support(forged.support_id)
