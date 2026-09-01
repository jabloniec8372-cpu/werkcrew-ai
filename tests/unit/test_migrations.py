from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from werkcrew_ai.migrations import MigrationStateError, discover_migrations
from werkcrew_ai.persistence import (
    MIGRATIONS_DIRECTORY,
    MIGRATION_PATH,
    SqliteBusinessRepository,
    SqlitePersistence,
)


NOW = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
MIGRATION_IDS = (
    "0001_m7_persistent_dispatch",
    "0002_sequential_migration_history",
    "0003_m1_canonical_job",
)
FROZEN_0001_SHA256 = (
    "2cbb90268d7a8ecd0ec7682e1265d70da2a700ca3399687bdc0b2c825e3af1fb"
)
FROZEN_0002_SHA256 = (
    "bfac9d609bcad36c1aea365e4711f1b033eb75fcdaafe56c37b9797bd439d983"
)


def _history(database_path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            """
            SELECT migration_id, version, name, checksum_sha256, applied_at
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()


def _initialize_legacy_0001(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (NOW.isoformat(),),
        )
        connection.execute(
            """
            INSERT INTO jobs(
                job_id, title, address_status, address_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                "gen1-existing-job",
                "Existing Gen1 job",
                "UNCONFIRMED",
                "{}",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )


def test_fresh_database_runs_all_migrations_in_sequence(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.db"
    persistence = SqlitePersistence(database_path)

    assert persistence.initialize(now=NOW) == MIGRATION_IDS
    assert persistence.applied_migration_ids() == MIGRATION_IDS

    rows = _history(database_path)
    assert [row[0] for row in rows] == list(MIGRATION_IDS)
    assert [row[1] for row in rows] == [1, 2, 3]


def test_existing_0001_database_applies_only_later_migrations(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "existing.db"
    _initialize_legacy_0001(database_path)
    persistence = SqlitePersistence(database_path)

    assert persistence.initialize(now=NOW) == MIGRATION_IDS[1:]
    assert persistence.applied_migration_ids() == MIGRATION_IDS

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT title FROM jobs WHERE job_id='gen1-existing-job'"
        ).fetchone() == ("Existing Gen1 job",)


def test_second_initialize_has_zero_duplicate_effects(tmp_path: Path) -> None:
    database_path = tmp_path / "idempotent.db"
    persistence = SqlitePersistence(database_path)

    assert persistence.initialize(now=NOW) == MIGRATION_IDS
    first_history = _history(database_path)
    assert persistence.initialize(now=NOW) == ()
    assert _history(database_path) == first_history


def test_migration_history_persists_exact_ids_and_checksums(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "history.db"
    persistence = SqlitePersistence(database_path)
    migrations = discover_migrations(MIGRATIONS_DIRECTORY)

    persistence.initialize(now=NOW)

    assert _history(database_path) == [
        (
            migration.migration_id,
            migration.version,
            migration.name,
            migration.checksum_sha256,
            NOW.isoformat(),
        )
        for migration in migrations
    ]


def test_unknown_migration_history_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "unknown.db"
    persistence = SqlitePersistence(database_path)
    persistence.initialize(now=NOW)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO schema_migrations(
                migration_id, version, name, checksum_sha256, applied_at
            ) VALUES('0004_unknown', 4, 'unknown', ?, ?)
            """,
            ("0" * 64, NOW.isoformat()),
        )

    with pytest.raises(MigrationStateError, match="unknown"):
        persistence.initialize(now=NOW)


def test_inconsistent_schema_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "inconsistent.db"
    persistence = SqlitePersistence(database_path)
    persistence.initialize(now=NOW)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE agent_trace_events")

    with pytest.raises(MigrationStateError, match="schema"):
        persistence.initialize(now=NOW)


def test_existing_repository_uses_shared_sqlite_foundation(tmp_path: Path) -> None:
    repository = SqliteBusinessRepository(tmp_path / "business.db")

    assert isinstance(repository, SqlitePersistence)
    assert repository.initialize(now=NOW) == MIGRATION_IDS
    assert repository.applied_migration_ids() == MIGRATION_IDS


def test_migration_0001_remains_frozen() -> None:
    assert hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest() == (
        FROZEN_0001_SHA256
    )


def test_migration_0002_remains_frozen() -> None:
    migration_path = MIGRATIONS_DIRECTORY / "0002_sequential_migration_history.sql"
    assert hashlib.sha256(migration_path.read_bytes()).hexdigest() == (
        FROZEN_0002_SHA256
    )
