from __future__ import annotations

from datetime import datetime
from pathlib import Path

from werkcrew_ai.agent import AgentStatus, PersistentDispatchOrchestrator
from werkcrew_ai.dispatch import (
    COMPANY_TIMEZONE,
    PersistentGateStatus,
    ReplanProposalStatus,
)
from werkcrew_ai.infrastructure.m7_demo import (
    M7_NOW,
    M7_PLANNING_DATE,
    M7_WORKDAY_END,
    M7_WORKDAY_START,
    M7_WORKER_ID,
    seed_m7_demo,
)
from werkcrew_ai.persistence import SqliteBusinessRepository
from werkcrew_ai.persistence import StaleRevisionError

from .fakes import ErrorModel, ScriptedModel


PROPOSAL_ID = "replan-c3e6a80b97740d2d2072ee91"


def first_model(ids) -> ScriptedModel:
    return ScriptedModel(
        [
            (
                "tool",
                "get_multi_job_dispatch_state",
                {
                    "initiating_workflow_instance_id": ids.workflow_b,
                    "affected_job_ids": ["job-b-demo", "job-c-demo"],
                },
            ),
            (
                "tool",
                "update_material_readiness",
                {
                    "job_id": "job-b-demo",
                    "scheduled_task_id": ids.task_b,
                    "expected_revision": 0,
                    "available_at": "2026-09-14T11:00:00+02:00",
                },
            ),
            (
                "tool",
                "create_daily_replan_proposal",
                {
                    "initiating_workflow_instance_id": ids.workflow_b,
                    "affected_job_ids": ["job-b-demo", "job-c-demo"],
                    "worker_id": M7_WORKER_ID,
                },
            ),
            ("tool", "request_replan_approval", {"proposal_id": PROPOSAL_ID}),
        ]
    )


def resume_model() -> ScriptedModel:
    return ScriptedModel(
        [("text", "Owner replan decision was persisted. No quote was sent.", None)]
    )


def orchestrator(repository, ids, storage, model):
    return PersistentDispatchOrchestrator(
        repository,
        initiating_workflow_instance_id=ids.workflow_b,
        now=M7_NOW,
        planning_date=M7_PLANNING_DATE,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
        model=model,
        session_storage_dir=str(storage),
    )


def interrupted(tmp_path: Path):
    repository = SqliteBusinessRepository(tmp_path / "business.db")
    ids = seed_m7_demo(repository)
    first = orchestrator(repository, ids, tmp_path / "sessions", first_model(ids)).run_material_delay_scenario(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        available_at=datetime(2026, 9, 14, 11, 0, tzinfo=COMPANY_TIMEZONE),
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
    )
    gate = repository.pending_gate_for_workflow(ids.workflow_b)
    assert first.stop_reason == "interrupt"
    assert gate is not None
    return repository, ids, first, gate


def test_real_replan_interrupt_fresh_agent_approve_atomic_apply(tmp_path: Path) -> None:
    repository, ids, first, gate = interrupted(tmp_path)
    before = repository.calendar_for_jobs(("job-b-demo", "job-c-demo"))

    second = orchestrator(
        repository, ids, tmp_path / "sessions", resume_model()
    ).resume_replan_decision(gate_id=gate.gate_id, action="APPROVE_REPLAN")

    final_gate = repository.get_gate(gate.gate_id)
    proposal = repository.get_proposal(PROPOSAL_ID)
    decision = repository.owner_decisions(proposal_id=PROPOSAL_ID)
    after = repository.calendar_for_jobs(("job-b-demo", "job-c-demo"))
    assert first.agent_instance_id != second.agent_instance_id
    assert first.session_id == second.session_id == gate.session_id
    assert first.agent_id == second.agent_id == gate.agent_id
    assert first.session_storage == second.session_storage
    assert first.interrupt_id == second.interrupt_id == final_gate.interrupt_id
    assert final_gate.status is PersistentGateStatus.RESOLVED
    assert proposal.status is ReplanProposalStatus.APPLIED
    assert len(decision) == 1
    assert decision[0].action == "APPROVE_REPLAN"
    assert decision[0].source == "COORDINATOR_UI"
    assert decision[0].actor_role == "OWNER"
    assert second.agent_status is AgentStatus.OWNER_DECISION_RECORDED
    assert [item.job_id for item in before] == ["job-b-demo", "job-c-demo"]
    assert [item.job_id for item in after] == ["job-c-demo", "job-b-demo"]
    assert all(item.worker_id == M7_WORKER_ID for item in after)
    assert all(old.vehicle_id == new.vehicle_id for old, new in zip(sorted(before, key=lambda x: x.assignment_id), sorted(after, key=lambda x: x.assignment_id), strict=True))
    assert all(item.revision == 1 for item in after)
    assert repository.get_workflow(ids.workflow_b).revision == 1
    assert repository.get_workflow(ids.workflow_c).revision == 1
    traces_b = repository.trace_for_job("job-b-demo")
    traces_c = repository.trace_for_job("job-c-demo")
    applied_b = next(item for item in traces_b if item.reason_code == "REPLAN_APPLIED")
    applied_c = next(item for item in traces_c if item.reason_code == "REPLAN_APPLIED")
    assert applied_b.correlation_id == applied_c.correlation_id


