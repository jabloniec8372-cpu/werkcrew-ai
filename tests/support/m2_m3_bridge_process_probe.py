from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from werkcrew_ai.api.m2_runtime import (
    m2_repository,
    router,
    server_event_id,
    server_timestamp,
)
from werkcrew_ai.field.repository import FIELD_EVENT, M2DurableRepository
from werkcrew_ai.planning.m2_bridge import (
    M2EffectSourceIdentity,
    M2UnavailableToM3Bridge,
)


def _counts(repository: M2DurableRepository) -> dict[str, int]:
    uri = f"{Path(repository.database_path).as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return {
            "inbox": connection.execute(
                "SELECT count(*) FROM m2_input_inbox"
            ).fetchone()[0],
            "outbox": connection.execute(
                "SELECT count(*) FROM m2_effect_outbox"
            ).fetchone()[0],
            "receipts": connection.execute(
                "SELECT count(*) FROM m2_input_inbox "
                "WHERE appended_receipt_json IS NOT NULL"
            ).fetchone()[0],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("action", choices=("post-unavailable", "build-request"))
    parser.add_argument("--event-id", required=True)
    args = parser.parse_args()
    repository = M2DurableRepository(args.database)

    if args.action == "post-unavailable":
        now = datetime(2026, 9, 6, 8, 0, tzinfo=timezone.utc)
        generated_server_event_id = str(uuid4())
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[m2_repository] = lambda: repository
        app.dependency_overrides[server_timestamp] = lambda: now
        app.dependency_overrides[server_event_id] = (
            lambda: generated_server_event_id
        )
        response = TestClient(app).post(
            "/api/m2/field-events/unavailable-today-reported",
            headers={"X-WERKcrew-Worker-ID": "worker-api"},
            json={
                "event_id": args.event_id,
                "schema_version": 1,
                "event_type": "UNAVAILABLE_TODAY_REPORTED",
                "actor_id": "worker-api",
                "occurred_at": "2026-09-06T09:59:00+02:00",
                "plan_day_id": "plan-api",
                "reason_class": "SICK",
                "offline_origin": False,
            },
        )
        content = response.json()
        print(
            json.dumps(
                {
                    "replayed": content["replayed"],
                    "server_event_id": content["server_event_id"],
                    "status_code": response.status_code,
                },
                sort_keys=True,
            )
        )
        return

    request = M2UnavailableToM3Bridge(repository).build_request(
        M2EffectSourceIdentity(FIELD_EVENT, args.event_id, 0)
    )
    print(
        json.dumps(
            {
                "assignment_ids": [
                    item.assignment_id for item in request.snapshot.assignments
                ],
                "counts": _counts(repository),
                "fingerprint": request.request_fingerprint,
                "plan_revision": request.snapshot.plan_day.plan_day_revision,
                "source": {
                    "effect_ordinal": request.snapshot.source.effect_ordinal,
                    "event_id": request.snapshot.source.event_id,
                    "server_event_id": request.snapshot.source.server_event_id,
                },
                "worker_available": request.snapshot.plan_day.worker_available,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
