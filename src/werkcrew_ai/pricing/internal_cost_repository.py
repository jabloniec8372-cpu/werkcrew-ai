"""SQLite authority for Gen2 internal scheduled-labor-cost support evidence."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from werkcrew_ai.field.repository import M2DurableRepository, M2PersistenceError
from werkcrew_ai.field.serialization import serialize_worker_registry, sha256_text
from werkcrew_ai.planning.bounded_feasibility import (
    M3BoundedFeasibilityResult,
    generate_bounded_repair_candidates,
)
from werkcrew_ai.planning.feasibility_repository import (
    M3FeasibilitySupportRepository,
)
from werkcrew_ai.planning.feasibility_support import FeasibilitySupportSnapshot
from werkcrew_ai.pricing.internal_cost_support import (
    CONFIGURATION_ID,
    RATE_SEMANTICS,
    RULE_VERSION,
    V1_INTERNAL_LABOR_RATES,
    InternalCostCandidateBinding,
    InternalCostSourceCapture,
    InternalCostSupportCut,
    InternalLaborCostRule,
    InternalLaborCostSubject,
    InternalLaborRateSelection,
    LaborCostSubjectScope,
    M5InternalCostSupportConflictError,
    M5InternalCostSupportStorageError,
    M5InternalCostSupportValidationError,
    NoSourceReason,
    RateSelectionStatus,
    WorkerInternalCostRateRevision,
    current_internal_labor_cost_rule,
    internal_cost_rule_semantic_json,
    internal_cost_support_semantic_json,
    labor_cost_subject_semantic_json,
    rate_selection_semantic_json,
    rate_source_semantic_json,
    restore_internal_cost_rule,
    restore_internal_cost_support,
    restore_rate_source,
    restore_source_capture,
    source_capture_semantic_json,
)


def derive_internal_cost_subjects(
    result: M3BoundedFeasibilityResult,
    support: FeasibilitySupportSnapshot,
) -> tuple[InternalLaborCostSubject, ...]:
    """Derive the complete bounded baseline/candidate universe; no caller list."""

    if type(result) is not M3BoundedFeasibilityResult:
        raise M5InternalCostSupportValidationError("INVALID_VALUE", "m3_result")
    if type(support) is not FeasibilitySupportSnapshot:
        raise M5InternalCostSupportValidationError("INVALID_VALUE", "feasibility_support")
    if (
        result.source_evaluation_input_id,
        result.source_evaluation_input_fingerprint,
        result.source_support_snapshot_id,
        result.source_support_snapshot_fingerprint,
        result.company_plan_id,
        result.base_plan_revision,
    ) != (
        support.evaluation_input_id,
        support.evaluation_input_fingerprint,
        support.support_snapshot_id,
        support.support_fingerprint,
        support.company_plan_id,
        support.base_plan_revision,
    ):
        raise M5InternalCostSupportConflictError(
            "M3_RESULT_SUPPORT_BINDING_MISMATCH", "m3_result"
        )
    base_by_commitment = {
        item.commitment_id: item for item in support.schedule.placements
    }
    if len(base_by_commitment) != len(support.schedule.placements):
        raise M5InternalCostSupportConflictError(
            "BASE_SCHEDULE_DUPLICATE_COMMITMENT", "schedule"
        )
    subjects: list[InternalLaborCostSubject] = []
    for position, candidate in enumerate(result.candidates):
        if (
            candidate.source_evaluation_input_id,
            candidate.source_evaluation_input_fingerprint,
            candidate.source_support_snapshot_id,
            candidate.source_support_snapshot_fingerprint,
            candidate.company_plan_id,
            candidate.base_plan_revision,
            candidate.base_plan_revision_id,
            candidate.base_plan_revision_fingerprint,
        ) != (
            support.evaluation_input_id,
            support.evaluation_input_fingerprint,
            support.support_snapshot_id,
            support.support_fingerprint,
            support.company_plan_id,
            support.base_plan_revision,
            support.base_plan_revision_id,
            support.base_plan_revision_fingerprint,
        ):
            raise M5InternalCostSupportConflictError(
                "CANDIDATE_SUPPORT_BINDING_MISMATCH", "candidate"
            )
        proposed_by_commitment = {
            item.commitment_id: item for item in candidate.proposed_worker_placements
        }
        if set(proposed_by_commitment) != set(candidate.modified_commitment_ids):
            raise M5InternalCostSupportConflictError(
                "CANDIDATE_MODIFIED_SCOPE_MISMATCH", "candidate"
            )
        for commitment_id in candidate.modified_commitment_ids:
            baseline = base_by_commitment.get(commitment_id)
            proposed = proposed_by_commitment[commitment_id]
            if baseline is None or len(baseline.worker_ids) != 1:
                raise M5InternalCostSupportConflictError(
                    "BASE_SINGLE_PLACEMENT_MISSING", "baseline"
                )
            common = dict(
                candidate_position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
                commitment_id=commitment_id,
            )
            subjects.append(
                InternalLaborCostSubject(
                    **common,
                    scope=LaborCostSubjectScope.BASELINE,
                    worker_id=baseline.worker_ids[0],
                    interval_start=baseline.planned_start,
                    interval_end=baseline.planned_end,
                )
            )
            subjects.append(
                InternalLaborCostSubject(
                    **common,
                    scope=LaborCostSubjectScope.CANDIDATE,
                    worker_id=proposed.worker_id,
                    interval_start=proposed.proposed_start,
                    interval_end=proposed.proposed_end,
                )
            )
    return tuple(
        sorted(
            subjects,
            key=lambda item: (
                item.candidate_position,
                item.scope.value,
                item.commitment_id,
                item.worker_id,
                item.interval_start,
                item.interval_end,
            ),
        )
    )


class M5InternalCostSupportRepository(M3FeasibilitySupportRepository):
    """Privileged M5 source ingestion and common post-M3-C cut materializer.

    Capture accepts only the durable M3-B/C0 identities.  It reconstructs M3-C,
    candidates, subjects, rate selections and completeness from authoritative
    state inside the repository.
    """

    @staticmethod
    def _rule_from_row(row) -> InternalLaborCostRule:
        value = restore_internal_cost_rule(
            row["canonical_semantic_json"], row["rule_fingerprint"], row["rule_id"]
        )
        actual = (row["schema_version"], row["rule_version"], row["rule_revision"])
        expected = (value.schema_version, value.rule_version, value.rule_revision)
        if actual != expected:
            raise M5InternalCostSupportStorageError(
                "RULE_METADATA_MISMATCH", "internal_cost_rule"
            )
        return value

    def _ensure_rule(self, connection) -> InternalLaborCostRule:
        expected = current_internal_labor_cost_rule()
        rows = connection.execute(
            "SELECT * FROM m5_internal_cost_rules ORDER BY rule_revision"
        ).fetchall()
        if rows:
            if len(rows) != 1 or self._rule_from_row(rows[0]) != expected:
                raise M5InternalCostSupportStorageError(
                    "RULE_AUTHORITY_MISMATCH", "internal_cost_rule"
                )
            return expected
        connection.execute(
            """
            INSERT INTO m5_internal_cost_rules(
                rule_id, rule_fingerprint, rule_revision, schema_version,
                rule_version, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                expected.rule_id,
                expected.rule_fingerprint,
                expected.rule_revision,
                expected.schema_version,
                expected.rule_version,
                internal_cost_rule_semantic_json(expected),
            ),
        )
        return expected

    @staticmethod
    def _rate_source_from_row(row) -> WorkerInternalCostRateRevision:
        value = restore_rate_source(
            row["canonical_semantic_json"],
            row["source_fingerprint"],
            row["source_record_id"],
        )
        actual = (
            row["worker_id"],
            row["source_revision"],
            row["previous_source_record_id"],
            row["effective_from"],
            row["effective_until"],
            row["worker_registry_revision"],
            row["worker_registry_fingerprint"],
            row["rule_id"],
            row["rule_fingerprint"],
            row["schema_version"],
        )
        expected = (
            value.worker_id,
            value.source_revision,
            value.previous_source_record_id,
            None if value.effective_from is None else value.effective_from.isoformat(),
            None if value.effective_until is None else value.effective_until.isoformat(),
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
            value.rule_id,
            value.rule_fingerprint,
            value.schema_version,
        )
        if actual != expected:
            raise M5InternalCostSupportStorageError(
                "RATE_SOURCE_METADATA_MISMATCH", "rate_source"
            )
        return value

    @staticmethod
    def _capture_from_row(row) -> InternalCostSourceCapture:
        value = restore_source_capture(
            row["canonical_semantic_json"],
            row["capture_fingerprint"],
            row["capture_id"],
        )
        actual = (
            row["ledger_generation"],
            row["source_record_id"],
            row["source_fingerprint"],
            row["worker_id"],
            row["source_revision"],
            row["schema_version"],
        )
        expected = (
            value.ledger_generation,
            value.source_record_id,
            value.source_fingerprint,
            value.worker_id,
            value.source_revision,
            value.schema_version,
        )
        if actual != expected:
            raise M5InternalCostSupportStorageError(
                "SOURCE_CAPTURE_METADATA_MISMATCH", "source_capture"
            )
        return value

    @staticmethod
    def _current_registry(connection, database_path):
        try:
            registry = M2DurableRepository(database_path)._worker_registry_from_connection(
                connection
            )
        except (M2PersistenceError, ValueError) as error:
            raise M5InternalCostSupportStorageError(
                "WORKER_REGISTRY_INVALID", "worker_registry"
            ) from error
        raw = serialize_worker_registry(registry)
        return registry, raw, sha256_text(raw)

    @staticmethod
    def _next_generation(connection) -> int:
        return connection.execute(
            "SELECT COALESCE(max(ledger_generation), 0) + 1 FROM m5_internal_cost_source_captures"
        ).fetchone()[0]

    def _insert_source_and_capture(
        self,
        connection,
        source: WorkerInternalCostRateRevision,
    ) -> tuple[WorkerInternalCostRateRevision, InternalCostSourceCapture]:
        connection.execute(
            """
            INSERT INTO m5_internal_labor_rate_sources(
                source_record_id, source_fingerprint, worker_id,
                source_revision, previous_source_record_id,
                effective_from, effective_until, worker_registry_revision,
                worker_registry_fingerprint, canonical_worker_registry_json,
                rule_id, rule_fingerprint, schema_version,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source.source_record_id,
                source.source_fingerprint,
                source.worker_id,
                source.source_revision,
                source.previous_source_record_id,
                None if source.effective_from is None else source.effective_from.isoformat(),
                None if source.effective_until is None else source.effective_until.isoformat(),
                source.worker_registry_revision,
                source.worker_registry_fingerprint,
                source.canonical_worker_registry_json,
                source.rule_id,
                source.rule_fingerprint,
                source.schema_version,
                rate_source_semantic_json(source),
            ),
        )
        capture = InternalCostSourceCapture(
            ledger_generation=self._next_generation(connection),
            source_record_id=source.source_record_id,
            source_fingerprint=source.source_fingerprint,
            worker_id=source.worker_id,
            source_revision=source.source_revision,
        )
        connection.execute(
            """
            INSERT INTO m5_internal_cost_source_captures(
                capture_id, capture_fingerprint, ledger_generation,
                source_record_id, source_fingerprint, worker_id,
                source_revision, schema_version, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                capture.capture_id,
                capture.capture_fingerprint,
                capture.ledger_generation,
                capture.source_record_id,
                capture.source_fingerprint,
                capture.worker_id,
                capture.source_revision,
                capture.schema_version,
                source_capture_semantic_json(capture),
            ),
        )
        return source, capture

    def _ensure_v1_configuration(self, connection) -> tuple[WorkerInternalCostRateRevision, ...]:
        rule = self._ensure_rule(connection)
        rows = connection.execute(
            "SELECT * FROM m5_internal_labor_rate_sources WHERE source_revision=0 ORDER BY worker_id"
        ).fetchall()
        if rows:
            sources = tuple(self._rate_source_from_row(row) for row in rows)
            if tuple((item.worker_id, item.rate_amount) for item in sources) != V1_INTERNAL_LABOR_RATES:
                raise M5InternalCostSupportStorageError(
                    "V1_CONFIGURATION_MISMATCH", "rate_sources"
                )
            self._validate_cost_ledger(connection)
            return sources

        registry, registry_json, registry_fingerprint = self._current_registry(
            connection, self.database_path
        )
        missing = tuple(
            worker_id
            for worker_id, _ in V1_INTERNAL_LABOR_RATES
            if worker_id not in registry.worker_ids
        )
        if missing:
            raise M5InternalCostSupportConflictError(
                "CANONICAL_WORKER_IDENTITY_MISSING", ",".join(missing)
            )
        sources = []
        for worker_id, amount in V1_INTERNAL_LABOR_RATES:
            source = WorkerInternalCostRateRevision(
                worker_id=worker_id,
                rate_amount=amount,
                source_revision=0,
                previous_source_record_id=None,
                effective_from=None,
                effective_until=None,
                worker_registry_revision=registry.registry_revision,
                worker_registry_fingerprint=registry_fingerprint,
                canonical_worker_registry_json=registry_json,
                rule_id=rule.rule_id,
                rule_fingerprint=rule.rule_fingerprint,
            )
            self._insert_source_and_capture(connection, source)
            sources.append(source)
        self._validate_cost_ledger(connection)
        return tuple(sources)

    def install_v1_configuration(self) -> tuple[WorkerInternalCostRateRevision, ...]:
        """Install/replay only the frozen host-owned six-worker configuration."""

        try:
            with self.transaction() as connection:
                return self._ensure_v1_configuration(connection)
        except sqlite3.IntegrityError as error:
            raise M5InternalCostSupportConflictError(
                "V1_CONFIGURATION_PERSISTENCE_CONFLICT", "rate_sources"
            ) from error
        except sqlite3.Error as error:
            raise M5InternalCostSupportStorageError(
                "V1_CONFIGURATION_STORAGE_FAILURE", "rate_sources"
            ) from error

    def append_internal_labor_rate_revision(
        self,
        *,
        worker_id: str,
        rate_amount: Decimal,
        effective_from: datetime,
        effective_until: datetime | None = None,
        expected_previous_source_record_id: str,
    ) -> WorkerInternalCostRateRevision:
        """Privileged append path; this is source ingestion, never capture input."""

        try:
            with self.transaction() as connection:
                self._ensure_v1_configuration(connection)
                rule = self._ensure_rule(connection)
                registry, registry_json, registry_fingerprint = self._current_registry(
                    connection, self.database_path
                )
                if worker_id not in registry.worker_ids:
                    raise M5InternalCostSupportConflictError(
                        "CANONICAL_WORKER_IDENTITY_MISSING", "worker_id"
                    )
                row = connection.execute(
                    """
                    SELECT * FROM m5_internal_labor_rate_sources
                    WHERE worker_id=? ORDER BY source_revision DESC LIMIT 1
                    """,
                    (worker_id,),
                ).fetchone()
                if row is None:
                    raise M5InternalCostSupportConflictError(
                        "RATE_SOURCE_PARENT_MISSING", "worker_id"
                    )
                prior = self._rate_source_from_row(row)
                if prior.source_record_id != expected_previous_source_record_id:
                    raise M5InternalCostSupportConflictError(
                        "STALE_RATE_SOURCE_PARENT", "expected_previous_source_record_id"
                    )
                source = WorkerInternalCostRateRevision(
                    worker_id=worker_id,
                    rate_amount=rate_amount,
                    source_revision=prior.source_revision + 1,
                    previous_source_record_id=prior.source_record_id,
                    effective_from=effective_from,
                    effective_until=effective_until,
                    worker_registry_revision=registry.registry_revision,
                    worker_registry_fingerprint=registry_fingerprint,
                    canonical_worker_registry_json=registry_json,
                    rule_id=rule.rule_id,
                    rule_fingerprint=rule.rule_fingerprint,
                )
                if prior.effective_from is not None and source.effective_from <= prior.effective_from:
                    raise M5InternalCostSupportConflictError(
                        "NON_MONOTONIC_EFFECTIVE_CHRONOLOGY", "effective_from"
                    )
                # Revision zero is the frozen initial/open-start configuration.
                # Its first successor establishes the end of that initial period.
                # Every later revision has an explicit persisted interval; an
                # open-ended head cannot be followed and finite intervals may
                # only be adjacent to, or precede, the next revision.
                if (
                    prior.source_revision > 0
                    and (
                        prior.effective_until is None
                        or prior.effective_until > source.effective_from
                    )
                ):
                    raise M5InternalCostSupportConflictError(
                        "OVERLAPPING_EFFECTIVE_INTERVAL", "effective_from"
                    )
                self._insert_source_and_capture(connection, source)
                self._validate_cost_ledger(connection)
                return source
        except sqlite3.IntegrityError as error:
            raise M5InternalCostSupportConflictError(
                "RATE_SOURCE_PERSISTENCE_CONFLICT", "rate_source"
            ) from error
        except sqlite3.Error as error:
            raise M5InternalCostSupportStorageError(
                "RATE_SOURCE_STORAGE_FAILURE", "rate_source"
            ) from error

    def _validate_cost_ledger(self, connection) -> None:
        rule_rows = connection.execute(
            "SELECT * FROM m5_internal_cost_rules ORDER BY rule_revision"
        ).fetchall()
        if len(rule_rows) != 1:
            raise M5InternalCostSupportStorageError(
                "RULE_AUTHORITY_MISMATCH", "internal_cost_rule"
            )
        authoritative_rule = self._rule_from_row(rule_rows[0])
        if authoritative_rule != current_internal_labor_cost_rule():
            raise M5InternalCostSupportStorageError(
                "RULE_AUTHORITY_MISMATCH", "internal_cost_rule"
            )
        source_rows = connection.execute(
            "SELECT * FROM m5_internal_labor_rate_sources ORDER BY worker_id, source_revision"
        ).fetchall()
        capture_rows = connection.execute(
            "SELECT * FROM m5_internal_cost_source_captures ORDER BY ledger_generation"
        ).fetchall()
        sources = tuple(self._rate_source_from_row(row) for row in source_rows)
        captures = tuple(self._capture_from_row(row) for row in capture_rows)
        for source in sources:
            rule_row = connection.execute(
                "SELECT * FROM m5_internal_cost_rules WHERE rule_id=?",
                (source.rule_id,),
            ).fetchone()
            if (
                rule_row is None
                or self._rule_from_row(rule_row) != authoritative_rule
                or source.rule_fingerprint != rule_row["rule_fingerprint"]
            ):
                raise M5InternalCostSupportStorageError(
                    "RATE_SOURCE_RULE_AUTHORITY_MISMATCH", "rate_source"
                )
        frozen_r0 = tuple(
            (source.worker_id, source.rate_amount)
            for source in sources
            if source.source_revision == 0
        )
        if sources and frozen_r0 != V1_INTERNAL_LABOR_RATES:
            raise M5InternalCostSupportStorageError(
                "V1_CONFIGURATION_MISMATCH", "rate_sources"
            )
        if tuple(item.ledger_generation for item in captures) != tuple(
            range(1, len(captures) + 1)
        ):
            raise M5InternalCostSupportStorageError(
                "SOURCE_CAPTURE_CHRONOLOGY_INVALID", "ledger_generation"
            )
        source_by_id = {item.source_record_id: item for item in sources}
        if len(source_by_id) != len(sources) or {
            item.source_record_id for item in captures
        } != set(source_by_id):
            raise M5InternalCostSupportStorageError(
                "SOURCE_CAPTURE_COVERAGE_MISMATCH", "source_capture"
            )
        for capture in captures:
            source = source_by_id[capture.source_record_id]
            if (
                capture.source_fingerprint,
                capture.worker_id,
                capture.source_revision,
            ) != (
                source.source_fingerprint,
                source.worker_id,
                source.source_revision,
            ):
                raise M5InternalCostSupportStorageError(
                    "SOURCE_CAPTURE_BINDING_MISMATCH", "source_capture"
                )
        by_worker: dict[str, list[WorkerInternalCostRateRevision]] = {}
        for source in sources:
            by_worker.setdefault(source.worker_id, []).append(source)
        for worker_sources in by_worker.values():
            for position, source in enumerate(worker_sources):
                expected_parent = None if position == 0 else worker_sources[position - 1].source_record_id
                if source.source_revision != position or source.previous_source_record_id != expected_parent:
                    raise M5InternalCostSupportStorageError(
                        "RATE_SOURCE_CHAIN_INVALID", "source_revision"
                    )
                if (
                    position
                    and worker_sources[position - 1].effective_from is not None
                    and source.effective_from <= worker_sources[position - 1].effective_from
                ):
                    raise M5InternalCostSupportStorageError(
                        "EFFECTIVE_CHRONOLOGY_INVALID", "effective_from"
                    )
                if position > 1:
                    previous = worker_sources[position - 1]
                    if (
                        previous.effective_until is None
                        or previous.effective_until > source.effective_from
                    ):
                        raise M5InternalCostSupportStorageError(
                            "OVERLAPPING_EFFECTIVE_INTERVAL", "effective_from"
                        )

    def _sources_for_worker_at_cut(self, connection, worker_id: str, generation: int):
        rows = connection.execute(
            """
            SELECT source.*, capture.capture_id, capture.capture_fingerprint,
                   capture.ledger_generation AS capture_generation,
                   capture.canonical_semantic_json AS capture_semantic_json
            FROM m5_internal_labor_rate_sources AS source
            JOIN m5_internal_cost_source_captures AS capture
              ON capture.source_record_id=source.source_record_id
             AND capture.source_fingerprint=source.source_fingerprint
            WHERE source.worker_id=? AND capture.ledger_generation<=?
            ORDER BY source.source_revision
            """,
            (worker_id, generation),
        ).fetchall()
        values = []
        for row in rows:
            source = self._rate_source_from_row(row)
            capture = restore_source_capture(
                row["capture_semantic_json"], row["capture_fingerprint"], row["capture_id"]
            )
            if capture.ledger_generation != row["capture_generation"]:
                raise M5InternalCostSupportStorageError(
                    "SOURCE_CAPTURE_METADATA_MISMATCH", "source_capture"
                )
            values.append((source, capture))
        return tuple(values)

    def _select_rate(
        self, connection, subject: InternalLaborCostSubject, generation: int
    ) -> InternalLaborRateSelection:
        captured = self._sources_for_worker_at_cut(
            connection, subject.worker_id, generation
        )
        if not captured:
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
        covering = []
        touching = []
        first_successor_start = (
            captured[1][0].effective_from if len(captured) > 1 else None
        )
        for source, capture in captured:
            start = source.effective_from
            end = source.effective_until
            # Revision zero is the initial/open-start configuration.  Its first
            # successor is the sole boundary supplied by the append-only history.
            # Later revisions always use their persisted bounds verbatim: this
            # path never repairs or truncates an overlapping interval.
            initial_period = (
                source.source_revision == 0 and first_successor_start is not None
            )
            starts_before = start is None or start <= subject.interval_start
            ends_after = (
                subject.interval_end <= first_successor_start
                if initial_period
                else end is None or subject.interval_end <= end
            )
            if starts_before and ends_after:
                covering.append((source, capture))
            overlaps = (
                (
                    subject.interval_start < first_successor_start
                    if initial_period
                    else end is None or subject.interval_start < end
                )
                and (start is None or start < subject.interval_end)
            )
            if overlaps:
                touching.append((source, capture))
        if len(covering) == 1:
            source, capture = covering[0]
            return InternalLaborRateSelection(
                subject_id=subject.subject_id,
                subject_fingerprint=subject.subject_fingerprint,
                status=RateSelectionStatus.SELECTED,
                source_record_id=source.source_record_id,
                source_fingerprint=source.source_fingerprint,
                source_revision=source.source_revision,
                source_capture_id=capture.capture_id,
                source_capture_fingerprint=capture.capture_fingerprint,
                source_capture_generation=capture.ledger_generation,
                no_source_reason=None,
            )
        if len(covering) > 1:
            reason = NoSourceReason.AMBIGUOUS_RATE_SOURCE
        elif len(touching) > 1:
            reason = NoSourceReason.MULTI_REVISION_COVERAGE_UNSUPPORTED
        else:
            reason = NoSourceReason.RATE_INTERVAL_NOT_COVERED
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
            no_source_reason=reason,
        )

    @staticmethod
    def _candidate_bindings(result: M3BoundedFeasibilityResult):
        return tuple(
            InternalCostCandidateBinding(
                position=position,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.candidate_fingerprint,
            )
            for position, candidate in enumerate(result.candidates)
        )

    def _build_cut(self, connection, evaluation, support, result):
        rule = self._ensure_rule(connection)
        generation = connection.execute(
            "SELECT COALESCE(max(ledger_generation), 0) FROM m5_internal_cost_source_captures"
        ).fetchone()[0]
        subjects = derive_internal_cost_subjects(result, support)
        selections = tuple(
            self._select_rate(connection, subject, generation) for subject in subjects
        )
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
            candidate_bindings=self._candidate_bindings(result),
            rule_id=rule.rule_id,
            rule_revision=rule.rule_revision,
            rule_fingerprint=rule.rule_fingerprint,
            source_cut_generation=generation,
            subjects=subjects,
            selections=selections,
        )

    def _persist_cut(self, connection, value: InternalCostSupportCut) -> None:
        raw = internal_cost_support_semantic_json(value)
        existing = connection.execute(
            "SELECT * FROM m5_internal_cost_support_cuts WHERE support_id=?",
            (value.support_id,),
        ).fetchone()
        if existing is not None:
            if self._cut_from_row(connection, existing) != value:
                raise M5InternalCostSupportConflictError(
                    "SUPPORT_REPLAY_CONFLICT", "support_id"
                )
            return
        connection.execute(
            """
            INSERT INTO m5_internal_cost_support_cuts(
                support_id, support_fingerprint, schema_version,
                evaluation_input_id, evaluation_input_fingerprint,
                feasibility_support_snapshot_id,
                feasibility_support_snapshot_fingerprint, company_plan_id,
                base_plan_revision, base_plan_revision_id,
                base_plan_revision_fingerprint, m3_result_id,
                m3_result_fingerprint, rule_id, rule_revision,
                rule_fingerprint, source_cut_generation,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.support_id,
                value.support_fingerprint,
                value.schema_version,
                value.evaluation_input_id,
                value.evaluation_input_fingerprint,
                value.feasibility_support_snapshot_id,
                value.feasibility_support_snapshot_fingerprint,
                value.company_plan_id,
                value.base_plan_revision,
                value.base_plan_revision_id,
                value.base_plan_revision_fingerprint,
                value.m3_result_id,
                value.m3_result_fingerprint,
                value.rule_id,
                value.rule_revision,
                value.rule_fingerprint,
                value.source_cut_generation,
                raw,
            ),
        )
        connection.executemany(
            """
            INSERT INTO m5_internal_cost_support_candidates(
                support_id, candidate_position, candidate_id,
                candidate_fingerprint
            ) VALUES(?,?,?,?)
            """,
            (
                (value.support_id, item.position, item.candidate_id, item.candidate_fingerprint)
                for item in value.candidate_bindings
            ),
        )
        connection.executemany(
            """
            INSERT INTO m5_internal_cost_support_subjects(
                support_id, subject_id, subject_fingerprint,
                candidate_position, candidate_id, candidate_fingerprint,
                subject_scope, commitment_id, worker_id, interval_start,
                interval_end, schema_version, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                (
                    value.support_id,
                    item.subject_id,
                    item.subject_fingerprint,
                    item.candidate_position,
                    item.candidate_id,
                    item.candidate_fingerprint,
                    item.scope.value,
                    item.commitment_id,
                    item.worker_id,
                    item.interval_start.isoformat(),
                    item.interval_end.isoformat(),
                    item.schema_version,
                    labor_cost_subject_semantic_json(item),
                )
                for item in value.subjects
            ),
        )
        connection.executemany(
            """
            INSERT INTO m5_internal_cost_support_selections(
                support_id, subject_id, selection_id,
                selection_fingerprint, selection_status, source_record_id,
                source_fingerprint, source_revision, source_capture_id,
                source_capture_fingerprint, source_capture_generation,
                no_source_reason, schema_version, canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                (
                    value.support_id,
                    item.subject_id,
                    item.selection_id,
                    item.selection_fingerprint,
                    item.status.value,
                    item.source_record_id,
                    item.source_fingerprint,
                    item.source_revision,
                    item.source_capture_id,
                    item.source_capture_fingerprint,
                    item.source_capture_generation,
                    None if item.no_source_reason is None else item.no_source_reason.value,
                    item.schema_version,
                    rate_selection_semantic_json(item),
                )
                for item in value.selections
            ),
        )

    def capture_internal_cost_support(
        self,
        evaluation_input_id: str,
        feasibility_support_snapshot_id: str,
    ) -> InternalCostSupportCut:
        """Capture one coherent source cut for the repository-derived M3-C set."""

        if type(evaluation_input_id) is not str or not evaluation_input_id.strip():
            raise M5InternalCostSupportValidationError("INVALID_VALUE", "evaluation_input_id")
        if type(feasibility_support_snapshot_id) is not str or not feasibility_support_snapshot_id.strip():
            raise M5InternalCostSupportValidationError("INVALID_VALUE", "feasibility_support_snapshot_id")
        try:
            with self.transaction() as connection:
                evaluation_row = connection.execute(
                    "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
                    (evaluation_input_id,),
                ).fetchone()
                support_row = connection.execute(
                    "SELECT * FROM m3_feasibility_support_snapshots WHERE support_snapshot_id=?",
                    (feasibility_support_snapshot_id,),
                ).fetchone()
                if evaluation_row is None or support_row is None:
                    raise M5InternalCostSupportConflictError(
                        "M3_EVIDENCE_NOT_FOUND", "evaluation_or_support"
                    )
                evaluation = self._from_row(connection, evaluation_row)
                support = self._support_from_row(connection, support_row)
                if support.evaluation_input_id != evaluation.evaluation_input_id:
                    raise M5InternalCostSupportConflictError(
                        "M3_EVIDENCE_BINDING_MISMATCH", "evaluation_or_support"
                    )
                result = generate_bounded_repair_candidates(evaluation, support)
                # Source installation is a separate privileged bootstrap action.
                # A capture before it is valid and records explicit NO_SOURCE;
                # capture never manufactures mutable source facts on demand.
                self._ensure_rule(connection)
                self._validate_cost_ledger(connection)
                value = self._build_cut(connection, evaluation, support, result)
                self._persist_cut(connection, value)
                return value
        except sqlite3.IntegrityError as error:
            raise M5InternalCostSupportConflictError(
                "SUPPORT_PERSISTENCE_CONFLICT", "internal_cost_support"
            ) from error
        except sqlite3.Error as error:
            raise M5InternalCostSupportStorageError(
                "SUPPORT_STORAGE_FAILURE", "internal_cost_support"
            ) from error

    @staticmethod
    def _subject_from_row(row) -> InternalLaborCostSubject:
        try:
            value = InternalLaborCostSubject(
                candidate_position=row["candidate_position"],
                candidate_id=row["candidate_id"],
                candidate_fingerprint=row["candidate_fingerprint"],
                scope=LaborCostSubjectScope(row["subject_scope"]),
                commitment_id=row["commitment_id"],
                worker_id=row["worker_id"],
                interval_start=datetime.fromisoformat(row["interval_start"]),
                interval_end=datetime.fromisoformat(row["interval_end"]),
            )
        except (TypeError, ValueError) as error:
            raise M5InternalCostSupportStorageError(
                "INVALID_SUBJECT", "subject"
            ) from error
        if (
            value.subject_id,
            value.subject_fingerprint,
            value.schema_version,
            labor_cost_subject_semantic_json(value),
        ) != (
            row["subject_id"],
            row["subject_fingerprint"],
            row["schema_version"],
            row["canonical_semantic_json"],
        ):
            raise M5InternalCostSupportStorageError(
                "SUBJECT_METADATA_MISMATCH", "subject"
            )
        return value

    @staticmethod
    def _selection_from_row(row) -> InternalLaborRateSelection:
        try:
            value = InternalLaborRateSelection(
                subject_id=row["subject_id"],
                subject_fingerprint=row["subject_fingerprint"],
                status=RateSelectionStatus(row["selection_status"]),
                source_record_id=row["source_record_id"],
                source_fingerprint=row["source_fingerprint"],
                source_revision=row["source_revision"],
                source_capture_id=row["source_capture_id"],
                source_capture_fingerprint=row["source_capture_fingerprint"],
                source_capture_generation=row["source_capture_generation"],
                no_source_reason=(
                    None
                    if row["no_source_reason"] is None
                    else NoSourceReason(row["no_source_reason"])
                ),
            )
        except (TypeError, ValueError) as error:
            raise M5InternalCostSupportStorageError(
                "INVALID_SELECTION", "selection"
            ) from error
        if (
            value.selection_id,
            value.selection_fingerprint,
            value.schema_version,
            rate_selection_semantic_json(value),
        ) != (
            row["selection_id"],
            row["selection_fingerprint"],
            row["schema_version"],
            row["canonical_semantic_json"],
        ):
            raise M5InternalCostSupportStorageError(
                "SELECTION_METADATA_MISMATCH", "selection"
            )
        return value

    def _cut_from_row(self, connection, row) -> InternalCostSupportCut:
        value = restore_internal_cost_support(
            row["canonical_semantic_json"], row["support_fingerprint"], row["support_id"]
        )
        actual = (
            row["schema_version"],
            row["evaluation_input_id"],
            row["evaluation_input_fingerprint"],
            row["feasibility_support_snapshot_id"],
            row["feasibility_support_snapshot_fingerprint"],
            row["company_plan_id"],
            row["base_plan_revision"],
            row["base_plan_revision_id"],
            row["base_plan_revision_fingerprint"],
            row["m3_result_id"],
            row["m3_result_fingerprint"],
            row["rule_id"],
            row["rule_revision"],
            row["rule_fingerprint"],
            row["source_cut_generation"],
        )
        expected = (
            value.schema_version,
            value.evaluation_input_id,
            value.evaluation_input_fingerprint,
            value.feasibility_support_snapshot_id,
            value.feasibility_support_snapshot_fingerprint,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
            value.m3_result_id,
            value.m3_result_fingerprint,
            value.rule_id,
            value.rule_revision,
            value.rule_fingerprint,
            value.source_cut_generation,
        )
        if actual != expected:
            raise M5InternalCostSupportStorageError(
                "SUPPORT_METADATA_MISMATCH", "internal_cost_support"
            )
        self._validate_cost_ledger(connection)
        rule_row = connection.execute(
            "SELECT * FROM m5_internal_cost_rules WHERE rule_id=?", (value.rule_id,)
        ).fetchone()
        if rule_row is None or self._rule_from_row(rule_row) != current_internal_labor_cost_rule():
            raise M5InternalCostSupportStorageError(
                "SUPPORT_RULE_MISMATCH", "internal_cost_rule"
            )
        evaluation_row = connection.execute(
            "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
            (value.evaluation_input_id,),
        ).fetchone()
        support_row = connection.execute(
            "SELECT * FROM m3_feasibility_support_snapshots WHERE support_snapshot_id=?",
            (value.feasibility_support_snapshot_id,),
        ).fetchone()
        if evaluation_row is None or support_row is None:
            raise M5InternalCostSupportStorageError(
                "BOUND_M3_EVIDENCE_NOT_FOUND", "internal_cost_support"
            )
        evaluation = self._from_row(connection, evaluation_row)
        support = self._support_from_row(connection, support_row)
        result = generate_bounded_repair_candidates(evaluation, support)
        expected_bindings = self._candidate_bindings(result)
        expected_subjects = derive_internal_cost_subjects(result, support)
        expected_selections = tuple(
            sorted(
                (
                    self._select_rate(
                        connection, subject, value.source_cut_generation
                    )
                    for subject in expected_subjects
                ),
                key=lambda item: item.subject_id,
            )
        )
        if (
            value.evaluation_input_fingerprint,
            value.feasibility_support_snapshot_fingerprint,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
            value.m3_result_id,
            value.m3_result_fingerprint,
            value.candidate_bindings,
            value.subjects,
            value.selections,
        ) != (
            evaluation.evaluation_input_fingerprint,
            support.support_fingerprint,
            evaluation.company_plan_id,
            evaluation.base_plan_revision,
            evaluation.base_plan_revision_id,
            evaluation.base_plan_revision_fingerprint,
            result.result_id,
            result.result_fingerprint,
            expected_bindings,
            expected_subjects,
            expected_selections,
        ):
            raise M5InternalCostSupportStorageError(
                "SUPPORT_DERIVATION_MISMATCH", "internal_cost_support"
            )

        candidate_rows = connection.execute(
            "SELECT * FROM m5_internal_cost_support_candidates WHERE support_id=? ORDER BY candidate_position",
            (value.support_id,),
        ).fetchall()
        projected_candidates = tuple(
            InternalCostCandidateBinding(
                position=item["candidate_position"],
                candidate_id=item["candidate_id"],
                candidate_fingerprint=item["candidate_fingerprint"],
            )
            for item in candidate_rows
        )
        subject_rows = connection.execute(
            "SELECT * FROM m5_internal_cost_support_subjects WHERE support_id=? ORDER BY candidate_position, subject_scope, commitment_id, worker_id, interval_start, interval_end",
            (value.support_id,),
        ).fetchall()
        projected_subjects = tuple(self._subject_from_row(item) for item in subject_rows)
        selection_rows = connection.execute(
            """
            SELECT selection.*, subject.subject_fingerprint
            FROM m5_internal_cost_support_selections AS selection
            JOIN m5_internal_cost_support_subjects AS subject
              ON subject.support_id=selection.support_id
             AND subject.subject_id=selection.subject_id
            WHERE selection.support_id=? ORDER BY selection.subject_id
            """,
            (value.support_id,),
        ).fetchall()
        projected_selections = tuple(self._selection_from_row(item) for item in selection_rows)
        if (
            projected_candidates,
            projected_subjects,
            projected_selections,
        ) != (value.candidate_bindings, value.subjects, value.selections):
            raise M5InternalCostSupportStorageError(
                "SUPPORT_PROJECTION_MISMATCH", "internal_cost_support"
            )
        return value

    def get_internal_cost_support(self, support_id: str) -> InternalCostSupportCut:
        if type(support_id) is not str or not support_id.strip():
            raise M5InternalCostSupportValidationError("INVALID_VALUE", "support_id")
        with self._read_snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM m5_internal_cost_support_cuts WHERE support_id=?",
                (support_id,),
            ).fetchone()
            if row is None:
                raise M5InternalCostSupportStorageError(
                    "SUPPORT_NOT_FOUND", "support_id"
                )
            return self._cut_from_row(connection, row)


__all__ = [
    "M5InternalCostSupportRepository",
    "derive_internal_cost_subjects",
]
