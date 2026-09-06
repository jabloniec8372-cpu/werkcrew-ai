"""Call the narrow M2 HTTP slice in a fresh Python process."""

from __future__ import annotations

import argparse
import json
import os


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--plan-day", required=True)
    parser.add_argument("--occurred-at", required=True)
    arguments = parser.parse_args()

    os.environ["WERKCREW_DB_PATH"] = arguments.database

    from fastapi.testclient import TestClient

    from werkcrew_ai.api.app import app
    from werkcrew_ai.field import M2DurableRepository

    request = {
        "event_id": arguments.event_id,
        "schema_version": 1,
        "event_type": "DAY_PLAN_ACTIVATED",
        "actor_id": arguments.worker,
        "occurred_at": arguments.occurred_at,
        "plan_day_id": arguments.plan_day,
        "offline_origin": False,
    }
    response = TestClient(app).post(
        "/api/m2/field-events/day-plan-activated",
        headers={"X-WERKcrew-Worker-ID": arguments.worker},
        json=request,
    )

    repository = M2DurableRepository(arguments.database)
    with repository._connect() as connection:
        counts = {
            "inbox": connection.execute(
                "SELECT count(*) FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' AND input_id=?",
                (arguments.event_id,),
            ).fetchone()[0],
            "receipts": connection.execute(
                "SELECT count(*) FROM m2_input_inbox WHERE input_namespace='FIELD_EVENT' "
                "AND input_id=? AND appended_receipt_json IS NOT NULL",
                (arguments.event_id,),
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT count(*) FROM m2_effect_outbox WHERE input_namespace='FIELD_EVENT' AND input_id=?",
                (arguments.event_id,),
            ).fetchone()[0],
        }
    plan = repository.get_plan_day_root(arguments.plan_day)
    print(
        json.dumps(
            {
                "status_code": response.status_code,
                "response": response.json(),
                "counts": counts,
                "plan_status": plan.status.value,
                "plan_revision": plan.plan_day_revision,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
