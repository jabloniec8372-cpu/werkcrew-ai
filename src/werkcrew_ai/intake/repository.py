"""SQLite-owned repository for the canonical, partial M1 job."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from werkcrew_ai.intake.models import (
    CanonicalFactInput,
    CanonicalJob,
    CanonicalJobFact,
    CanonicalJobIntake,
    CanonicalJobLifecycle,
    FactKnowledgeState,
    FactVerificationState,
    JobFactName,
)
from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY, SqlitePersistence


class CanonicalJobPersistenceError(RuntimeError):
    pass


class DuplicateCanonicalJobError(CanonicalJobPersistenceError):
    pass


class CanonicalJobNotFoundError(CanonicalJobPersistenceError):
    pass


class FactRevisionConflictError(CanonicalJobPersistenceError):
    pass


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _canonical_job_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("job_id must be non-blank")
    if value != value.strip():
        raise ValueError("job_id must not contain surrounding whitespace")
    return value


class CanonicalJobRepository(SqlitePersistence):
    """Persist new M1 jobs only through the shared SQLite foundation."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        migrations_directory: str | Path = MIGRATIONS_DIRECTORY,
    ) -> None:
        super().__init__(
            database_path,
            migrations_directory=migrations_directory,
        )

    def create_job(
        self,
        *,
        job_id: str,
        intake: CanonicalJobIntake,
        created_at: datetime,
    ) -> CanonicalJob:
        """Create one durable RECEIVED job and its exact initial evidence."""

        canonical_id = _canonical_job_id(job_id)
        _require_aware(created_at, "created_at")
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO canonical_jobs(
                        job_id, lifecycle_state, created_at, updated_at
                    ) VALUES(?, 'RECEIVED', ?, ?)
                    """,
                    (canonical_id, created_at.isoformat(), created_at.isoformat()),
                )
                connection.execute(
                    """
                    INSERT INTO canonical_job_intake(
                        job_id, intake_source, raw_text, raw_payload, received_at
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_id,
                        intake.intake_source,
                        intake.raw_text,
                        intake.raw_payload,
                        created_at.isoformat(),
                    ),
                )
                for fact in intake.facts:
                    self._insert_fact(
                        connection,
                        job_id=canonical_id,
                        fact=fact,
                        revision=1,
                        recorded_at=created_at,
                    )
        except sqlite3.IntegrityError as error:
            raise DuplicateCanonicalJobError(
                f"Canonical job already exists: {canonical_id}"
            ) from error
        return self.get_job(canonical_id)

    def get_job(self, job_id: str) -> CanonicalJob:
        canonical_id = _canonical_job_id(job_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    j.job_id,
                    j.lifecycle_state,
                    j.created_at,
                    j.updated_at,
                    i.intake_source,
                    i.raw_text,
                    i.raw_payload
                FROM canonical_jobs AS j
                JOIN canonical_job_intake AS i ON i.job_id = j.job_id
                WHERE j.job_id = ?
                """,
                (canonical_id,),
            ).fetchone()
            if row is None:
                raise CanonicalJobNotFoundError(
                    f"Unknown canonical job: {canonical_id}"
                )
            fact_rows = connection.execute(
                """
                SELECT f.*
                FROM canonical_job_facts AS f
                WHERE f.job_id = ?
                  AND f.revision = (
                    SELECT MAX(current.revision)
                    FROM canonical_job_facts AS current
                    WHERE current.job_id = f.job_id
                      AND current.fact_name = f.fact_name
                  )
                ORDER BY f.fact_name
                """,
                (canonical_id,),
            ).fetchall()
        return CanonicalJob(
            job_id=row["job_id"],
            lifecycle_state=CanonicalJobLifecycle(row["lifecycle_state"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            intake_source=row["intake_source"],
            raw_text=row["raw_text"],
            raw_payload=row["raw_payload"],
            facts=tuple(self._fact_from_row(item) for item in fact_rows),
        )

    def record_fact(
        self,
        *,
        job_id: str,
        fact: CanonicalFactInput,
        recorded_at: datetime,
        expected_revision: int | None = None,
    ) -> CanonicalJobFact:
        """Append a fact revision; replacing current knowledge must be explicit."""

        canonical_id = _canonical_job_id(job_id)
        _require_aware(recorded_at, "recorded_at")
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT updated_at FROM canonical_jobs WHERE job_id = ?",
                (canonical_id,),
            ).fetchone()
            if job is None:
                raise CanonicalJobNotFoundError(
                    f"Unknown canonical job: {canonical_id}"
                )
            if recorded_at < datetime.fromisoformat(job["updated_at"]):
                raise ValueError("recorded_at cannot precede the current job state")
            current = connection.execute(
                """
                SELECT MAX(revision) AS revision
                FROM canonical_job_facts
                WHERE job_id = ? AND fact_name = ?
                """,
                (canonical_id, fact.name.value),
            ).fetchone()["revision"]
            if current is None:
                if expected_revision is not None:
                    raise FactRevisionConflictError(
                        "New fact cannot declare an existing revision"
                    )
                revision = 1
            else:
                if expected_revision != current:
                    raise FactRevisionConflictError(
                        f"Expected fact revision {current}"
                    )
                revision = current + 1
            self._insert_fact(
                connection,
                job_id=canonical_id,
                fact=fact,
                revision=revision,
                recorded_at=recorded_at,
            )
            connection.execute(
                "UPDATE canonical_jobs SET updated_at = ? WHERE job_id = ?",
                (recorded_at.isoformat(), canonical_id),
            )
        return CanonicalJobFact(
            job_id=canonical_id,
            name=fact.name,
            revision=revision,
            value=fact.value,
            knowledge_state=fact.knowledge_state,
            verification_state=fact.verification_state,
            provenance_source=fact.provenance_source,
            recorded_at=recorded_at,
        )

    def fact_history(
        self,
        job_id: str,
        name: JobFactName,
    ) -> tuple[CanonicalJobFact, ...]:
        canonical_id = _canonical_job_id(job_id)
        with closing(self._connect()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM canonical_jobs WHERE job_id = ?",
                (canonical_id,),
            ).fetchone()
            if exists is None:
                raise CanonicalJobNotFoundError(
                    f"Unknown canonical job: {canonical_id}"
                )
            rows = connection.execute(
                """
                SELECT * FROM canonical_job_facts
                WHERE job_id = ? AND fact_name = ?
                ORDER BY revision
                """,
                (canonical_id, name.value),
            ).fetchall()
        return tuple(self._fact_from_row(row) for row in rows)

    @staticmethod
    def _insert_fact(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        fact: CanonicalFactInput,
        revision: int,
        recorded_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO canonical_job_facts(
                job_id, fact_name, revision, fact_value,
                knowledge_state, verification_state,
                provenance_source, recorded_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                fact.name.value,
                revision,
                fact.value,
                fact.knowledge_state.value,
                fact.verification_state.value,
                fact.provenance_source,
                recorded_at.isoformat(),
            ),
        )

    @staticmethod
    def _fact_from_row(row: sqlite3.Row) -> CanonicalJobFact:
        return CanonicalJobFact(
            job_id=row["job_id"],
            name=JobFactName(row["fact_name"]),
            revision=row["revision"],
            value=row["fact_value"],
            knowledge_state=FactKnowledgeState(row["knowledge_state"]),
            verification_state=FactVerificationState(
                row["verification_state"]
            ),
            provenance_source=row["provenance_source"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )
