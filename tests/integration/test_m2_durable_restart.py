from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from werkcrew_ai.field.models import (
    AssignmentKind,
    AssignmentState,
    CompletionType,
    DirectiveClass,
    DirectiveDefinition,
    DirectiveRoot,
    DirectiveType,
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
    ReductionOutcome,
    SystemSignal,
    SystemSignalType,
    TaskDefinition,
    TaskState,
    TaskStatus,
    WorkerIdentityRegistry,
)
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.intake import (
    CanonicalJobIntake,
    CanonicalJobRepository,
    M1BoundaryPublicationRepository,
)


NOW = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


def _publication(path: Path, job_id: str):
    CanonicalJobRepository(path).create_job(
        job_id=job_id,
        intake=CanonicalJobIntake("restart-test", None, None, ()),
        created_at=NOW - timedelta(days=1),
    )
    boundary = M1BoundaryPublicationRepository(path)
    return boundary.publish_handoff(
        boundary.current_projection(job_id), published_at=NOW - timedelta(hours=12)
    )


def _task(publication, task_id: str, *, safe_hold: bool = False) -> TaskState:
    return TaskState(
        TaskDefinition(
            task_id,
            f"{task_id}-v1",
            publication.job_id,
            publication.handoff_id,
            publication.source_revision,
            f"restart {task_id}",
            CompletionType.TASK,
        ),
        status=TaskStatus.SAFE_HOLD if safe_hold else TaskStatus.OPEN,
        blocked_pending_resolution=safe_hold,
        execution_authorized=not safe_hold,
    )


def _assignment(job_id: str, task_id: str, assignment_id: str):
    return AssignmentState(
        assignment_id,
        job_id,
        task_id,
        f"{task_id}-v1",
        AssignmentKind.SINGLE,
        ("worker-restart",),
        ("shared-plan",),
    )


def _event(event_id: str, event_type: FieldEventType, **values):
    return FieldEventInput(
        FieldEventEnvelope(
            event_id,
            1,
            event_type,
            "worker-restart",
            NOW,
            **values,
        ),
        f"server-{event_id}",
    )


