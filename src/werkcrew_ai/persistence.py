"""SQLite source of truth for M7 business state and cross-job operations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from werkcrew_ai.dispatch.models import (
    AddressStatus,
    AgentTraceEvent,
    AvailabilityWindow,
    CalendarAssignment,
    CalendarStatus,
    ConfirmedCoordinates,
    DispatchVehicle,
    DispatchWorker,
    MaterialReadiness,
    MaterialReadinessStatus,
    PersistentGate,
    PersistentGateStatus,
    PersistentJob,
    PersistentOwnerDecision,
    PersistentWorkflow,
    ReplanApprovalClaim,
    ReplanProposal,
    ReplanProposalStatus,
    RouteSnapshot,
    RouteSnapshotStatus,
    SchedulePlacement,
    SessionBinding,
    StructuredAddress,
    TraceActor,
    require_aware,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPOSITORY_ROOT / "migrations" / "0001_m7_persistent_dispatch.sql"
FORBIDDEN_SNAPSHOT_KEYS = {
    "calendar_assignments",
    "material_readiness",
    "route_snapshots",
    "replan_proposals",
    "agent_trace_events",
}


class PersistenceError(RuntimeError):
    pass


class NotFoundError(PersistenceError):
    pass


class StaleRevisionError(PersistenceError):
    pass


class CrossJobReferenceError(PersistenceError):
    pass


class GateConflictError(PersistenceError):
    pass


class GateInputError(ValueError):
    pass


class RecoveryInconsistencyError(PersistenceError):
    pass


@dataclass(frozen=True, slots=True)
class SqliteSettings:
    database_path: str

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> SqliteSettings:
        source = os.environ if environment is None else environment
        configured = source.get("WERKCREW_DB_PATH", "").strip()
        if configured:
            path = Path(configured)
        elif source.get("LOCALAPPDATA", "").strip():
            path = (
                Path(source["LOCALAPPDATA"])
                / "WERKcrew_AI"
                / "werkcrew-business.db"
            )
        elif source.get("XDG_DATA_HOME", "").strip():
            path = (
                Path(source["XDG_DATA_HOME"])
                / "WERKcrew_AI"
                / "werkcrew-business.db"
            )
        else:
            path = Path.home() / ".local" / "share" / "WERKcrew_AI" / "werkcrew-business.db"
        return cls(str(path.expanduser().resolve()))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _placement_dict(item: SchedulePlacement) -> dict[str, Any]:
    return {
        "assignment_id": item.assignment_id,
        "job_id": item.job_id,
        "scheduled_task_id": item.scheduled_task_id,
        "worker_id": item.worker_id,
        "vehicle_id": item.vehicle_id,
        "start_at": item.start_at.isoformat(),
        "end_at": item.end_at.isoformat(),
    }


def _placement(value: Mapping[str, Any]) -> SchedulePlacement:
    return SchedulePlacement(
        assignment_id=value["assignment_id"],
        job_id=value["job_id"],
        scheduled_task_id=value["scheduled_task_id"],
        worker_id=value["worker_id"],
        vehicle_id=value.get("vehicle_id"),
        start_at=datetime.fromisoformat(value["start_at"]),
        end_at=datetime.fromisoformat(value["end_at"]),
    )


def _proposal_content(item: ReplanProposal) -> dict[str, Any]:
    return {
        "proposal_id": item.proposal_id,
        "initiating_workflow_instance_id": item.initiating_workflow_instance_id,
        "affected_job_ids": list(item.affected_job_ids),
        "affected_workflow_instance_ids": list(item.affected_workflow_instance_ids),
        "affected_assignment_ids": list(item.affected_assignment_ids),
        "before_schedule": [_placement_dict(value) for value in item.before_schedule],
        "after_schedule": [_placement_dict(value) for value in item.after_schedule],
        "reason_codes": list(item.reason_codes),
        "material_readiness_references": list(item.material_readiness_references),
        "route_snapshot_references": list(item.route_snapshot_references),
        "worker_idle_time_before_seconds": item.worker_idle_time_before_seconds,
        "worker_idle_time_after_seconds": item.worker_idle_time_after_seconds,
        "travel_time_before_seconds": item.travel_time_before_seconds,
        "travel_time_after_seconds": item.travel_time_after_seconds,
        "deadline_impact": item.deadline_impact,
        "conditional": item.conditional,
    }


class SqliteBusinessRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(Path(database_path).expanduser().resolve())

    def initialize(self, *, now: datetime) -> None:
        require_aware(now, "now")
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not MIGRATION_PATH.is_file():
            raise PersistenceError(f"Missing migration: {MIGRATION_PATH}")
        connection = self._connect()
        try:
            connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (now.isoformat(),),
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_snapshot(snapshot: Mapping[str, Any]) -> None:
        duplicates = FORBIDDEN_SNAPSHOT_KEYS.intersection(snapshot)
        if duplicates:
            raise ValueError(
                "Workflow snapshot cannot own normalized cross-job facts: "
                + ", ".join(sorted(duplicates))
            )

    def create_job_workflow(
        self,
        *,
        job: PersistentJob,
        workflow_instance_id: str,
        state: str,
        snapshot: Mapping[str, Any],
        schema_version: int,
        now: datetime,
        session_id: str,
        agent_id: str,
    ) -> PersistentWorkflow:
        require_aware(now, "now")
        self._validate_snapshot(snapshot)
        if job.address_status is AddressStatus.CONFIRMED and job.coordinates is None:
            raise ValueError("CONFIRMED job address requires fixture coordinates")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO jobs(
                    job_id,title,address_status,address_json,latitude,longitude,
                    coordinate_source,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    job.job_id,
                    job.title,
                    job.address_status.value,
                    _json(asdict(job.address)),
                    job.coordinates.latitude if job.coordinates else None,
                    job.coordinates.longitude if job.coordinates else None,
                    job.coordinates.source if job.coordinates else None,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO workflow_instances(
                    workflow_instance_id,job_id,revision,schema_version,state,
                    snapshot_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    workflow_instance_id,
                    job.job_id,
                    0,
                    schema_version,
                    state,
                    _json(dict(snapshot)),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                """INSERT INTO session_bindings(
                    workflow_instance_id,session_id,agent_id,created_at
                ) VALUES(?,?,?,?)""",
                (workflow_instance_id, session_id, agent_id, now.isoformat()),
            )
        return self.get_workflow(workflow_instance_id)

    def get_job(self, job_id: str) -> PersistentJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown job_id: {job_id}")
        address = StructuredAddress(**json.loads(row["address_json"]))
        coordinates = (
            ConfirmedCoordinates(
                latitude=row["latitude"],
                longitude=row["longitude"],
                source=row["coordinate_source"],
            )
            if row["latitude"] is not None
            else None
        )
        return PersistentJob(
            job_id=row["job_id"],
            title=row["title"],
            address=address,
            address_status=AddressStatus(row["address_status"]),
            coordinates=coordinates,
        )

    def list_jobs(self) -> tuple[PersistentJob, ...]:
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT job_id FROM jobs ORDER BY job_id")]
        return tuple(self.get_job(item) for item in ids)

    def get_workflow(self, workflow_instance_id: str) -> PersistentWorkflow:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_instances WHERE workflow_instance_id=?",
                (workflow_instance_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown workflow_instance_id: {workflow_instance_id}")
        return PersistentWorkflow(
            workflow_instance_id=row["workflow_instance_id"],
            job_id=row["job_id"],
            revision=row["revision"],
            schema_version=row["schema_version"],
            state=row["state"],
            snapshot=json.loads(row["snapshot_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def workflows_for_jobs(self, job_ids: tuple[str, ...]) -> tuple[PersistentWorkflow, ...]:
        if not job_ids:
            return ()
        placeholders = ",".join("?" for _ in job_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT workflow_instance_id FROM workflow_instances WHERE job_id IN ({placeholders}) ORDER BY job_id, created_at",
                job_ids,
            ).fetchall()
        return tuple(self.get_workflow(row[0]) for row in rows)

    def update_workflow_cas(
        self,
        *,
        workflow_instance_id: str,
        expected_revision: int,
        state: str,
        snapshot: Mapping[str, Any],
        now: datetime,
    ) -> PersistentWorkflow:
        require_aware(now, "now")
        self._validate_snapshot(snapshot)
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE workflow_instances
                   SET revision=revision+1,state=?,snapshot_json=?,updated_at=?
                   WHERE workflow_instance_id=? AND revision=?""",
                (
                    state,
                    _json(dict(snapshot)),
                    now.isoformat(),
                    workflow_instance_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise StaleRevisionError(
                    f"STALE workflow revision for {workflow_instance_id}"
                )
        return self.get_workflow(workflow_instance_id)

    def get_session_binding(self, workflow_instance_id: str) -> SessionBinding:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_bindings WHERE workflow_instance_id=?",
                (workflow_instance_id,),
            ).fetchone()
        if row is None:
            raise RecoveryInconsistencyError(
                f"Missing session binding for {workflow_instance_id}"
            )
        return SessionBinding(
            workflow_instance_id=row["workflow_instance_id"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def put_worker(self, worker: DispatchWorker, *, now: datetime) -> None:
        require_aware(now, "now")
        availability = [
            {"start_at": item.start_at.isoformat(), "end_at": item.end_at.isoformat()}
            for item in worker.availability
        ]
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO dispatch_workers(worker_id,skill_ids_json,availability_json,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                     skill_ids_json=excluded.skill_ids_json,
                     availability_json=excluded.availability_json,
                     updated_at=excluded.updated_at""",
                (worker.worker_id, _json(worker.skill_ids), _json(availability), now.isoformat()),
            )

    def get_worker(self, worker_id: str) -> DispatchWorker:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dispatch_workers WHERE worker_id=?", (worker_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown worker_id: {worker_id}")
        return DispatchWorker(
            worker_id=row["worker_id"],
            skill_ids=tuple(json.loads(row["skill_ids_json"])),
            availability=tuple(
                AvailabilityWindow(
                    datetime.fromisoformat(item["start_at"]),
                    datetime.fromisoformat(item["end_at"]),
                )
                for item in json.loads(row["availability_json"])
            ),
        )

    def put_vehicle(self, vehicle: DispatchVehicle, *, now: datetime) -> None:
        availability = [
            {"start_at": item.start_at.isoformat(), "end_at": item.end_at.isoformat()}
            for item in vehicle.availability
        ]
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO dispatch_vehicles(vehicle_id,availability_json,updated_at)
                   VALUES(?,?,?) ON CONFLICT(vehicle_id) DO UPDATE SET
                   availability_json=excluded.availability_json,updated_at=excluded.updated_at""",
                (vehicle.vehicle_id, _json(availability), now.isoformat()),
            )

    def get_vehicle(self, vehicle_id: str) -> DispatchVehicle:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dispatch_vehicles WHERE vehicle_id=?", (vehicle_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown vehicle_id: {vehicle_id}")
        return DispatchVehicle(
            vehicle_id=row["vehicle_id"],
            availability=tuple(
                AvailabilityWindow(
                    datetime.fromisoformat(item["start_at"]),
                    datetime.fromisoformat(item["end_at"]),
                )
                for item in json.loads(row["availability_json"])
            ),
        )

    def add_calendar_assignment(
        self, assignment: CalendarAssignment, *, now: datetime
    ) -> None:
        require_aware(now, "now")
        with self.transaction() as connection:
            workflow = connection.execute(
                "SELECT job_id FROM workflow_instances WHERE workflow_instance_id=?",
                (assignment.workflow_instance_id,),
            ).fetchone()
            if workflow is None or workflow["job_id"] != assignment.job_id:
                raise CrossJobReferenceError(
                    "Calendar assignment workflow/job reference mismatch"
                )
            connection.execute(
                """INSERT INTO calendar_assignments(
                    assignment_id,job_id,workflow_instance_id,scheduled_task_id,
                    work_item_name,worker_id,required_skill_ids_json,vehicle_id,
                    start_at,end_at,hard_deadline,status,revision,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    assignment.assignment_id,
                    assignment.job_id,
                    assignment.workflow_instance_id,
                    assignment.scheduled_task_id,
                    assignment.work_item_name,
                    assignment.worker_id,
                    _json(assignment.required_skill_ids),
                    assignment.vehicle_id,
                    assignment.start_at.isoformat(),
                    assignment.end_at.isoformat(),
                    assignment.hard_deadline.isoformat() if assignment.hard_deadline else None,
                    assignment.status.value,
                    assignment.revision,
                    now.isoformat(),
                ),
            )

    @staticmethod
    def _calendar_from_row(row: sqlite3.Row) -> CalendarAssignment:
        return CalendarAssignment(
            assignment_id=row["assignment_id"],
            job_id=row["job_id"],
            workflow_instance_id=row["workflow_instance_id"],
            scheduled_task_id=row["scheduled_task_id"],
            work_item_name=row["work_item_name"],
            worker_id=row["worker_id"],
            required_skill_ids=tuple(json.loads(row["required_skill_ids_json"])),
            vehicle_id=row["vehicle_id"],
            start_at=datetime.fromisoformat(row["start_at"]),
            end_at=datetime.fromisoformat(row["end_at"]),
            hard_deadline=_dt(row["hard_deadline"]),
            status=CalendarStatus(row["status"]),
            revision=row["revision"],
        )

    def calendar_for_jobs(self, job_ids: tuple[str, ...]) -> tuple[CalendarAssignment, ...]:
        if not job_ids:
            return ()
        placeholders = ",".join("?" for _ in job_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM calendar_assignments WHERE job_id IN ({placeholders}) ORDER BY start_at,assignment_id",
                job_ids,
            ).fetchall()
        return tuple(self._calendar_from_row(row) for row in rows)

    def get_assignment(self, assignment_id: str) -> CalendarAssignment:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM calendar_assignments WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown assignment_id: {assignment_id}")
        return self._calendar_from_row(row)

    def put_material_readiness(self, value: MaterialReadiness) -> MaterialReadiness:
        with self.transaction() as connection:
            assignment = connection.execute(
                """SELECT job_id FROM calendar_assignments
                   WHERE job_id=? AND scheduled_task_id=?""",
                (value.job_id, value.scheduled_task_id),
            ).fetchone()
            if assignment is None:
                raise CrossJobReferenceError(
                    "Material readiness does not match a task in the same job"
                )
            connection.execute(
                """INSERT INTO material_readiness(
                    job_id,scheduled_task_id,status,available_at,blocking,source,revision,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,scheduled_task_id) DO UPDATE SET
                  status=excluded.status,available_at=excluded.available_at,
                  blocking=excluded.blocking,source=excluded.source,
                  revision=material_readiness.revision+1,updated_at=excluded.updated_at""",
                (
                    value.job_id,
                    value.scheduled_task_id,
                    value.status.value,
                    value.available_at.isoformat() if value.available_at else None,
                    int(value.blocking),
                    value.source,
                    value.revision,
                    value.updated_at.isoformat(),
                ),
            )
        return self.get_material_readiness(value.job_id, value.scheduled_task_id)

    def update_material_readiness_cas(
        self,
        *,
        job_id: str,
        scheduled_task_id: str,
        expected_revision: int,
        status: MaterialReadinessStatus,
        available_at: datetime | None,
        blocking: bool,
        source: str,
        now: datetime,
    ) -> MaterialReadiness:
        candidate = MaterialReadiness(
            job_id,
            scheduled_task_id,
            status,
            available_at,
            blocking,
            source,
            now,
            expected_revision,
        )
        with self.transaction() as connection:
            changed = connection.execute(
                """UPDATE material_readiness SET status=?,available_at=?,blocking=?,
                   source=?,updated_at=?,revision=revision+1
                   WHERE job_id=? AND scheduled_task_id=? AND revision=?""",
                (
                    candidate.status.value,
                    candidate.available_at.isoformat() if candidate.available_at else None,
                    int(candidate.blocking),
                    candidate.source,
                    candidate.updated_at.isoformat(),
                    job_id,
                    scheduled_task_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                exists = connection.execute(
                    "SELECT 1 FROM material_readiness WHERE job_id=? AND scheduled_task_id=?",
                    (job_id, scheduled_task_id),
                ).fetchone()
                if exists is None:
                    raise CrossJobReferenceError(
                        "Material task/job reference does not exist"
                    )
                raise StaleRevisionError("STALE material readiness revision")
        return self.get_material_readiness(job_id, scheduled_task_id)

    def get_material_readiness(self, job_id: str, task_id: str) -> MaterialReadiness:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM material_readiness WHERE job_id=? AND scheduled_task_id=?",
                (job_id, task_id),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown material readiness: {job_id}/{task_id}")
        return MaterialReadiness(
            job_id=row["job_id"],
            scheduled_task_id=row["scheduled_task_id"],
            status=MaterialReadinessStatus(row["status"]),
            available_at=_dt(row["available_at"]),
            blocking=bool(row["blocking"]),
            source=row["source"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            revision=row["revision"],
        )

    def materials_for_jobs(self, job_ids: tuple[str, ...]) -> tuple[MaterialReadiness, ...]:
        assignments = self.calendar_for_jobs(job_ids)
        values = []
        for item in assignments:
            try:
                values.append(self.get_material_readiness(item.job_id, item.scheduled_task_id))
            except NotFoundError:
                continue
        return tuple(values)

    def save_route_snapshot(self, value: RouteSnapshot) -> RouteSnapshot:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO route_snapshots(
                    route_snapshot_id,origin_reference,origin_fingerprint,
                    destination_reference,destination_fingerprint,transport_mode,
                    departure_time_basis,distance_meters,travel_duration_seconds,
                    provider,retrieved_at,status,input_fingerprint
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value.route_snapshot_id,
                    value.origin_reference,
                    value.origin_fingerprint,
                    value.destination_reference,
                    value.destination_fingerprint,
                    value.transport_mode,
                    value.departure_time_basis,
                    value.distance_meters,
                    value.travel_duration_seconds,
                    value.provider,
                    value.retrieved_at.isoformat(),
                    value.status.value,
                    value.input_fingerprint,
                ),
            )
            row = connection.execute(
                "SELECT route_snapshot_id FROM route_snapshots WHERE input_fingerprint=?",
                (value.input_fingerprint,),
            ).fetchone()
        return self.get_route_snapshot(row[0])

    def get_route_snapshot(self, route_snapshot_id: str) -> RouteSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM route_snapshots WHERE route_snapshot_id=?",
                (route_snapshot_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown route_snapshot_id: {route_snapshot_id}")
        return RouteSnapshot(
            route_snapshot_id=row["route_snapshot_id"],
            origin_reference=row["origin_reference"],
            origin_fingerprint=row["origin_fingerprint"],
            destination_reference=row["destination_reference"],
            destination_fingerprint=row["destination_fingerprint"],
            transport_mode=row["transport_mode"],
            departure_time_basis=row["departure_time_basis"],
            distance_meters=row["distance_meters"],
            travel_duration_seconds=row["travel_duration_seconds"],
            provider=row["provider"],
            retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            status=RouteSnapshotStatus(row["status"]),
            input_fingerprint=row["input_fingerprint"],
        )

    def routes_for_job_pairs(self, job_ids: tuple[str, ...]) -> tuple[RouteSnapshot, ...]:
        if not job_ids:
            return ()
        placeholders = ",".join("?" for _ in job_ids)
        parameters = (*job_ids, *job_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT route_snapshot_id FROM route_snapshots
                    WHERE origin_reference IN ({placeholders})
                      AND destination_reference IN ({placeholders})
                    ORDER BY route_snapshot_id""",
                parameters,
            ).fetchall()
        return tuple(self.get_route_snapshot(row[0]) for row in rows)

    def save_proposal(self, proposal: ReplanProposal) -> ReplanProposal:
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT proposal_id FROM replan_proposals WHERE proposal_fingerprint=?",
                (proposal.proposal_fingerprint,),
            ).fetchone()
            if existing is not None:
                return self.get_proposal(existing[0])
            affected_mapping: dict[str, str] = {}
            for job_id in proposal.affected_job_ids:
                rows = connection.execute(
                    """SELECT DISTINCT workflow_instance_id
                       FROM calendar_assignments
                       WHERE job_id=? AND assignment_id IN ({})""".format(
                        ",".join("?" for _ in proposal.affected_assignment_ids)
                    ),
                    (job_id, *proposal.affected_assignment_ids),
                ).fetchall()
                if len(rows) != 1 or rows[0][0] not in proposal.affected_workflow_instance_ids:
                    raise CrossJobReferenceError(
                        "Proposal affected job/workflow/task mapping is inconsistent"
                    )
                affected_mapping[job_id] = rows[0][0]
            content = _proposal_content(proposal)
            content["affected_job_workflows"] = affected_mapping
            connection.execute(
                """INSERT INTO replan_proposals(
                    proposal_id,initiating_workflow_instance_id,status,content_json,
                    proposal_fingerprint,expected_workflow_revisions_json,
                    expected_calendar_revisions_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    proposal.proposal_id,
                    proposal.initiating_workflow_instance_id,
                    proposal.status.value,
                    _json(content),
                    proposal.proposal_fingerprint,
                    _json(proposal.expected_workflow_revisions),
                    _json(proposal.expected_calendar_revisions),
                    proposal.created_at.isoformat(),
                ),
            )
            for job_id, workflow_id in affected_mapping.items():
                connection.execute(
                    "INSERT INTO replan_proposal_jobs(proposal_id,job_id,workflow_instance_id) VALUES(?,?,?)",
                    (proposal.proposal_id, job_id, workflow_id),
                )
            for assignment_id in proposal.affected_assignment_ids:
                connection.execute(
                    "INSERT INTO replan_proposal_tasks(proposal_id,assignment_id) VALUES(?,?)",
                    (proposal.proposal_id, assignment_id),
                )
        return self.get_proposal(proposal.proposal_id)

    def get_proposal(self, proposal_id: str) -> ReplanProposal:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM replan_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown proposal_id: {proposal_id}")
        content = json.loads(row["content_json"])
        return ReplanProposal(
            proposal_id=row["proposal_id"],
            initiating_workflow_instance_id=row["initiating_workflow_instance_id"],
            status=ReplanProposalStatus(row["status"]),
            affected_job_ids=tuple(content["affected_job_ids"]),
            affected_workflow_instance_ids=tuple(content["affected_workflow_instance_ids"]),
            affected_assignment_ids=tuple(content["affected_assignment_ids"]),
            before_schedule=tuple(_placement(item) for item in content["before_schedule"]),
            after_schedule=tuple(_placement(item) for item in content["after_schedule"]),
            reason_codes=tuple(content["reason_codes"]),
            material_readiness_references=tuple(content["material_readiness_references"]),
            route_snapshot_references=tuple(content["route_snapshot_references"]),
            expected_workflow_revisions=tuple(
                (item[0], int(item[1]))
                for item in json.loads(row["expected_workflow_revisions_json"])
            ),
            expected_calendar_revisions=tuple(
                (item[0], int(item[1]))
                for item in json.loads(row["expected_calendar_revisions_json"])
            ),
            worker_idle_time_before_seconds=content["worker_idle_time_before_seconds"],
            worker_idle_time_after_seconds=content["worker_idle_time_after_seconds"],
            travel_time_before_seconds=content["travel_time_before_seconds"],
            travel_time_after_seconds=content["travel_time_after_seconds"],
            deadline_impact=content["deadline_impact"],
            conditional=content["conditional"],
            proposal_fingerprint=row["proposal_fingerprint"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_proposals(self) -> tuple[ReplanProposal, ...]:
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT proposal_id FROM replan_proposals ORDER BY created_at,proposal_id")]
        return tuple(self.get_proposal(item) for item in ids)

    def create_replan_gate(
        self,
        *,
        proposal_id: str,
        session_id: str,
        agent_id: str,
        now: datetime,
    ) -> PersistentGate:
        proposal = self.get_proposal(proposal_id)
        if proposal.status is not ReplanProposalStatus.PENDING_OWNER_APPROVAL:
            raise GateConflictError("Proposal is not pending owner approval")
        binding = self.get_session_binding(proposal.initiating_workflow_instance_id)
        if binding.session_id != session_id or binding.agent_id != agent_id:
            raise RecoveryInconsistencyError("Session binding does not match replan gate")
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT gate_id FROM pending_gates WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if existing is not None:
                return self.get_gate(existing[0])
            gate_id = f"replan-gate-{uuid4().hex}"
            connection.execute(
                """INSERT INTO pending_gates(
                    gate_id,workflow_instance_id,gate_type,proposal_id,gate_fingerprint,
                    session_id,agent_id,interrupt_id,status,response_fingerprint,
                    decision_id,created_at,resolved_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    gate_id,
                    proposal.initiating_workflow_instance_id,
                    "REPLAN_OWNER_REVIEW",
                    proposal_id,
                    proposal.proposal_fingerprint,
                    session_id,
                    agent_id,
                    None,
                    PersistentGateStatus.PENDING.value,
                    None,
                    None,
                    now.isoformat(),
                    None,
                ),
            )
        return self.get_gate(gate_id)

    def save_pending_gate(self, gate: PersistentGate) -> PersistentGate:
        """Persist a recovered/legacy owner gate after validating its workflow binding."""

        if gate.status is not PersistentGateStatus.PENDING:
            raise GateInputError("Only a PENDING gate can be initially persisted")
        workflow = self.get_workflow(gate.workflow_instance_id)
        binding = self.get_session_binding(gate.workflow_instance_id)
        if gate.session_id != binding.session_id or gate.agent_id != binding.agent_id:
            raise RecoveryInconsistencyError("Gate does not match session binding")
        if gate.proposal_id is not None:
            proposal = self.get_proposal(gate.proposal_id)
            if proposal.initiating_workflow_instance_id != workflow.workflow_instance_id:
                raise CrossJobReferenceError("Gate proposal belongs to another workflow")
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO pending_gates(
                    gate_id,workflow_instance_id,gate_type,proposal_id,gate_fingerprint,
                    session_id,agent_id,interrupt_id,status,response_fingerprint,
                    decision_id,created_at,resolved_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    gate.gate_id,
                    gate.workflow_instance_id,
                    gate.gate_type,
                    gate.proposal_id,
                    gate.gate_fingerprint,
                    gate.session_id,
                    gate.agent_id,
                    gate.interrupt_id,
                    gate.status.value,
                    gate.response_fingerprint,
                    gate.decision_id,
                    gate.created_at.isoformat(),
                    gate.resolved_at.isoformat() if gate.resolved_at else None,
                ),
            )
        return self.get_gate(gate.gate_id)

    def get_gate(self, gate_id: str) -> PersistentGate:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pending_gates WHERE gate_id=?", (gate_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown gate_id: {gate_id}")
        return PersistentGate(
            gate_id=row["gate_id"],
            workflow_instance_id=row["workflow_instance_id"],
            gate_type=row["gate_type"],
            proposal_id=row["proposal_id"],
            gate_fingerprint=row["gate_fingerprint"],
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            interrupt_id=row["interrupt_id"],
            status=PersistentGateStatus(row["status"]),
            response_fingerprint=row["response_fingerprint"],
            decision_id=row["decision_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=_dt(row["resolved_at"]),
        )

    def pending_gate_for_workflow(self, workflow_instance_id: str) -> PersistentGate | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT gate_id FROM pending_gates
                   WHERE workflow_instance_id=? AND status IN ('PENDING','RESUMING')
                   ORDER BY created_at DESC LIMIT 1""",
                (workflow_instance_id,),
            ).fetchone()
        return self.get_gate(row[0]) if row else None

    def bind_gate_interrupt(self, *, gate_id: str, interrupt_id: str) -> PersistentGate:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pending_gates WHERE gate_id=?", (gate_id,)
            ).fetchone()
            if row is None or row["status"] != "PENDING":
                raise GateConflictError("Gate is not PENDING")
            if row["interrupt_id"] not in (None, interrupt_id):
                raise GateConflictError("Gate is bound to a different interrupt")
            connection.execute(
                "UPDATE pending_gates SET interrupt_id=? WHERE gate_id=?",
                (interrupt_id, gate_id),
            )
        return self.get_gate(gate_id)

    def recover_pending_gate(
        self,
        *,
        workflow_instance_id: str,
        session_storage_dir: str,
    ) -> PersistentGate | None:
        gate = self.pending_gate_for_workflow(workflow_instance_id)
        if gate is None:
            return None
        binding = self.get_session_binding(workflow_instance_id)
        if gate.session_id != binding.session_id or gate.agent_id != binding.agent_id:
            raise RecoveryInconsistencyError("Gate/session binding mismatch")
        if gate.interrupt_id is None:
            raise RecoveryInconsistencyError("Pending gate has no interrupt_id")
        session_file = Path(session_storage_dir) / f"session_{binding.session_id}" / "session.json"
        if not session_file.is_file():
            raise RecoveryInconsistencyError(
                "SQLite has a pending gate but the Strands session is unavailable"
            )
        return gate

    def claim_replan_response(
        self,
        *,
        gate_id: str,
        action: str,
    ) -> ReplanApprovalClaim:
        if action not in {"APPROVE_REPLAN", "REJECT_REPLAN"}:
            raise GateInputError("Unknown replan owner action")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pending_gates WHERE gate_id=?", (gate_id,)
            ).fetchone()
            if row is None or row["gate_type"] != "REPLAN_OWNER_REVIEW":
                raise GateConflictError("Unknown replan gate")
            canonical = {
                "gate_id": gate_id,
                "proposal_id": row["proposal_id"],
                "action": action,
                "proposal_fingerprint": row["gate_fingerprint"],
            }
            response_fingerprint = _hash(
                {"schema": "werkcrew-replan-response-v1", **canonical}
            )
            if row["status"] == "RESOLVED":
                decision = self.get_owner_decision(row["decision_id"])
                if row["response_fingerprint"] == response_fingerprint:
                    return ReplanApprovalClaim(None, None, decision)
                raise GateConflictError("Gate was resolved with a different response")
            if row["status"] != "PENDING":
                raise GateConflictError(f"Gate cannot be claimed from {row['status']}")
            if not row["interrupt_id"]:
                raise RecoveryInconsistencyError("Pending gate has no interrupt_id")
            proposal = connection.execute(
                "SELECT status,proposal_fingerprint FROM replan_proposals WHERE proposal_id=?",
                (row["proposal_id"],),
            ).fetchone()
            if (
                proposal is None
                or proposal["status"] != "PENDING_OWNER_APPROVAL"
                or proposal["proposal_fingerprint"] != row["gate_fingerprint"]
            ):
                connection.execute(
                    "UPDATE pending_gates SET status='INVALIDATED' WHERE gate_id=?",
                    (gate_id,),
                )
                raise GateConflictError("Replan proposal is stale or inconsistent")
            connection.execute(
                """UPDATE pending_gates SET status='RESUMING',response_fingerprint=?
                   WHERE gate_id=?""",
                (response_fingerprint, gate_id),
            )
            return ReplanApprovalClaim(canonical, row["interrupt_id"], None)

    def get_owner_decision(self, decision_id: str) -> PersistentOwnerDecision:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM owner_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown decision_id: {decision_id}")
        return PersistentOwnerDecision(
            decision_id=row["decision_id"],
            gate_id=row["gate_id"],
            workflow_instance_id=row["workflow_instance_id"],
            proposal_id=row["proposal_id"],
            action=row["action"],
            selected_plan_id=row["selected_plan_id"],
            decision_fingerprint=row["decision_fingerprint"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
            actor_role=row["actor_role"],
            source=row["source"],
        )

    def owner_decisions(self, *, proposal_id: str | None = None) -> tuple[PersistentOwnerDecision, ...]:
        with self._connect() as connection:
            if proposal_id is None:
                rows = connection.execute("SELECT decision_id FROM owner_decisions ORDER BY decided_at").fetchall()
            else:
                rows = connection.execute(
                    "SELECT decision_id FROM owner_decisions WHERE proposal_id=? ORDER BY decided_at",
                    (proposal_id,),
                ).fetchall()
        return tuple(self.get_owner_decision(row[0]) for row in rows)

    def apply_replan_response(
        self,
        response: Mapping[str, Any],
        *,
        now: datetime,
    ) -> PersistentOwnerDecision:
        require_aware(now, "now")
        required = {"gate_id", "proposal_id", "action", "proposal_fingerprint"}
        if set(response) != required:
            raise GateInputError("Canonical replan response has unexpected fields")
        action = response["action"]
        if action not in {"APPROVE_REPLAN", "REJECT_REPLAN"}:
            raise GateInputError("Unknown replan owner action")
        response_fingerprint = _hash(
            {"schema": "werkcrew-replan-response-v1", **dict(response)}
        )
        operation_id = "replan-owner-response:" + response_fingerprint
        stale_message: str | None = None
        decision_id = "owner-decision-" + response_fingerprint.removeprefix("sha256:")[:24]

        with self.transaction() as connection:
            existing_operation = connection.execute(
                "SELECT result_json FROM idempotency_records WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if existing_operation is not None:
                result = json.loads(existing_operation["result_json"])
                return self.get_owner_decision(result["decision_id"])
            gate = connection.execute(
                "SELECT * FROM pending_gates WHERE gate_id=?", (response["gate_id"],)
            ).fetchone()
            if gate is None or gate["status"] != "RESUMING":
                raise GateConflictError("Replan commit requires RESUMING gate")
            if (
                gate["proposal_id"] != response["proposal_id"]
                or gate["gate_fingerprint"] != response["proposal_fingerprint"]
                or gate["response_fingerprint"] != response_fingerprint
            ):
                raise GateConflictError("Canonical response does not match claimed gate")
            proposal_row = connection.execute(
                "SELECT * FROM replan_proposals WHERE proposal_id=?",
                (response["proposal_id"],),
            ).fetchone()
            if proposal_row is None or proposal_row["status"] != "PENDING_OWNER_APPROVAL":
                raise GateConflictError("Proposal cannot be decided")
            content = json.loads(proposal_row["content_json"])
            expected_workflows = {
                item[0]: int(item[1])
                for item in json.loads(proposal_row["expected_workflow_revisions_json"])
            }
            expected_calendar = {
                item[0]: int(item[1])
                for item in json.loads(proposal_row["expected_calendar_revisions_json"])
            }
            for workflow_id, expected in expected_workflows.items():
                row = connection.execute(
                    "SELECT revision FROM workflow_instances WHERE workflow_instance_id=?",
                    (workflow_id,),
                ).fetchone()
                if row is None or row["revision"] != expected:
                    stale_message = f"STALE workflow revision: {workflow_id}"
                    break
            if stale_message is None:
                for assignment_id, expected in expected_calendar.items():
                    row = connection.execute(
                        "SELECT revision FROM calendar_assignments WHERE assignment_id=?",
                        (assignment_id,),
                    ).fetchone()
                    if row is None or row["revision"] != expected:
                        stale_message = f"STALE calendar revision: {assignment_id}"
                        break
            if stale_message is not None:
                connection.execute(
                    "UPDATE replan_proposals SET status='STALE',decided_at=? WHERE proposal_id=?",
                    (now.isoformat(), response["proposal_id"]),
                )
                connection.execute(
                    "UPDATE pending_gates SET status='INVALIDATED',resolved_at=? WHERE gate_id=?",
                    (now.isoformat(), response["gate_id"]),
                )
                self._insert_trace_rows(
                    connection,
                    content=content,
                    now=now,
                    action="Replan rejected as stale",
                    result_summary=stale_message,
                    reason_code="REPLAN_STALE",
                    success=False,
                    gate_id=response["gate_id"],
                    decision_id=None,
                    proposal_id=response["proposal_id"],
                    operation_id=operation_id,
                )
            else:
                decision = PersistentOwnerDecision(
                    decision_id=decision_id,
                    gate_id=response["gate_id"],
                    workflow_instance_id=gate["workflow_instance_id"],
                    proposal_id=response["proposal_id"],
                    action=action,
                    selected_plan_id=None,
                    decision_fingerprint=response_fingerprint,
                    decided_at=now,
                    actor_role="OWNER",
                    source="COORDINATOR_UI",
                )
                connection.execute(
                    """INSERT INTO owner_decisions(
                        decision_id,gate_id,workflow_instance_id,proposal_id,action,
                        selected_plan_id,decision_fingerprint,decided_at,actor_role,source
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        decision.decision_id,
                        decision.gate_id,
                        decision.workflow_instance_id,
                        decision.proposal_id,
                        decision.action,
                        None,
                        decision.decision_fingerprint,
                        now.isoformat(),
                        decision.actor_role,
                        decision.source,
                    ),
                )
                if action == "APPROVE_REPLAN":
                    after_by_id = {
                        item["assignment_id"]: item for item in content["after_schedule"]
                    }
                    for assignment_id, expected in expected_calendar.items():
                        placement = after_by_id[assignment_id]
                        changed = connection.execute(
                            """UPDATE calendar_assignments
                               SET start_at=?,end_at=?,revision=revision+1,updated_at=?
                               WHERE assignment_id=? AND revision=?""",
                            (
                                placement["start_at"],
                                placement["end_at"],
                                now.isoformat(),
                                assignment_id,
                                expected,
                            ),
                        ).rowcount
                        if changed != 1:
                            raise StaleRevisionError("CAS failed during calendar apply")
                    for workflow_id, expected in expected_workflows.items():
                        changed = connection.execute(
                            """UPDATE workflow_instances SET revision=revision+1,updated_at=?
                               WHERE workflow_instance_id=? AND revision=?""",
                            (now.isoformat(), workflow_id, expected),
                        ).rowcount
                        if changed != 1:
                            raise StaleRevisionError("CAS failed during workflow apply")
                    proposal_status = "APPLIED"
                    reason_code = "REPLAN_APPLIED"
                    summary = "Owner-approved assignment order applied atomically."
                else:
                    proposal_status = "REJECTED"
                    reason_code = "REPLAN_REJECTED"
                    summary = "Owner rejected the proposal; confirmed calendar was preserved."
                connection.execute(
                    "UPDATE replan_proposals SET status=?,decided_at=? WHERE proposal_id=?",
                    (proposal_status, now.isoformat(), response["proposal_id"]),
                )
                connection.execute(
                    """UPDATE pending_gates SET status='RESOLVED',decision_id=?,resolved_at=?
                       WHERE gate_id=?""",
                    (decision_id, now.isoformat(), response["gate_id"]),
                )
                connection.execute(
                    "INSERT INTO idempotency_records(operation_id,operation_type,result_json,created_at) VALUES(?,?,?,?)",
                    (
                        operation_id,
                        "REPLAN_OWNER_RESPONSE",
                        _json({"decision_id": decision_id}),
                        now.isoformat(),
                    ),
                )
                self._insert_trace_rows(
                    connection,
                    content=content,
                    now=now,
                    action="Owner replan decision recorded",
                    result_summary=summary,
                    reason_code=reason_code,
                    success=True,
                    gate_id=response["gate_id"],
                    decision_id=decision_id,
                    proposal_id=response["proposal_id"],
                    operation_id=operation_id,
                )

        if stale_message is not None:
            raise StaleRevisionError(stale_message)
        return self.get_owner_decision(decision_id)

    def _insert_trace_rows(
        self,
        connection: sqlite3.Connection,
        *,
        content: Mapping[str, Any],
        now: datetime,
        action: str,
        result_summary: str,
        reason_code: str,
        success: bool,
        gate_id: str | None,
        decision_id: str | None,
        proposal_id: str | None,
        operation_id: str | None,
    ) -> None:
        correlation_id = f"replan:{proposal_id}"
        workflows = content["affected_job_workflows"]
        for job_id in content["affected_job_ids"]:
            connection.execute(
                """INSERT INTO agent_trace_events(
                    event_id,timestamp,job_id,workflow_instance_id,actor,action,
                    redacted_input_summary,result_summary,previous_state,next_state,
                    reason_code,rule_version,external_data_source,success,gate_id,
                    owner_decision_id,proposal_id,operation_id,correlation_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"trace-{uuid4().hex}",
                    now.isoformat(),
                    job_id,
                    workflows[job_id],
                    TraceActor.HUMAN.value,
                    action,
                    "Owner action resolved from trusted gate/proposal state.",
                    result_summary,
                    None,
                    None,
                    reason_code,
                    "daily-dispatch-v1",
                    None,
                    int(success),
                    gate_id,
                    decision_id,
                    proposal_id,
                    operation_id,
                    correlation_id,
                ),
            )

    def append_trace(self, event: AgentTraceEvent) -> None:
        require_aware(event.timestamp, "trace.timestamp")
        self._validate_public_trace(event)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO agent_trace_events(
                    event_id,timestamp,job_id,workflow_instance_id,actor,action,
                    redacted_input_summary,result_summary,previous_state,next_state,
                    reason_code,rule_version,external_data_source,success,gate_id,
                    owner_decision_id,proposal_id,operation_id,correlation_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.job_id,
                    event.workflow_instance_id,
                    event.actor.value,
                    event.action,
                    event.redacted_input_summary,
                    event.result_summary,
                    event.previous_state,
                    event.next_state,
                    event.reason_code,
                    event.rule_version,
                    event.external_data_source,
                    int(event.success),
                    event.gate_id,
                    event.owner_decision_id,
                    event.proposal_id,
                    event.operation_id,
                    event.correlation_id,
                ),
            )

    def _validate_public_trace(self, event: AgentTraceEvent) -> None:
        text = " ".join(
            (event.action, event.redacted_input_summary, event.result_summary)
        ).lower()
        forbidden = ("chain-of-thought", "system prompt", "raw provider response", "secret")
        if any(value in text for value in forbidden):
            raise ValueError("Public trace contains forbidden private content")
        job = self.get_job(event.job_id)
        sensitive = [
            f"{job.address.street} {job.address.house_number}".lower(),
            job.address.postal_code.lower(),
        ]
        if job.coordinates is not None:
            sensitive.extend(
                [job.coordinates.latitude.lower(), job.coordinates.longitude.lower()]
            )
        if any(value and value in text for value in sensitive):
            raise ValueError("Public trace contains address or exact coordinates")

    def trace_for_job(self, job_id: str) -> tuple[AgentTraceEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_trace_events WHERE job_id=? ORDER BY timestamp,event_id",
                (job_id,),
            ).fetchall()
        return tuple(
            AgentTraceEvent(
                event_id=row["event_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                job_id=row["job_id"],
                workflow_instance_id=row["workflow_instance_id"],
                actor=TraceActor(row["actor"]),
                action=row["action"],
                redacted_input_summary=row["redacted_input_summary"],
                result_summary=row["result_summary"],
                previous_state=row["previous_state"],
                next_state=row["next_state"],
                reason_code=row["reason_code"],
                rule_version=row["rule_version"],
                external_data_source=row["external_data_source"],
                success=bool(row["success"]),
                gate_id=row["gate_id"],
                owner_decision_id=row["owner_decision_id"],
                proposal_id=row["proposal_id"],
                operation_id=row["operation_id"],
                correlation_id=row["correlation_id"],
            )
            for row in rows
        )
