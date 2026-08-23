from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from tests.unit.agent.fakes import ScriptedModel
from werkcrew_ai.agent import PersistentDispatchOrchestrator
from werkcrew_ai.dispatch import COMPANY_TIMEZONE
from werkcrew_ai.infrastructure.m7_demo import (
    M7_NOW,
    M7_PLANNING_DATE,
    M7_WORKDAY_END,
    M7_WORKDAY_START,
    M7_WORKER_ID,
    seed_m7_demo,
)
from werkcrew_ai.persistence import SqliteBusinessRepository


PROPOSAL_ID = "replan-c3e6a80b97740d2d2072ee91"


def first_model(ids):
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


def orchestrator(repository, workflow_id, storage, model):
    return PersistentDispatchOrchestrator(
        repository,
        initiating_workflow_instance_id=workflow_id,
        now=M7_NOW,
        planning_date=M7_PLANNING_DATE,
        workday_start=M7_WORKDAY_START,
        workday_end=M7_WORKDAY_END,
        model=model,
        session_storage_dir=str(storage),
    )


parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("create", "recover"))
parser.add_argument("database")
parser.add_argument("storage")
args = parser.parse_args()
repository = SqliteBusinessRepository(args.database)
storage = Path(args.storage)

if args.mode == "create":
    ids = seed_m7_demo(repository)
    outcome = orchestrator(repository, ids.workflow_b, storage, first_model(ids)).run_material_delay_scenario(
        job_id="job-b-demo",
        scheduled_task_id=ids.task_b,
        available_at=datetime(2026, 9, 14, 11, 0, tzinfo=COMPANY_TIMEZONE),
        affected_job_ids=("job-b-demo", "job-c-demo"),
        worker_id=M7_WORKER_ID,
    )
    gate = repository.get_gate(outcome.gate_id)
    result = {
        "workflow_id": ids.workflow_b,
        "workflow_state": repository.get_workflow(ids.workflow_b).state,
        "workflow_revision": repository.get_workflow(ids.workflow_b).revision,
        "session_id": outcome.session_id,
        "agent_id": outcome.agent_id,
        "gate_id": gate.gate_id,
        "interrupt_id": gate.interrupt_id,
        "gate_status": gate.status.value,
        "trace_count": len(repository.trace_for_job("job-b-demo")),
        "decision_count": len(repository.owner_decisions()),
        "stop_reason": outcome.stop_reason,
    }
else:
    workflow = repository.get_workflow("workflow-job-b-demo-v1")
    gate = repository.recover_pending_gate(
        workflow_instance_id=workflow.workflow_instance_id,
        session_storage_dir=str(storage),
    )
    binding = repository.get_session_binding(workflow.workflow_instance_id)
    result = {
        "workflow_id": workflow.workflow_instance_id,
        "workflow_state": workflow.state,
        "workflow_revision": workflow.revision,
        "session_id": binding.session_id,
        "agent_id": binding.agent_id,
        "gate_id": gate.gate_id,
        "interrupt_id": gate.interrupt_id,
        "gate_status": gate.status.value,
        "trace_count": len(repository.trace_for_job("job-b-demo")),
        "decision_count": len(repository.owner_decisions()),
        "auto_resumed": False,
    }

print(json.dumps(result, sort_keys=True))