def test_fresh_process_rehydrates_only_from_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    repository = M2DurableRepository(path)
    repository.initialize(now=NOW)
    repository.create_worker_registry(WorkerIdentityRegistry(("worker-restart",)))
    repository.create_plan_day_root(
        PlanDayRoot(
            "shared-plan",
            "worker-restart",
            date(2026, 9, 4),
            NOW - timedelta(hours=2),
            PlanDayStatus.ACTIVE,
            confirmed_plan_reference="plan-before-ack",
        )
    )

    publication_a = _publication(path, "job-restart-a")
    safe = _task(publication_a, "task-safe", safe_hold=True)
    completed = _task(publication_a, "task-completed")
    repository.create_job_execution_root(
        M2JobExecutionRoot(
            "job-restart-a",
            (safe, completed),
            (
                _assignment("job-restart-a", "task-safe", "assignment-safe"),
                _assignment(
                    "job-restart-a", "task-completed", "assignment-completed"
                ),
            ),
        )
    )
    publication_b = _publication(path, "job-restart-b")
    task_b = _task(publication_b, "task-job-b")
    repository.create_job_execution_root(
        M2JobExecutionRoot(
            "job-restart-b",
            (task_b,),
            (_assignment("job-restart-b", "task-job-b", "assignment-job-b"),),
        )
    )

    directive = DirectiveRoot(
        DirectiveDefinition(
            "directive-plan-only",
            DirectiveType.ACTION_REQUIRED,
            DirectiveClass.ACTION,
            "worker-restart",
            NOW - timedelta(minutes=10),
            plan_day_id="shared-plan",
            proposed_plan_reference="plan-after-ack",
        )
    )
    repository.create_directive_root(directive)
    context = PolicyTimeContext(NOW, M2Policy())
    repository.execute(
        _event(
            "ack-plan",
            FieldEventType.WORKER_ACKNOWLEDGED,
            plan_day_id="shared-plan",
            directive_id="directive-plan-only",
        ),
        context,
        received_at=NOW,
    )
    repository.execute(
        _event(
            "evidence-safe",
            FieldEventType.SITE_PROBLEM_REPORTED,
            plan_day_id="shared-plan",
            job_id="job-restart-a",
            task_id="task-safe",
            assignment_id="assignment-safe",
            attachments=(
                EvidenceItem("evidence-restart", EvidenceKind.PHOTO, "blob:restart"),
            ),
        ),
        context,
        received_at=NOW,
    )
    repository.execute(
        _event(
            "completion-restart",
            FieldEventType.TASK_COMPLETION_REPORTED,
            plan_day_id="shared-plan",
            job_id="job-restart-a",
            task_id="task-completed",
            assignment_id="assignment-completed",
        ),
        context,
        received_at=NOW,
    )
    applied_retry_input = _event(
        "fresh-retry-applied",
        FieldEventType.DAY_CLOSE_REPORTED,
        plan_day_id="shared-plan",
    )
    rejected_retry_input = _event(
        "fresh-retry-rejected",
        FieldEventType.SITE_PROBLEM_REPORTED,
        job_id="unknown-restart-job",
        task_id="unknown-restart-task",
        assignment_id="unknown-restart-assignment",
        attachments=(
            EvidenceItem(
                "fresh-retry-unresolved-evidence",
                EvidenceKind.PHOTO,
                "blob:fresh-retry",
            ),
        ),
    )
    noop_retry_input = SystemSignal(
        "fresh-retry-noop",
        SystemSignalType.STATE_REHYDRATED,
        plan_day_id="shared-plan",
    )
    assert repository.execute(
        applied_retry_input, context, received_at=NOW
    ).outcome is ReductionOutcome.APPLIED
    assert repository.execute(
        rejected_retry_input, context, received_at=NOW
    ).outcome is ReductionOutcome.REJECTED
    assert repository.execute(
        noop_retry_input, context, received_at=NOW
    ).outcome is ReductionOutcome.NOOP
    del repository

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    completed_process = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "support" / "m2_process_probe.py"),
            str(path),
            "--jobs",
            "job-restart-a,job-restart-b",
            "--plan",
            "shared-plan",
            "--directive",
            "directive-plan-only",
            "--retry",
            "FIELD_EVENT:fresh-retry-applied",
            "--retry",
            "FIELD_EVENT:fresh-retry-rejected",
            "--retry",
            "SYSTEM_SIGNAL:fresh-retry-noop",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    result = json.loads(completed_process.stdout)

    assert result["directive_informed"] is True
    assert result["plan_confirmed_reference"] == "plan-after-ack"
    assert result["tasks"]["task-safe"] == {
        "blocked": True,
        "completion_event_id": None,
        "evidence_ids": ["evidence-restart"],
        "execution_authorized": False,
        "status": "SAFE_HOLD",
    }
    assert result["tasks"]["task-completed"]["completion_event_id"] == (
        "completion-restart"
    )
    assert "evidence-safe" in result["receipt_ids"]
    assert set(result["job_revisions"]) == {"job-restart-a", "job-restart-b"}
    expected_outcomes = {
        "FIELD_EVENT:fresh-retry-applied": "APPLIED",
        "FIELD_EVENT:fresh-retry-rejected": "REJECTED",
        "SYSTEM_SIGNAL:fresh-retry-noop": "NOOP",
    }
    assert set(result["retry_results"]) == set(expected_outcomes)
    for identity, expected_outcome in expected_outcomes.items():
        retry = result["retry_results"][identity]
        assert retry["outcome"] == expected_outcome
        assert retry["replayed"] is True
        assert retry["inbox_count_before"] == retry["inbox_count_after"] == 1
        assert retry["outbox_count_before"] == retry["outbox_count_after"]
        assert retry["receipt_sha256_before"] == retry["receipt_sha256_after"]
        assert retry["policy_context_unchanged"] is True
        assert retry["root_revisions_unchanged"] is True


