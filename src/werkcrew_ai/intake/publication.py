"""Immutable canonical M1 to M2 handoff publication boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from werkcrew_ai.intake.models import (
    CanonicalJobActivity,
    CanonicalJobLifecycle,
    FactKnowledgeState,
    FactVerificationState,
    JobFactName,
)
from werkcrew_ai.intake.repository import (
    CanonicalJobNotFoundError,
    CanonicalJobPersistenceError,
    CanonicalJobRepository,
    _canonical_job_id,
    _require_aware,
)
from werkcrew_ai.persistence import MIGRATIONS_DIRECTORY


HANDOFF_PROJECTION_SCHEMA_VERSION = "m1-m2-handoff-v1"


class HandoffPublicationError(CanonicalJobPersistenceError):
    pass


class HandoffProjectionMismatchError(HandoffPublicationError):
    pass


class HandoffPublicationConflictError(HandoffPublicationError):
    pass


class HandoffNotFoundError(HandoffPublicationError):
    pass


class HandoffStorageIntegrityError(HandoffPublicationError):
    pass


@dataclass(frozen=True, slots=True)
class M1HandoffFact:
    name: JobFactName
    revision: int
    value: str | None
    knowledge_state: FactKnowledgeState
    verification_state: FactVerificationState
    provenance_source: str
    evidence_id: str | None

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("fact revision must be positive")
        if not self.provenance_source.strip():
            raise ValueError("provenance_source must be non-blank")


@dataclass(frozen=True, slots=True)
class M1HandoffProjection:
    job_id: str
    source_revision: int
    lifecycle_state: CanonicalJobLifecycle
    activity_state: CanonicalJobActivity
    facts: tuple[M1HandoffFact, ...]
    schema_version: str = HANDOFF_PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _canonical_job_id(self.job_id)
        if isinstance(self.source_revision, bool) or self.source_revision < 1:
            raise ValueError("source_revision must be positive")
        if self.schema_version != HANDOFF_PROJECTION_SCHEMA_VERSION:
            raise ValueError("unsupported handoff projection schema version")
        names = tuple(fact.name.value for fact in self.facts)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("handoff facts must be unique and sorted by name")


@dataclass(frozen=True, slots=True)
class M1HandoffPublication:
    handoff_id: str
    projection: M1HandoffProjection
    content_sha256: str
    published_at: datetime

    @property
    def job_id(self) -> str:
        return self.projection.job_id

    @property
    def source_revision(self) -> int:
        return self.projection.source_revision


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _projection_payload(projection: M1HandoffProjection) -> dict[str, Any]:
    return {
        "activity_state": projection.activity_state.value,
        "facts": [
            {
                "evidence_id": fact.evidence_id,
                "knowledge_state": fact.knowledge_state.value,
                "name": fact.name.value,
                "provenance_source": fact.provenance_source,
                "revision": fact.revision,
                "value": fact.value,
                "verification_state": fact.verification_state.value,
            }
            for fact in projection.facts
        ],
        "job_id": projection.job_id,
        "lifecycle_state": projection.lifecycle_state.value,
        "schema_version": projection.schema_version,
        "source_revision": projection.source_revision,
    }


def _projection_from_payload(value: dict[str, Any]) -> M1HandoffProjection:
    return M1HandoffProjection(
        job_id=value["job_id"],
        source_revision=value["source_revision"],
        lifecycle_state=CanonicalJobLifecycle(value["lifecycle_state"]),
        activity_state=CanonicalJobActivity(value["activity_state"]),
        facts=tuple(
            M1HandoffFact(
                name=JobFactName(fact["name"]),
                revision=fact["revision"],
                value=fact["value"],
                knowledge_state=FactKnowledgeState(fact["knowledge_state"]),
                verification_state=FactVerificationState(
                    fact["verification_state"]
                ),
                provenance_source=fact["provenance_source"],
                evidence_id=fact["evidence_id"],
            )
            for fact in value["facts"]
        ),
        schema_version=value["schema_version"],
    )


class M1BoundaryPublicationRepository(CanonicalJobRepository):
    """Publish immutable current-state handoffs through the shared SQLite DB."""

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

    def current_projection(self, job_id: str) -> M1HandoffProjection:
        canonical_id = _canonical_job_id(job_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            return self._projection_from_connection(connection, canonical_id)

    def publish_handoff(
        self,
        projection: M1HandoffProjection,
        *,
        published_at: datetime,
    ) -> M1HandoffPublication:
        _require_aware(published_at, "published_at")
        canonical_content = _canonical_json(_projection_payload(projection))
        content_sha256 = hashlib.sha256(
            canonical_content.encode("utf-8")
        ).hexdigest()

        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM m1_handoff_publications
                WHERE job_id = ? AND source_revision = ?
                """,
                (projection.job_id, projection.source_revision),
            ).fetchone()
            if existing is not None:
                publication = self._publication_from_row(existing)
                if (
                    existing["content_sha256"] == content_sha256
                    and existing["canonical_content_json"] == canonical_content
                ):
                    return publication
                raise HandoffPublicationConflictError(
                    "Conflicting handoff content already exists for "
                    f"({projection.job_id}, {projection.source_revision})"
                )

            current = self._projection_from_connection(
                connection,
                projection.job_id,
            )
            if current != projection:
                raise HandoffProjectionMismatchError(
                    "Handoff projection does not match current canonical M1 state"
                )

            publication = M1HandoffPublication(
                handoff_id=f"m1h-{uuid.uuid4().hex}",
                projection=projection,
                content_sha256=content_sha256,
                published_at=published_at,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO m1_handoff_publications(
                        job_id, source_revision, handoff_id,
                        projection_schema_version, content_sha256,
                        canonical_content_json, published_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        publication.job_id,
                        publication.source_revision,
                        publication.handoff_id,
                        publication.projection.schema_version,
                        publication.content_sha256,
                        canonical_content,
                        publication.published_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HandoffPublicationConflictError(
                    "Handoff publication identity conflict"
                ) from error
            return publication

    def get_handoff(
        self,
        job_id: str,
        source_revision: int,
    ) -> M1HandoffPublication:
        canonical_id = _canonical_job_id(job_id)
        if isinstance(source_revision, bool) or source_revision < 1:
            raise ValueError("source_revision must be positive")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM m1_handoff_publications
                WHERE job_id = ? AND source_revision = ?
                """,
                (canonical_id, source_revision),
            ).fetchone()
        if row is None:
            raise HandoffNotFoundError(
                f"Unknown M1 handoff: ({canonical_id}, {source_revision})"
            )
        return self._publication_from_row(row)

    def handoffs_for_job(
        self,
        job_id: str,
    ) -> tuple[M1HandoffPublication, ...]:
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
                SELECT * FROM m1_handoff_publications
                WHERE job_id = ?
                ORDER BY source_revision
                """,
                (canonical_id,),
            ).fetchall()
        return tuple(self._publication_from_row(row) for row in rows)

    @staticmethod
    def _projection_from_connection(
        connection: sqlite3.Connection,
        job_id: str,
    ) -> M1HandoffProjection:
        row = connection.execute(
            """
            SELECT job_id, lifecycle_state, activity_state, source_revision
            FROM canonical_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise CanonicalJobNotFoundError(f"Unknown canonical job: {job_id}")
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
            (job_id,),
        ).fetchall()
        return M1HandoffProjection(
            job_id=row["job_id"],
            source_revision=row["source_revision"],
            lifecycle_state=CanonicalJobLifecycle(row["lifecycle_state"]),
            activity_state=CanonicalJobActivity(row["activity_state"]),
            facts=tuple(
                M1HandoffFact(
                    name=JobFactName(fact["fact_name"]),
                    revision=fact["revision"],
                    value=fact["fact_value"],
                    knowledge_state=FactKnowledgeState(fact["knowledge_state"]),
                    verification_state=FactVerificationState(
                        fact["verification_state"]
                    ),
                    provenance_source=fact["provenance_source"],
                    evidence_id=fact["follow_up_evidence_id"],
                )
                for fact in fact_rows
            ),
        )

    @staticmethod
    def _publication_from_row(row: sqlite3.Row) -> M1HandoffPublication:
        raw = row["canonical_content_json"]
        expected_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if expected_hash != row["content_sha256"]:
            raise HandoffStorageIntegrityError(
                "Stored handoff content hash does not match"
            )
        try:
            payload = json.loads(raw)
            projection = _projection_from_payload(payload)
            published_at = datetime.fromisoformat(row["published_at"])
            _require_aware(published_at, "stored published_at")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise HandoffStorageIntegrityError(
                "Stored handoff content is invalid"
            ) from error
        if _canonical_json(_projection_payload(projection)) != raw:
            raise HandoffStorageIntegrityError(
                "Stored handoff content is not canonical JSON"
            )
        if (
            projection.job_id != row["job_id"]
            or projection.source_revision != row["source_revision"]
            or projection.schema_version != row["projection_schema_version"]
        ):
            raise HandoffStorageIntegrityError(
                "Stored handoff identity does not match its content"
            )
        return M1HandoffPublication(
            handoff_id=row["handoff_id"],
            projection=projection,
            content_sha256=row["content_sha256"],
            published_at=published_at,
        )
