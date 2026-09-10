"""Durable M3-B EvaluationInput production from verified M2 and B0 state."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from werkcrew_ai.field.models import TaskStatus
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.planning.current_plan import M3BootstrapError
from werkcrew_ai.planning.current_plan_repository import CurrentPlanRepository
from werkcrew_ai.planning.evaluation_input import (
    CandidateInducedImpactScope,
    CurrentCommitmentEvidence,
    CurrentM2AssignmentPrecondition,
    CurrentM2TaskPrecondition,
    DependencyEvidence,
    DependencyImpactReference,
    DependencyImpactScope,
    DirectCurrentImpactScope,
    HistoricalAssignmentEvidence,
    HistoricalJobRootEvidence,
    HistoricalSourceScope,
    ImpactCommitmentReference,
    M3EvaluationInput,
    M3EvaluationInputConflictError,
    M3EvaluationInputStorageError,
    PlanDayAssociationEvidence,
    restore_evaluation_input,
    semantic_evaluation_input_json,
)
from werkcrew_ai.planning.m2_bridge import M3FeasibilityEvaluationRequest
from werkcrew_ai.planning.m3_unavailability_impact import (
    AUTHORITY_SCHEMA_VERSION,
    SCOPE_SCHEMA_VERSION,
    M3CurrentCommitment,
    M3CurrentPlanningScope,
    M3PlanningScopeAuthority,
    evaluate_unavailability_impact,
)


class M3EvaluationRepository(CurrentPlanRepository):
    """Trusted producer and append-only store for one fresh evaluation input."""

    @staticmethod
    def _scope_from_current(revision, request, canonical_tasks):
        day = request.snapshot.plan_day
        historical_ids = {item.assignment_id for item in request.snapshot.assignments}
        applicable = tuple(
            commitment
            for commitment in revision.commitments
            if canonical_tasks[(commitment.job_id, commitment.task_id)].status
            is not TaskStatus.DONE
            and (
                (
                    commitment.business_date == day.business_date
                    and day.worker_id in commitment.planned_worker_ids
                )
                or historical_ids.intersection(commitment.source_m2_assignment_ids)
            )
        )
        scope = M3CurrentPlanningScope(
            schema_version=SCOPE_SCHEMA_VERSION,
            company_plan_id=revision.company_plan_id,
            company_plan_revision=revision.revision,
            plan_day_id=day.plan_day_id,
            unavailable_worker_id=day.worker_id,
            business_date=day.business_date,
            source_request_fingerprint=request.request_fingerprint,
            coverage_complete=True,
            commitments=tuple(
                M3CurrentCommitment(
                    commitment_id=item.commitment_id,
                    job_id=item.job_id,
                    task_id=item.task_id,
                    assigned_worker_ids=item.planned_worker_ids,
                    source_m2_assignment_ids=item.source_m2_assignment_ids,
                )
                for item in applicable
            ),
        )
        authority = M3PlanningScopeAuthority(
            schema_version=AUTHORITY_SCHEMA_VERSION,
            company_plan_id=revision.company_plan_id,
            company_plan_revision=revision.revision,
            plan_day_id=day.plan_day_id,
            unavailable_worker_id=day.worker_id,
            business_date=day.business_date,
            source_request_fingerprint=request.request_fingerprint,
            expected_snapshot_fingerprint=scope.snapshot_fingerprint,
        )
        return scope, authority

    @staticmethod
    def _dependency_impacts(revision, active_commitments, direct):
        active_by_task = {item.task_id: item for item in active_commitments}
        direct_tasks = {item.task_id for item in direct.affected_commitments}
        adjacency: dict[str, tuple[str, ...]] = {}
        for task_id in {edge.predecessor_task_id for edge in revision.dependencies}:
            adjacency[task_id] = tuple(sorted(
                edge.successor_task_id
                for edge in revision.dependencies
                if edge.predecessor_task_id == task_id
            ))
        origins: dict[str, set[str]] = {}
        pending = [(task_id, task_id) for task_id in sorted(direct_tasks)]
        while pending:
            task_id, origin = pending.pop(0)
            for successor in adjacency.get(task_id, ()):
                # DONE tasks are absent from active_by_task and terminate impact
                # propagation; M2 supersession is never consulted here.
                if successor not in active_by_task:
                    continue
                values = origins.setdefault(successor, set())
                if origin not in values:
                    values.add(origin)
                    pending.append((successor, origin))
        return tuple(
            DependencyImpactReference(
                commitment_id=active_by_task[task_id].commitment_id,
                job_id=active_by_task[task_id].job_id,
                task_id=task_id,
                originating_direct_task_ids=tuple(sorted(values)),
            )
            for task_id, values in sorted(origins.items())
            if task_id not in direct_tasks
        )

    def _validate_revision_evidence(self, connection, value, error_type) -> None:
        """Bind durable evidence to one exact immutable B0 PlanRevision."""
        try:
            history = self._history(connection, value.company_plan_id)
        except M3BootstrapError as error:
            raise error_type("BASE_PLAN_REVISION_INVALID", "base_plan_revision") from error
        if value.base_plan_revision >= len(history):
            raise error_type("BASE_PLAN_REVISION_INVALID", "base_plan_revision")
        revision = history[value.base_plan_revision]
        if (
            revision.company_plan_id,
            revision.revision,
            revision.revision_id,
            revision.fingerprint,
        ) != (
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
        ):
            raise error_type("BASE_PLAN_REVISION_MISMATCH", "base_plan_revision")
        expected_dependencies = tuple(
            (
                edge.predecessor_task_id,
                edge.successor_task_id,
                edge.relation,
                edge.provenance_reference,
            )
            for edge in revision.dependencies
        )
        actual_dependencies = tuple(
            (
                edge.predecessor_task_id,
                edge.successor_task_id,
                edge.relation,
                edge.provenance_reference,
            )
            for edge in value.dependency_impact.edges
        )
        if actual_dependencies != expected_dependencies:
            raise error_type("BASE_PLAN_DEPENDENCIES_MISMATCH", "dependency_impact.edges")

    def _build_input(self, connection, trusted) -> M3EvaluationInput:
        request = trusted.request
        day = request.snapshot.plan_day
        association = connection.execute(
            "SELECT * FROM m3_plan_day_associations WHERE plan_day_id=?",
            (day.plan_day_id,),
        ).fetchone()
        if association is None or association["company_plan_id"] != trusted.company_plan.company_plan_id:
            raise M3EvaluationInputConflictError("CURRENT_ASSOCIATION_CHANGED", "plan_day_id")
        history = self._history(connection, trusted.company_plan.company_plan_id)
        if not history or history[-1] != trusted.revision:
            raise M3EvaluationInputConflictError("CURRENT_PLAN_REVISION_CHANGED", "base_plan_revision")
        revision = history[-1]
        self._validate_stored_associations(connection, revision)
        canonical_tasks = self._validate_bindings(connection, revision)
        scope, authority = self._scope_from_current(revision, request, canonical_tasks)
        if scope != trusted.scope or authority != trusted.authority:
            raise M3EvaluationInputConflictError("CURRENT_SNAPSHOT_CHANGED", "current_planning_scope")
        direct = evaluate_unavailability_impact(request, scope, authority=authority)

        job_rows, job_roots, task_rows, handoff_rows = {}, {}, {}, {}
        m2_reader = M2DurableRepository(self.database_path)
        task_preconditions = []
        current_commitments = []
        assignment_preconditions = []
        for commitment in revision.commitments:
            job_key = commitment.job_id
            if job_key not in job_rows:
                job_rows[job_key] = connection.execute(
                    "SELECT * FROM m2_job_execution_roots WHERE job_id=?", (job_key,)
                ).fetchone()
                if job_rows[job_key] is not None:
                    job_roots[job_key] = m2_reader._job_root_from_connection(
                        connection, job_rows[job_key]
                    )
            task_key = (commitment.job_id, commitment.task_id)
            task_rows[task_key] = connection.execute(
                "SELECT * FROM m2_task_routes WHERE job_id=? AND task_id=?",
                task_key,
            ).fetchone()
            handoff_key = (commitment.job_id, commitment.source_revision, commitment.source_handoff_id)
            if handoff_key not in handoff_rows:
                handoff_rows[handoff_key] = connection.execute(
                    "SELECT * FROM m1_handoff_publications WHERE job_id=? AND source_revision=? AND handoff_id=?",
                    handoff_key,
                ).fetchone()
            job_row, task_row, handoff_row = job_rows[job_key], task_rows[task_key], handoff_rows[handoff_key]
            if job_row is None or task_row is None or handoff_row is None:
                raise M3EvaluationInputStorageError("CURRENT_PRECONDITION_MISSING", "canonical_task")
            task = canonical_tasks[task_key]
            task_preconditions.append(CurrentM2TaskPrecondition(
                job_id=commitment.job_id,
                job_execution_revision=job_row["job_execution_revision"],
                job_root_sha256=job_row["content_sha256"],
                task_id=commitment.task_id,
                task_definition_version=commitment.task_definition_version,
                task_route_sha256=task_row["route_sha256"],
                source_handoff_id=commitment.source_handoff_id,
                source_revision=commitment.source_revision,
                task_status=task.status,
            ))
            if task.status is not TaskStatus.DONE:
                current_commitments.append(CurrentCommitmentEvidence(
                    commitment_id=commitment.commitment_id,
                    job_id=commitment.job_id,
                    task_id=commitment.task_id,
                    task_definition_version=commitment.task_definition_version,
                    source_handoff_id=commitment.source_handoff_id,
                    source_revision=commitment.source_revision,
                    source_handoff_sha256=handoff_row["content_sha256"],
                    business_date=commitment.business_date,
                    planned_worker_ids=commitment.planned_worker_ids,
                    source_m2_assignment_ids=commitment.source_m2_assignment_ids,
                ))
            root = job_roots[commitment.job_id]
            for assignment_id in commitment.source_m2_assignment_ids:
                assignment = root.assignment(assignment_id)
                route = connection.execute(
                    "SELECT * FROM m2_assignment_routes WHERE assignment_id=?", (assignment_id,)
                ).fetchone()
                if assignment is None or route is None:
                    raise M3EvaluationInputStorageError("CURRENT_PRECONDITION_MISSING", "assignment")
                assignment_preconditions.append(CurrentM2AssignmentPrecondition(
                    assignment_id=assignment.assignment_id,
                    job_id=assignment.job_id,
                    task_id=assignment.task_id,
                    assignment_route_sha256=route["route_sha256"],
                    plan_day_ids=assignment.plan_day_ids,
                ))

        direct_scope = DirectCurrentImpactScope(
            m3a_evaluation_id=direct.evaluation_id,
            m3a_evaluation_fingerprint=direct.evaluation_fingerprint,
            outcome=direct.outcome,
            commitments=tuple(ImpactCommitmentReference(
                commitment_id=item.commitment_id, job_id=item.job_id, task_id=item.task_id
            ) for item in direct.affected_commitments),
        )
        dependency_scope = DependencyImpactScope(
            edges=tuple(DependencyEvidence(
                predecessor_task_id=item.predecessor_task_id,
                successor_task_id=item.successor_task_id,
                relation=item.relation,
                provenance_reference=item.provenance_reference,
            ) for item in revision.dependencies),
            commitments=self._dependency_impacts(revision, tuple(current_commitments), direct),
        )
        source = request.snapshot.source
        historical_scope = HistoricalSourceScope(
            request_fingerprint=request.request_fingerprint,
            input_namespace=source.input_namespace,
            event_id=source.event_id,
            server_event_id=source.server_event_id,
            input_payload_sha256=source.input_payload_sha256,
            reduction_proof_sha256=source.reduction_proof_sha256,
            effect_ordinal=source.effect_ordinal,
            effect_sha256=source.effect_sha256,
            historical_plan_result_sha256=request.snapshot.plan_day.result_sha256,
            assignments=tuple(HistoricalAssignmentEvidence(
                assignment_id=item.assignment_id,
                job_id=item.job_id,
                task_id=item.task_id,
                task_definition_version=item.task_definition_version,
                assignment_kind=item.assignment_kind,
                member_worker_ids=item.member_worker_ids,
                plan_day_ids=item.plan_day_ids,
                lead_worker_id=item.lead_worker_id,
                released_worker_ids=item.released_worker_ids,
                supersedes_assignment_id=item.supersedes_assignment_id,
                task_status=item.task_status,
                task_source_handoff_id=item.task_source_handoff_id,
                task_source_revision=item.task_source_revision,
                supersedes_task_id=item.supersedes_task_id,
                job_execution_revision=item.job_execution_revision,
                job_root_sha256=item.job_root_sha256,
            ) for item in request.snapshot.assignments),
            job_roots=tuple(HistoricalJobRootEvidence(
                job_id=item[0], revision=item[1], content_sha256=item[2]
            ) for item in request.snapshot.job_root_preimages),
        )
        value = M3EvaluationInput(
            historical_source_scope=historical_scope,
            company_plan_id=revision.company_plan_id,
            base_plan_revision=revision.revision,
            base_plan_revision_id=revision.revision_id,
            base_plan_revision_fingerprint=revision.fingerprint,
            current_planning_scope_fingerprint=scope.snapshot_fingerprint,
            unavailable_worker_id=day.worker_id,
            business_date=day.business_date,
            plan_day_id=day.plan_day_id,
            plan_day_associations=tuple(PlanDayAssociationEvidence(
                plan_day_id=item.plan_day_id, worker_id=item.worker_id, business_date=item.business_date
            ) for item in revision.plan_days),
            current_active_commitments=tuple(current_commitments),
            current_m2_task_preconditions=tuple(task_preconditions),
            current_m2_assignment_preconditions=tuple(assignment_preconditions),
            direct_current_impact=direct_scope,
            dependency_impact=dependency_scope,
            candidate_induced_impact=CandidateInducedImpactScope(),
        )
        self._validate_revision_evidence(
            connection, value, M3EvaluationInputConflictError
        )
        return value

    def record_evaluation_input(self, request: M3FeasibilityEvaluationRequest) -> M3EvaluationInput:
        """Idempotently persist the exact current evidence selected for request."""
        trusted = self.produce_current_snapshot(request)
        try:
            with self.transaction() as connection:
                value = self._build_input(connection, trusted)
                self._validate_revision_evidence(
                    connection, value, M3EvaluationInputConflictError
                )
                raw = semantic_evaluation_input_json(value)
                existing = connection.execute(
                    "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
                    (value.evaluation_input_id,),
                ).fetchone()
                if existing is not None:
                    restored = self._from_row(connection, existing)
                    if restored != value:
                        raise M3EvaluationInputConflictError("EVALUATION_REPLAY_CONFLICT", "evaluation_input_id")
                    return restored
                connection.execute(
                    "INSERT INTO m3_evaluation_inputs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        value.evaluation_input_id,
                        value.evaluation_input_fingerprint,
                        value.schema_version,
                        value.rule_version,
                        value.historical_source_scope.request_fingerprint,
                        value.historical_source_scope.event_id,
                        value.company_plan_id,
                        value.base_plan_revision,
                        value.base_plan_revision_id,
                        value.current_planning_scope_fingerprint,
                        raw,
                    ),
                )
                return value
        except sqlite3.IntegrityError as error:
            raise M3EvaluationInputConflictError("EVALUATION_PERSISTENCE_CONFLICT", "evaluation_input") from error
        except sqlite3.Error as error:
            raise M3EvaluationInputStorageError("EVALUATION_STORAGE_FAILURE", "evaluation_input") from error

    def _from_row(self, connection, row) -> M3EvaluationInput:
        value = restore_evaluation_input(
            row["canonical_semantic_json"], row["evaluation_input_fingerprint"], row["evaluation_input_id"]
        )
        expected = (
            value.schema_version,
            value.rule_version,
            value.historical_source_scope.request_fingerprint,
            value.historical_source_scope.event_id,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.current_planning_scope_fingerprint,
        )
        actual = (
            row["schema_version"], row["rule_version"], row["source_request_fingerprint"],
            row["source_event_id"], row["company_plan_id"], row["base_plan_revision"],
            row["base_plan_revision_id"], row["current_scope_fingerprint"],
        )
        if actual != expected:
            raise M3EvaluationInputStorageError("EVALUATION_METADATA_MISMATCH", "evaluation_input")
        self._validate_revision_evidence(
            connection, value, M3EvaluationInputStorageError
        )
        return value

    def get_evaluation_input(self, evaluation_input_id: str) -> M3EvaluationInput:
        from werkcrew_ai.planning.evaluation_input import _identity

        _identity(evaluation_input_id, "evaluation_input_id")
        with self._read_snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
                (evaluation_input_id,),
            ).fetchone()
            if row is None:
                raise M3EvaluationInputStorageError("EVALUATION_INPUT_NOT_FOUND", "evaluation_input_id")
            return self._from_row(connection, row)