def test_fresh_process_retries_completed_applied_rejected_and_noop(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fresh-retry.db"
    repository = M2DurableRepository(path)
    repository.initialize(now=NOW)
    repository.create_worker_registry(WorkerIdentityRegistry(("worker-restart",)))
    repository.create_plan_day_root(
        PlanDayRoot(
            "fresh-retry-plan",
            "worker-restart",
            date(2026, 9, 4),
            NOW - timedelta(hours=1),
            PlanDayStatus.ISSUED,
        )
    )
    repository.create_directive_root(
        DirectiveRoot(
            DirectiveDefinition(
                "fresh-retry-directive",
                DirectiveType.INFO_NOTICE,
                DirectiveClass.INFO,
                "worker-restart",
                NOW - timedelta(minutes=5),
                plan_day_id="fresh-retry-plan",
            )
        )
    )
    context = PolicyTimeContext(NOW, M2Policy())
    applied = _event(
        "subprocess-applied",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id="fresh-retry-plan",
    )
    rejected = _event(
        "subprocess-rejected",
        FieldEventType.SITE_PROBLEM_REPORTED,
        job_id="missing-job",
        task_id="missing-task",
        assignment_id="missing-assignment",
        attachments=(
            EvidenceItem("subprocess-raw", EvidenceKind.PHOTO, "blob:subprocess"),
        ),
    )
    noop = SystemSignal(
        "subprocess-noop",
        SystemSignalType.STATE_REHYDRATED,
        plan_day_id="fresh-retry-plan",
    )
    unsupported = _event(
        "subprocess-unsupported",
        FieldEventType.DAY_PLAN_ACTIVATED,
        plan_day_id="fresh-retry-plan",
    )
    assert repository.execute(applied, context, received_at=NOW).outcome is (
        ReductionOutcome.APPLIED
    )
    assert repository.execute(rejected, context, received_at=NOW).outcome is (
        ReductionOutcome.REJECTED
    )
    assert repository.execute(noop, context, received_at=NOW).outcome is (
        ReductionOutcome.NOOP
    )
    assert repository.execute(
        unsupported,
        replace(context, rule_version="m2-unsupported-rule"),
        received_at=NOW,
    ).outcome is ReductionOutcome.REJECTED
    del repository

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    completed_process = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "support" / "m2_process_probe.py"),
            str(path),
            "--jobs",
            "",
            "--plan",
            "fresh-retry-plan",
            "--directive",
            "fresh-retry-directive",
            "--retry",
            "FIELD_EVENT:subprocess-applied",
            "--retry",
            "FIELD_EVENT:subprocess-rejected",
            "--retry",
            "SYSTEM_SIGNAL:subprocess-noop",
            "--retry",
            "FIELD_EVENT:subprocess-unsupported",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    result = json.loads(completed_process.stdout)
    expected = {
        "FIELD_EVENT:subprocess-applied": "APPLIED",
        "FIELD_EVENT:subprocess-rejected": "REJECTED",
        "SYSTEM_SIGNAL:subprocess-noop": "NOOP",
        "FIELD_EVENT:subprocess-unsupported": "REJECTED",
    }
    assert set(result["retry_results"]) == set(expected)
    for identity, outcome in expected.items():
        retry = result["retry_results"][identity]
        assert retry["outcome"] == outcome
        assert retry["replayed"] is True
        assert retry["inbox_count_before"] == retry["inbox_count_after"] == 1
        assert retry["outbox_count_before"] == retry["outbox_count_after"]
        assert retry["receipt_sha256_before"] == retry["receipt_sha256_after"]
        assert retry["policy_context_unchanged"] is True
        assert retry["root_revisions_unchanged"] is True
