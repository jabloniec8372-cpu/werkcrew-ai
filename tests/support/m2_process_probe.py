"""Fresh-process probe for restart-safe M2 SQLite state."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import timedelta

from werkcrew_ai.field.models import M2ReductionScope, ProcessedEventLedger
from werkcrew_ai.field.repository import M2DurableRepository
from werkcrew_ai.field.serialization import deserialize_policy_time_context


def _durable_snapshot(repository: M2DurableRepository, namespace: str, input_id: str):
    connection = repository._connect()
    try:
        inbox = connection.execute(
            "SELECT * FROM m2_input_inbox WHERE input_namespace=? AND input_id=?",
            (namespace, input_id),
        ).fetchone()
        return {
            "inbox_count": connection.execute(
                "SELECT count(*) FROM m2_input_inbox WHERE input_namespace=? AND input_id=?",
                (namespace, input_id),
            ).fetchone()[0],
            "outbox_count": connection.execute(
                "SELECT count(*) FROM m2_effect_outbox WHERE input_namespace=? AND input_id=?",
                (namespace, input_id),
            ).fetchone()[0],
            "receipt_sha256": inbox["appended_receipt_sha256"],
            "policy_context_json": inbox["policy_context_json"],
            "root_revisions": {
                "jobs": dict(
                    connection.execute(
                        "SELECT job_id, job_execution_revision FROM m2_job_execution_roots"
                    ).fetchall()
                ),
                "plans": dict(
                    connection.execute(
                        "SELECT plan_day_id, plan_day_revision FROM m2_plan_day_roots"
                    ).fetchall()
                ),
                "directives": dict(
                    connection.execute(
                        "SELECT directive_id, directive_revision FROM m2_directive_roots"
                    ).fetchall()
                ),
            },
        }, repository._explicit_input_from_row(inbox)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--directive", required=True)
    parser.add_argument("--retry", action="append", default=[])
    arguments = parser.parse_args()

    repository = M2DurableRepository(arguments.database)
    registry = repository.get_worker_registry()
    jobs = tuple(
        repository.get_job_execution_root(job_id)
        for job_id in arguments.jobs.split(",")
        if job_id
    )
    plan = repository.get_plan_day_root(arguments.plan)
    directive = repository.get_directive_root(arguments.directive)
    ledger = repository.load_processed_event_ledger()
    informed_scope = M2ReductionScope(
        publications=(),
        worker_registry=registry,
        plan_day_roots=(plan,),
        directive_roots=(directive,),
        processed_events=ProcessedEventLedger(
            tuple(
                receipt
                for receipt in ledger.receipts
                if receipt.event.event_id == directive.acknowledged_event_id
            )
        ),
    )
    tasks = {
        task.definition.task_id: {
            "blocked": task.blocked_pending_resolution,
            "completion_event_id": task.completion_event_id,
            "evidence_ids": [item.evidence_id for item in task.evidence],
            "execution_authorized": task.execution_authorized,
            "status": task.status.value,
        }
        for job in jobs
        for task in job.tasks
    }
    result = {
        "directive_ack_event_id": directive.acknowledged_event_id,
        "directive_informed": informed_scope.directive_worker_informed(
            directive.directive_id
        ),
        "job_revisions": {job.job_id: job.job_execution_revision for job in jobs},
        "plan_confirmed_reference": plan.confirmed_plan_reference,
        "plan_revision": plan.plan_day_revision,
        "receipt_ids": [item.event.event_id for item in ledger.receipts],
        "tasks": tasks,
        "worker_ids": list(registry.worker_ids),
    }
    retry_results = {}
    for value in arguments.retry:
        namespace, input_id = value.split(":", 1)
        before, explicit_input = _durable_snapshot(repository, namespace, input_id)
        stored_context = deserialize_policy_time_context(
            before["policy_context_json"]
        )
        replay = repository.execute(
            explicit_input,
            replace(stored_context, now=stored_context.now + timedelta(days=1)),
            received_at=stored_context.now + timedelta(days=1),
        )
        after, _ = _durable_snapshot(repository, namespace, input_id)
        retry_results[value] = {
            "outcome": replay.outcome.value,
            "replayed": replay.replayed,
            "inbox_count_before": before["inbox_count"],
            "inbox_count_after": after["inbox_count"],
            "outbox_count_before": before["outbox_count"],
            "outbox_count_after": after["outbox_count"],
            "receipt_sha256_before": before["receipt_sha256"],
            "receipt_sha256_after": after["receipt_sha256"],
            "policy_context_unchanged": (
                before["policy_context_json"] == after["policy_context_json"]
            ),
            "root_revisions_unchanged": (
                before["root_revisions"] == after["root_revisions"]
            ),
        }
    result["retry_results"] = retry_results
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
