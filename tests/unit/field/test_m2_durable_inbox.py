from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from werkcrew_ai.field.models import (
    ActionExceptionReason,
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DeliveryEvidence,
    DirectiveClass,
    DirectiveDefinition,
    DirectiveRoot,
    DirectiveType,
    EffectType,
    EvidenceItem,
    EvidenceKind,
    FieldEventEnvelope,
    FieldEventInput,
    FieldEventType,
    M2JobExecutionRoot,
    M2Policy,
    PlanDayRoot,
    PlanDayStatus,
    PolicyTimeContext,
    ProblemHint,
    Reduction,
    ReductionOutcome,
    RootDelta,
    RootKind,
    SystemSignal,
    SystemSignalType,
    TaskDefinition,
    TaskState,
    TaskStatus,
    WorkerIdentityRegistry,
)
from werkcrew_ai.field.repository import (
    FIELD_EVENT,
    SYSTEM_SIGNAL,
    DurableReductionResult,
    IngressAcceptance,
    M2ConflictError,
    M2DurableRepository,
    M2InputPendingError,
    M2NotFoundError,
    M2StaleRevisionError,
    M2StorageIntegrityError,
)
from werkcrew_ai.field.reducer import reduce
from werkcrew_ai.field.serialization import (
    ImmutablePrecondition,
    PreconditionVector,
    ResultingRevision,
    ResultingRevisionVector,
    RootAccess,
    RootPrecondition,
    RoutingVector,
    canonical_json,
    deserialize_directive_root,
    deserialize_field_event_envelope,
    deserialize_job_execution_root,
    deserialize_plan_day_root,
    deserialize_policy_time_context,
    deserialize_precondition_vector,
    deserialize_processed_event_receipt,
    deserialize_routing_vector,
    deserialize_system_signal,
    deserialize_worker_registry,
    serialize_directive_root,
    serialize_effects,
    serialize_field_event_envelope,
    serialize_job_execution_root,
    serialize_plan_day_root,
    serialize_policy_time_context,
    serialize_processed_event_receipt,
    serialize_precondition_vector,
    serialize_resulting_revision_vector,
    serialize_routing_vector,
    serialize_string_tuple,
    serialize_system_signal,
    serialize_worker_registry,
    verify_canonical_document,
)
from werkcrew_ai.intake import (
    CanonicalFactInput,
    CanonicalJobIntake,
    CanonicalJobRepository,
    FactKnowledgeState,
    FactVerificationState,
    JobFactName,
    M1BoundaryPublicationRepository,
)
from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY, SqlitePersistence
from werkcrew_ai.migrations import discover_migrations


NOW = datetime(2026, 9, 4, 8, 20, tzinfo=timezone.utc)
WORKER = "worker-one"
OTHER_WORKER = "worker-two"


@dataclass(frozen=True)
class Seed:
    job_id: str
    task_id: str
    assignment_id: str
    plan_day_id: str
    publication: object


def _initialize(path: Path, *, fault=None) -> M2DurableRepository:
    repository = M2DurableRepository(path, fault_injector=fault)
    repository.initialize(now=NOW)
    return repository


def _registry(repository: M2DurableRepository) -> None:
    repository.create_worker_registry(
        WorkerIdentityRegistry((WORKER, OTHER_WORKER))
    )


def _publication(path: Path, job_id: str):
    CanonicalJobRepository(path).create_job(
        job_id=job_id,
        intake=CanonicalJobIntake("durable-test", None, None, ()),
        created_at=NOW - timedelta(days=1),
    )
    boundary = M1BoundaryPublicationRepository(path)
    return boundary.publish_handoff(
        boundary.current_projection(job_id),
        published_at=NOW - timedelta(hours=12),
    )


def _plan(plan_day_id: str = "plan-day-one", *, status=PlanDayStatus.ISSUED):
    return PlanDayRoot(
        plan_day_id=plan_day_id,
        worker_id=WORKER,
        business_date=date(2026, 9, 4),
        start_at=NOW - timedelta(hours=1),
        status=status,
        confirmed_plan_reference="plan-original",
    )


def _task(publication, task_id: str, *, status=TaskStatus.OPEN) -> TaskState:
    return TaskState(
        definition=TaskDefinition(
            task_id=task_id,
            definition_version=f"{task_id}-v1",
            job_id=publication.job_id,
            source_handoff_id=publication.handoff_id,
            source_revision=publication.source_revision,
            business_meaning=f"work for {task_id}",
            completion_type=CompletionType.TASK,
        ),
        status=status,
        blocked_pending_resolution=status is TaskStatus.SAFE_HOLD,
        execution_authorized=status is not TaskStatus.SAFE_HOLD,
    )


def _assignment(job_id: str, task_id: str, assignment_id: str, plan_day_id: str):
    return AssignmentState(
        assignment_id=assignment_id,
        job_id=job_id,
        task_id=task_id,
        task_definition_version=f"{task_id}-v1",
        kind=AssignmentKind.SINGLE,
        member_worker_ids=(WORKER,),
        plan_day_ids=(plan_day_id,),
    )


def _seed(
    path: Path,
    repository: M2DurableRepository,
    suffix: str = "one",
    *,
    plan_day_id: str = "plan-day-one",
    task_status: TaskStatus = TaskStatus.OPEN,
) -> Seed:
    _registry(repository)
    repository.create_plan_day_root(_plan(plan_day_id))
    job_id = f"job-{suffix}"
    task_id = f"task-{suffix}"
    assignment_id = f"assignment-{suffix}"
    publication = _publication(path, job_id)
    task = _task(publication, task_id, status=task_status)
    repository.create_job_execution_root(
        M2JobExecutionRoot(
            job_id,
            (task,),
            (_assignment(job_id, task_id, assignment_id, plan_day_id),),
        )
    )
    return Seed(job_id, task_id, assignment_id, plan_day_id, publication)


def _context(*, now: datetime = NOW) -> PolicyTimeContext:
    return PolicyTimeContext(now=now, policy=M2Policy())


def _event(
    event_id: str,
    event_type: FieldEventType,
    *,
    server_event_id: str | None = None,
    actor_id: str = WORKER,
    **values,
) -> FieldEventInput:
    return FieldEventInput(
        FieldEventEnvelope(
            event_id=event_id,
            schema_version=1,
            event_type=event_type,
            actor_id=actor_id,
            occurred_at=NOW,
            **values,
        ),
        server_event_id or f"server-{event_id}",
    )


def _task_event(seed: Seed, event_id: str, event_type: FieldEventType, **values):
    return _event(
        event_id,
        event_type,
        job_id=seed.job_id,
        task_id=seed.task_id,
        assignment_id=seed.assignment_id,
        plan_day_id=seed.plan_day_id,
        **values,
    )


def _directive(seed: Seed, directive_id: str, directive_class: DirectiveClass):
    directive_type = (
        DirectiveType.STOP_DIRECTIVE
        if directive_class is DirectiveClass.STOP
        else DirectiveType.ACTION_REQUIRED
    )
    return DirectiveRoot(
        DirectiveDefinition(
            directive_id=directive_id,
            directive_type=directive_type,
            directive_class=directive_class,
            worker_id=WORKER,
            issued_at=NOW - timedelta(minutes=5),
            escalation_due_at=NOW - timedelta(minutes=1),
            job_id=seed.job_id,
            task_id=seed.task_id,
            assignment_id=seed.assignment_id,
            plan_day_id=seed.plan_day_id,
            proposed_plan_reference="plan-updated"
            if directive_class is DirectiveClass.ACTION
            else None,
        )
    )


def _ack(seed: Seed, directive_id: str, event_id: str = "event-ack"):
    return _task_event(
        seed,
        event_id,
        FieldEventType.WORKER_ACKNOWLEDGED,
        directive_id=directive_id,
    )


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _crash_at(expected: str):
    def crash(actual: str) -> None:
        if actual == expected:
            raise RuntimeError(f"injected crash at {actual}")

    return crash


def test_t01_identical_field_event_retry_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "t01.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event("event-activate", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id)

    first = repo.execute(event, _context(), received_at=NOW)
    replay = M2DurableRepository(path).execute(
        event,
        _context(now=NOW + timedelta(hours=2)),
        received_at=NOW + timedelta(hours=2),
    )

    assert first.outcome is ReductionOutcome.APPLIED
    assert replay.outcome is first.outcome and replay.replayed
    assert replay.root_deltas == () and replay.emitted_effects == ()
    assert replay.response_effects == first.response_effects
    assert _count(path, "m2_effect_outbox") == len(first.emitted_effects)


def test_t02_same_event_id_changed_payload_or_target_is_durable_conflict(tmp_path: Path) -> None:
    path = tmp_path / "t02.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    first = _event("same-event", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id)
    repo.execute(first, _context(), received_at=NOW)
    changed = _event(
        "same-event",
        FieldEventType.START_DELAY_REPORTED,
        plan_day_id=seed.plan_day_id,
        eta="09:00",
    )

    with pytest.raises(M2ConflictError, match="PAYLOAD_MISMATCH"):
        M2DurableRepository(path).execute(changed, _context(), received_at=NOW)
    with pytest.raises(M2ConflictError, match="PAYLOAD_MISMATCH"):
        M2DurableRepository(path).execute(changed, _context(), received_at=NOW)

    assert _count(path, "m2_input_conflicts") == 1
    assert M2DurableRepository(path).get_plan_day_root(seed.plan_day_id).status is PlanDayStatus.ACTIVE


def test_t03_different_event_id_same_server_event_id_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "t03.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.execute(
        _event("event-owner", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id, server_event_id="server-one"),
        _context(),
        received_at=NOW,
    )
    collision = repo.execute(
        _event(
            "event-collision",
            FieldEventType.START_DELAY_REPORTED,
            plan_day_id=seed.plan_day_id,
            eta="09:00",
            server_event_id="server-one",
        ),
        _context(),
        received_at=NOW,
    )

    assert collision.outcome is ReductionOutcome.REJECTED
    assert collision.reason_codes == ("SERVER_EVENT_ID_CONFLICT",)
    assert _count(path, "m2_input_conflicts") == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_input_inbox WHERE claimed_server_event_id='server-one'"
        ).fetchone() == (1,)


def test_t04_reducer_rejection_is_persisted_and_replayed(tmp_path: Path) -> None:
    path = tmp_path / "t04.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(
        seed,
        "event-invalid-block",
        FieldEventType.WORK_START_BLOCKED,
    )
    first = repo.execute(event, _context(), received_at=NOW)
    replay = M2DurableRepository(path).execute(event, _context(), received_at=NOW)

    assert first.outcome is ReductionOutcome.REJECTED
    assert "WORK_START_BLOCKED_REASON_REQUIRED" in first.reason_codes
    assert replay.replayed and replay.reason_codes == first.reason_codes
    assert len(M2DurableRepository(path).load_processed_event_ledger().receipts) == 1


def test_t05_crash_after_tx1_leaves_received_input_restartable(tmp_path: Path) -> None:
    path = tmp_path / "t05.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event("event-tx1", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id)
    accepted = repo.accept_input(event, _context(), received_at=NOW)
    assert isinstance(accepted, IngressAcceptance)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id='event-tx1'"
        ).fetchone() == ("RECEIVED",)
    result = M2DurableRepository(path).process_input(FIELD_EVENT, "event-tx1", completed_at=NOW)
    assert result.outcome is ReductionOutcome.APPLIED


def test_t06_crash_during_hydration_or_reducer_rolls_back_tx2(tmp_path: Path) -> None:
    path = tmp_path / "t06.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event("event-hydration-crash", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id)
    repo.accept_input(event, _context(), received_at=NOW)

    crashing = M2DurableRepository(path, fault_injector=_crash_at("during_hydration"))
    with pytest.raises(RuntimeError, match="during_hydration"):
        crashing.process_input(FIELD_EVENT, event.event.event_id, completed_at=NOW)

    assert M2DurableRepository(path).get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone() == ("RECEIVED",)


def test_t07_crash_after_first_root_update_rolls_back_everything(tmp_path: Path) -> None:
    path = tmp_path / "t07.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(_directive(seed, "directive-action", DirectiveClass.ACTION))
    event = _ack(seed, "directive-action")
    repo.accept_input(event, _context(), received_at=NOW)

    crashing = M2DurableRepository(path, fault_injector=_crash_at("after_first_root_update"))
    with pytest.raises(RuntimeError, match="after_first_root_update"):
        crashing.process_input(FIELD_EVENT, event.event.event_id, completed_at=NOW)

    fresh = M2DurableRepository(path)
    assert fresh.get_directive_root("directive-action").directive_revision == 0
    assert fresh.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    assert _count(path, "m2_effect_outbox") == 0


def test_t08_commit_before_response_then_retry_returns_first_result(tmp_path: Path) -> None:
    path = tmp_path / "t08.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event("event-commit", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id)
    first = repo.execute(event, _context(), received_at=NOW)
    del first

    replay = M2DurableRepository(path).execute(event, _context(), received_at=NOW)
    assert replay.replayed and replay.outcome is ReductionOutcome.APPLIED
    assert M2DurableRepository(path).get_plan_day_root(seed.plan_day_id).plan_day_revision == 1
    assert _count(path, "m2_effect_outbox") == 1


def test_t09_concurrent_events_same_job_root_serialize_without_lost_update(tmp_path: Path) -> None:
    path = tmp_path / "t09.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    events = tuple(
        _task_event(
            seed,
            f"event-problem-{index}",
            FieldEventType.SITE_PROBLEM_REPORTED,
            text=f"problem {index}",
        )
        for index in range(2)
    )

    def run(item):
        return M2DurableRepository(path).execute(item, _context(), received_at=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(run, events))

    assert all(item.outcome is ReductionOutcome.APPLIED for item in results)
    root = M2DurableRepository(path).get_job_execution_root(seed.job_id)
    assert root.job_execution_revision == 2
    assert len(root.task(seed.task_id).observations) == 2


def test_t10_independent_job_roots_keep_independent_revisions(tmp_path: Path) -> None:
    path = tmp_path / "t10.db"
    repo = _initialize(path)
    first = _seed(path, repo, "a", plan_day_id="plan-a")
    second = _seed(path, repo, "b", plan_day_id="plan-b")
    for seed in (first, second):
        repo.execute(
            _task_event(seed, f"problem-{seed.job_id}", FieldEventType.SITE_PROBLEM_REPORTED, text="x"),
            _context(),
            received_at=NOW,
        )
    assert repo.get_job_execution_root(first.job_id).job_execution_revision == 1
    assert repo.get_job_execution_root(second.job_id).job_execution_revision == 1


def test_t11_stale_mutated_root_cas_precondition_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "t11.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    with repo._connect() as connection:
        row = connection.execute(
            "SELECT plan_day_revision, content_sha256 FROM m2_plan_day_roots WHERE plan_day_id=?",
            (seed.plan_day_id,),
        ).fetchone()
    stale = RootPrecondition("PLAN_DAY", seed.plan_day_id, RootAccess.MUTATE, row[0], row[1])
    repo.execute(
        _event("activate", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id),
        _context(),
        received_at=NOW,
    )
    with repo.transaction() as connection, pytest.raises(M2StaleRevisionError):
        repo._recheck_preconditions(connection, PreconditionVector((stale,)))


def test_t12_safe_hold_survives_database_reload(tmp_path: Path) -> None:
    path = tmp_path / "t12.db"
    repo = _initialize(path)
    seed = _seed(path, repo, task_status=TaskStatus.SAFE_HOLD)
    task = M2DurableRepository(path).get_job_execution_root(seed.job_id).task(seed.task_id)
    assert task.status is TaskStatus.SAFE_HOLD
    assert task.blocked_pending_resolution and not task.execution_authorized


def test_t13_ack_receipt_and_informed_state_survive_reload(tmp_path: Path) -> None:
    path = tmp_path / "t13.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(_directive(seed, "directive-action", DirectiveClass.ACTION))
    repo.execute(_ack(seed, "directive-action"), _context(), received_at=NOW)
    fresh = M2DurableRepository(path)
    signal = SystemSignal("e1-check", SystemSignalType.DIRECTIVE_E1_ELAPSED, directive_id="directive-action")
    with fresh._connect() as connection:
        scope = fresh._hydrate(connection, signal).scope
    assert scope.directive_worker_informed("directive-action")


def test_t14_evidence_identity_and_scope_survive_reload(tmp_path: Path) -> None:
    path = tmp_path / "t14.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    evidence = EvidenceItem("evidence-one", EvidenceKind.PHOTO, "blob:one")
    repo.execute(
        _task_event(seed, "problem-evidence", FieldEventType.SITE_PROBLEM_REPORTED, attachments=(evidence,)),
        _context(),
        received_at=NOW,
    )
    changed = replace(evidence, content_reference="blob:forged")
    result = M2DurableRepository(path).execute(
        _task_event(seed, "problem-forged", FieldEventType.SITE_PROBLEM_REPORTED, attachments=(changed,)),
        _context(),
        received_at=NOW,
    )
    assert result.outcome is ReductionOutcome.REJECTED
    assert "EVIDENCE_ID_CONFLICT" in result.reason_codes
    assert _count(path, "m2_evidence_identities") == 1


def test_t15_completion_event_id_survives_restart_and_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "t15.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(seed, "completion-one", FieldEventType.TASK_COMPLETION_REPORTED)
    first = repo.execute(event, _context(), received_at=NOW)
    replay = M2DurableRepository(path).execute(event, _context(), received_at=NOW)
    task = M2DurableRepository(path).get_job_execution_root(seed.job_id).task(seed.task_id)
    assert first.outcome is ReductionOutcome.APPLIED and replay.replayed
    assert task.completion_event_id == "completion-one"
    assert _count(path, "m2_effect_outbox") == 1


def test_t16_system_signal_is_durably_deduplicated(tmp_path: Path) -> None:
    path = tmp_path / "t16.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    signal = SystemSignal("signal-window", SystemSignalType.START_WINDOW_ELAPSED, plan_day_id=seed.plan_day_id)
    first = repo.execute(signal, _context(), received_at=NOW)
    replay = M2DurableRepository(path).execute(signal, _context(), received_at=NOW)
    assert first.outcome is ReductionOutcome.APPLIED
    assert replay.replayed and replay.emitted_effects == ()
    assert _count(path, "m2_effect_outbox") == 1


def test_t17_effect_outbox_recording_is_exactly_once_per_input_ordinal(tmp_path: Path) -> None:
    path = tmp_path / "t17.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(seed, "blocked", FieldEventType.WORK_START_BLOCKED, reason_class="NO_ACCESS")
    first = repo.execute(event, _context(), received_at=NOW)
    M2DurableRepository(path).execute(event, _context(), received_at=NOW)
    assert _count(path, "m2_effect_outbox") == len(first.emitted_effects)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT group_concat(effect_ordinal, ',') FROM m2_effect_outbox WHERE input_id='blocked'"
        ).fetchone()[0] == ",".join(str(i) for i in range(len(first.emitted_effects)))


