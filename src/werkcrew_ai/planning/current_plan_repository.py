"""Trusted bootstrap writes and read-only M3 snapshots in the shared SQLite DB.

Import is a privileged host/bootstrap operation, not a client credential, plan
candidate, owner decision or APPLY endpoint. Readers never accept a caller scope.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from werkcrew_ai.field.models import TaskState, TaskStatus
from werkcrew_ai.field.repository import M2DurableRepository, M2PersistenceError
from werkcrew_ai.intake.publication import M1BoundaryPublicationRepository
from werkcrew_ai.planning.current_plan import (
    CompanyPlan, M3BootstrapConflictError, M3BootstrapStorageError,
    PlanRevision, deserialize_plan_revision, identity, record, require,
    revision_number, serialize_plan_revision,
)
from werkcrew_ai.planning.m2_bridge import (
    M2EffectSourceIdentity, M2M3BridgeSourceError, M2UnavailableToM3Bridge,
    M3FeasibilityEvaluationRequest,
)
from werkcrew_ai.planning.m3_unavailability_impact import (
    AUTHORITY_SCHEMA_VERSION, SCOPE_SCHEMA_VERSION,
    M3CurrentCommitment, M3CurrentPlanningScope, M3PlanningScopeAuthority,
    M3UnavailabilityImpactResult, _validate_request, evaluate_unavailability_impact,
)
from werkcrew_ai.persistence import SqlitePersistence


@dataclass(frozen=True, slots=True, kw_only=True)
class CurrentPlanSnapshot:
    """Producer output, not a credential to be accepted back from a client."""

    company_plan: CompanyPlan
    revision: PlanRevision
    request: M3FeasibilityEvaluationRequest
    scope: M3CurrentPlanningScope
    authority: M3PlanningScopeAuthority


def _commitment_identity(item) -> tuple:
    return (item.commitment_id, item.job_id, item.task_id, item.task_definition_version,
            item.source_handoff_id, item.source_revision, item.business_date)


class CurrentPlanRepository(SqlitePersistence):
    @contextmanager
    def _read_snapshot(self):
        # No initialize(), WAL configuration, migration or implicit DB creation.
        try:
            connection = sqlite3.connect(Path(self.database_path).as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error as error:
            raise M3BootstrapStorageError("READ_UNAVAILABLE", "database") from error
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            yield connection
        except sqlite3.Error as error:
            raise M3BootstrapStorageError("READ_INVALID", "database") from error
        finally:
            connection.rollback()
            connection.close()

    @staticmethod
    def _company(connection, company_plan_id: str) -> CompanyPlan:
        row = connection.execute("SELECT * FROM m3_company_plans WHERE company_plan_id=?", (company_plan_id,)).fetchone()
        if row is None:
            raise M3BootstrapStorageError("COMPANY_PLAN_NOT_FOUND", "company_plan_id")
        return CompanyPlan(company_plan_id=row["company_plan_id"], provenance_reference=row["provenance_reference"])

    @staticmethod
    def _history(connection, company_plan_id: str) -> tuple[PlanRevision, ...]:
        rows = connection.execute("SELECT * FROM m3_plan_revisions WHERE company_plan_id=? ORDER BY revision", (company_plan_id,)).fetchall()
        history, seen = [], {}
        for expected_number, row in enumerate(rows):
            revision = deserialize_plan_revision(row["canonical_content_json"])
            previous = history[-1].revision_id if history else None
            if (revision.company_plan_id != company_plan_id or revision.revision != expected_number
                    or revision.revision != row["revision"] or revision.previous_revision_id != previous
                    or revision.previous_revision_id != row["previous_revision_id"]
                    or revision.revision_id != row["revision_id"] or revision.fingerprint != row["content_sha256"]):
                raise M3BootstrapStorageError("REVISION_CHAIN_INVALID", "revision")
            for commitment in revision.commitments:
                key = _commitment_identity(commitment)
                for identity_key in (("commitment", commitment.commitment_id), ("task", commitment.task_id)):
                    if identity_key in seen and seen[identity_key] != key:
                        raise M3BootstrapStorageError("COMMITMENT_IDENTITY_CHANGED", "commitment")
                    seen[identity_key] = key
            history.append(revision)
        return tuple(history)

    @staticmethod
    def _m2_plan(connection, plan_day_id):
        row = connection.execute("SELECT * FROM m2_plan_day_roots WHERE plan_day_id=?", (plan_day_id,)).fetchone()
        if row is None:
            raise M3BootstrapConflictError("M2_PLAN_DAY_NOT_FOUND", "plan_day_id")
        return M2DurableRepository._plan_day_from_row(row)

    def _validate_bindings(self, connection, revision: PlanRevision) -> dict[tuple[str, str], TaskState]:
        """Validate identity/provenance and return canonical M2 task evidence."""
        associations = {d.plan_day_id: d for d in revision.plan_days}
        for association in revision.plan_days:
            root = self._m2_plan(connection, association.plan_day_id)
            if (root.worker_id, root.business_date) != (association.worker_id, association.business_date):
                raise M3BootstrapConflictError("M2_PLAN_DAY_BINDING_MISMATCH", "plan_day_id")
        roots = {}
        canonical_tasks = {}
        m2_reader = M2DurableRepository(self.database_path)
        for commitment in revision.commitments:
            if commitment.job_id not in roots:
                row = connection.execute("SELECT * FROM m2_job_execution_roots WHERE job_id=?", (commitment.job_id,)).fetchone()
                if row is None:
                    raise M3BootstrapConflictError("CANONICAL_JOB_NOT_FOUND", "job_id")
                roots[commitment.job_id] = m2_reader._job_root_from_connection(connection, row)
            root = roots[commitment.job_id]
            task = root.task(commitment.task_id)
            if task is None or (task.definition.definition_version, task.definition.source_handoff_id, task.definition.source_revision) != (
                commitment.task_definition_version, commitment.source_handoff_id, commitment.source_revision
            ):
                raise M3BootstrapConflictError("CANONICAL_TASK_BINDING_MISMATCH", "task_id")
            canonical_tasks[(commitment.job_id, commitment.task_id)] = task
            publication_row = connection.execute(
                "SELECT * FROM m1_handoff_publications WHERE job_id=? AND source_revision=? AND handoff_id=?",
                (commitment.job_id, commitment.source_revision, commitment.source_handoff_id),
            ).fetchone()
            if publication_row is None:
                raise M3BootstrapConflictError("PUBLICATION_LINEAGE_MISSING", "source_handoff_id")
            M1BoundaryPublicationRepository._publication_from_row(publication_row)
            for assignment_id in commitment.source_m2_assignment_ids:
                assignment = root.assignment(assignment_id)
                if assignment is None or assignment.task_id != commitment.task_id:
                    raise M3BootstrapConflictError("ASSIGNMENT_LINEAGE_MISMATCH", "source_m2_assignment_ids")
                # B0 imports exact-task, same-day lineage only. Cross-date repair
                # semantics need a later explicit contract, never guessed here.
                if not assignment.plan_day_ids or any(
                    plan_id not in associations or associations[plan_id].business_date != commitment.business_date
                    for plan_id in assignment.plan_day_ids
                ):
                    raise M3BootstrapConflictError("ASSIGNMENT_DAY_LINEAGE_MISMATCH", "source_m2_assignment_ids")
        return canonical_tasks

    @staticmethod
    def _validate_stored_associations(connection, revision: PlanRevision) -> None:
        for association in revision.plan_days:
            row = connection.execute("SELECT * FROM m3_plan_day_associations WHERE plan_day_id=?", (association.plan_day_id,)).fetchone()
            if row is None or (row["company_plan_id"], row["worker_id"], row["business_date"]) != (
                revision.company_plan_id, association.worker_id, association.business_date.isoformat()
            ):
                raise M3BootstrapStorageError("STORED_ASSOCIATION_MISMATCH", "plan_day_id")

    def import_revision(self, company: CompanyPlan, revision: PlanRevision) -> PlanRevision:
        """Privileged baseline import, exact-parent append, or immutable replay.

        No candidate/repair acceptance, owner decision, execution or publication.
        Access to this writer must be restricted by the host, like other bootstrap
        repositories. A source/provenance string is not authentication.
        """
        record(company, CompanyPlan)
        require(replace(company) == company, "company_plan")
        raw = serialize_plan_revision(revision)
        require(company.company_plan_id == revision.company_plan_id, "company_plan_id")
        try:
            with self.transaction() as connection:
                exists = connection.execute("SELECT 1 FROM m3_company_plans WHERE company_plan_id=?", (company.company_plan_id,)).fetchone()
                if exists:
                    if self._company(connection, company.company_plan_id) != company:
                        raise M3BootstrapConflictError("COMPANY_PLAN_IDENTITY_CONFLICT", "company_plan_id")
                else:
                    require(revision.revision == 0, "initial_revision")
                    connection.execute("INSERT INTO m3_company_plans VALUES(?,?)", (company.company_plan_id, company.provenance_reference))
                history = self._history(connection, company.company_plan_id)
                if exists and not history:
                    raise M3BootstrapStorageError("REVISION_HISTORY_MISSING", "company_plan_id")
                if revision.revision < len(history):
                    if serialize_plan_revision(history[revision.revision]) != raw:
                        raise M3BootstrapConflictError("REVISION_REPLAY_CONFLICT", "revision")
                    return history[revision.revision]
                if revision.revision != len(history) or revision.previous_revision_id != (history[-1].revision_id if history else None):
                    raise M3BootstrapConflictError("STALE_REVISION_PARENT", "revision")
                self._validate_bindings(connection, revision)
                for commitment in revision.commitments:
                    # Stable canonical task/commitment ownership across ALL plans
                    # and history, including placements removed by a later import.
                    matches = connection.execute(
                        "SELECT r.company_plan_id, r.canonical_content_json FROM m3_plan_revisions r, "
                        "json_each(r.canonical_content_json, '$.commitments') c WHERE "
                        "json_extract(c.value, '$.task_id')=? OR json_extract(c.value, '$.commitment_id')=?",
                        (commitment.task_id, commitment.commitment_id),
                    ).fetchall()
                    for match in matches:
                        if match["company_plan_id"] != company.company_plan_id:
                            raise M3BootstrapConflictError("TASK_PLAN_OWNERSHIP_CONFLICT", "task_id")
                        old_revision = deserialize_plan_revision(match["canonical_content_json"])
                        if any(_commitment_identity(old) != _commitment_identity(commitment) for old in old_revision.commitments
                               if old.task_id == commitment.task_id or old.commitment_id == commitment.commitment_id):
                            raise M3BootstrapConflictError("COMMITMENT_IDENTITY_CONFLICT", "commitment_id")
                for association in revision.plan_days:
                    row = connection.execute("SELECT * FROM m3_plan_day_associations WHERE plan_day_id=?", (association.plan_day_id,)).fetchone()
                    expected = (company.company_plan_id, association.worker_id, association.business_date.isoformat())
                    if row is None:
                        connection.execute("INSERT INTO m3_plan_day_associations VALUES(?,?,?,?)", (association.plan_day_id, *expected))
                    elif (row["company_plan_id"], row["worker_id"], row["business_date"]) != expected:
                        raise M3BootstrapConflictError("PLAN_DAY_OWNERSHIP_CONFLICT", "plan_day_id")
                connection.execute("INSERT INTO m3_plan_revisions VALUES(?,?,?,?,?,?)", (
                    company.company_plan_id, revision.revision, revision.revision_id,
                    revision.previous_revision_id, revision.fingerprint, raw,
                ))
            return revision
        except sqlite3.IntegrityError as error:
            raise M3BootstrapConflictError("BOOTSTRAP_CONSTRAINT_CONFLICT", "revision") from error

    def get_revision(self, company_plan_id: str, revision: int | None = None) -> PlanRevision:
        identity(company_plan_id, "company_plan_id")
        if revision is not None:
            revision_number(revision)
        with self._read_snapshot() as connection:
            self._company(connection, company_plan_id)
            history = self._history(connection, company_plan_id)
            if not history or (revision is not None and revision >= len(history)):
                raise M3BootstrapStorageError("REVISION_NOT_FOUND", "revision")
            selected = history[-1] if revision is None else history[revision]
            self._validate_stored_associations(connection, selected)
            self._validate_bindings(connection, selected)
            return selected

    def produce_current_snapshot(self, request: M3FeasibilityEvaluationRequest) -> CurrentPlanSnapshot:
        """Resolve from persisted membership/head, never a caller scope or pin."""
        _validate_request(request)
        source = request.snapshot.source
        try:
            verified = M2UnavailableToM3Bridge(M2DurableRepository(self.database_path)).build_request(
                M2EffectSourceIdentity(source.input_namespace, source.event_id, source.effect_ordinal)
            )
        except (M2PersistenceError, M2M3BridgeSourceError) as error:
            raise M3BootstrapStorageError("HISTORICAL_CAUSE_INVALID", "request") from error
        if verified != request:
            raise M3BootstrapConflictError("HISTORICAL_CAUSE_MISMATCH", "request")
        day = verified.snapshot.plan_day
        with self._read_snapshot() as connection:
            association = connection.execute("SELECT * FROM m3_plan_day_associations WHERE plan_day_id=?", (day.plan_day_id,)).fetchone()
            if association is None:
                raise M3BootstrapConflictError("M3_ASSOCIATION_REQUIRED", "plan_day_id")
            company = self._company(connection, association["company_plan_id"])
            history = self._history(connection, company.company_plan_id)
            if not history:
                raise M3BootstrapStorageError("REVISION_HISTORY_MISSING", "company_plan_id")
            current = history[-1]
            self._validate_stored_associations(connection, current)
            canonical_tasks = self._validate_bindings(connection, current)
            if not any((a.plan_day_id, a.worker_id, a.business_date) == (day.plan_day_id, day.worker_id, day.business_date) for a in current.plan_days):
                raise M3BootstrapConflictError("ASSOCIATION_NOT_CURRENT", "plan_day_id")
            historical_ids = {item.assignment_id for item in verified.snapshot.assignments}
            applicable = tuple(c for c in current.commitments if
                canonical_tasks[(c.job_id, c.task_id)].status is not TaskStatus.DONE
                and ((c.business_date == day.business_date and day.worker_id in c.planned_worker_ids)
                     or historical_ids.intersection(c.source_m2_assignment_ids)))
            # Enumerated from the complete immutable revision, not caller filtering.
            scope = M3CurrentPlanningScope(
                schema_version=SCOPE_SCHEMA_VERSION, company_plan_id=company.company_plan_id,
                company_plan_revision=current.revision, plan_day_id=day.plan_day_id,
                unavailable_worker_id=day.worker_id, business_date=day.business_date,
                source_request_fingerprint=verified.request_fingerprint, coverage_complete=True,
                commitments=tuple(M3CurrentCommitment(
                    commitment_id=c.commitment_id, job_id=c.job_id, task_id=c.task_id,
                    assigned_worker_ids=c.planned_worker_ids, source_m2_assignment_ids=c.source_m2_assignment_ids,
                ) for c in applicable),
            )
            authority = M3PlanningScopeAuthority(
                schema_version=AUTHORITY_SCHEMA_VERSION, company_plan_id=company.company_plan_id,
                company_plan_revision=current.revision, plan_day_id=day.plan_day_id,
                unavailable_worker_id=day.worker_id, business_date=day.business_date,
                source_request_fingerprint=verified.request_fingerprint,
                expected_snapshot_fingerprint=scope.snapshot_fingerprint,
            )
            return CurrentPlanSnapshot(company_plan=company, revision=current, request=verified, scope=scope, authority=authority)

    def evaluate_current_unavailability(self, request: M3FeasibilityEvaluationRequest) -> M3UnavailabilityImpactResult:
        snapshot = self.produce_current_snapshot(request)
        return evaluate_unavailability_impact(snapshot.request, snapshot.scope, authority=snapshot.authority)
