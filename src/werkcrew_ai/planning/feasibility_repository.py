"""Host-owned M3-C0 feasibility evidence persistence and snapshot producer."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from werkcrew_ai.field.repository import M2DurableRepository, M2PersistenceError
from werkcrew_ai.field.serialization import serialize_worker_registry
from werkcrew_ai.planning.evaluation_repository import M3EvaluationRepository
from werkcrew_ai.planning.feasibility_support import (
    FEASIBILITY_SUPPORT_RULE_VERSION,
    AvailabilityEvidence,
    AvailabilityKnowledge,
    FeasibilitySource,
    FeasibilitySourceKind,
    FeasibilitySupportSnapshot,
    M3FeasibilitySupportConflictError,
    M3FeasibilitySupportStorageError,
    M3FeasibilitySupportValidationError,
    PlanScheduleSource,
    ReadinessEvidence,
    ReadinessState,
    RouteEvidence,
    RouteKnowledge,
    RouteSource,
    SourceSelection,
    SourceSelectionCut,
    TaskConstraintSource,
    TaskReadinessSource,
    VehicleAvailabilitySource,
    VehicleTechnicalEvidence,
    WorkerAvailabilitySource,
    WorkerRegistryProvenance,
    WorkerRegistryProvenanceCapture,
    WorkerSkillLevelEvidence,
    WorkerTechnicalEvidence,
    current_m8_configuration,
    m8_feasibility_configuration_fingerprint,
    m8_feasibility_configuration_json,
    restore_m8_feasibility_configuration,
    restore_feasibility_support,
    restore_source_selection_cut,
    restore_source,
    restore_worker_registry_provenance,
    restore_worker_registry_capture,
    route_subject_fingerprint,
    schedule_subject_fingerprint,
    snapshot_semantic_json,
    source_cut_semantic_json,
    source_semantic_json,
    task_subject_fingerprint,
    task_readiness_subject_fingerprint,
    vehicle_subject_fingerprint,
    worker_subject_fingerprint,
    worker_registry_provenance_semantic_json,
    worker_registry_capture_semantic_json,
)


ROUTE_TRANSPORT_MODE = "CAR"


class M3FeasibilitySupportRepository(M3EvaluationRepository):
    """Privileged source adapters and deterministic read-model materializer.

    Individual source records may be supplied only to these host/bootstrap
    methods. Snapshot creation accepts an EvaluationInput identity, never a
    caller-composed aggregate, mapping, placement set or authority credential.
    """

    @staticmethod
    def _worker_registry_provenance_from_row(row) -> WorkerRegistryProvenance:
        value = restore_worker_registry_provenance(
            row["canonical_semantic_json"],
            row["worker_registry_provenance_fingerprint"],
            row["worker_registry_provenance_id"],
        )
        actual = (
            row["schema_version"],
            row["worker_registry_schema_version"],
            row["worker_registry_revision"],
            row["worker_registry_fingerprint"],
            row["canonical_worker_registry_json"],
        )
        expected = (
            value.schema_version,
            value.worker_registry_schema_version,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
            value.canonical_worker_registry_json,
        )
        if actual != expected:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_PROVENANCE_METADATA_MISMATCH",
                "worker_registry_provenance",
            )
        return value

    def _worker_registry_provenance_by_id(
        self, connection, provenance_id: str
    ) -> WorkerRegistryProvenance:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_worker_registry_provenance
            WHERE worker_registry_provenance_id=?
            """,
            (provenance_id,),
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_PROVENANCE_NOT_FOUND",
                "worker_registry_provenance_id",
            )
        return self._worker_registry_provenance_from_row(row)

    @staticmethod
    def _worker_registry_capture_from_row(
        row,
    ) -> WorkerRegistryProvenanceCapture:
        value = restore_worker_registry_capture(
            row["canonical_semantic_json"],
            row["worker_registry_capture_fingerprint"],
            row["worker_registry_capture_id"],
        )
        actual = (
            row["ledger_generation"],
            row["capture_kind"],
            row["schema_version"],
            row["worker_registry_provenance_id"],
            row["worker_registry_provenance_fingerprint"],
            row["worker_registry_revision"],
            row["worker_registry_fingerprint"],
        )
        expected = (
            value.ledger_generation,
            value.capture_kind,
            value.schema_version,
            value.worker_registry_provenance_id,
            value.worker_registry_provenance_fingerprint,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
        )
        if actual != expected:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_CAPTURE_METADATA_MISMATCH",
                "worker_registry_capture",
            )
        return value

    def _worker_registry_capture_by_id(
        self, connection, capture_id: str
    ) -> WorkerRegistryProvenanceCapture:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_worker_registry_captures
            WHERE worker_registry_capture_id=?
            """,
            (capture_id,),
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_CAPTURE_NOT_FOUND",
                "worker_registry_capture_id",
            )
        return self._worker_registry_capture_from_row(row)

    def _capture_current_worker_registry(
        self, connection
    ) -> tuple[WorkerRegistryProvenance, WorkerRegistryProvenanceCapture]:
        try:
            registry = M2DurableRepository(
                self.database_path
            )._worker_registry_from_connection(connection)
        except (M2PersistenceError, ValueError) as error:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_INVALID", "worker_registry"
            ) from error
        row = connection.execute(
            "SELECT * FROM m2_worker_registry WHERE registry_key='GLOBAL'"
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_MISSING", "worker_registry"
            )
        raw = serialize_worker_registry(registry)
        if (
            row["root_schema_version"],
            row["registry_revision"],
            row["canonical_root_json"],
        ) != (
            "m2-worker-registry-v1",
            registry.registry_revision,
            raw,
        ):
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_AUTHORITY_MISMATCH", "worker_registry"
            )
        value = WorkerRegistryProvenance(
            worker_registry_revision=registry.registry_revision,
            worker_registry_fingerprint=row["content_sha256"],
            canonical_worker_registry_json=raw,
        )
        existing = connection.execute(
            """
            SELECT * FROM m3_feasibility_worker_registry_provenance
            WHERE worker_registry_provenance_id=?
            """,
            (value.worker_registry_provenance_id,),
        ).fetchone()
        if existing is not None:
            restored = self._worker_registry_provenance_from_row(existing)
            if restored != value:
                raise M3FeasibilitySupportConflictError(
                    "WORKER_REGISTRY_PROVENANCE_REPLAY_CONFLICT",
                    "worker_registry_provenance_id",
                )
            value = restored
        else:
            connection.execute(
                """
                INSERT INTO m3_feasibility_worker_registry_provenance(
                    worker_registry_provenance_id,
                    worker_registry_provenance_fingerprint,
                    schema_version, worker_registry_schema_version,
                    worker_registry_revision, worker_registry_fingerprint,
                    canonical_worker_registry_json, canonical_semantic_json
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    value.worker_registry_provenance_id,
                    value.worker_registry_provenance_fingerprint,
                    value.schema_version,
                    value.worker_registry_schema_version,
                    value.worker_registry_revision,
                    value.worker_registry_fingerprint,
                    value.canonical_worker_registry_json,
                    worker_registry_provenance_semantic_json(value),
                ),
            )

        self._validate_feasibility_ledger_chronology(connection)
        capture_row = connection.execute(
            """
            SELECT * FROM m3_feasibility_worker_registry_captures
            WHERE worker_registry_provenance_id=?
            """,
            (value.worker_registry_provenance_id,),
        ).fetchone()
        if capture_row is not None:
            capture = self._worker_registry_capture_from_row(capture_row)
            if (
                capture.worker_registry_provenance_id,
                capture.worker_registry_provenance_fingerprint,
                capture.worker_registry_revision,
                capture.worker_registry_fingerprint,
            ) != (
                value.worker_registry_provenance_id,
                value.worker_registry_provenance_fingerprint,
                value.worker_registry_revision,
                value.worker_registry_fingerprint,
            ):
                raise M3FeasibilitySupportStorageError(
                    "WORKER_REGISTRY_CAPTURE_BINDING_MISMATCH",
                    "worker_registry_capture",
                )
            return value, capture

        capture = WorkerRegistryProvenanceCapture(
            ledger_generation=self._source_cut_generation(connection) + 1,
            worker_registry_provenance_id=value.worker_registry_provenance_id,
            worker_registry_provenance_fingerprint=(
                value.worker_registry_provenance_fingerprint
            ),
            worker_registry_revision=value.worker_registry_revision,
            worker_registry_fingerprint=value.worker_registry_fingerprint,
        )
        connection.execute(
            """
            INSERT INTO m3_feasibility_worker_registry_captures(
                worker_registry_capture_id,
                worker_registry_capture_fingerprint, ledger_generation,
                capture_kind, schema_version,
                worker_registry_provenance_id,
                worker_registry_provenance_fingerprint,
                worker_registry_revision, worker_registry_fingerprint,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                capture.worker_registry_capture_id,
                capture.worker_registry_capture_fingerprint,
                capture.ledger_generation,
                capture.capture_kind,
                capture.schema_version,
                capture.worker_registry_provenance_id,
                capture.worker_registry_provenance_fingerprint,
                capture.worker_registry_revision,
                capture.worker_registry_fingerprint,
                worker_registry_capture_semantic_json(capture),
            ),
        )
        return value, capture

    @staticmethod
    def _m8_from_row(row) -> dict:
        document = restore_m8_feasibility_configuration(
            row["canonical_semantic_json"], row["m8_configuration_fingerprint"]
        )
        actual = (
            row["schema_version"],
            row["service_catalog_version"],
            row["planning_profile_version"],
            row["skill_matrix_version"],
            row["vehicle_policy_version"],
        )
        expected = (
            document["schema_version"],
            document["service_catalog_version"],
            document["planning_profile_version"],
            document["skill_matrix_version"],
            document["vehicle_policy_version"],
        )
        if actual != expected:
            raise M3FeasibilitySupportStorageError(
                "M8_CONFIGURATION_METADATA_MISMATCH", "m8_configuration"
            )
        return document

    def _m8_by_fingerprint(self, connection, fingerprint: str) -> dict:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_m8_configurations
            WHERE m8_configuration_fingerprint=?
            """,
            (fingerprint,),
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportStorageError(
                "M8_CONFIGURATION_NOT_FOUND", "m8_configuration_fingerprint"
            )
        return self._m8_from_row(row)

    def _ensure_current_m8(self, connection) -> dict:
        configuration = current_m8_configuration()
        raw = m8_feasibility_configuration_json(configuration)
        fingerprint = m8_feasibility_configuration_fingerprint(configuration)
        document = restore_m8_feasibility_configuration(raw, fingerprint)
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_m8_configurations
            WHERE m8_configuration_fingerprint=?
            """,
            (fingerprint,),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO m3_feasibility_m8_configurations(
                    m8_configuration_fingerprint, schema_version,
                    service_catalog_version, planning_profile_version,
                    skill_matrix_version, vehicle_policy_version,
                    canonical_semantic_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    fingerprint,
                    document["schema_version"],
                    document["service_catalog_version"],
                    document["planning_profile_version"],
                    document["skill_matrix_version"],
                    document["vehicle_policy_version"],
                    raw,
                ),
            )
            return document
        restored = self._m8_from_row(row)
        if restored != document:
            raise M3FeasibilitySupportStorageError(
                "M8_CONFIGURATION_REPLAY_MISMATCH", "m8_configuration"
            )
        return restored

    def _ensure_current_m8(self, connection) -> dict:
        configuration = current_m8_configuration()
        raw = m8_feasibility_configuration_json(configuration)
        fingerprint = m8_feasibility_configuration_fingerprint(configuration)
        document = restore_m8_feasibility_configuration(raw, fingerprint)
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_m8_configurations
            WHERE m8_configuration_fingerprint=?
            """,
            (fingerprint,),
        ).fetchone()
        if row is not None:
            if self._m8_from_row(row) != document:
                raise M3FeasibilitySupportStorageError(
                    "M8_CONFIGURATION_REPLAY_CONFLICT", "m8_configuration"
                )
            return document
        connection.execute(
            """
            INSERT INTO m3_feasibility_m8_configurations(
                m8_configuration_fingerprint, schema_version,
                service_catalog_version, planning_profile_version,
                skill_matrix_version, vehicle_policy_version,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                fingerprint,
                document["schema_version"],
                document["service_catalog_version"],
                document["planning_profile_version"],
                document["skill_matrix_version"],
                document["vehicle_policy_version"],
                raw,
            ),
        )
        return document

    @staticmethod
    def _m8_profile(configuration: dict, sku: str) -> dict:
        matches = tuple(
            item for item in configuration["profiles"] if item["sku"] == sku
        )
        if len(matches) != 1:
            raise M3FeasibilitySupportStorageError(
                "M8_PROFILE_NOT_FOUND", "m8_sku"
            )
        return matches[0]

    @staticmethod
    def _source_from_row(row) -> FeasibilitySource:
        value = restore_source(
            row["canonical_semantic_json"],
            row["source_fingerprint"],
            row["source_record_id"],
        )
        actual = (
            row["source_kind"],
            row["subject_fingerprint"],
            row["source_revision"],
            row["previous_source_record_id"],
            row["schema_version"],
        )
        expected = (
            value.source_kind.value,
            value.subject_fingerprint,
            value.source_revision,
            value.previous_source_record_id,
            value.schema_version,
        )
        if actual != expected:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_METADATA_MISMATCH", "source"
            )
        if type(row["ledger_generation"]) is not int or row["ledger_generation"] < 1:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_LEDGER_GENERATION_INVALID", "ledger_generation"
            )
        return value

    def _latest_source(
        self,
        connection,
        kind: FeasibilitySourceKind,
        subject_fingerprint: str,
    ) -> FeasibilitySource | None:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_source_records
            WHERE source_kind=? AND subject_fingerprint=?
            ORDER BY source_revision DESC LIMIT 1
            """,
            (kind.value, subject_fingerprint),
        ).fetchone()
        return None if row is None else self._source_from_row(row)

    def _latest_source_at_cut(
        self,
        connection,
        kind: FeasibilitySourceKind,
        subject_fingerprint: str,
        source_cut_generation: int,
    ) -> FeasibilitySource | None:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_source_records
            WHERE source_kind=? AND subject_fingerprint=?
              AND ledger_generation <= ?
            ORDER BY ledger_generation DESC LIMIT 1
            """,
            (kind.value, subject_fingerprint, source_cut_generation),
        ).fetchone()
        return None if row is None else self._source_from_row(row)

    @staticmethod
    def _source_cut_generation(connection) -> int:
        return connection.execute(
            """
            SELECT COALESCE(max(ledger_generation), 0)
            FROM (
                SELECT ledger_generation FROM m3_feasibility_source_records
                UNION ALL
                SELECT ledger_generation
                FROM m3_feasibility_worker_registry_captures
            )
            """
        ).fetchone()[0]

    @staticmethod
    def _validate_feasibility_ledger_chronology(connection) -> None:
        generations = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT ledger_generation
                FROM (
                    SELECT ledger_generation FROM m3_feasibility_source_records
                    UNION ALL
                    SELECT ledger_generation
                    FROM m3_feasibility_worker_registry_captures
                )
                ORDER BY ledger_generation
                """
            ).fetchall()
        )
        if generations != tuple(range(1, len(generations) + 1)):
            raise M3FeasibilitySupportStorageError(
                "FEASIBILITY_LEDGER_CHRONOLOGY_INVALID",
                "ledger_generation",
            )

    def _source_by_id(self, connection, source_record_id: str) -> FeasibilitySource:
        row = connection.execute(
            "SELECT * FROM m3_feasibility_source_records WHERE source_record_id=?",
            (source_record_id,),
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_NOT_FOUND", "source_record_id"
            )
        return self._source_from_row(row)

    def _canonical_task(self, connection, job_id: str, task_id: str):
        row = connection.execute(
            "SELECT * FROM m2_job_execution_roots WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportConflictError(
                "CANONICAL_TASK_NOT_FOUND", "task_id"
            )
        try:
            root = M2DurableRepository(self.database_path)._job_root_from_connection(
                connection, row
            )
        except (M2PersistenceError, ValueError) as error:
            raise M3FeasibilitySupportStorageError(
                "CANONICAL_TASK_INVALID", "task_id"
            ) from error
        task = root.task(task_id)
        if task is None:
            raise M3FeasibilitySupportConflictError(
                "CANONICAL_TASK_NOT_FOUND", "task_id"
            )
        return task

    def _validate_source(
        self, connection, value: FeasibilitySource, *, m8_configuration: dict | None = None
    ) -> None:
        if type(value) is TaskConstraintSource:
            task = self._canonical_task(connection, value.job_id, value.task_id)
            expected = (
                value.task_definition_version,
                value.source_handoff_id,
                value.source_revision_number,
            )
            actual = (
                task.definition.definition_version,
                task.definition.source_handoff_id,
                task.definition.source_revision,
            )
            if actual != expected:
                raise M3FeasibilitySupportConflictError(
                    "CANONICAL_TASK_BINDING_MISMATCH", "task_id"
                )
            prior = self._latest_source(
                connection,
                FeasibilitySourceKind.TASK_CONSTRAINT,
                value.subject_fingerprint,
            )
            if prior is not None and prior.m8_sku != value.m8_sku:
                raise M3FeasibilitySupportConflictError(
                    "TASK_M8_BINDING_CHANGED", "m8_sku"
                )
            configuration = self._m8_by_fingerprint(
                connection, value.m8_configuration_fingerprint
            )
            profile = self._m8_profile(configuration, value.m8_sku)
            if (
                value.m8_service_catalog_version,
                value.m8_planning_profile_version,
                value.required_skill_key,
                value.minimum_skill_level,
                value.required_vehicle_class,
            ) != (
                configuration["service_catalog_version"],
                configuration["planning_profile_version"],
                profile["required_skill_key"],
                profile["minimum_skill_level"],
                profile["vehicle_class"],
            ):
                raise M3FeasibilitySupportConflictError(
                    "TASK_M8_PROVENANCE_MISMATCH", "m8_configuration"
                )
            return
        if type(value) is PlanScheduleSource:
            history = self._history(connection, value.company_plan_id)
            if value.base_plan_revision >= len(history):
                raise M3FeasibilitySupportConflictError(
                    "BASE_PLAN_REVISION_NOT_FOUND", "base_plan_revision"
                )
            revision = history[value.base_plan_revision]
            if (
                revision.revision_id,
                revision.fingerprint,
            ) != (
                value.base_plan_revision_id,
                value.base_plan_revision_fingerprint,
            ):
                raise M3FeasibilitySupportConflictError(
                    "BASE_PLAN_REVISION_MISMATCH", "base_plan_revision"
                )
            expected = {
                (
                    item.commitment_id,
                    item.job_id,
                    item.task_id,
                    item.task_definition_version,
                    item.business_date,
                    item.planned_worker_ids,
                )
                for item in revision.commitments
            }
            actual = {
                (
                    item.commitment_id,
                    item.job_id,
                    item.task_id,
                    item.task_definition_version,
                    item.business_date,
                    item.worker_ids,
                )
                for item in value.placements
            }
            if actual != expected or len(actual) != len(value.placements):
                raise M3FeasibilitySupportConflictError(
                    "INCOMPLETE_PLAN_SCHEDULE", "placements"
                )
            configuration = (
                self._ensure_current_m8(connection)
                if m8_configuration is None
                else m8_configuration
            )
            known_vehicles = {
                item["vehicle_id"] for item in configuration["vehicles"]
            }
            if any(
                item.vehicle_id is not None
                and item.vehicle_id not in known_vehicles
                for item in value.placements
            ):
                raise M3FeasibilitySupportConflictError(
                    "UNKNOWN_CANONICAL_VEHICLE", "vehicle_id"
                )
            return
        if type(value) is WorkerAvailabilitySource:
            row = connection.execute(
                "SELECT 1 FROM m2_worker_identities WHERE worker_id=?",
                (value.worker_id,),
            ).fetchone()
            if row is None:
                raise M3FeasibilitySupportConflictError(
                    "CANONICAL_WORKER_NOT_FOUND", "worker_id"
                )
            return
        if type(value) is TaskReadinessSource:
            task = self._canonical_task(connection, value.job_id, value.task_id)
            if task.definition.definition_version != value.task_definition_version:
                raise M3FeasibilitySupportConflictError(
                    "CANONICAL_TASK_BINDING_MISMATCH", "task_id"
                )
            return
        if type(value) is VehicleAvailabilitySource:
            configuration = (
                self._ensure_current_m8(connection)
                if m8_configuration is None
                else m8_configuration
            )
            if value.vehicle_id not in {
                item["vehicle_id"] for item in configuration["vehicles"]
            }:
                raise M3FeasibilitySupportConflictError(
                    "CANONICAL_VEHICLE_NOT_FOUND", "vehicle_id"
                )
            return
        if type(value) is RouteSource:
            endpoints = {
                (source.location_reference, source.location_fingerprint)
                for source in self._current_sources(
                    connection, FeasibilitySourceKind.TASK_CONSTRAINT
                )
            }
            if (
                (value.origin_reference, value.origin_fingerprint) not in endpoints
                or (value.destination_reference, value.destination_fingerprint)
                not in endpoints
            ):
                raise M3FeasibilitySupportConflictError(
                    "ROUTE_ENDPOINT_NOT_CANONICAL", "route_endpoints"
                )
            return
        raise M3FeasibilitySupportValidationError("INVALID_SOURCE_TYPE", "source")

    def _current_sources(
        self, connection, kind: FeasibilitySourceKind
    ) -> tuple[FeasibilitySource, ...]:
        rows = connection.execute(
            """
            SELECT source.*
            FROM m3_feasibility_source_records AS source
            JOIN (
                SELECT subject_fingerprint, max(source_revision) AS source_revision
                FROM m3_feasibility_source_records
                WHERE source_kind=?
                GROUP BY subject_fingerprint
            ) AS head
              ON head.subject_fingerprint = source.subject_fingerprint
             AND head.source_revision = source.source_revision
            WHERE source.source_kind=?
            ORDER BY source.subject_fingerprint
            """,
            (kind.value, kind.value),
        ).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

    def _record_source(self, value: FeasibilitySource) -> FeasibilitySource:
        expected_types = {
            TaskConstraintSource,
            PlanScheduleSource,
            WorkerAvailabilitySource,
            TaskReadinessSource,
            VehicleAvailabilitySource,
            RouteSource,
        }
        if type(value) not in expected_types:
            raise M3FeasibilitySupportValidationError(
                "INVALID_SOURCE_TYPE", "source"
            )
        if type(value) is TaskConstraintSource:
            source_semantic_json(value)
        elif replace(value) != value:
            raise M3FeasibilitySupportValidationError(
                "INVALID_SOURCE_TYPE", "source"
            )
        try:
            with self.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM m3_feasibility_source_records WHERE source_record_id=?",
                    (value.source_record_id,),
                ).fetchone()
                if existing is not None:
                    restored = self._source_from_row(existing)
                    if restored != value:
                        raise M3FeasibilitySupportConflictError(
                            "SOURCE_REPLAY_CONFLICT", "source_record_id"
                        )
                    return restored
                m8_configuration = None
                if type(value) in (
                    TaskConstraintSource,
                    PlanScheduleSource,
                    VehicleAvailabilitySource,
                ):
                    m8_configuration = self._ensure_current_m8(connection)
                if (
                    type(value) is TaskConstraintSource
                    and value.m8_configuration_fingerprint
                    != m8_feasibility_configuration_fingerprint(
                        current_m8_configuration()
                    )
                ):
                    raise M3FeasibilitySupportConflictError(
                        "TASK_M8_CONFIGURATION_NOT_CURRENT", "m8_configuration"
                    )
                self._validate_source(
                    connection, value, m8_configuration=m8_configuration
                )
                prior = self._latest_source(
                    connection, value.source_kind, value.subject_fingerprint
                )
                expected_revision = 0 if prior is None else prior.source_revision + 1
                expected_parent = None if prior is None else prior.source_record_id
                if (
                    value.source_revision != expected_revision
                    or value.previous_source_record_id != expected_parent
                ):
                    raise M3FeasibilitySupportConflictError(
                        "STALE_SOURCE_PARENT", "source_revision"
                    )
                ledger_generation = self._source_cut_generation(connection) + 1
                connection.execute(
                    """
                    INSERT INTO m3_feasibility_source_records(
                        source_record_id, source_kind, subject_fingerprint,
                        source_revision, previous_source_record_id,
                        ledger_generation, schema_version, source_fingerprint,
                        canonical_semantic_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        value.source_record_id,
                        value.source_kind.value,
                        value.subject_fingerprint,
                        value.source_revision,
                        value.previous_source_record_id,
                        ledger_generation,
                        value.schema_version,
                        value.source_fingerprint,
                        source_semantic_json(value),
                    ),
                )
                return value
        except sqlite3.IntegrityError as error:
            raise M3FeasibilitySupportConflictError(
                "SOURCE_PERSISTENCE_CONFLICT", "source"
            ) from error
        except sqlite3.Error as error:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_STORAGE_FAILURE", "source"
            ) from error

    def record_task_constraint(
        self, value: TaskConstraintSource
    ) -> TaskConstraintSource:
        return self._record_source(value)

    def record_plan_schedule(self, value: PlanScheduleSource) -> PlanScheduleSource:
        return self._record_source(value)

    def record_worker_availability(
        self, value: WorkerAvailabilitySource
    ) -> WorkerAvailabilitySource:
        return self._record_source(value)

    def record_task_readiness(
        self, value: TaskReadinessSource
    ) -> TaskReadinessSource:
        return self._record_source(value)

    def record_vehicle_availability(
        self, value: VehicleAvailabilitySource
    ) -> VehicleAvailabilitySource:
        return self._record_source(value)

    def record_route(self, value: RouteSource) -> RouteSource:
        return self._record_source(value)

    @staticmethod
    def _availability(
        subject_id: str,
        source: WorkerAvailabilitySource | VehicleAvailabilitySource | None,
    ) -> AvailabilityEvidence:
        if source is None:
            return AvailabilityEvidence(
                subject_id=subject_id,
                knowledge=AvailabilityKnowledge.UNKNOWN,
                coverage_start=None,
                coverage_end=None,
                available_windows=(),
                source_record_id=None,
                source_fingerprint=None,
                source_revision=None,
            )
        return AvailabilityEvidence(
            subject_id=subject_id,
            knowledge=AvailabilityKnowledge.KNOWN,
            coverage_start=source.coverage_start,
            coverage_end=source.coverage_end,
            available_windows=source.available_windows,
            source_record_id=source.source_record_id,
            source_fingerprint=source.source_fingerprint,
            source_revision=source.source_revision,
        )

    @staticmethod
    def _readiness(commitment, source: TaskReadinessSource | None) -> ReadinessEvidence:
        if source is None:
            return ReadinessEvidence(
                job_id=commitment.job_id,
                task_id=commitment.task_id,
                task_definition_version=commitment.task_definition_version,
                status=ReadinessState.UNKNOWN,
                expected_at=None,
                blocker_references=(),
                source_record_id=None,
                source_fingerprint=None,
                source_revision=None,
            )
        return ReadinessEvidence(
            job_id=source.job_id,
            task_id=source.task_id,
            task_definition_version=source.task_definition_version,
            status=source.status,
            expected_at=source.expected_at,
            blocker_references=source.blocker_references,
            source_record_id=source.source_record_id,
            source_fingerprint=source.source_fingerprint,
            source_revision=source.source_revision,
        )

    @staticmethod
    def _route(
        origin: tuple[str, str],
        destination: tuple[str, str],
        source: RouteSource | None,
    ) -> RouteEvidence:
        if source is None:
            return RouteEvidence(
                origin_reference=origin[0],
                origin_fingerprint=origin[1],
                destination_reference=destination[0],
                destination_fingerprint=destination[1],
                transport_mode=ROUTE_TRANSPORT_MODE,
                knowledge=RouteKnowledge.UNKNOWN,
                departure_time_basis=None,
                distance_meters=None,
                travel_duration_seconds=None,
                buffer_seconds=None,
                buffer_rule_version=None,
                route_snapshot_version=None,
                provider=None,
                source_record_id=None,
                source_fingerprint=None,
                source_revision=None,
            )
        return RouteEvidence(
            origin_reference=source.origin_reference,
            origin_fingerprint=source.origin_fingerprint,
            destination_reference=source.destination_reference,
            destination_fingerprint=source.destination_fingerprint,
            transport_mode=source.transport_mode,
            knowledge=RouteKnowledge.KNOWN,
            departure_time_basis=source.departure_time_basis,
            distance_meters=source.distance_meters,
            travel_duration_seconds=source.travel_duration_seconds,
            buffer_seconds=source.buffer_seconds,
            buffer_rule_version=source.buffer_rule_version,
            route_snapshot_version=source.route_snapshot_version,
            provider=source.provider,
            source_record_id=source.source_record_id,
            source_fingerprint=source.source_fingerprint,
            source_revision=source.source_revision,
        )

    @staticmethod
    def _evidence_source_bindings(
        task_constraints,
        schedule,
        worker_availability,
        readiness,
        vehicle_availability,
        routes,
    ) -> tuple:
        bindings = [
            (
                item.source_kind.value,
                item.subject_fingerprint,
                item.source_record_id,
            )
            for item in (*task_constraints, schedule)
        ]
        bindings.extend(
            (
                FeasibilitySourceKind.WORKER_AVAILABILITY.value,
                worker_subject_fingerprint(item.subject_id),
                item.source_record_id,
            )
            for item in worker_availability
        )
        bindings.extend(
            (
                FeasibilitySourceKind.TASK_READINESS.value,
                task_readiness_subject_fingerprint(
                    item.job_id, item.task_id, item.task_definition_version
                ),
                item.source_record_id,
            )
            for item in readiness
        )
        bindings.extend(
            (
                FeasibilitySourceKind.VEHICLE_AVAILABILITY.value,
                vehicle_subject_fingerprint(item.subject_id),
                item.source_record_id,
            )
            for item in vehicle_availability
        )
        bindings.extend(
            (
                FeasibilitySourceKind.ROUTE.value,
                route_subject_fingerprint(
                    item.origin_reference,
                    item.origin_fingerprint,
                    item.destination_reference,
                    item.destination_fingerprint,
                    item.transport_mode,
                ),
                item.source_record_id,
            )
            for item in routes
        )
        return tuple(sorted(bindings, key=lambda item: (item[0], item[1])))

    @classmethod
    def _support_source_bindings(cls, value: FeasibilitySupportSnapshot) -> tuple:
        return cls._evidence_source_bindings(
            value.task_constraints,
            value.schedule,
            value.worker_availability,
            value.readiness,
            value.vehicle_availability,
            value.routes,
        )

    def _make_source_cut(
        self,
        connection,
        evaluation,
        worker_registry_provenance: WorkerRegistryProvenance,
        worker_registry_capture: WorkerRegistryProvenanceCapture,
        source_cut_generation: int,
        bindings: tuple,
    ) -> SourceSelectionCut:
        selections = []
        for kind_raw, subject_fingerprint, source_record_id in bindings:
            if source_record_id is None:
                selections.append(
                    SourceSelection(
                        source_kind=FeasibilitySourceKind(kind_raw),
                        subject_fingerprint=subject_fingerprint,
                        source_record_id=None,
                        source_revision=None,
                        source_fingerprint=None,
                    )
                )
                continue
            source = self._source_by_id(connection, source_record_id)
            if (
                source.source_kind.value,
                source.subject_fingerprint,
            ) != (kind_raw, subject_fingerprint):
                raise M3FeasibilitySupportStorageError(
                    "SOURCE_SELECTION_SUBJECT_MISMATCH", "source_selection"
                )
            selections.append(
                SourceSelection(
                    source_kind=source.source_kind,
                    subject_fingerprint=source.subject_fingerprint,
                    source_record_id=source.source_record_id,
                    source_revision=source.source_revision,
                    source_fingerprint=source.source_fingerprint,
                )
            )
        return SourceSelectionCut(
            evaluation_input_id=evaluation.evaluation_input_id,
            evaluation_input_fingerprint=evaluation.evaluation_input_fingerprint,
            company_plan_id=evaluation.company_plan_id,
            base_plan_revision=evaluation.base_plan_revision,
            base_plan_revision_id=evaluation.base_plan_revision_id,
            base_plan_revision_fingerprint=evaluation.base_plan_revision_fingerprint,
            worker_registry_provenance_id=(
                worker_registry_provenance.worker_registry_provenance_id
            ),
            worker_registry_provenance_fingerprint=(
                worker_registry_provenance.worker_registry_provenance_fingerprint
            ),
            worker_registry_capture_id=(
                worker_registry_capture.worker_registry_capture_id
            ),
            worker_registry_capture_fingerprint=(
                worker_registry_capture.worker_registry_capture_fingerprint
            ),
            worker_registry_capture_generation=(
                worker_registry_capture.ledger_generation
            ),
            source_cut_generation=source_cut_generation,
            selections=tuple(selections),
        )

    def _validate_support_bindings(
        self, connection, value: FeasibilitySupportSnapshot
    ) -> None:
        actual = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT evidence_kind, subject_fingerprint, source_record_id
                FROM m3_feasibility_support_source_bindings
                WHERE support_snapshot_id=?
                ORDER BY evidence_kind, subject_fingerprint
                """,
                (value.support_snapshot_id,),
            ).fetchall()
        )
        if actual != self._support_source_bindings(value):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_SOURCE_BINDINGS_MISMATCH", "source_bindings"
            )

    @staticmethod
    def _source_cut_from_row(row) -> SourceSelectionCut:
        value = restore_source_selection_cut(
            row["canonical_semantic_json"],
            row["source_cut_fingerprint"],
            row["source_cut_id"],
        )
        actual = (
            row["source_cut_generation"],
            row["schema_version"],
            row["evaluation_input_id"],
            row["evaluation_input_fingerprint"],
            row["company_plan_id"],
            row["base_plan_revision"],
            row["base_plan_revision_id"],
            row["base_plan_revision_fingerprint"],
            row["worker_registry_provenance_id"],
            row["worker_registry_provenance_fingerprint"],
            row["worker_registry_capture_id"],
            row["worker_registry_capture_fingerprint"],
            row["worker_registry_capture_generation"],
        )
        expected = (
            value.source_cut_generation,
            value.schema_version,
            value.evaluation_input_id,
            value.evaluation_input_fingerprint,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
            value.worker_registry_provenance_id,
            value.worker_registry_provenance_fingerprint,
            value.worker_registry_capture_id,
            value.worker_registry_capture_fingerprint,
            value.worker_registry_capture_generation,
        )
        if actual != expected:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_CUT_METADATA_MISMATCH", "source_selection_cut"
            )
        return value

    def _persist_source_cut(self, connection, value: SourceSelectionCut) -> None:
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_source_selection_cuts
            WHERE source_cut_id=?
            """,
            (value.source_cut_id,),
        ).fetchone()
        if row is not None:
            if self._source_cut_from_row(row) != value:
                raise M3FeasibilitySupportConflictError(
                    "SOURCE_CUT_REPLAY_CONFLICT", "source_cut_id"
                )
            return
        connection.execute(
            """
            INSERT INTO m3_feasibility_source_selection_cuts(
                source_cut_id, source_cut_generation, source_cut_fingerprint,
                schema_version, evaluation_input_id,
                evaluation_input_fingerprint, company_plan_id,
                base_plan_revision, base_plan_revision_id,
                base_plan_revision_fingerprint,
                worker_registry_provenance_id,
                worker_registry_provenance_fingerprint,
                worker_registry_capture_id,
                worker_registry_capture_fingerprint,
                worker_registry_capture_generation,
                canonical_semantic_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                value.source_cut_id,
                value.source_cut_generation,
                value.source_cut_fingerprint,
                value.schema_version,
                value.evaluation_input_id,
                value.evaluation_input_fingerprint,
                value.company_plan_id,
                value.base_plan_revision,
                value.base_plan_revision_id,
                value.base_plan_revision_fingerprint,
                value.worker_registry_provenance_id,
                value.worker_registry_provenance_fingerprint,
                value.worker_registry_capture_id,
                value.worker_registry_capture_fingerprint,
                value.worker_registry_capture_generation,
                source_cut_semantic_json(value),
            ),
        )

    def _required_source_selection_keys(
        self,
        connection,
        evaluation,
        configuration: dict,
        worker_registry_provenance: WorkerRegistryProvenance,
        source_cut_generation: int,
    ) -> tuple[tuple[str, str], ...]:
        keys = {
            (
                FeasibilitySourceKind.PLAN_SCHEDULE.value,
                schedule_subject_fingerprint(
                    evaluation.company_plan_id,
                    evaluation.base_plan_revision_id,
                ),
            )
        }
        constraint_heads = []
        for commitment in evaluation.current_active_commitments:
            constraint_subject = task_subject_fingerprint(
                commitment.job_id,
                commitment.task_id,
                commitment.task_definition_version,
            )
            keys.add(
                (FeasibilitySourceKind.TASK_CONSTRAINT.value, constraint_subject)
            )
            keys.add(
                (
                    FeasibilitySourceKind.TASK_READINESS.value,
                    task_readiness_subject_fingerprint(
                        commitment.job_id,
                        commitment.task_id,
                        commitment.task_definition_version,
                    ),
                )
            )
            constraint = self._latest_source_at_cut(
                connection,
                FeasibilitySourceKind.TASK_CONSTRAINT,
                constraint_subject,
                source_cut_generation,
            )
            if type(constraint) is not TaskConstraintSource:
                raise M3FeasibilitySupportStorageError(
                    "SOURCE_CUT_TASK_CONSTRAINT_MISSING", "source_selection_cut"
                )
            constraint_heads.append(constraint)

        keys.update(
            (
                FeasibilitySourceKind.WORKER_AVAILABILITY.value,
                worker_subject_fingerprint(worker_id),
            )
            for worker_id in worker_registry_provenance.worker_ids
        )
        keys.update(
            (
                FeasibilitySourceKind.VEHICLE_AVAILABILITY.value,
                vehicle_subject_fingerprint(vehicle["vehicle_id"]),
            )
            for vehicle in configuration["vehicles"]
        )
        endpoints = tuple(
            sorted(
                {
                    (item.location_reference, item.location_fingerprint)
                    for item in constraint_heads
                }
            )
        )
        keys.update(
            (
                FeasibilitySourceKind.ROUTE.value,
                route_subject_fingerprint(
                    origin[0],
                    origin[1],
                    destination[0],
                    destination[1],
                    ROUTE_TRANSPORT_MODE,
                ),
            )
            for origin in endpoints
            for destination in endpoints
            if origin != destination
        )
        return tuple(sorted(keys))

    def _validate_source_cut(
        self,
        connection,
        value: FeasibilitySupportSnapshot,
        evaluation,
    ) -> tuple[
        SourceSelectionCut,
        WorkerRegistryProvenance,
        WorkerRegistryProvenanceCapture,
    ]:
        self._validate_feasibility_ledger_chronology(connection)
        row = connection.execute(
            """
            SELECT * FROM m3_feasibility_source_selection_cuts
            WHERE source_cut_id=?
            """,
            (value.source_cut_id,),
        ).fetchone()
        if row is None:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_CUT_NOT_FOUND", "source_cut_id"
            )
        source_cut = self._source_cut_from_row(row)
        cut_binding = (
            source_cut.source_cut_id,
            source_cut.source_cut_generation,
            source_cut.source_cut_fingerprint,
            source_cut.evaluation_input_id,
            source_cut.evaluation_input_fingerprint,
            source_cut.company_plan_id,
            source_cut.base_plan_revision,
            source_cut.base_plan_revision_id,
            source_cut.base_plan_revision_fingerprint,
            source_cut.worker_registry_provenance_id,
            source_cut.worker_registry_provenance_fingerprint,
            source_cut.worker_registry_capture_id,
            source_cut.worker_registry_capture_fingerprint,
            source_cut.worker_registry_capture_generation,
        )
        support_binding = (
            value.source_cut_id,
            value.source_cut_generation,
            value.source_cut_fingerprint,
            value.evaluation_input_id,
            value.evaluation_input_fingerprint,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
            value.worker_registry_provenance_id,
            value.worker_registry_provenance_fingerprint,
            value.worker_registry_capture_id,
            value.worker_registry_capture_fingerprint,
            value.worker_registry_capture_generation,
        )
        if cut_binding != support_binding:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_SOURCE_CUT_MISMATCH", "source_selection_cut"
            )
        if source_cut.source_cut_generation > 0 and connection.execute(
            """
            SELECT 1
            FROM (
                SELECT ledger_generation FROM m3_feasibility_source_records
                UNION ALL
                SELECT ledger_generation
                FROM m3_feasibility_worker_registry_captures
            )
            WHERE ledger_generation=?
            """,
            (source_cut.source_cut_generation,),
        ).fetchone() is None:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_CUT_GENERATION_NOT_FOUND", "source_cut_generation"
            )

        worker_registry_provenance = self._worker_registry_provenance_by_id(
            connection, source_cut.worker_registry_provenance_id
        )
        worker_registry_capture = self._worker_registry_capture_by_id(
            connection, source_cut.worker_registry_capture_id
        )
        if (
            worker_registry_provenance.worker_registry_provenance_fingerprint,
            worker_registry_provenance.worker_registry_revision,
            worker_registry_provenance.worker_registry_fingerprint,
        ) != (
            source_cut.worker_registry_provenance_fingerprint,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
        ):
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_PROVENANCE_BINDING_MISMATCH",
                "worker_registry_provenance",
            )

        capture_binding = (
            worker_registry_capture.worker_registry_capture_fingerprint,
            worker_registry_capture.ledger_generation,
            worker_registry_capture.worker_registry_provenance_id,
            worker_registry_capture.worker_registry_provenance_fingerprint,
            worker_registry_capture.worker_registry_revision,
            worker_registry_capture.worker_registry_fingerprint,
        )
        expected_capture_binding = (
            source_cut.worker_registry_capture_fingerprint,
            source_cut.worker_registry_capture_generation,
            worker_registry_provenance.worker_registry_provenance_id,
            worker_registry_provenance.worker_registry_provenance_fingerprint,
            worker_registry_provenance.worker_registry_revision,
            worker_registry_provenance.worker_registry_fingerprint,
        )
        if capture_binding != expected_capture_binding:
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_CAPTURE_BINDING_MISMATCH",
                "worker_registry_capture",
            )
        if (
            worker_registry_capture.ledger_generation
            > source_cut.source_cut_generation
        ):
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_CAPTURE_AFTER_SOURCE_CUT",
                "worker_registry_capture_generation",
            )

        configuration = self._m8_by_fingerprint(
            connection, value.m8_configuration_fingerprint
        )
        required_keys = self._required_source_selection_keys(
            connection,
            evaluation,
            configuration,
            worker_registry_provenance,
            source_cut.source_cut_generation,
        )
        selection_by_key = {
            (item.source_kind.value, item.subject_fingerprint): item
            for item in source_cut.selections
        }
        if tuple(sorted(selection_by_key)) != required_keys:
            raise M3FeasibilitySupportStorageError(
                "SOURCE_CUT_SUBJECT_UNIVERSE_MISMATCH", "source_selections"
            )
        for (kind_raw, subject_fingerprint), selection in selection_by_key.items():
            head = self._latest_source_at_cut(
                connection,
                FeasibilitySourceKind(kind_raw),
                subject_fingerprint,
                source_cut.source_cut_generation,
            )
            expected = (
                None,
                None,
                None,
            ) if head is None else (
                head.source_record_id,
                head.source_revision,
                head.source_fingerprint,
            )
            actual = (
                selection.source_record_id,
                selection.source_revision,
                selection.source_fingerprint,
            )
            if actual != expected:
                raise M3FeasibilitySupportStorageError(
                    "SOURCE_CUT_HEAD_MISMATCH", "source_selection"
                )
        cut_bindings = tuple(
            (
                item.source_kind.value,
                item.subject_fingerprint,
                item.source_record_id,
            )
            for item in source_cut.selections
        )
        if cut_bindings != self._support_source_bindings(value):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_SOURCE_CUT_SELECTION_MISMATCH", "source_selection"
            )
        return source_cut, worker_registry_provenance, worker_registry_capture

    def _validate_support_content(
        self,
        connection,
        value: FeasibilitySupportSnapshot,
        evaluation,
        worker_registry_provenance: WorkerRegistryProvenance,
    ) -> None:
        history = self._history(connection, evaluation.company_plan_id)
        if evaluation.base_plan_revision >= len(history):
            raise M3FeasibilitySupportStorageError(
                "BASE_PLAN_REVISION_NOT_FOUND", "base_plan_revision"
            )
        revision = history[evaluation.base_plan_revision]
        exact_revision = (
            evaluation.company_plan_id,
            evaluation.base_plan_revision,
            evaluation.base_plan_revision_id,
            evaluation.base_plan_revision_fingerprint,
        )
        if exact_revision != (
            revision.company_plan_id,
            revision.revision,
            revision.revision_id,
            revision.fingerprint,
        ) or exact_revision != (
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
        ):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_PLAN_REVISION_MISMATCH", "base_plan_revision"
            )

        configuration = self._m8_by_fingerprint(
            connection, value.m8_configuration_fingerprint
        )
        if (
            value.m8_service_catalog_version,
            value.m8_planning_profile_version,
            value.m8_skill_matrix_version,
            value.m8_vehicle_policy_version,
        ) != (
            configuration["service_catalog_version"],
            configuration["planning_profile_version"],
            configuration["skill_matrix_version"],
            configuration["vehicle_policy_version"],
        ):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_M8_PROVENANCE_MISMATCH", "m8_configuration"
            )

        commitments = tuple(evaluation.current_active_commitments)
        expected_task_keys = {
            (item.job_id, item.task_id, item.task_definition_version)
            for item in commitments
        }
        constraint_keys = {
            (item.job_id, item.task_id, item.task_definition_version)
            for item in value.task_constraints
        }
        readiness_keys = {
            (item.job_id, item.task_id, item.task_definition_version)
            for item in value.readiness
        }
        if (
            constraint_keys != expected_task_keys
            or len(value.task_constraints) != len(expected_task_keys)
            or readiness_keys != expected_task_keys
            or len(value.readiness) != len(expected_task_keys)
        ):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_TASK_COVERAGE_MISMATCH", "task_coverage"
            )
        commitment_by_key = {
            (item.job_id, item.task_id, item.task_definition_version): item
            for item in commitments
        }
        for source in value.task_constraints:
            stored = self._source_by_id(connection, source.source_record_id)
            if stored != source:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_MISMATCH", "task_constraint"
                )
            self._validate_source(connection, source)
            commitment = commitment_by_key[
                (source.job_id, source.task_id, source.task_definition_version)
            ]
            if (
                source.source_handoff_id,
                source.source_revision_number,
                source.m8_configuration_fingerprint,
            ) != (
                commitment.source_handoff_id,
                commitment.source_revision,
                value.m8_configuration_fingerprint,
            ):
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_TASK_BINDING_MISMATCH", "task_constraint"
                )

        stored_schedule = self._source_by_id(
            connection, value.schedule.source_record_id
        )
        if stored_schedule != value.schedule:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_SOURCE_MISMATCH", "schedule"
            )
        if (
            value.schedule.company_plan_id,
            value.schedule.base_plan_revision,
            value.schedule.base_plan_revision_id,
            value.schedule.base_plan_revision_fingerprint,
        ) != exact_revision:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_SCHEDULE_REVISION_MISMATCH", "schedule"
            )
        self._validate_source(
            connection, value.schedule, m8_configuration=configuration
        )
        placement_by_key = {
            (item.job_id, item.task_id): item for item in value.schedule.placements
        }
        for constraint in value.task_constraints:
            placement = placement_by_key.get((constraint.job_id, constraint.task_id))
            if placement is None or int(
                (placement.planned_end - placement.planned_start).total_seconds()
            ) != constraint.duration_seconds:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_PLANNED_DURATION_MISMATCH", "schedule"
                )

        if (
            value.worker_registry_provenance_id,
            value.worker_registry_provenance_fingerprint,
            value.worker_registry_revision,
            value.worker_registry_fingerprint,
        ) != (
            worker_registry_provenance.worker_registry_provenance_id,
            worker_registry_provenance.worker_registry_provenance_fingerprint,
            worker_registry_provenance.worker_registry_revision,
            worker_registry_provenance.worker_registry_fingerprint,
        ):
            raise M3FeasibilitySupportStorageError(
                "WORKER_REGISTRY_PROVENANCE_MISMATCH", "worker_registry"
            )
        required_skills = tuple(
            sorted({item.required_skill_key for item in value.task_constraints})
        )
        configured_workers = {
            item["worker_id"]: item for item in configuration["workers"]
        }
        expected_workers = []
        for worker_id in worker_registry_provenance.worker_ids:
            profile = configured_workers.get(worker_id)
            levels = (
                {}
                if profile is None
                else {item["skill_key"]: item["level"] for item in profile["skills"]}
            )
            expected_workers.append(
                WorkerTechnicalEvidence(
                    worker_id=worker_id,
                    m8_worker_known=profile is not None,
                    skill_matrix_version=configuration["skill_matrix_version"],
                    license_b=None if profile is None else profile["license_b"],
                    skill_levels=tuple(
                        WorkerSkillLevelEvidence(
                            required_skill_key=skill,
                            level=levels.get(skill),
                        )
                        for skill in required_skills
                    ),
                )
            )
        if tuple(expected_workers) != value.worker_technical_evidence:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_WORKER_TECHNICAL_MISMATCH", "worker_technical_evidence"
            )

        expected_worker_ids = tuple(worker_registry_provenance.worker_ids)
        if tuple(item.subject_id for item in value.worker_availability) != expected_worker_ids:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_WORKER_AVAILABILITY_COVERAGE_MISMATCH",
                "worker_availability",
            )
        for evidence in value.worker_availability:
            source = (
                None
                if evidence.source_record_id is None
                else self._source_by_id(connection, evidence.source_record_id)
            )
            if source is not None and type(source) is not WorkerAvailabilitySource:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_TYPE_MISMATCH", "worker_availability"
                )
            if self._availability(evidence.subject_id, source) != evidence:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_VALUE_MISMATCH", "worker_availability"
                )

        for evidence in value.readiness:
            commitment = commitment_by_key[
                (evidence.job_id, evidence.task_id, evidence.task_definition_version)
            ]
            source = (
                None
                if evidence.source_record_id is None
                else self._source_by_id(connection, evidence.source_record_id)
            )
            if source is not None and type(source) is not TaskReadinessSource:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_TYPE_MISMATCH", "readiness"
                )
            if self._readiness(commitment, source) != evidence:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_VALUE_MISMATCH", "readiness"
                )

        expected_vehicles = tuple(
            VehicleTechnicalEvidence(
                vehicle_id=item["vehicle_id"],
                kind=item["kind"],
                capabilities=tuple(item["capabilities"]),
                assigned_worker_id=item["assigned_worker_id"],
            )
            for item in configuration["vehicles"]
        )
        if expected_vehicles != value.vehicle_technical_evidence:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_VEHICLE_TECHNICAL_MISMATCH", "vehicle_technical_evidence"
            )
        if tuple(item.subject_id for item in value.vehicle_availability) != tuple(
            item.vehicle_id for item in expected_vehicles
        ):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_VEHICLE_AVAILABILITY_COVERAGE_MISMATCH",
                "vehicle_availability",
            )
        for evidence in value.vehicle_availability:
            source = (
                None
                if evidence.source_record_id is None
                else self._source_by_id(connection, evidence.source_record_id)
            )
            if source is not None and type(source) is not VehicleAvailabilitySource:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_TYPE_MISMATCH", "vehicle_availability"
                )
            if self._availability(evidence.subject_id, source) != evidence:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_VALUE_MISMATCH", "vehicle_availability"
                )

        endpoints = tuple(
            sorted(
                {
                    (item.location_reference, item.location_fingerprint)
                    for item in value.task_constraints
                }
            )
        )
        expected_route_keys = tuple(
            (origin, destination)
            for origin in endpoints
            for destination in endpoints
            if origin != destination
        )
        actual_route_keys = tuple(
            (
                (item.origin_reference, item.origin_fingerprint),
                (item.destination_reference, item.destination_fingerprint),
            )
            for item in value.routes
        )
        if actual_route_keys != expected_route_keys:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_ROUTE_COVERAGE_MISMATCH", "routes"
            )
        for evidence in value.routes:
            source = (
                None
                if evidence.source_record_id is None
                else self._source_by_id(connection, evidence.source_record_id)
            )
            if source is not None and type(source) is not RouteSource:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_TYPE_MISMATCH", "route"
                )
            origin = (evidence.origin_reference, evidence.origin_fingerprint)
            destination = (
                evidence.destination_reference,
                evidence.destination_fingerprint,
            )
            if self._route(origin, destination, source) != evidence:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_SOURCE_VALUE_MISMATCH", "route"
                )

    def _build_support(
        self,
        connection,
        evaluation_input,
        worker_registry_provenance: WorkerRegistryProvenance,
        worker_registry_capture: WorkerRegistryProvenanceCapture,
    ) -> FeasibilitySupportSnapshot:
        m8_configuration = self._ensure_current_m8(connection)
        m8_configuration_fingerprint = m8_feasibility_configuration_fingerprint(
            current_m8_configuration()
        )
        source_cut_generation = self._source_cut_generation(connection)
        history = self._history(connection, evaluation_input.company_plan_id)
        if evaluation_input.base_plan_revision >= len(history):
            raise M3FeasibilitySupportStorageError(
                "BASE_PLAN_REVISION_NOT_FOUND", "base_plan_revision"
            )
        revision = history[evaluation_input.base_plan_revision]
        if (
            revision.revision_id,
            revision.fingerprint,
        ) != (
            evaluation_input.base_plan_revision_id,
            evaluation_input.base_plan_revision_fingerprint,
        ):
            raise M3FeasibilitySupportStorageError(
                "BASE_PLAN_REVISION_MISMATCH", "base_plan_revision"
            )
        schedule = self._latest_source_at_cut(
            connection,
            FeasibilitySourceKind.PLAN_SCHEDULE,
            schedule_subject_fingerprint(
                evaluation_input.company_plan_id,
                evaluation_input.base_plan_revision_id,
            ),
            source_cut_generation,
        )
        if type(schedule) is not PlanScheduleSource:
            raise M3FeasibilitySupportConflictError(
                "PLAN_SCHEDULE_MISSING", "schedule"
            )
        self._validate_source(
            connection, schedule, m8_configuration=m8_configuration
        )

        task_constraints = []
        for commitment in evaluation_input.current_active_commitments:
            source = self._latest_source_at_cut(
                connection,
                FeasibilitySourceKind.TASK_CONSTRAINT,
                task_subject_fingerprint(
                    commitment.job_id,
                    commitment.task_id,
                    commitment.task_definition_version,
                ),
                source_cut_generation,
            )
            if type(source) is not TaskConstraintSource:
                raise M3FeasibilitySupportConflictError(
                    "TASK_M8_BINDING_MISSING", "task_constraint"
                )
            if source.m8_configuration_fingerprint != m8_configuration_fingerprint:
                raise M3FeasibilitySupportConflictError(
                    "TASK_M8_CONFIGURATION_STALE", "task_constraint"
                )
            self._validate_source(connection, source)
            if (
                source.source_handoff_id,
                source.source_revision_number,
            ) != (
                commitment.source_handoff_id,
                commitment.source_revision,
            ):
                raise M3FeasibilitySupportConflictError(
                    "TASK_CONSTRAINT_LINEAGE_MISMATCH", "task_constraint"
                )
            task_constraints.append(source)
        task_constraints = tuple(task_constraints)
        constraint_by_task = {
            (item.job_id, item.task_id): item for item in task_constraints
        }
        placement_by_task = {
            (item.job_id, item.task_id): item for item in schedule.placements
        }
        if any(
            int(
                (
                    placement_by_task[key].planned_end
                    - placement_by_task[key].planned_start
                ).total_seconds()
            )
            != constraint.duration_seconds
            for key, constraint in constraint_by_task.items()
        ):
            raise M3FeasibilitySupportConflictError(
                "PLANNED_DURATION_MISMATCH", "duration_seconds"
            )

        required_skills = tuple(
            sorted({item.required_skill_key for item in task_constraints})
        )
        m8_workers = {
            item["worker_id"]: item for item in m8_configuration["workers"]
        }
        worker_technical = []
        worker_availability = []
        for worker_id in worker_registry_provenance.worker_ids:
            profile = m8_workers.get(worker_id)
            skill_by_key = (
                {}
                if profile is None
                else {item["skill_key"]: item["level"] for item in profile["skills"]}
            )
            worker_technical.append(
                WorkerTechnicalEvidence(
                    worker_id=worker_id,
                    m8_worker_known=profile is not None,
                    skill_matrix_version=m8_configuration["skill_matrix_version"],
                    license_b=None if profile is None else profile["license_b"],
                    skill_levels=tuple(
                        WorkerSkillLevelEvidence(
                            required_skill_key=skill,
                            level=skill_by_key.get(skill),
                        )
                        for skill in required_skills
                    ),
                )
            )
            availability = self._latest_source_at_cut(
                connection,
                FeasibilitySourceKind.WORKER_AVAILABILITY,
                worker_subject_fingerprint(worker_id),
                source_cut_generation,
            )
            if availability is not None and type(availability) is not WorkerAvailabilitySource:
                raise M3FeasibilitySupportStorageError(
                    "SOURCE_TYPE_MISMATCH", "worker_availability"
                )
            worker_availability.append(self._availability(worker_id, availability))

        readiness = []
        for commitment in evaluation_input.current_active_commitments:
            source = self._latest_source_at_cut(
                connection,
                FeasibilitySourceKind.TASK_READINESS,
                task_readiness_subject_fingerprint(
                    commitment.job_id,
                    commitment.task_id,
                    commitment.task_definition_version,
                ),
                source_cut_generation,
            )
            if source is not None and type(source) is not TaskReadinessSource:
                raise M3FeasibilitySupportStorageError(
                    "SOURCE_TYPE_MISMATCH", "readiness"
                )
            readiness.append(self._readiness(commitment, source))

        vehicles = tuple(
            VehicleTechnicalEvidence(
                vehicle_id=item["vehicle_id"],
                kind=item["kind"],
                capabilities=tuple(item["capabilities"]),
                assigned_worker_id=item["assigned_worker_id"],
            )
            for item in m8_configuration["vehicles"]
        )
        vehicle_availability = []
        for vehicle in vehicles:
            source = self._latest_source_at_cut(
                connection,
                FeasibilitySourceKind.VEHICLE_AVAILABILITY,
                vehicle_subject_fingerprint(vehicle.vehicle_id),
                source_cut_generation,
            )
            if source is not None and type(source) is not VehicleAvailabilitySource:
                raise M3FeasibilitySupportStorageError(
                    "SOURCE_TYPE_MISMATCH", "vehicle_availability"
                )
            vehicle_availability.append(self._availability(vehicle.vehicle_id, source))

        endpoints = tuple(
            sorted(
                {
                    (item.location_reference, item.location_fingerprint)
                    for item in task_constraints
                }
            )
        )
        routes = []
        for origin in endpoints:
            for destination in endpoints:
                if origin == destination:
                    continue
                source = self._latest_source_at_cut(
                    connection,
                    FeasibilitySourceKind.ROUTE,
                    route_subject_fingerprint(
                        origin[0],
                        origin[1],
                        destination[0],
                        destination[1],
                        ROUTE_TRANSPORT_MODE,
                    ),
                    source_cut_generation,
                )
                if source is not None and type(source) is not RouteSource:
                    raise M3FeasibilitySupportStorageError(
                        "SOURCE_TYPE_MISMATCH", "route"
                    )
                routes.append(self._route(origin, destination, source))

        bindings = self._evidence_source_bindings(
            task_constraints,
            schedule,
            tuple(worker_availability),
            tuple(readiness),
            tuple(vehicle_availability),
            tuple(routes),
        )
        source_cut = self._make_source_cut(
            connection,
            evaluation_input,
            worker_registry_provenance,
            worker_registry_capture,
            source_cut_generation,
            bindings,
        )
        return FeasibilitySupportSnapshot(
            evaluation_input_id=evaluation_input.evaluation_input_id,
            evaluation_input_fingerprint=evaluation_input.evaluation_input_fingerprint,
            company_plan_id=evaluation_input.company_plan_id,
            base_plan_revision=evaluation_input.base_plan_revision,
            base_plan_revision_id=evaluation_input.base_plan_revision_id,
            base_plan_revision_fingerprint=evaluation_input.base_plan_revision_fingerprint,
            source_cut_id=source_cut.source_cut_id,
            source_cut_generation=source_cut.source_cut_generation,
            source_cut_fingerprint=source_cut.source_cut_fingerprint,
            m8_service_catalog_version=m8_configuration["service_catalog_version"],
            m8_planning_profile_version=m8_configuration["planning_profile_version"],
            m8_skill_matrix_version=m8_configuration["skill_matrix_version"],
            m8_vehicle_policy_version=m8_configuration["vehicle_policy_version"],
            m8_configuration_fingerprint=m8_configuration_fingerprint,
            worker_registry_provenance_id=(
                worker_registry_provenance.worker_registry_provenance_id
            ),
            worker_registry_provenance_fingerprint=(
                worker_registry_provenance.worker_registry_provenance_fingerprint
            ),
            worker_registry_capture_id=(
                worker_registry_capture.worker_registry_capture_id
            ),
            worker_registry_capture_fingerprint=(
                worker_registry_capture.worker_registry_capture_fingerprint
            ),
            worker_registry_capture_generation=(
                worker_registry_capture.ledger_generation
            ),
            worker_registry_revision=(
                worker_registry_provenance.worker_registry_revision
            ),
            worker_registry_fingerprint=(
                worker_registry_provenance.worker_registry_fingerprint
            ),
            task_constraints=task_constraints,
            schedule=schedule,
            worker_technical_evidence=tuple(worker_technical),
            worker_availability=tuple(worker_availability),
            readiness=tuple(readiness),
            vehicle_technical_evidence=vehicles,
            vehicle_availability=tuple(vehicle_availability),
            routes=tuple(routes),
        )

    def record_feasibility_support(
        self, evaluation_input_id: str
    ) -> FeasibilitySupportSnapshot:
        if type(evaluation_input_id) is not str or not evaluation_input_id.strip():
            raise M3FeasibilitySupportValidationError(
                "INVALID_VALUE", "evaluation_input_id"
            )
        try:
            with self.transaction() as connection:
                evaluation_row = connection.execute(
                    "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
                    (evaluation_input_id,),
                ).fetchone()
                if evaluation_row is None:
                    raise M3FeasibilitySupportConflictError(
                        "EVALUATION_INPUT_NOT_FOUND", "evaluation_input_id"
                    )
                evaluation_input = self._from_row(connection, evaluation_row)
                history = self._history(connection, evaluation_input.company_plan_id)
                if evaluation_input.base_plan_revision >= len(history):
                    raise M3FeasibilitySupportStorageError(
                        "BASE_PLAN_REVISION_NOT_FOUND", "base_plan_revision"
                    )
                revision = history[evaluation_input.base_plan_revision]
                if (
                    revision.revision_id,
                    revision.fingerprint,
                ) != (
                    evaluation_input.base_plan_revision_id,
                    evaluation_input.base_plan_revision_fingerprint,
                ):
                    raise M3FeasibilitySupportStorageError(
                        "BASE_PLAN_REVISION_MISMATCH", "base_plan_revision"
                    )
                worker_registry_provenance, worker_registry_capture = (
                    self._capture_current_worker_registry(connection)
                )
                value = self._build_support(
                    connection,
                    evaluation_input,
                    worker_registry_provenance,
                    worker_registry_capture,
                )
                source_cut = self._make_source_cut(
                    connection,
                    evaluation_input,
                    worker_registry_provenance,
                    worker_registry_capture,
                    value.source_cut_generation,
                    self._support_source_bindings(value),
                )
                if (
                    source_cut.source_cut_id,
                    source_cut.source_cut_generation,
                    source_cut.source_cut_fingerprint,
                ) != (
                    value.source_cut_id,
                    value.source_cut_generation,
                    value.source_cut_fingerprint,
                ):
                    raise M3FeasibilitySupportStorageError(
                        "SUPPORT_SOURCE_CUT_BUILD_MISMATCH", "source_selection_cut"
                    )
                self._persist_source_cut(connection, source_cut)
                _, captured_registry, _ = self._validate_source_cut(
                    connection, value, evaluation_input
                )
                self._validate_support_content(
                    connection, value, evaluation_input, captured_registry
                )
                raw = snapshot_semantic_json(value)
                existing = connection.execute(
                    "SELECT * FROM m3_feasibility_support_snapshots WHERE support_snapshot_id=?",
                    (value.support_snapshot_id,),
                ).fetchone()
                if existing is not None:
                    restored = self._support_from_row(connection, existing)
                    if restored != value:
                        raise M3FeasibilitySupportConflictError(
                            "SUPPORT_REPLAY_CONFLICT", "support_snapshot_id"
                        )
                    return restored
                connection.execute(
                    """
                    INSERT INTO m3_feasibility_support_snapshots(
                        support_snapshot_id, support_fingerprint,
                        schema_version, rule_version, evaluation_input_id,
                        evaluation_input_fingerprint, company_plan_id,
                        base_plan_revision, base_plan_revision_id,
                        base_plan_revision_fingerprint, source_cut_id,
                        source_cut_generation, source_cut_fingerprint,
                        worker_registry_provenance_id,
                        worker_registry_provenance_fingerprint,
                        worker_registry_capture_id,
                        worker_registry_capture_fingerprint,
                        worker_registry_capture_generation,
                        canonical_semantic_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        value.support_snapshot_id,
                        value.support_fingerprint,
                        value.schema_version,
                        value.rule_version,
                        value.evaluation_input_id,
                        value.evaluation_input_fingerprint,
                        value.company_plan_id,
                        value.base_plan_revision,
                        value.base_plan_revision_id,
                        value.base_plan_revision_fingerprint,
                        value.source_cut_id,
                        value.source_cut_generation,
                        value.source_cut_fingerprint,
                        value.worker_registry_provenance_id,
                        value.worker_registry_provenance_fingerprint,
                        value.worker_registry_capture_id,
                        value.worker_registry_capture_fingerprint,
                        value.worker_registry_capture_generation,
                        raw,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO m3_feasibility_support_source_bindings(
                        support_snapshot_id, evidence_kind,
                        subject_fingerprint, source_record_id
                    ) VALUES(?,?,?,?)
                    """,
                    (
                        (value.support_snapshot_id, *binding)
                        for binding in self._support_source_bindings(value)
                    ),
                )
                return value
        except sqlite3.IntegrityError as error:
            raise M3FeasibilitySupportConflictError(
                "SUPPORT_PERSISTENCE_CONFLICT", "feasibility_support"
            ) from error
        except sqlite3.Error as error:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_STORAGE_FAILURE", "feasibility_support"
            ) from error

    def _support_from_row(self, connection, row) -> FeasibilitySupportSnapshot:
        value = restore_feasibility_support(
            row["canonical_semantic_json"],
            row["support_fingerprint"],
            row["support_snapshot_id"],
        )
        actual = (
            row["schema_version"],
            row["rule_version"],
            row["evaluation_input_id"],
            row["evaluation_input_fingerprint"],
            row["company_plan_id"],
            row["base_plan_revision"],
            row["base_plan_revision_id"],
            row["base_plan_revision_fingerprint"],
            row["source_cut_id"],
            row["source_cut_generation"],
            row["source_cut_fingerprint"],
            row["worker_registry_provenance_id"],
            row["worker_registry_provenance_fingerprint"],
            row["worker_registry_capture_id"],
            row["worker_registry_capture_fingerprint"],
            row["worker_registry_capture_generation"],
        )
        expected = (
            value.schema_version,
            FEASIBILITY_SUPPORT_RULE_VERSION,
            value.evaluation_input_id,
            value.evaluation_input_fingerprint,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
            value.source_cut_id,
            value.source_cut_generation,
            value.source_cut_fingerprint,
            value.worker_registry_provenance_id,
            value.worker_registry_provenance_fingerprint,
            value.worker_registry_capture_id,
            value.worker_registry_capture_fingerprint,
            value.worker_registry_capture_generation,
        )
        if actual != expected:
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_METADATA_MISMATCH", "feasibility_support"
            )
        evaluation_row = connection.execute(
            "SELECT * FROM m3_evaluation_inputs WHERE evaluation_input_id=?",
            (value.evaluation_input_id,),
        ).fetchone()
        if evaluation_row is None:
            raise M3FeasibilitySupportStorageError(
                "EVALUATION_INPUT_NOT_FOUND", "evaluation_input_id"
            )
        evaluation = self._from_row(connection, evaluation_row)
        if (
            evaluation.evaluation_input_fingerprint,
            evaluation.company_plan_id,
            evaluation.base_plan_revision,
            evaluation.base_plan_revision_id,
            evaluation.base_plan_revision_fingerprint,
        ) != (
            value.evaluation_input_fingerprint,
            value.company_plan_id,
            value.base_plan_revision,
            value.base_plan_revision_id,
            value.base_plan_revision_fingerprint,
        ):
            raise M3FeasibilitySupportStorageError(
                "SUPPORT_INPUT_MISMATCH", "evaluation_input"
            )
        _, worker_registry_provenance, _ = self._validate_source_cut(
            connection, value, evaluation
        )
        self._validate_support_bindings(connection, value)
        self._validate_support_content(
            connection, value, evaluation, worker_registry_provenance
        )
        return value

    def get_feasibility_support(
        self, support_snapshot_id: str
    ) -> FeasibilitySupportSnapshot:
        if type(support_snapshot_id) is not str or not support_snapshot_id.strip():
            raise M3FeasibilitySupportValidationError(
                "INVALID_VALUE", "support_snapshot_id"
            )
        with self._read_snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM m3_feasibility_support_snapshots WHERE support_snapshot_id=?",
                (support_snapshot_id,),
            ).fetchone()
            if row is None:
                raise M3FeasibilitySupportStorageError(
                    "SUPPORT_NOT_FOUND", "support_snapshot_id"
                )
            return self._support_from_row(connection, row)