def test_reject_preserves_calendar_and_is_gate_scoped(tmp_path: Path) -> None:
    repository, ids, _, gate = interrupted(tmp_path)
    before = repository.calendar_for_jobs(("job-b-demo", "job-c-demo"))
    outcome = orchestrator(repository, ids, tmp_path / "sessions", resume_model()).resume_replan_decision(
        gate_id=gate.gate_id,
        action="REJECT_REPLAN",
    )
    assert outcome.agent_status is AgentStatus.OWNER_DECISION_RECORDED
    assert repository.get_proposal(PROPOSAL_ID).status is ReplanProposalStatus.REJECTED
    assert repository.calendar_for_jobs(("job-b-demo", "job-c-demo")) == before
    assert len(repository.owner_decisions(proposal_id=PROPOSAL_ID)) == 1


def test_identical_retry_after_success_has_no_second_agent_or_effect(tmp_path: Path) -> None:
    repository, ids, _, gate = interrupted(tmp_path)
    first = orchestrator(repository, ids, tmp_path / "sessions", resume_model()).resume_replan_decision(
        gate_id=gate.gate_id,
        action="APPROVE_REPLAN",
    )
    revisions = tuple(item.revision for item in repository.calendar_for_jobs(("job-b-demo", "job-c-demo")))
    retry = orchestrator(repository, ids, tmp_path / "sessions", ErrorModel()).resume_replan_decision(
        gate_id=gate.gate_id,
        action="APPROVE_REPLAN",
    )
    assert first.agent_instance_id is not None
    assert retry.stop_reason == "idempotent"
    assert retry.agent_instance_id is None
    assert len(repository.owner_decisions(proposal_id=PROPOSAL_ID)) == 1
    assert tuple(item.revision for item in repository.calendar_for_jobs(("job-b-demo", "job-c-demo"))) == revisions


def test_error_after_sqlite_commit_survives_reopen_and_retry(tmp_path: Path) -> None:
    repository, ids, _, gate = interrupted(tmp_path)
    failed_narration = orchestrator(
        repository, ids, tmp_path / "sessions", ErrorModel()
    ).resume_replan_decision(gate_id=gate.gate_id, action="APPROVE_REPLAN")
    assert failed_narration.agent_status is AgentStatus.ERROR
    assert repository.get_proposal(PROPOSAL_ID).status is ReplanProposalStatus.APPLIED
    assert len(repository.owner_decisions(proposal_id=PROPOSAL_ID)) == 1
    revisions = tuple(item.revision for item in repository.calendar_for_jobs(("job-b-demo", "job-c-demo")))

    recovered = SqliteBusinessRepository(repository.database_path)
    retry = orchestrator(recovered, ids, tmp_path / "sessions", ErrorModel()).resume_replan_decision(
        gate_id=gate.gate_id,
        action="APPROVE_REPLAN",
    )
    assert retry.stop_reason == "idempotent"
    assert len(recovered.owner_decisions(proposal_id=PROPOSAL_ID)) == 1
    assert tuple(item.revision for item in recovered.calendar_for_jobs(("job-b-demo", "job-c-demo"))) == revisions == (1, 1)


def test_stale_proposal_cannot_partially_modify_calendar(tmp_path: Path) -> None:
    repository, ids, _, gate = interrupted(tmp_path)
    workflow_c = repository.get_workflow(ids.workflow_c)
    repository.update_workflow_cas(
        workflow_instance_id=ids.workflow_c,
        expected_revision=workflow_c.revision,
        state=workflow_c.state,
        snapshot=workflow_c.snapshot,
        now=M7_NOW,
    )
    before = repository.calendar_for_jobs(("job-b-demo", "job-c-demo"))
    outcome = orchestrator(
        repository, ids, tmp_path / "sessions", resume_model()
    ).resume_replan_decision(gate_id=gate.gate_id, action="APPROVE_REPLAN")
    assert repository.get_proposal(PROPOSAL_ID).status is ReplanProposalStatus.STALE
    assert repository.get_gate(gate.gate_id).status is PersistentGateStatus.INVALIDATED
    assert repository.owner_decisions(proposal_id=PROPOSAL_ID) == ()
    assert repository.calendar_for_jobs(("job-b-demo", "job-c-demo")) == before
    assert all(item.revision == 0 for item in before)