def test_t18_upgrade_0005_to_0006_preserves_m1_and_creates_no_fake_m2(tmp_path: Path) -> None:
    path = tmp_path / "t18.db"
    first_five = discover_migrations(MIGRATIONS_DIRECTORY)[:5]
    with sqlite3.connect(path) as connection:
        for migration in first_five:
            connection.executescript(migration.sql)
        connection.executemany(
            """
            INSERT INTO schema_migrations(
                migration_id, version, name, checksum_sha256, applied_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                (
                    migration.migration_id,
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                    NOW.isoformat(),
                )
                for migration in first_five
            ),
        )
    publication = _publication(path, "job-upgrade")

    upgraded = M2DurableRepository(path)
    assert upgraded.initialize(now=NOW) == (
        "0006_m2_durable_inbox",
        "0007_m3_current_plan_bootstrap",
        "0008_m3_evaluation_input",
        "0009_m3_feasibility_support",
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM m1_handoff_publications").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM m2_job_execution_roots").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM m2_plan_day_roots").fetchone() == (0,)
    assert publication.job_id == "job-upgrade"


def test_t19_multi_job_shared_plan_day_has_one_canonical_root_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "t19.db"
    repo = _initialize(path)
    first = _seed(path, repo, "a", plan_day_id="shared-plan")
    second = _seed(path, repo, "b", plan_day_id="shared-plan")
    result = repo.execute(
        _event("activate-shared", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id="shared-plan"),
        _context(),
        received_at=NOW,
    )
    assert tuple(item.root_kind for item in result.root_deltas) == (RootKind.PLAN_DAY,)
    assert _count(path, "m2_plan_day_roots") == 1
    fresh = M2DurableRepository(path)
    assert fresh.get_plan_day_root("shared-plan").plan_day_revision == 1
    assert fresh.get_job_execution_root(first.job_id).assignment(first.assignment_id).plan_day_ids == ("shared-plan",)
    assert fresh.get_job_execution_root(second.job_id).assignment(second.assignment_id).plan_day_ids == ("shared-plan",)


def _two_handoffs(path: Path, repo: M2DurableRepository, *, safe_a=False):
    _registry(repo)
    repo.create_plan_day_root(_plan())
    pub_a = _publication(path, "job-history")
    CanonicalJobRepository(path).record_fact(
        job_id="job-history",
        fact=CanonicalFactInput(
            JobFactName.SCOPE,
            "new scope",
            FactKnowledgeState.KNOWN,
            FactVerificationState.UNVERIFIED,
            "test",
        ),
        recorded_at=NOW - timedelta(hours=6),
    )
    boundary = M1BoundaryPublicationRepository(path)
    pub_b = boundary.publish_handoff(
        boundary.current_projection("job-history"), published_at=NOW - timedelta(hours=5)
    )
    task_a = _task(pub_a, "task-history-a", status=TaskStatus.SAFE_HOLD if safe_a else TaskStatus.OPEN)
    task_b = _task(pub_b, "task-history-b")
    assignments = (
        _assignment("job-history", "task-history-a", "assignment-history-a", "plan-day-one"),
        _assignment("job-history", "task-history-b", "assignment-history-b", "plan-day-one"),
    )
    repo.create_job_execution_root(M2JobExecutionRoot("job-history", (task_a, task_b), assignments))
    return pub_a, pub_b


def test_t20_handoffs_a_and_b_round_trip_in_one_job_root(tmp_path: Path) -> None:
    path = tmp_path / "t20.db"
    repo = _initialize(path)
    pub_a, pub_b = _two_handoffs(path, repo)
    root = M2DurableRepository(path).get_job_execution_root("job-history")
    assert root.task("task-history-a").definition.source_handoff_id == pub_a.handoff_id
    assert root.task("task-history-b").definition.source_handoff_id == pub_b.handoff_id
    assert {item.definition.source_revision for item in root.tasks} == {1, 2}


def test_t21_late_assignment_a_routes_to_a_after_b_exists_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "t21.db"
    repo = _initialize(path)
    _two_handoffs(path, repo)
    seed_a = Seed("job-history", "task-history-a", "assignment-history-a", "plan-day-one", None)
    result = M2DurableRepository(path).execute(
        _task_event(seed_a, "late-a", FieldEventType.SITE_PROBLEM_REPORTED, text="late A"),
        _context(),
        received_at=NOW,
    )
    root = M2DurableRepository(path).get_job_execution_root("job-history")
    assert result.outcome is ReductionOutcome.APPLIED
    assert tuple(item.event_id for item in root.task("task-history-a").observations) == ("late-a",)
    assert root.task("task-history-b").observations == ()


def test_t22_safe_hold_a_survives_restart_with_newer_handoff_b(tmp_path: Path) -> None:
    path = tmp_path / "t22.db"
    repo = _initialize(path)
    _two_handoffs(path, repo, safe_a=True)
    root = M2DurableRepository(path).get_job_execution_root("job-history")
    task_a = root.task("task-history-a")
    assert task_a.status is TaskStatus.SAFE_HOLD
    assert task_a.blocked_pending_resolution and not task_a.execution_authorized
    assert root.task("task-history-b").status is TaskStatus.OPEN


def test_t23_plan_only_directive_and_ack_survive_restart_without_job(tmp_path: Path) -> None:
    path = tmp_path / "t23.db"
    repo = _initialize(path)
    _registry(repo)
    repo.create_plan_day_root(_plan())
    directive = DirectiveRoot(
        DirectiveDefinition(
            "directive-plan-only",
            DirectiveType.ACTION_REQUIRED,
            DirectiveClass.ACTION,
            WORKER,
            NOW,
            plan_day_id="plan-day-one",
            proposed_plan_reference="plan-only-new",
        )
    )
    repo.create_directive_root(directive)
    event = _event(
        "ack-plan-only",
        FieldEventType.WORKER_ACKNOWLEDGED,
        plan_day_id="plan-day-one",
        directive_id="directive-plan-only",
    )
    repo.execute(event, _context(), received_at=NOW)
    fresh = M2DurableRepository(path)
    assert fresh.get_plan_day_root("plan-day-one").confirmed_plan_reference == "plan-only-new"
    assert fresh.get_directive_root("directive-plan-only").delivery_evidence is DeliveryEvidence.ACKED
    assert _count(path, "m2_job_execution_roots") == 0


def test_t24_worker_registry_is_canonical_across_jobs_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "t24.db"
    repo = _initialize(path)
    _seed(path, repo, "a", plan_day_id="plan-a")
    _seed(path, repo, "b", plan_day_id="plan-b")
    fresh = M2DurableRepository(path)
    assert fresh.get_worker_registry() == WorkerIdentityRegistry((WORKER, OTHER_WORKER))
    assert _count(path, "m2_worker_registry") == 1
    assert _count(path, "m2_worker_identities") == 2


def test_t25_action_ack_multi_root_crash_and_retry_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "t25.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(_directive(seed, "directive-action", DirectiveClass.ACTION))
    event = _ack(seed, "directive-action")
    repo.accept_input(event, _context(), received_at=NOW)
    with pytest.raises(RuntimeError):
        M2DurableRepository(path, fault_injector=_crash_at("after_outbox_insert")).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )
    assert M2DurableRepository(path).get_directive_root("directive-action").directive_revision == 0
    assert M2DurableRepository(path).get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    completed = M2DurableRepository(path).process_input(FIELD_EVENT, event.event.event_id, completed_at=NOW)
    assert len(completed.root_deltas) == 2
    assert _count(path, "m2_effect_outbox") == 1


def test_t26_stop_exception_multi_root_crash_and_retry_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "t26.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(_directive(seed, "directive-stop", DirectiveClass.STOP))
    event = _task_event(
        seed,
        "stop-exception",
        FieldEventType.WORKER_ACTION_EXCEPTION,
        directive_id="directive-stop",
        reason_class=ActionExceptionReason.OTHER.value,
    )
    repo.accept_input(event, _context(), received_at=NOW)
    with pytest.raises(RuntimeError):
        M2DurableRepository(path, fault_injector=_crash_at("after_first_root_update")).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )
    fresh = M2DurableRepository(path)
    assert fresh.get_directive_root("directive-stop").directive_revision == 0
    assert fresh.get_job_execution_root(seed.job_id).job_execution_revision == 0
    result = fresh.process_input(FIELD_EVENT, event.event.event_id, completed_at=NOW)
    assert {item.root_kind for item in result.root_deltas} == {RootKind.DIRECTIVE, RootKind.JOB_EXECUTION}


def test_t27_stale_read_authority_root_precondition_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "t27.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    with repo._connect() as connection:
        row = connection.execute(
            "SELECT plan_day_revision, content_sha256 FROM m2_plan_day_roots WHERE plan_day_id=?",
            (seed.plan_day_id,),
        ).fetchone()
    authority = RootPrecondition("PLAN_DAY", seed.plan_day_id, RootAccess.READ_AUTHORITY, row[0], row[1])
    repo.execute(
        _event("authority-change", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id=seed.plan_day_id),
        _context(),
        received_at=NOW,
    )
    with repo.transaction() as connection, pytest.raises(M2StaleRevisionError):
        repo._recheck_preconditions(connection, PreconditionVector((authority,)))


def test_t28_concurrent_jobs_sharing_plan_day_have_one_serial_revision(tmp_path: Path) -> None:
    path = tmp_path / "t28.db"
    repo = _initialize(path)
    first = _seed(path, repo, "a", plan_day_id="shared")
    second = _seed(path, repo, "b", plan_day_id="shared")
    events = (
        _event("activate-a", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id="shared", job_id=first.job_id),
        _event("activate-b", FieldEventType.DAY_PLAN_ACTIVATED, plan_day_id="shared", job_id=second.job_id),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                lambda item: M2DurableRepository(path).execute(item, _context(), received_at=NOW).outcome,
                events,
            )
        )
    assert outcomes.count(ReductionOutcome.APPLIED) == 1
    assert outcomes.count(ReductionOutcome.NOOP) == 1
    assert M2DurableRepository(path).get_plan_day_root("shared").plan_day_revision == 1


def test_t29_canonical_serializer_round_trip_equality_for_all_durable_values(tmp_path: Path) -> None:
    path = tmp_path / "t29.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    directive = _directive(seed, "directive-action", DirectiveClass.ACTION)
    event = _ack(seed, "directive-action")
    signal = SystemSignal("signal", SystemSignalType.START_WINDOW_ELAPSED, plan_day_id=seed.plan_day_id)
    context = _context()
    registry = repo.get_worker_registry()
    job = repo.get_job_execution_root(seed.job_id)
    plan = repo.get_plan_day_root(seed.plan_day_id)
    receipt = replace(
        repo.execute(
            _task_event(seed, "rejection", FieldEventType.WORK_START_BLOCKED),
            context,
            received_at=NOW,
        ).appended_receipt
    )
    assert deserialize_worker_registry(serialize_worker_registry(registry)) == registry
    assert deserialize_job_execution_root(serialize_job_execution_root(job)) == job
    assert deserialize_plan_day_root(serialize_plan_day_root(plan)) == plan
    assert deserialize_directive_root(serialize_directive_root(directive)) == directive
    assert deserialize_field_event_envelope(serialize_field_event_envelope(event.event)) == event.event
    assert deserialize_system_signal(serialize_system_signal(signal)) == signal
    assert deserialize_policy_time_context(serialize_policy_time_context(context)) == context
    assert deserialize_processed_event_receipt(serialize_processed_event_receipt(receipt)) == receipt


def test_t30_malformed_hash_revision_identity_or_projection_fails_closed(tmp_path: Path) -> None:
    mutations = (
        "UPDATE m2_job_execution_roots SET job_execution_revision=1, content_sha256='" + "0" * 64 + "' WHERE job_id='job-one'",
        "UPDATE m2_job_execution_roots SET job_execution_revision=1 WHERE job_id='job-one'",
        "UPDATE m2_job_execution_roots SET job_execution_revision=1, canonical_root_json=replace(canonical_root_json, '\"job-one\"', '\"job-forged\"'), content_sha256=werkcrew_sha256(replace(canonical_root_json, '\"job-one\"', '\"job-forged\"')) WHERE job_id='job-one'",
        "DROP TRIGGER m2_task_routes_no_update; UPDATE m2_task_routes SET route_json='{}', route_sha256=werkcrew_sha256('{}') WHERE task_id='task-one'",
    )
    for index, mutation in enumerate(mutations):
        path = tmp_path / f"t30-{index}.db"
        repo = _initialize(path)
        _seed(path, repo)
        with repo._connect() as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.executescript(mutation)
        with pytest.raises(M2StorageIntegrityError):
            M2DurableRepository(path).get_job_execution_root("job-one")


def test_initial_root_evidence_identity_cannot_migrate_to_another_job(tmp_path: Path) -> None:
    path = tmp_path / "initial-evidence-scope.db"
    repo = _initialize(path)
    _registry(repo)
    repo.create_plan_day_root(_plan("shared-plan"))
    publication_a = _publication(path, "job-evidence-a")
    evidence = EvidenceItem("global-evidence", EvidenceKind.PHOTO, "blob:fixed")
    task_a = replace(
        _task(publication_a, "task-evidence-a"), evidence=(evidence,)
    )
    assignment_a = _assignment(
        "job-evidence-a", "task-evidence-a", "assignment-evidence-a", "shared-plan"
    )
    repo.create_job_execution_root(
        M2JobExecutionRoot("job-evidence-a", (task_a,), (assignment_a,))
    )
    publication_b = _publication(path, "job-evidence-b")
    task_b = _task(publication_b, "task-evidence-b")
    assignment_b = _assignment(
        "job-evidence-b", "task-evidence-b", "assignment-evidence-b", "shared-plan"
    )
    repo.create_job_execution_root(
        M2JobExecutionRoot("job-evidence-b", (task_b,), (assignment_b,))
    )
    seed_b = Seed(
        "job-evidence-b",
        "task-evidence-b",
        "assignment-evidence-b",
        "shared-plan",
        publication_b,
    )

    result = M2DurableRepository(path).execute(
        _task_event(
            seed_b,
            "reuse-in-b",
            FieldEventType.SITE_PROBLEM_REPORTED,
            attachments=(evidence,),
        ),
        _context(),
        received_at=NOW,
    )

    assert result.outcome is ReductionOutcome.REJECTED
    assert "EVIDENCE_SCOPE_CONFLICT" in result.reason_codes
    assert M2DurableRepository(path).get_job_execution_root(
        "job-evidence-b"
    ).task("task-evidence-b").evidence == ()


def test_completion_hydrates_plan_scoped_stop_authority(tmp_path: Path) -> None:
    path = tmp_path / "plan-stop.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(
        DirectiveRoot(
            DirectiveDefinition(
                "plan-stop",
                DirectiveType.STOP_DIRECTIVE,
                DirectiveClass.STOP,
                WORKER,
                NOW,
                plan_day_id=seed.plan_day_id,
            ),
            stop_in_force=True,
        )
    )

    result = M2DurableRepository(path).execute(
        _task_event(seed, "completion-blocked", FieldEventType.TASK_COMPLETION_REPORTED),
        _context(),
        received_at=NOW,
    )

    assert result.outcome is ReductionOutcome.REJECTED
    assert "STOP_IN_FORCE" in result.reason_codes
    assert M2DurableRepository(path).get_job_execution_root(
        seed.job_id
    ).task(seed.task_id).status is TaskStatus.OPEN


def test_crash_after_inbox_completion_before_commit_rolls_back_tx2(tmp_path: Path) -> None:
    path = tmp_path / "before-commit-crash.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "activate-before-commit",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    with pytest.raises(RuntimeError, match="before_processing_commit"):
        M2DurableRepository(
            path, fault_injector=_crash_at("before_processing_commit")
        ).process_input(FIELD_EVENT, event.event.event_id, completed_at=NOW)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone() == ("RECEIVED",)
    assert M2DurableRepository(path).get_plan_day_root(
        seed.plan_day_id
    ).plan_day_revision == 0
    assert _count(path, "m2_effect_outbox") == 0


def test_late_ack_after_newer_directive_cannot_roll_back_confirmed_plan(tmp_path: Path) -> None:
    path = tmp_path / "late-ack.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    old = DirectiveRoot(
        DirectiveDefinition(
            "directive-old",
            DirectiveType.ACTION_REQUIRED,
            DirectiveClass.ACTION,
            WORKER,
            NOW - timedelta(minutes=20),
            job_id=seed.job_id,
            task_id=seed.task_id,
            assignment_id=seed.assignment_id,
            plan_day_id=seed.plan_day_id,
            proposed_plan_reference="plan-old-ack",
            issuance_sequence=1,
        )
    )
    newer = DirectiveRoot(
        DirectiveDefinition(
            "directive-new",
            DirectiveType.ACTION_REQUIRED,
            DirectiveClass.ACTION,
            WORKER,
            NOW - timedelta(minutes=10),
            job_id=seed.job_id,
            task_id=seed.task_id,
            assignment_id=seed.assignment_id,
            plan_day_id=seed.plan_day_id,
            proposed_plan_reference="plan-new-ack",
            issuance_sequence=2,
            supersedes_directive_id="directive-old",
        )
    )
    repo.create_directive_root(old)
    repo.create_directive_root(newer)
    repo.execute(_ack(seed, "directive-new", "ack-new"), _context(), received_at=NOW)
    late = repo.execute(
        _ack(seed, "directive-old", "ack-old-late"),
        _context(),
        received_at=NOW,
    )

    assert tuple(item.root_kind for item in late.root_deltas) == (RootKind.DIRECTIVE,)
    assert repo.get_directive_root("directive-old").delivery_evidence is DeliveryEvidence.ACKED
    assert repo.get_plan_day_root(seed.plan_day_id).confirmed_plan_reference == "plan-new-ack"
    with repo._connect() as connection:
        row = connection.execute(
            "SELECT precondition_vector_json FROM m2_input_inbox WHERE input_id='ack-old-late'"
        ).fetchone()
    vector = deserialize_precondition_vector(row[0])
    assert {
        item.root_id for item in vector.roots if item.root_kind == "DIRECTIVE"
    } == {"directive-old", "directive-new"}


def test_forged_stored_ack_receipt_fails_closed_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "forged-ack.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(_directive(seed, "directive-action", DirectiveClass.ACTION))
    repo.execute(_ack(seed, "directive-action"), _context(), received_at=NOW)
    with repo._connect() as connection:
        row = connection.execute(
            "SELECT appended_receipt_json FROM m2_input_inbox WHERE input_id='event-ack'"
        ).fetchone()
        import json

        payload = json.loads(row[0])
        payload["payload"]["event"]["actor_id"] = OTHER_WORKER
        forged = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        connection.execute("DROP TRIGGER m2_input_inbox_immutable")
        connection.execute(
            """
            UPDATE m2_input_inbox
            SET appended_receipt_json=?, appended_receipt_sha256=?
            WHERE input_id='event-ack'
            """,
            (forged, hashlib.sha256(forged.encode("utf-8")).hexdigest()),
        )

    fresh = M2DurableRepository(path)
    with fresh._connect() as connection, pytest.raises(M2StorageIntegrityError):
        fresh._hydrate(
            connection,
            SystemSignal(
                "e1-forged",
                SystemSignalType.DIRECTIVE_E1_ELAPSED,
                directive_id="directive-action",
            ),
        )


def test_worker_registry_read_authority_revision_is_rechecked(tmp_path: Path) -> None:
    path = tmp_path / "registry-cas.db"
    repo = _initialize(path)
    _registry(repo)
    with repo._connect() as connection:
        row = connection.execute(
            "SELECT registry_revision, content_sha256 FROM m2_worker_registry"
        ).fetchone()
    old = RootPrecondition(
        "WORKER_REGISTRY", "GLOBAL", RootAccess.READ_AUTHORITY, row[0], row[1]
    )
    updated = replace(repo.get_worker_registry(), registry_revision=1)
    raw = serialize_worker_registry(updated)
    with repo.transaction() as connection:
        connection.execute(
            """
            UPDATE m2_worker_registry
            SET registry_revision=1, canonical_root_json=?, content_sha256=?
            WHERE registry_key='GLOBAL'
            """,
            (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()),
        )
    with repo.transaction() as connection, pytest.raises(M2StaleRevisionError):
        repo._recheck_preconditions(connection, PreconditionVector((old,)))


def test_concurrent_acknowledgements_share_one_directive_root(tmp_path: Path) -> None:
    path = tmp_path / "shared-directive.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(_directive(seed, "shared-directive", DirectiveClass.ACTION))
    events = (
        _ack(seed, "shared-directive", "ack-concurrent-a"),
        _ack(seed, "shared-directive", "ack-concurrent-b"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda item: M2DurableRepository(path).execute(
                    item, _context(), received_at=NOW
                ),
                events,
            )
        )

    assert {item.outcome for item in results} == {
        ReductionOutcome.APPLIED,
        ReductionOutcome.NOOP,
    }
    assert M2DurableRepository(path).get_directive_root(
        "shared-directive"
    ).directive_revision == 1
    assert M2DurableRepository(path).get_plan_day_root(
        seed.plan_day_id
    ).plan_day_revision == 1
    assert _count(path, "m2_effect_outbox") == 1


def _assert_uncommitted_processing_failure(
    path: Path,
    input_namespace: str,
    input_id: str,
) -> None:
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            """
            SELECT processing_status FROM m2_input_inbox
            WHERE input_namespace=? AND input_id=?
            """,
            (input_namespace, input_id),
        ).fetchone() == ("RECEIVED",)
    assert _count(path, "m2_effect_outbox") == 0


def test_b1_early_finish_cannot_promote_read_authority_plan_to_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b1-read-authority.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    signal = SystemSignal(
        "b1-early-finish",
        SystemSignalType.EARLY_FINISH_EVALUATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(signal, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        plan = scope.plan_day(seed.plan_day_id)
        changed = replace(
            plan,
            plan_day_revision=plan.plan_day_revision + 1,
            day_close_reported=True,
        )
        return Reduction(
            state=replace(scope, plan_day_roots=(changed,)),
            outcome=ReductionOutcome.APPLIED,
            root_deltas=(
                RootDelta(RootKind.PLAN_DAY, seed.plan_day_id, 0, 1, changed),
            ),
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="READ_AUTHORITY"):
        M2DurableRepository(path).process_input(
            SYSTEM_SIGNAL, signal.signal_id, completed_at=NOW
        )

    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, SYSTEM_SIGNAL, signal.signal_id)


def test_b2_completion_cannot_mutate_read_authority_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b2-completion-plan.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(
        seed,
        "b2-completion",
        FieldEventType.TASK_COMPLETION_REPORTED,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        plan = scope.plan_day(seed.plan_day_id)
        changed = replace(
            plan,
            plan_day_revision=plan.plan_day_revision + 1,
            day_close_reported=True,
        )
        return Reduction(
            state=replace(scope, plan_day_roots=(changed,)),
            outcome=ReductionOutcome.APPLIED,
            root_deltas=(
                RootDelta(RootKind.PLAN_DAY, seed.plan_day_id, 0, 1, changed),
            ),
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="READ_AUTHORITY"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    assert repo.get_job_execution_root(seed.job_id).job_execution_revision == 0
    _assert_uncommitted_processing_failure(
        path, FIELD_EVENT, event.event.event_id
    )


def test_b3_delta_for_foreign_same_kind_root_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b3-foreign-root.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b3-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        foreign = replace(
            scope.plan_day(seed.plan_day_id),
            plan_day_id="foreign-plan",
            plan_day_revision=1,
        )
        return Reduction(
            state=scope,
            outcome=ReductionOutcome.APPLIED,
            root_deltas=(
                RootDelta(RootKind.PLAN_DAY, "foreign-plan", 0, 1, foreign),
            ),
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="outside hydrated scope"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_b4_duplicate_root_delta_identity_fails_before_first_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b4-duplicate-delta.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b4-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)
    faults: list[str] = []

    def forged(scope, _explicit_input, _context):
        plan = scope.plan_day(seed.plan_day_id)
        changed = replace(
            plan,
            status=PlanDayStatus.ACTIVE,
            plan_day_revision=1,
        )
        delta = RootDelta(RootKind.PLAN_DAY, seed.plan_day_id, 0, 1, changed)
        return Reduction(
            state=replace(scope, plan_day_roots=(changed,)),
            outcome=ReductionOutcome.APPLIED,
            root_deltas=(delta, delta),
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="must be unique"):
        M2DurableRepository(path, fault_injector=faults.append).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert "after_first_root_update" not in faults
    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_b5_reduction_state_must_equal_delta_next_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b5-state-delta-mismatch.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b5-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        plan = scope.plan_day(seed.plan_day_id)
        state_root = replace(plan, status=PlanDayStatus.ACTIVE, plan_day_revision=1)
        delta_root = replace(state_root, day_close_reported=True)
        return Reduction(
            state=replace(scope, plan_day_roots=(state_root,)),
            outcome=ReductionOutcome.APPLIED,
            root_deltas=(
                RootDelta(RootKind.PLAN_DAY, seed.plan_day_id, 0, 1, delta_root),
            ),
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="differs from Reduction.state"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_b6_state_change_without_delta_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b6-state-without-delta.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b6-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        plan = scope.plan_day(seed.plan_day_id)
        changed = replace(plan, status=PlanDayStatus.ACTIVE, plan_day_revision=1)
        return Reduction(
            state=replace(scope, plan_day_roots=(changed,)),
            outcome=ReductionOutcome.APPLIED,
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="change and root delta disagree"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_b7_delta_without_state_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b7-delta-without-state.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b7-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        plan = scope.plan_day(seed.plan_day_id)
        changed = replace(plan, status=PlanDayStatus.ACTIVE, plan_day_revision=1)
        return Reduction(
            state=scope,
            outcome=ReductionOutcome.APPLIED,
            root_deltas=(
                RootDelta(RootKind.PLAN_DAY, seed.plan_day_id, 0, 1, changed),
            ),
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="change and root delta disagree"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_b8_reduction_state_cannot_add_canonical_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b8-add-root.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b8-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        injected = replace(
            scope.plan_day(seed.plan_day_id),
            plan_day_id="injected-plan",
        )
        return Reduction(
            state=replace(
                scope,
                plan_day_roots=(*scope.plan_day_roots, injected),
            ),
            outcome=ReductionOutcome.APPLIED,
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="added or removed"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert _count(path, "m2_plan_day_roots") == 1
    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_b9_reduction_state_cannot_remove_hydrated_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "b9-remove-root.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "b9-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)

    def forged(scope, _explicit_input, _context):
        return Reduction(
            state=replace(scope, plan_day_roots=()),
            outcome=ReductionOutcome.APPLIED,
        )

    monkeypatch.setattr("werkcrew_ai.field.repository.reduce", forged)
    with pytest.raises(M2StorageIntegrityError, match="added or removed"):
        M2DurableRepository(path).process_input(
            FIELD_EVENT, event.event.event_id, completed_at=NOW
        )

    assert _count(path, "m2_plan_day_roots") == 1
    assert repo.get_plan_day_root(seed.plan_day_id).plan_day_revision == 0
    _assert_uncommitted_processing_failure(path, FIELD_EVENT, event.event.event_id)


def test_structural_mutation_policy_preserves_current_legal_root_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mutation-policy-positive.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    repo.create_directive_root(
        _directive(seed, "policy-e1-directive", DirectiveClass.ACTION)
    )

    technical_wait = repo.execute(
        _task_event(
            seed,
            "policy-technical-wait",
            FieldEventType.TECHNICAL_WAIT_REPORTED,
            wait_condition="awaiting material",
        ),
        _context(),
        received_at=NOW,
    )
    day_close = repo.execute(
        _event(
            "policy-day-close",
            FieldEventType.DAY_CLOSE_REPORTED,
            plan_day_id=seed.plan_day_id,
        ),
        _context(),
        received_at=NOW,
    )
    e1 = repo.execute(
        SystemSignal(
            "policy-e1",
            SystemSignalType.DIRECTIVE_E1_ELAPSED,
            plan_day_id=seed.plan_day_id,
            directive_id="policy-e1-directive",
            task_id=seed.task_id,
        ),
        _context(),
        received_at=NOW,
    )

    assert tuple(item.root_kind for item in technical_wait.root_deltas) == (
        RootKind.JOB_EXECUTION,
    )
    assert tuple(item.root_kind for item in day_close.root_deltas) == (
        RootKind.PLAN_DAY,
    )
    assert tuple(item.root_kind for item in e1.root_deltas) == (
        RootKind.DIRECTIVE,
    )


def _replacement_fixture(path: Path):
    repo = _initialize(path)
    seed = _seed(path, repo)
    directive = _directive(seed, "replace-directive", DirectiveClass.ACTION)
    repo.create_directive_root(directive)

    registry = repo.get_worker_registry()
    job = repo.get_job_execution_root(seed.job_id)
    plan = repo.get_plan_day_root(seed.plan_day_id)
    task = job.task(seed.task_id)
    changed_registry = replace(
        registry,
        worker_ids=(*registry.worker_ids, "replacement-worker"),
    )
    changed_job = replace(
        job,
        tasks=(replace(task, status=TaskStatus.BLOCKED),),
    )
    changed_plan = replace(plan, status=PlanDayStatus.ACTIVE)
    changed_directive = replace(
        directive,
        delivery_evidence=DeliveryEvidence.CHANNEL_ACCEPTED,
    )
    cases = (
        (
            "worker_registry",
            "m2_worker_registry",
            ("registry_key",),
            {"canonical_root_json": serialize_worker_registry(changed_registry)},
            lambda: repo.get_worker_registry(),
            registry,
        ),
        (
            "job_execution",
            "m2_job_execution_roots",
            ("job_id",),
            {"canonical_root_json": serialize_job_execution_root(changed_job)},
            lambda: repo.get_job_execution_root(seed.job_id),
            job,
        ),
        (
            "plan_day",
            "m2_plan_day_roots",
            ("plan_day_id",),
            {
                "status": changed_plan.status.value,
                "canonical_root_json": serialize_plan_day_root(changed_plan),
            },
            lambda: repo.get_plan_day_root(seed.plan_day_id),
            plan,
        ),
        (
            "directive",
            "m2_directive_roots",
            ("directive_id",),
            {
                "delivery_evidence": changed_directive.delivery_evidence.value,
                "canonical_root_json": serialize_directive_root(changed_directive),
            },
            lambda: repo.get_directive_root(directive.directive_id),
            directive,
        ),
    )
    return repo, seed, cases


def _replacement_statement(
    table: str,
    columns: tuple[str, ...],
    primary_key: tuple[str, ...],
    mode: str,
) -> str:
    placeholders = ",".join("?" for _ in columns)
    column_list = ",".join(columns)
    if mode == "INSERT OR REPLACE":
        return f"INSERT OR REPLACE INTO {table}({column_list}) VALUES({placeholders})"
    if mode == "REPLACE":
        return f"REPLACE INTO {table}({column_list}) VALUES({placeholders})"
    if mode == "INSERT OR IGNORE":
        return f"INSERT OR IGNORE INTO {table}({column_list}) VALUES({placeholders})"
    assignments = ",".join(
        f"{column}=excluded.{column}"
        for column in columns
        if column not in primary_key
    )
    return (
        f"INSERT INTO {table}({column_list}) VALUES({placeholders}) "
        f"ON CONFLICT({','.join(primary_key)}) DO UPDATE SET {assignments}"
    )


@pytest.mark.parametrize("recursive_triggers", (False, True))
@pytest.mark.parametrize(
    "mode",
    ("INSERT OR REPLACE", "REPLACE", "INSERT OR IGNORE", "UPSERT"),
)
def test_rpl1_rpl5_rpl10_all_root_insert_like_replacements_are_blocked(
    tmp_path: Path,
    recursive_triggers: bool,
    mode: str,
) -> None:
    repo, _seed_value, cases = _replacement_fixture(
        tmp_path / f"root-replace-{int(recursive_triggers)}-{mode.replace(' ', '-')}.db"
    )
    connection = repo._connect()
    try:
        connection.execute(
            f"PRAGMA recursive_triggers={int(recursive_triggers)}"
        )
        for _name, table, primary_key, overrides, loader, original in cases:
            stored = dict(connection.execute(f"SELECT * FROM {table}").fetchone())
            stored.update(overrides)
            stored["content_sha256"] = hashlib.sha256(
                stored["canonical_root_json"].encode("utf-8")
            ).hexdigest()
            columns = tuple(stored)
            with pytest.raises(sqlite3.IntegrityError, match="already exists"):
                connection.execute(
                    _replacement_statement(table, columns, primary_key, mode),
                    tuple(stored[column] for column in columns),
                )
            connection.rollback()
            assert loader() == original
    finally:
        connection.close()


def test_unavailable_tx2_persists_exhaustive_v2_historical_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unavailable-proof-v2.db"
    repository = _initialize(path)
    seed = _seed(path, repository)
    repository.execute(
        _event(
            "activate-before-v2",
            FieldEventType.DAY_PLAN_ACTIVATED,
            plan_day_id=seed.plan_day_id,
        ),
        _context(),
        received_at=NOW,
    )
    unavailable = _event(
        "unavailable-proof-v2",
        FieldEventType.UNAVAILABLE_TODAY_REPORTED,
        plan_day_id=seed.plan_day_id,
        reason_class="SICK",
    )
    repository.execute(unavailable, _context(), received_at=NOW)

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT reduction_input_proof_json, reduction_input_proof_sha256 "
            "FROM m2_input_inbox WHERE input_id=?",
            (unavailable.event.event_id,),
        ).fetchone()
    proof = verify_canonical_document(row[0], row[1])
    assert proof["schema_version"] == "m2-reduction-input-proof-v2"
    assert proof["payload"]["complete_plan_day_assignment_scope_ids"] == [
        seed.plan_day_id
    ]
    assert [
        item["payload"]["job_id"]
        for item in proof["payload"]["job_execution_roots"]
    ] == [seed.job_id]
    replay = M2DurableRepository(path).execute(
        unavailable,
        _context(now=NOW + timedelta(hours=1)),
        received_at=NOW + timedelta(hours=1),
    )
    assert replay.replayed is True


@pytest.mark.parametrize(
    ("case_name", "event_values", "expected_reason"),
    (
        ("missing-job", {"job_id": "job-later"}, "CROSS_JOB_REFERENCE"),
        ("missing-task", {"task_id": "task-later"}, "UNKNOWN_TASK"),
        (
            "missing-assignment",
            {"task_id": "task-later", "assignment_id": "assignment-later"},
            "UNKNOWN_ASSIGNMENT",
        ),
    ),
)
def test_completed_missing_reference_rejection_survives_later_materialization(
    tmp_path: Path,
    case_name: str,
    event_values: dict[str, str],
    expected_reason: str,
) -> None:
    path = tmp_path / f"temporal-rejection-{case_name}.db"
    repo = _initialize(path)
    _registry(repo)
    repo.create_plan_day_root(_plan("plan-later"))
    attachment = EvidenceItem(
        f"evidence-{case_name}", EvidenceKind.PHOTO, f"blob:{case_name}"
    )
    event = _event(
        f"event-{case_name}",
        FieldEventType.SITE_PROBLEM_REPORTED,
        plan_day_id="plan-later",
        attachments=(attachment,),
        **event_values,
    )
    first = repo.execute(event, _context(), received_at=NOW)
    assert first.outcome is ReductionOutcome.REJECTED
    assert expected_reason in first.reason_codes
    with repo._connect() as connection:
        stored_context = connection.execute(
            "SELECT policy_context_json FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()[0]

    publication = _publication(path, "job-later")
    repo.create_job_execution_root(
        M2JobExecutionRoot(
            "job-later",
            (_task(publication, "task-later"),),
            (
                _assignment(
                    "job-later",
                    "task-later",
                    "assignment-later",
                    "plan-later",
                ),
            ),
        )
    )
    del repo

    fresh = M2DurableRepository(path)
    replay = fresh.execute(
        event,
        _context(now=NOW + timedelta(days=1)),
        received_at=NOW + timedelta(days=1),
    )
    assert replay.outcome is ReductionOutcome.REJECTED
    assert replay.reason_codes == first.reason_codes
    assert replay.replayed is True
    assert fresh.get_job_execution_root("job-later").job_execution_revision == 0
    with fresh._connect() as connection:
        row = connection.execute(
            "SELECT policy_context_json FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()
        assert row[0] == stored_context
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_identities WHERE evidence_id=?",
            (attachment.evidence_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_usages WHERE evidence_id=?",
            (attachment.evidence_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM m2_effect_outbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()[0] == 0


def test_unsupported_rule_version_is_completed_and_replayed_with_receipt(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unsupported-rule.db"
    repo = _initialize(path)
    _registry(repo)
    repo.create_plan_day_root(_plan())
    event = _event(
        "unsupported-rule-event",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id="plan-day-one",
    )
    unsupported = replace(_context(), rule_version="m2-unsupported-rule")
    first = repo.execute(event, unsupported, received_at=NOW)
    assert first.outcome is ReductionOutcome.REJECTED
    assert first.reason_codes == ("UNSUPPORTED_RULE_VERSION",)
    assert first.appended_receipt is not None
    assert repo.get_plan_day_root("plan-day-one").plan_day_revision == 0
    with repo._connect() as connection:
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()[0] == "COMPLETED"
        assert connection.execute(
            "SELECT count(*) FROM m2_effect_outbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()[0] == 0
    del repo

    replay = M2DurableRepository(path).execute(
        event,
        _context(now=NOW + timedelta(days=1)),
        received_at=NOW + timedelta(days=1),
    )
    assert replay.outcome is ReductionOutcome.REJECTED
    assert replay.reason_codes == first.reason_codes
    assert replay.replayed is True
    assert M2DurableRepository(path).get_plan_day_root(
        "plan-day-one"
    ).plan_day_revision == 0


def _rewrite_completed_row(
    repository: M2DurableRepository,
    input_id: str,
    assignments: dict[str, object],
) -> None:
    with repository._connect() as connection:
        connection.execute("DROP TRIGGER m2_input_inbox_immutable")
        columns = ", ".join(f"{name}=?" for name in assignments)
        connection.execute(
            f"UPDATE m2_input_inbox SET {columns} "
            "WHERE input_namespace='FIELD_EVENT' AND input_id=?",
            (*assignments.values(), input_id),
        )
        connection.commit()


def test_completed_replay_rejects_missing_publication_routing_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "semantic-proof-publication.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(
        seed,
        "semantic-proof-publication",
        FieldEventType.WORK_START_BLOCKED,
    )
    repo.execute(event, _context(), received_at=NOW)
    with repo._connect() as connection:
        raw = connection.execute(
            "SELECT routing_json FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()[0]
    routing = deserialize_routing_vector(raw)
    forged = serialize_routing_vector(replace(routing, publication_keys=()))
    _rewrite_completed_row(
        repo,
        event.event.event_id,
        {
            "routing_json": forged,
            "routing_sha256": hashlib.sha256(forged.encode()).hexdigest(),
        },
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_completed_replay_rejects_omitted_immutable_preconditions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "semantic-proof-preconditions.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(
        seed,
        "semantic-proof-preconditions",
        FieldEventType.WORK_START_BLOCKED,
    )
    repo.execute(event, _context(), received_at=NOW)
    with repo._connect() as connection:
        raw = connection.execute(
            "SELECT precondition_vector_json FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone()[0]
    preconditions = deserialize_precondition_vector(raw)
    forged = serialize_precondition_vector(
        PreconditionVector(preconditions.roots, ())
    )
    _rewrite_completed_row(
        repo,
        event.event.event_id,
        {
            "precondition_vector_json": forged,
            "precondition_vector_sha256": hashlib.sha256(forged.encode()).hexdigest(),
        },
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_completed_replay_rejects_omitted_effect_and_outbox(
    tmp_path: Path,
) -> None:
    path = tmp_path / "semantic-proof-effects.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(
        seed,
        "semantic-proof-effects",
        FieldEventType.WORK_START_BLOCKED,
        reason_class="NO_ACCESS",
    )
    first = repo.execute(event, _context(), received_at=NOW)
    assert first.outcome is ReductionOutcome.APPLIED
    assert first.emitted_effects
    empty = serialize_effects(())
    with repo._connect() as connection:
        connection.execute("DROP TRIGGER m2_effect_outbox_no_delete")
        connection.execute(
            "DELETE FROM m2_effect_outbox WHERE input_id=?",
            (event.event.event_id,),
        )
        connection.commit()
    _rewrite_completed_row(
        repo,
        event.event.event_id,
        {"emitted_effects_json": empty},
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_completed_replay_rejects_applied_operation_rewritten_as_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "semantic-proof-outcome.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "semantic-proof-outcome",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    result = repo.execute(event, _context(), received_at=NOW)
    assert result.outcome is ReductionOutcome.APPLIED
    assert result.appended_receipt is not None
    forged_receipt = replace(
        result.appended_receipt,
        outcome=ReductionOutcome.REJECTED,
        response_effects=(),
        reason_codes=("FORGED_REJECTION",),
    )
    receipt_raw = serialize_processed_event_receipt(forged_receipt)
    empty_effects = serialize_effects(())
    empty_result = serialize_resulting_revision_vector(ResultingRevisionVector())
    reasons = serialize_string_tuple(("FORGED_REJECTION",), "ReasonCodes")
    with repo._connect() as connection:
        connection.execute("DROP TRIGGER m2_effect_outbox_no_delete")
        connection.execute(
            "DELETE FROM m2_effect_outbox WHERE input_id=?",
            (event.event.event_id,),
        )
        connection.commit()
    _rewrite_completed_row(
        repo,
        event.event.event_id,
        {
            "outcome": ReductionOutcome.REJECTED.value,
            "reason_codes_json": reasons,
            "response_effects_json": empty_effects,
            "emitted_effects_json": empty_effects,
            "resulting_revision_vector_json": empty_result,
            "resulting_revision_vector_sha256": hashlib.sha256(
                empty_result.encode()
            ).hexdigest(),
            "appended_receipt_json": receipt_raw,
            "appended_receipt_sha256": hashlib.sha256(
                receipt_raw.encode()
            ).hexdigest(),
        },
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_completion_rejected_is_contractual_durable_effect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "completion-rejected-effect.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _task_event(
        seed,
        "completion-rejected-effect",
        FieldEventType.TASK_COMPLETION_REPORTED,
        actor_id=OTHER_WORKER,
    )
    result = repo.execute(event, _context(), received_at=NOW)
    assert result.outcome is ReductionOutcome.REJECTED
    assert tuple(item.effect_type.value for item in result.emitted_effects) == (
        "COMPLETION_REJECTED",
    )
    assert repo.get_job_execution_root(seed.job_id).job_execution_revision == 0
    with repo._connect() as connection:
        assert [item[0] for item in connection.execute(
            "SELECT effect_type FROM m2_effect_outbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchall()] == ["COMPLETION_REJECTED"]


def test_unrelated_malformed_history_cannot_validate_or_poison_target_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unrelated-history.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    target = _event(
        "history-target",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    assert repo.execute(target, _context(), received_at=NOW).outcome is (
        ReductionOutcome.APPLIED
    )
    assert repo.execute(
        _event(
            "history-successor",
            FieldEventType.DAY_CLOSE_REPORTED,
            plan_day_id=seed.plan_day_id,
        ),
        _context(),
        received_at=NOW,
    ).outcome is ReductionOutcome.APPLIED
    unrelated = SystemSignal(
        "history-unrelated",
        SystemSignalType.STATE_REHYDRATED,
        task_id="unknown-unrelated-task",
    )
    repo.execute(unrelated, _context(), received_at=NOW)
    malformed = canonical_json(
        {
            "document_type": "UnrelatedMalformedDocument",
            "payload": {"roots": []},
            "schema_version": "unrelated-v1",
        }
    )
    with repo._connect() as connection:
        connection.execute("DROP TRIGGER m2_input_inbox_immutable")
        connection.execute(
            "UPDATE m2_input_inbox SET resulting_revision_vector_json=?, "
            "resulting_revision_vector_sha256=? WHERE input_namespace='SYSTEM_SIGNAL' "
            "AND input_id=?",
            (
                malformed,
                hashlib.sha256(malformed.encode()).hexdigest(),
                unrelated.signal_id,
            ),
        )
        connection.commit()

    replay = M2DurableRepository(path).execute(
        target, _context(), received_at=NOW
    )
    assert replay.outcome is ReductionOutcome.APPLIED
    assert replay.replayed is True
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(
            unrelated, _context(), received_at=NOW
        )


def _execute_site_evidence(
    repository: M2DurableRepository,
    seed: Seed,
    event_id: str,
    evidence: EvidenceItem,
) -> FieldEventInput:
    event = _task_event(
        seed,
        event_id,
        FieldEventType.SITE_PROBLEM_REPORTED,
        attachments=(evidence,),
    )
    assert repository.execute(event, _context(), received_at=NOW).outcome is (
        ReductionOutcome.APPLIED
    )
    return event


def _completed_preconditions(
    repository: M2DurableRepository, input_id: str
) -> PreconditionVector:
    with repository._connect() as connection:
        raw = connection.execute(
            "SELECT precondition_vector_json FROM m2_input_inbox WHERE input_id=?",
            (input_id,),
        ).fetchone()[0]
    return deserialize_precondition_vector(raw)


def test_evidence_replay_rejects_removed_required_input_precondition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence-missing-precondition.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    evidence = EvidenceItem("evidence-shared", EvidenceKind.PHOTO, "blob:shared")
    _execute_site_evidence(repo, seed, "evidence-owner", evidence)
    target = _execute_site_evidence(repo, seed, "evidence-reuse", evidence)
    stored = _completed_preconditions(repo, target.event.event_id)
    assert any(item.record_kind == "EVIDENCE" for item in stored.immutable_records)
    forged = serialize_precondition_vector(
        PreconditionVector(
            stored.roots,
            tuple(
                item
                for item in stored.immutable_records
                if item.record_kind != "EVIDENCE"
            ),
        )
    )
    _rewrite_completed_row(
        repo,
        target.event.event_id,
        {
            "precondition_vector_json": forged,
            "precondition_vector_sha256": hashlib.sha256(forged.encode()).hexdigest(),
        },
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(target, _context(), received_at=NOW)


def test_evidence_replay_rejects_extra_input_precondition(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence-extra-precondition.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    extra = EvidenceItem("evidence-extra", EvidenceKind.PHOTO, "blob:extra")
    _execute_site_evidence(repo, seed, "evidence-extra-owner", extra)
    target = _execute_site_evidence(
        repo,
        seed,
        "evidence-extra-target",
        EvidenceItem("evidence-target", EvidenceKind.PHOTO, "blob:target"),
    )
    stored = _completed_preconditions(repo, target.event.event_id)
    with repo._connect() as connection:
        digest = connection.execute(
            "SELECT content_sha256 FROM m2_evidence_identities WHERE evidence_id=?",
            (extra.evidence_id,),
        ).fetchone()[0]
    forged = serialize_precondition_vector(
        PreconditionVector(
            stored.roots,
            (*stored.immutable_records, ImmutablePrecondition("EVIDENCE", extra.evidence_id, digest)),
        )
    )
    _rewrite_completed_row(
        repo,
        target.event.event_id,
        {
            "precondition_vector_json": forged,
            "precondition_vector_sha256": hashlib.sha256(forged.encode()).hexdigest(),
        },
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(target, _context(), received_at=NOW)


def test_evidence_replay_requires_result_created_identity(tmp_path: Path) -> None:
    path = tmp_path / "evidence-deleted-identity.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    evidence = EvidenceItem("evidence-delete-id", EvidenceKind.PHOTO, "blob:id")
    event = _execute_site_evidence(repo, seed, "evidence-delete-id-event", evidence)
    with repo._connect() as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TRIGGER m2_evidence_identities_no_delete")
        connection.execute(
            "DELETE FROM m2_evidence_identities WHERE evidence_id=?",
            (evidence.evidence_id,),
        )
        connection.commit()
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_evidence_replay_requires_result_created_usage(tmp_path: Path) -> None:
    path = tmp_path / "evidence-deleted-usage.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    evidence = EvidenceItem("evidence-delete-use", EvidenceKind.PHOTO, "blob:use")
    event = _execute_site_evidence(repo, seed, "evidence-delete-use-event", evidence)
    with repo._connect() as connection:
        connection.execute("DROP TRIGGER m2_evidence_usages_no_delete")
        connection.execute(
            "DELETE FROM m2_evidence_usages WHERE input_id=? AND evidence_id=?",
            (event.event.event_id, evidence.evidence_id),
        )
        connection.commit()
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_evidence_replay_rejects_changed_canonical_owner_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence-owner-corrupt.db"
    repo = _initialize(path)
    seed_a = _seed(path, repo, "evidence-owner-a", plan_day_id="evidence-plan")
    publication_b = _publication(path, "job-evidence-owner-b")
    task_b = _task(publication_b, "task-evidence-owner-b")
    assignment_b = _assignment(
        "job-evidence-owner-b",
        "task-evidence-owner-b",
        "assignment-evidence-owner-b",
        "evidence-plan",
    )
    repo.create_job_execution_root(
        M2JobExecutionRoot("job-evidence-owner-b", (task_b,), (assignment_b,))
    )
    evidence = EvidenceItem("evidence-owner", EvidenceKind.PHOTO, "blob:owner")
    event = _execute_site_evidence(repo, seed_a, "evidence-owner-event", evidence)
    with repo._connect() as connection:
        row = connection.execute(
            "SELECT canonical_evidence_json FROM m2_evidence_identities "
            "WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()
        document = json.loads(row[0])
        document["owner_scope"] = {
            "assignment_id": assignment_b.assignment_id,
            "job_id": assignment_b.job_id,
            "stage_id": None,
            "task_id": assignment_b.task_id,
        }
        forged = canonical_json(document)
        connection.execute("DROP TRIGGER m2_evidence_identities_no_update")
        connection.execute(
            """
            UPDATE m2_evidence_identities
            SET owner_job_id=?, owner_task_id=?, owner_assignment_id=?,
                canonical_evidence_json=?, content_sha256=?
            WHERE evidence_id=?
            """,
            (
                assignment_b.job_id,
                assignment_b.task_id,
                assignment_b.assignment_id,
                forged,
                hashlib.sha256(forged.encode()).hexdigest(),
                evidence.evidence_id,
            ),
        )
        connection.commit()
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(event, _context(), received_at=NOW)


def test_deleted_evidence_authority_cannot_be_rebound_by_later_job(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence-no-rebind.db"
    repo = _initialize(path)
    seed_a = _seed(path, repo, "evidence-rebind-a", plan_day_id="evidence-plan-a")
    repo.create_plan_day_root(_plan("evidence-plan-b"))
    publication_b = _publication(path, "job-evidence-rebind-b")
    task_b = _task(publication_b, "task-evidence-rebind-b")
    assignment_b = _assignment(
        "job-evidence-rebind-b",
        "task-evidence-rebind-b",
        "assignment-evidence-rebind-b",
        "evidence-plan-b",
    )
    repo.create_job_execution_root(
        M2JobExecutionRoot("job-evidence-rebind-b", (task_b,), (assignment_b,))
    )
    seed_b = Seed(
        assignment_b.job_id,
        assignment_b.task_id,
        assignment_b.assignment_id,
        "evidence-plan-b",
        publication_b,
    )
    evidence = EvidenceItem("evidence-global-owner", EvidenceKind.PHOTO, "blob:global")
    owner = _execute_site_evidence(repo, seed_a, "evidence-global-first", evidence)
    with repo._connect() as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TRIGGER m2_evidence_usages_no_delete")
        connection.execute("DROP TRIGGER m2_evidence_identities_no_delete")
        connection.execute(
            "DELETE FROM m2_evidence_usages WHERE input_id=?",
            (owner.event.event_id,),
        )
        connection.execute(
            "DELETE FROM m2_evidence_identities WHERE evidence_id=?",
            (evidence.evidence_id,),
        )
        connection.commit()
    attempted = _task_event(
        seed_b,
        "evidence-global-rebind",
        FieldEventType.SITE_PROBLEM_REPORTED,
        attachments=(evidence,),
    )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).execute(attempted, _context(), received_at=NOW)
    with repo._connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_identities WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_usages WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id=?",
            (attempted.event.event_id,),
        ).fetchone()[0] == "RECEIVED"


def test_valid_evidence_replay_survives_later_job_root_revision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence-valid-history.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    first_evidence = EvidenceItem("evidence-history-a", EvidenceKind.PHOTO, "blob:a")
    first = _execute_site_evidence(repo, seed, "evidence-history-first", first_evidence)
    immediate = M2DurableRepository(path).execute(first, _context(), received_at=NOW)
    assert immediate.outcome is ReductionOutcome.APPLIED and immediate.replayed
    _execute_site_evidence(
        repo,
        seed,
        "evidence-history-later",
        EvidenceItem("evidence-history-b", EvidenceKind.PHOTO, "blob:b"),
    )
    assert repo.get_job_execution_root(seed.job_id).job_execution_revision == 2
    historical = M2DurableRepository(path).execute(first, _context(), received_at=NOW)
    assert historical.outcome is ReductionOutcome.APPLIED
    assert historical.replayed is True


def test_legitimate_evidence_reuse_within_exact_scope_remains_valid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence-valid-reuse.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    evidence = EvidenceItem("evidence-reuse-valid", EvidenceKind.PHOTO, "blob:reuse")
    _execute_site_evidence(repo, seed, "evidence-reuse-first", evidence)
    second = _execute_site_evidence(repo, seed, "evidence-reuse-second", evidence)
    replay = M2DurableRepository(path).execute(second, _context(), received_at=NOW)
    assert replay.outcome is ReductionOutcome.APPLIED and replay.replayed
    with repo._connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_identities WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_usages WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()[0] == 2


def test_r01_rejected_unknown_scope_attachment_completes_without_evidence_projection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r01-unknown-scope.db"
    repo = _initialize(path)
    _registry(repo)
    attachment = EvidenceItem("evidence-unresolved", EvidenceKind.PHOTO, "blob:raw")
    event = _event(
        "unresolved-attachment",
        FieldEventType.SITE_PROBLEM_REPORTED,
        job_id="unknown-job",
        task_id="unknown-task",
        assignment_id="unknown-assignment",
        attachments=(attachment,),
    )

    first = repo.execute(event, _context(), received_at=NOW)

    assert first.outcome is ReductionOutcome.REJECTED
    assert first.appended_receipt is not None
    assert first.appended_receipt.event.attachments == (attachment,)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT processing_status, appended_receipt_json FROM m2_input_inbox "
            "WHERE input_namespace='FIELD_EVENT' AND input_id=?",
            (event.event.event_id,),
        ).fetchone()
        assert row is not None and row[0] == "COMPLETED"
        assert attachment.content_reference in row[1]
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_identities"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_usages"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM m2_effect_outbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone() == (0,)

    replay = M2DurableRepository(path).execute(
        event,
        _context(now=NOW + timedelta(hours=1)),
        received_at=NOW + timedelta(hours=1),
    )
    assert replay.outcome is ReductionOutcome.REJECTED
    assert replay.replayed is True
    assert replay.reason_codes == first.reason_codes
    assert M2DurableRepository(path).get_worker_registry().registry_revision == 0


def test_r01_rejected_cross_scope_attachment_creates_no_evidence_projection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r01-cross-scope.db"
    repo = _initialize(path)
    seed_a = _seed(path, repo, "r01-a", plan_day_id="r01-shared-plan")
    seed_b = _seed(path, repo, "r01-b", plan_day_id="r01-shared-plan")
    attachment = EvidenceItem("evidence-cross-scope", EvidenceKind.PHOTO, "blob:cross")
    event = _event(
        "cross-scope-attachment",
        FieldEventType.SITE_PROBLEM_REPORTED,
        job_id=seed_a.job_id,
        task_id=seed_a.task_id,
        assignment_id=seed_b.assignment_id,
        plan_day_id=seed_a.plan_day_id,
        attachments=(attachment,),
    )

    result = repo.execute(event, _context(), received_at=NOW)

    assert result.outcome is ReductionOutcome.REJECTED
    assert result.appended_receipt is not None
    assert result.appended_receipt.event.attachments == (attachment,)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone() == ("COMPLETED",)
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_identities WHERE evidence_id=?",
            (attachment.evidence_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM m2_evidence_usages WHERE evidence_id=?",
            (attachment.evidence_id,),
        ).fetchone() == (0,)
    assert repo.get_job_execution_root(seed_a.job_id).job_execution_revision == 0
    assert repo.get_job_execution_root(seed_b.job_id).job_execution_revision == 0
    replay = M2DurableRepository(path).execute(
        event,
        _context(now=NOW + timedelta(hours=1)),
        received_at=NOW + timedelta(hours=1),
    )
    assert replay.outcome is ReductionOutcome.REJECTED
    assert replay.replayed is True
    assert replay.reason_codes == result.reason_codes


def test_r02_completed_applied_without_root_mutation_fails_closed_on_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r02-omitted-root.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "r02-activate",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)
    with repo.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' "
            "AND input_id=?",
            (event.event.event_id,),
        ).fetchone()
        hydration = repo._hydrate(connection, event)
        reduction = reduce(hydration.scope, event, _context())
        assert reduction.outcome is ReductionOutcome.APPLIED
        assert reduction.root_deltas
        repo._insert_outbox(
            connection,
            FIELD_EVENT,
            event.event.event_id,
            reduction.emitted_effects,
            NOW,
        )
        # Deliberately persist the real reducer receipt/effects but omit its root write.
        repo._complete_inbox(
            connection,
            row,
            reduction,
            hydration.scope,
            hydration.routing,
            hydration.preconditions,
            ResultingRevisionVector(),
            NOW,
        )

    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).process_input(
            FIELD_EVENT,
            event.event.event_id,
            completed_at=NOW + timedelta(minutes=1),
        )
    assert M2DurableRepository(path).get_plan_day_root(
        seed.plan_day_id
    ).plan_day_revision == 0


def test_r02_completed_field_event_without_receipt_is_blocked_by_schema(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r02-missing-receipt.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "r02-no-receipt",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        with repo.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' "
                "AND input_id=?",
                (event.event.event_id,),
            ).fetchone()
            hydration = repo._hydrate(connection, event)
            reduction = reduce(hydration.scope, event, _context())
            repo._complete_inbox(
                connection,
                row,
                replace(reduction, appended_receipt=None),
                hydration.scope,
                hydration.routing,
                hydration.preconditions,
                ResultingRevisionVector(),
                NOW,
            )
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT processing_status FROM m2_input_inbox WHERE input_id=?",
            (event.event.event_id,),
        ).fetchone() == ("RECEIVED",)


def test_r02_result_vector_cannot_claim_an_unwritten_root_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r02-unwritten-result.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "r02-unwritten-result",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.accept_input(event, _context(), received_at=NOW)
    with repo.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' "
            "AND input_id=?",
            (event.event.event_id,),
        ).fetchone()
        hydration = repo._hydrate(connection, event)
        reduction = reduce(hydration.scope, event, _context())
        delta = reduction.root_deltas[0]
        next_raw = serialize_plan_day_root(delta.next_root)
        claimed_result = ResultingRevisionVector(
            (
                ResultingRevision(
                    delta.root_kind.value,
                    delta.root_id,
                    delta.resulting_revision,
                    hashlib.sha256(next_raw.encode("utf-8")).hexdigest(),
                ),
            )
        )
        repo._insert_outbox(
            connection,
            FIELD_EVENT,
            event.event.event_id,
            reduction.emitted_effects,
            NOW,
        )
        repo._complete_inbox(
            connection,
            row,
            reduction,
            hydration.scope,
            hydration.routing,
            hydration.preconditions,
            claimed_result,
            NOW,
        )

    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).process_input(
            FIELD_EVENT,
            event.event.event_id,
            completed_at=NOW + timedelta(minutes=1),
        )
    assert M2DurableRepository(path).get_plan_day_root(
        seed.plan_day_id
    ).plan_day_revision == 0


def test_r02_partial_stop_exception_root_set_fails_closed_on_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r02-partial-stop.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    directive = _directive(seed, "r02-stop", DirectiveClass.STOP)
    repo.create_directive_root(directive)
    event = _task_event(
        seed,
        "r02-stop-exception",
        FieldEventType.WORKER_ACTION_EXCEPTION,
        directive_id=directive.directive_id,
        reason_class=ActionExceptionReason.VEHICLE_UNSAFE.value,
    )
    repo.accept_input(event, _context(), received_at=NOW)
    with repo.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' "
            "AND input_id=?",
            (event.event.event_id,),
        ).fetchone()
        hydration = repo._hydrate(connection, event)
        reduction = reduce(hydration.scope, event, _context())
        assert {item.root_kind for item in reduction.root_deltas} == {
            RootKind.DIRECTIVE,
            RootKind.JOB_EXECUTION,
        }
        directive_delta = next(
            item
            for item in reduction.root_deltas
            if item.root_kind is RootKind.DIRECTIVE
        )
        partial_result = repo._apply_deltas(connection, (directive_delta,))
        repo._insert_outbox(
            connection,
            FIELD_EVENT,
            event.event.event_id,
            reduction.emitted_effects,
            NOW,
        )
        repo._complete_inbox(
            connection,
            row,
            reduction,
            hydration.scope,
            hydration.routing,
            hydration.preconditions,
            partial_result,
            NOW,
        )

    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).process_input(
            FIELD_EVENT,
            event.event.event_id,
            completed_at=NOW + timedelta(minutes=1),
        )
    assert repo.get_directive_root(directive.directive_id).directive_revision == 1
    assert repo.get_job_execution_root(seed.job_id).job_execution_revision == 0


def test_r02_corrupt_resulting_revision_vector_fails_closed_on_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r02-corrupt-vector.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "r02-vector",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.execute(event, _context(), received_at=NOW)
    plan = repo.get_plan_day_root(seed.plan_day_id)
    forged = serialize_resulting_revision_vector(
        ResultingRevisionVector(
            (
                ResultingRevision(
                    RootKind.PLAN_DAY.value,
                    seed.plan_day_id,
                    plan.plan_day_revision + 1,
                    "0" * 64,
                ),
            )
        )
    )
    with repo._connect() as connection:
        connection.execute("DROP TRIGGER m2_input_inbox_immutable")
        connection.execute(
            "UPDATE m2_input_inbox SET resulting_revision_vector_json=?, "
            "resulting_revision_vector_sha256=werkcrew_sha256(?) WHERE input_id=?",
            (forged, forged, event.event.event_id),
        )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).process_input(
            FIELD_EVENT,
            event.event.event_id,
            completed_at=NOW + timedelta(minutes=1),
        )


def test_r02_outbox_mismatch_fails_closed_on_replay(tmp_path: Path) -> None:
    path = tmp_path / "r02-outbox.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    event = _event(
        "r02-outbox",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    first = repo.execute(event, _context(), received_at=NOW)
    assert first.emitted_effects
    with repo._connect() as connection:
        connection.execute("DROP TRIGGER m2_effect_outbox_no_delete")
        connection.execute(
            "DELETE FROM m2_effect_outbox WHERE input_namespace='FIELD_EVENT' "
            "AND input_id=? AND effect_ordinal=0",
            (event.event.event_id,),
        )
    with pytest.raises(M2StorageIntegrityError):
        M2DurableRepository(path).process_input(
            FIELD_EVENT,
            event.event.event_id,
            completed_at=NOW + timedelta(minutes=1),
        )


def test_r02_valid_completed_results_remain_replayable(tmp_path: Path) -> None:
    path = tmp_path / "r02-valid-replays.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    applied = _event(
        "r02-valid-applied",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    rejected = _task_event(
        seed,
        "r02-valid-rejected",
        FieldEventType.WORK_START_BLOCKED,
    )
    noop = SystemSignal(
        "r02-valid-noop",
        SystemSignalType.STATE_REHYDRATED,
        plan_day_id=seed.plan_day_id,
    )
    expected = (
        repo.execute(applied, _context(), received_at=NOW),
        repo.execute(rejected, _context(), received_at=NOW),
        repo.execute(noop, _context(), received_at=NOW),
    )
    # Advance the same canonical plan once more: replay of the earlier APPLIED
    # result must prove its exact hash through the contiguous durable transition.
    repo.execute(
        _event(
            "r02-later-plan-change",
            FieldEventType.DAY_CLOSE_REPORTED,
            plan_day_id=seed.plan_day_id,
        ),
        _context(),
        received_at=NOW,
    )
    replayed = (
        M2DurableRepository(path).execute(applied, _context(), received_at=NOW),
        M2DurableRepository(path).execute(rejected, _context(), received_at=NOW),
        M2DurableRepository(path).execute(noop, _context(), received_at=NOW),
    )
    assert tuple(item.outcome for item in expected) == (
        ReductionOutcome.APPLIED,
        ReductionOutcome.REJECTED,
        ReductionOutcome.NOOP,
    )
    assert all(item.replayed for item in replayed)
    assert tuple(item.outcome for item in replayed) == tuple(
        item.outcome for item in expected
    )


def test_r02_applied_idempotent_state_replacement_remains_replayable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "r02-applied-without-delta.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    first = _event(
        "r02-first-day-close",
        FieldEventType.DAY_CLOSE_REPORTED,
        plan_day_id=seed.plan_day_id,
    )
    repeated = _event(
        "r02-repeated-day-close",
        FieldEventType.DAY_CLOSE_REPORTED,
        plan_day_id=seed.plan_day_id,
    )
    assert repo.execute(first, _context(), received_at=NOW).root_deltas
    repeated_result = repo.execute(repeated, _context(), received_at=NOW)
    assert repeated_result.outcome is ReductionOutcome.APPLIED
    assert repeated_result.root_deltas == ()

    replay = M2DurableRepository(path).execute(
        repeated,
        _context(now=NOW + timedelta(hours=1)),
        received_at=NOW + timedelta(hours=1),
    )
    assert replay.outcome is ReductionOutcome.APPLIED
    assert replay.replayed is True


def test_rpl6_root_replace_is_blocked_even_with_revision_plus_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replace-plus-one.db"
    repo = _initialize(path)
    _registry(repo)
    original = _plan("replace-plus-one")
    repo.create_plan_day_root(original)
    changed = replace(
        original,
        status=PlanDayStatus.ACTIVE,
        plan_day_revision=1,
    )
    raw = serialize_plan_day_root(changed)
    connection = repo._connect()
    try:
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError, match="identity already exists"):
            connection.execute(
                """
                INSERT OR REPLACE INTO m2_plan_day_roots(
                    plan_day_id, root_schema_version, worker_id, business_date,
                    status, plan_day_revision, canonical_root_json, content_sha256
                ) VALUES(?, 'm2-plan-day-root-v1', ?, ?, ?, ?, ?, ?)
                """,
                (
                    changed.plan_day_id,
                    changed.worker_id,
                    changed.business_date.isoformat(),
                    changed.status.value,
                    changed.plan_day_revision,
                    raw,
                    hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                ),
            )
        connection.rollback()
    finally:
        connection.close()
    assert repo.get_plan_day_root(original.plan_day_id) == original


def test_rpl7_legal_root_update_plus_one_still_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "legal-update.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    result = repo.execute(
        _event(
            "legal-activation",
            FieldEventType.DAY_PLAN_ACTIVATED,
            plan_day_id=seed.plan_day_id,
        ),
        _context(),
        received_at=NOW,
    )
    root = repo.get_plan_day_root(seed.plan_day_id)
    assert result.outcome is ReductionOutcome.APPLIED
    assert root.status is PlanDayStatus.ACTIVE
    assert root.plan_day_revision == 1


def test_rpl8_append_only_changed_duplicate_replace_is_blocked(
    tmp_path: Path,
) -> None:
    path = tmp_path / "append-only-replace.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    connection = repo._connect()
    try:
        connection.execute("PRAGMA recursive_triggers=OFF")
        stored = dict(
            connection.execute(
                "SELECT * FROM m2_task_routes WHERE task_id=?",
                (seed.task_id,),
            ).fetchone()
        )
        route = verify_canonical_document(stored["route_json"])
        route["business_meaning"] = "forged replacement meaning"
        stored["route_json"] = canonical_json(route)
        stored["route_sha256"] = hashlib.sha256(
            stored["route_json"].encode("utf-8")
        ).hexdigest()
        columns = tuple(stored)
        with pytest.raises(sqlite3.IntegrityError, match="identity already exists"):
            connection.execute(
                _replacement_statement(
                    "m2_task_routes",
                    columns,
                    ("task_id",),
                    "INSERT OR REPLACE",
                ),
                tuple(stored[column] for column in columns),
            )
        connection.rollback()
    finally:
        connection.close()
    assert repo.get_job_execution_root(seed.job_id).task(
        seed.task_id
    ).definition.business_meaning != "forged replacement meaning"


def test_rpl9_repository_idempotency_remains_compatible_with_strict_inserts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replace-idempotency.db"
    repo = _initialize(path)
    seed = _seed(path, repo)
    original_plan = repo.get_plan_day_root(seed.plan_day_id)
    assert repo.create_plan_day_root(original_plan) == original_plan

    event = _event(
        "replace-conflict-owner",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id=seed.plan_day_id,
    )
    repo.execute(event, _context(), received_at=NOW)
    changed = _event(
        "replace-conflict-owner",
        FieldEventType.START_DELAY_REPORTED,
        plan_day_id=seed.plan_day_id,
        eta="10:00",
    )
    for offset in (1, 2):
        with pytest.raises(M2ConflictError, match="PAYLOAD_MISMATCH"):
            repo.execute(
                changed,
                _context(),
                received_at=NOW + timedelta(minutes=offset),
            )
    assert _count(path, "m2_input_conflicts") == 1


@pytest.mark.parametrize(
    "root_name",
    ("worker_registry", "job_execution", "plan_day", "directive"),
)
def test_raw_sql_root_corruption_matrix_fails_closed(
    tmp_path: Path,
    root_name: str,
) -> None:
    path = tmp_path / f"raw-root-{root_name}.db"
    repo, seed, cases = _replacement_fixture(path)
    _name, table, primary_key, overrides, _loader, _original = next(
        item for item in cases if item[0] == root_name
    )
    identity_column = primary_key[0]
    revision_column = {
        "worker_registry": "registry_revision",
        "job_execution": "job_execution_revision",
        "plan_day": "plan_day_revision",
        "directive": "directive_revision",
    }[root_name]
    identity_value = {
        "worker_registry": "GLOBAL",
        "job_execution": seed.job_id,
        "plan_day": seed.plan_day_id,
        "directive": "replace-directive",
    }[root_name]

    connection = repo._connect()
    try:
        connection.execute("PRAGMA recursive_triggers=OFF")
        stored = dict(
            connection.execute(
                f"SELECT * FROM {table} WHERE {identity_column}=?",
                (identity_value,),
            ).fetchone()
        )
        forged_raw = overrides["canonical_root_json"]
        forged_hash = hashlib.sha256(forged_raw.encode("utf-8")).hexdigest()

        def canonical_with(**changes):
            document = json.loads(forged_raw)
            payload = document["payload"]
            payload.update(changes)
            return canonical_json(document)

        plus_two_raw = canonical_with(**{revision_column: 2})
        backwards_raw = canonical_with(**{revision_column: -1})
        wrong_type_document = json.loads(forged_raw)
        wrong_type_document["document_type"] = "ForgedRoot"
        wrong_type_raw = canonical_json(wrong_type_document)

        if root_name == "worker_registry":
            mismatched_identity_raw = canonical_with(registry_revision=1)
            identity_update = {"registry_key": "OTHER"}
            scalar_update = {"root_schema_version": "forged-schema"}
        else:
            mismatch_document = json.loads(forged_raw)
            if root_name == "directive":
                mismatch_document["payload"]["definition"]["directive_id"] = (
                    "mismatched-identity"
                )
            else:
                mismatch_document["payload"][identity_column] = "mismatched-identity"
            mismatch_document["payload"][revision_column] = 1
            mismatched_identity_raw = canonical_json(mismatch_document)
            identity_update = {identity_column: "mismatched-row-identity"}
            scalar_update = {
                "job_execution": {"root_schema_version": "forged-schema"},
                "plan_day": {"status": PlanDayStatus.ACTIVE.value},
                "directive": {
                    "delivery_evidence": DeliveryEvidence.CHANNEL_ACCEPTED.value
                },
            }[root_name]

        def update_sql(values: dict[str, object]):
            assignments = ",".join(f"{column}=?" for column in values)
            return (
                f"UPDATE {table} SET {assignments} "
                f"WHERE {identity_column}=?",
                (*values.values(), identity_value),
            )

        replacement = dict(stored)
        replacement.update(overrides)
        replacement["content_sha256"] = forged_hash
        replacement_columns = tuple(replacement)
        operations = (
            update_sql(
                {
                    "canonical_root_json": forged_raw,
                    "content_sha256": stored["content_sha256"],
                    revision_column: 1,
                }
            ),
            update_sql(
                {
                    "canonical_root_json": forged_raw,
                    "content_sha256": "0" * 64,
                    revision_column: 1,
                }
            ),
            update_sql({**identity_update, revision_column: 1}),
            update_sql(
                {
                    "canonical_root_json": forged_raw,
                    "content_sha256": forged_hash,
                    revision_column: 0,
                }
            ),
            update_sql(
                {
                    "canonical_root_json": plus_two_raw,
                    "content_sha256": hashlib.sha256(
                        plus_two_raw.encode("utf-8")
                    ).hexdigest(),
                    revision_column: 2,
                }
            ),
            update_sql(
                {
                    "canonical_root_json": backwards_raw,
                    "content_sha256": hashlib.sha256(
                        backwards_raw.encode("utf-8")
                    ).hexdigest(),
                    revision_column: -1,
                }
            ),
            update_sql({**scalar_update, revision_column: 1}),
            update_sql(
                {
                    "canonical_root_json": wrong_type_raw,
                    "content_sha256": hashlib.sha256(
                        wrong_type_raw.encode("utf-8")
                    ).hexdigest(),
                    revision_column: 1,
                }
            ),
            update_sql(
                {
                    "canonical_root_json": " " + stored["canonical_root_json"],
                    "content_sha256": hashlib.sha256(
                        (" " + stored["canonical_root_json"]).encode("utf-8")
                    ).hexdigest(),
                    revision_column: 1,
                }
            ),
            update_sql(
                {
                    "canonical_root_json": mismatched_identity_raw,
                    "content_sha256": hashlib.sha256(
                        mismatched_identity_raw.encode("utf-8")
                    ).hexdigest(),
                    revision_column: 1,
                }
            ),
            (
                f"DELETE FROM {table} WHERE {identity_column}=?",
                (identity_value,),
            ),
            (
                _replacement_statement(
                    table,
                    replacement_columns,
                    primary_key,
                    "INSERT OR REPLACE",
                ),
                tuple(replacement[column] for column in replacement_columns),
            ),
        )

        def strict_read_from_current_transaction() -> None:
            if root_name == "worker_registry":
                repo._worker_registry_from_connection(connection)
            else:
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE {identity_column}=?",
                    (identity_value,),
                ).fetchone()
                if row is None:
                    raise M2StorageIntegrityError("canonical root disappeared")
                if root_name == "job_execution":
                    repo._job_root_from_connection(connection, row)
                elif root_name == "plan_day":
                    repo._plan_day_from_row(row)
                else:
                    repo._directive_from_row(row)

        for sql, parameters in operations:
            try:
                connection.execute(sql, parameters)
            except sqlite3.DatabaseError:
                connection.rollback()
                continue
            try:
                with pytest.raises((M2StorageIntegrityError, ValueError)):
                    strict_read_from_current_transaction()
            finally:
                connection.rollback()
    finally:
        connection.close()
